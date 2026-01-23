"""
Claude Agent SDK LLM Provider for HolmesGPT

This module provides an LLM implementation that uses the Claude Agent SDK
instead of the HTTP API or CLI. This enables:
- Using your Claude Max subscription ($20/mo flat rate)
- Proper MCP tool integration with structured tool calling
- SDK-managed agentic loop for tool execution

The Claude Agent SDK handles the entire agentic loop internally, so this LLM
implementation bypasses HolmesGPT's manual tool calling loop by returning
responses without tool_calls.
"""

import asyncio
import logging
import sys
from typing import Any, Dict, List, Optional, Type, Union

import nest_asyncio
from litellm.litellm_core_utils.streaming_handler import CustomStreamWrapper
from litellm.types.utils import (
    Choices,
    Message,
    ModelResponse,
    Usage,
)
from pydantic import BaseModel

from holmes.core.llm import LLM, TokenCountMetadata
from holmes.core.mcp_tools import (
    create_holmes_mcp_server,
    get_allowed_tools_list,
    set_execution_context,
    clear_execution_context,
)

# Apply nest_asyncio to allow running async code in existing event loops
# This is needed because HolmesGPT's LLM.completion() is synchronous
nest_asyncio.apply()

logger = logging.getLogger(__name__)

# Model mapping from HolmesGPT model names to Claude SDK model names
MODEL_MAPPING = {
    "anthropic/claude-sonnet-4-5-20250929": "sonnet",
    "anthropic/claude-opus-4-5-20251101": "opus",
    "anthropic/claude-haiku-3-5-20241022": "haiku",
    "claude-sonnet-4-5": "sonnet",
    "claude-opus-4-5": "opus",
    "claude-haiku-3-5": "haiku",
    "sonnet": "sonnet",
    "opus": "opus",
    "haiku": "haiku",
}

# Default context window sizes for Claude models
CONTEXT_WINDOWS = {
    "sonnet": 200000,
    "opus": 200000,
    "haiku": 200000,
}

# Default max output tokens
MAX_OUTPUT_TOKENS = {
    "sonnet": 64000,
    "opus": 32000,
    "haiku": 8192,
}


class ClaudeAgentSDKLLM(LLM):
    """
    LLM implementation using Claude Agent SDK.

    This uses your Claude Max subscription instead of API credits and provides
    proper MCP tool integration. The SDK handles the agentic loop internally,
    so we don't return tool_calls in responses (which would trigger HolmesGPT's loop).

    Key differences from ClaudeCLILLM:
    - Uses SDK instead of CLI subprocess
    - Registers HolmesGPT tools as MCP tools
    - SDK executes tools directly via MCP protocol
    - Returns final response only (no intermediate tool_calls)
    """

    def __init__(
        self,
        model: str = "sonnet",
        max_turns: int = 40,
        permission_mode: str = "bypassPermissions",
        tool_executor: Optional[Any] = None,
    ):
        """
        Initialize the Claude Agent SDK LLM.

        Args:
            model: Model name (sonnet, opus, haiku) or full Anthropic model name
            max_turns: Maximum conversation turns for SDK to handle
            permission_mode: Permission mode for tool execution
            tool_executor: HolmesGPT ToolExecutor for accessing tools
        """
        self.model = self._normalize_model_name(model)
        self.max_turns = max_turns
        self.permission_mode = permission_mode
        self.tool_executor = tool_executor
        self._tools: List[Any] = []

        logger.info(
            f"Initialized ClaudeAgentSDKLLM with model={self.model}, "
            f"max_turns={self.max_turns}, permission_mode={self.permission_mode}"
        )

    def _normalize_model_name(self, model: str) -> str:
        """
        Convert various model name formats to SDK-compatible names.

        Handles:
        - Direct mappings (e.g., "sonnet" -> "sonnet")
        - Full Anthropic names (e.g., "anthropic/claude-sonnet-4-5-20250929" -> "sonnet")
        - Pattern-based extraction for new models (e.g., "claude-3.5-sonnet-v2" -> "sonnet")
        """
        # Try direct mapping first
        if model in MODEL_MAPPING:
            return MODEL_MAPPING[model]

        # Try pattern-based extraction for Claude models
        model_lower = model.lower()

        # Check for known model family names in the string
        for family in ["sonnet", "opus", "haiku"]:
            if family in model_lower:
                return family

        # Final fallback: extract base name (e.g., "some/model-name-v1" -> "model")
        base_name = model.split("/")[-1].split("-")[0]
        logger.warning(
            f"Unknown model '{model}', falling back to base name: {base_name}. "
            f"Consider adding explicit mapping for this model."
        )
        return base_name

    def set_tool_executor(self, tool_executor: Any) -> None:
        """
        Set the tool executor to access HolmesGPT tools.

        This is called by the config layer to provide access to tools
        that will be converted to MCP format.

        Args:
            tool_executor: HolmesGPT ToolExecutor instance
        """
        self.tool_executor = tool_executor
        if tool_executor:
            self._tools = list(tool_executor.tools_by_name.values())
            logger.info(f"SDK LLM received {len(self._tools)} tools from executor")

    def set_tools(self, tools: List[Any]) -> None:
        """
        Directly set the tools to be converted to MCP format.

        Args:
            tools: List of HolmesGPT Tool instances
        """
        self._tools = tools
        logger.info(f"SDK LLM set {len(tools)} tools directly")

    def _build_prompt_from_messages(self, messages: List[Dict[str, Any]]) -> str:
        """
        Convert HolmesGPT messages format to a single prompt for Claude SDK.

        Args:
            messages: List of message dicts with 'role' and 'content'

        Returns:
            Single prompt string
        """
        prompt_parts = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            # Handle content that might be a list (multimodal)
            if isinstance(content, list):
                text_parts = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                    elif isinstance(part, str):
                        text_parts.append(part)
                content = "\n".join(text_parts)

            if role == "system":
                prompt_parts.append(f"SYSTEM INSTRUCTIONS:\n{content}\n")
            elif role == "user":
                prompt_parts.append(f"USER: {content}\n")
            elif role == "assistant":
                prompt_parts.append(f"ASSISTANT: {content}\n")
            elif role == "tool":
                tool_name = msg.get("name", "tool")
                prompt_parts.append(f"TOOL RESULT ({tool_name}):\n{content}\n")

        return "\n".join(prompt_parts)

    async def _execute_sdk_query(
        self,
        prompt: str,
        tools: Optional[List[Any]] = None,
    ) -> str:
        """
        Execute a query using Claude Agent SDK.

        This is the async implementation that:
        1. Converts HolmesGPT tools to MCP format
        2. Creates an SDK client with MCP server
        3. Executes the query (SDK handles tool calling internally)
        4. Returns the final response text

        Args:
            prompt: The prompt to send to Claude
            tools: Optional list of HolmesGPT tools (uses self._tools if not provided)

        Returns:
            Final response text from Claude
        """
        from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, AssistantMessage, TextBlock, ResultMessage

        # Use provided tools or fall back to instance tools
        tools_to_use = tools or self._tools

        # Set up execution context for MCP tool handlers
        set_execution_context(
            llm=self,
            max_token_count=self.get_max_token_count_for_single_tool(),
            tools_by_name={t.name: t for t in tools_to_use} if tools_to_use else {},
        )

        try:
            # Build options
            options_kwargs: Dict[str, Any] = {
                "model": self.model,
                "max_turns": self.max_turns,
                "permission_mode": self.permission_mode,
            }

            # If we have tools, create MCP server
            if tools_to_use:
                mcp_server = create_holmes_mcp_server(
                    tools=tools_to_use,
                    server_name="holmes",
                    version="1.0.0"
                )
                allowed_tools = get_allowed_tools_list(tools_to_use, "holmes")

                options_kwargs["mcp_servers"] = {"holmes": mcp_server}
                options_kwargs["allowed_tools"] = allowed_tools

                logger.info(
                    f"SDK query with {len(tools_to_use)} tools: "
                    f"{[t.name for t in tools_to_use[:5]]}..."
                )

            options = ClaudeAgentOptions(**options_kwargs)

            result_text = ""
            total_cost_usd = 0.0
            num_turns = 0

            # Execute via SDK
            async with ClaudeSDKClient(options=options) as client:
                await client.query(prompt)

                # Collect response
                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                result_text += block.text
                    elif isinstance(message, ResultMessage):
                        if message.total_cost_usd:
                            total_cost_usd = message.total_cost_usd
                        num_turns = message.num_turns
                        logger.info(
                            f"SDK query completed: {num_turns} turns, "
                            f"${total_cost_usd:.4f} cost"
                        )

            return result_text

        finally:
            # Clean up execution context
            clear_execution_context()

    def completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Union[str, dict]] = None,
        response_format: Optional[Union[dict, Type[BaseModel]]] = None,
        temperature: Optional[float] = None,
        drop_params: Optional[bool] = None,
        stream: Optional[bool] = None,
    ) -> Union[ModelResponse, CustomStreamWrapper]:
        """
        Execute completion using Claude Agent SDK.

        This method converts the HolmesGPT message format to a prompt,
        executes it via SDK (which handles tools internally via MCP),
        and returns the result in the expected ModelResponse format.

        IMPORTANT: Since the SDK handles the entire agentic loop internally,
        we always return a final response WITHOUT tool_calls. This signals
        to HolmesGPT that investigation is complete and prevents it from
        trying to execute tools again (the SDK already did that).

        Args:
            messages: List of message dicts
            tools: Tool definitions (converted from HolmesGPT format)
            tool_choice: Ignored - SDK handles tool selection
            response_format: Ignored
            temperature: Ignored
            drop_params: Ignored
            stream: Ignored - SDK handles streaming internally

        Returns:
            ModelResponse with final answer (no tool_calls)
        """
        # Build prompt from messages
        prompt = self._build_prompt_from_messages(messages)

        logger.info(f"SDK completion: {len(messages)} messages, prompt length: {len(prompt)}")

        # Execute via SDK (handles the agentic loop internally)
        try:
            # Run async code synchronously
            # nest_asyncio allows this to work even if already in an event loop
            loop = asyncio.get_event_loop()
            response_text = loop.run_until_complete(
                self._execute_sdk_query(prompt, self._tools)
            )
        except RuntimeError:
            # If no event loop, create one
            response_text = asyncio.run(
                self._execute_sdk_query(prompt, self._tools)
            )

        # Build ModelResponse in LiteLLM-compatible format
        # Since SDK handles tools internally, we return the final result
        # without tool_calls - this signals to HolmesGPT that investigation is complete
        model_response = ModelResponse(
            id="claude-sdk-response",
            created=0,
            model=f"claude-sdk/{self.model}",
            object="chat.completion",
            choices=[
                Choices(
                    index=0,
                    finish_reason="stop",
                    message=Message(
                        role="assistant",
                        content=response_text,
                        tool_calls=None,  # No tool_calls = investigation complete
                    ),
                )
            ],
            usage=Usage(
                prompt_tokens=len(prompt) // 4,  # Rough estimate
                completion_tokens=len(response_text) // 4,
                total_tokens=(len(prompt) + len(response_text)) // 4,
            ),
        )

        logger.info(f"SDK completion done: {len(response_text)} chars response")

        return model_response

    def get_context_window_size(self) -> int:
        """Return the context window size for the model."""
        return CONTEXT_WINDOWS.get(self.model, 200000)

    def get_maximum_output_token(self) -> int:
        """Return the maximum output tokens for the model."""
        return MAX_OUTPUT_TOKENS.get(self.model, 64000)

    def count_tokens(
        self,
        messages: list[dict],
        tools: Optional[list[dict[str, Any]]] = None
    ) -> TokenCountMetadata:
        """
        Estimate token count for messages.

        Since we don't have direct access to Claude's tokenizer via SDK,
        we use a rough estimate of 3 characters per token (more conservative
        than the typical 4 chars/token) to provide safety margin.

        WARNING: This is a rough approximation. Actual token counts may vary:
        - Non-ASCII text (especially CJK characters) may use more tokens per char
        - Code with many special characters may tokenize differently
        - JSON/YAML structures may have higher token density

        For production use with critical context window management, consider
        using the anthropic package's count_tokens() for accurate counts.
        """
        total_chars = 0
        system_chars = 0
        user_chars = 0
        assistant_chars = 0
        tool_chars = 0

        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    p.get("text", "") if isinstance(p, dict) else str(p)
                    for p in content
                )

            char_count = len(str(content))
            total_chars += char_count

            role = msg.get("role", "user")
            if role == "system":
                system_chars += char_count
            elif role == "user":
                user_chars += char_count
            elif role == "assistant":
                assistant_chars += char_count
            elif role == "tool":
                tool_chars += char_count

        # Estimate tool definition tokens
        tools_to_call_chars = 0
        if tools:
            # Rough estimate: 50 chars per tool for definition overhead
            tools_to_call_chars = sum(
                len(str(t.get("function", {}).get("description", ""))) + 50
                for t in tools
            )
            total_chars += tools_to_call_chars

        # Convert to tokens (conservative estimate: 3 chars per token for safety margin)
        # This errs on the side of overestimating token usage to avoid context overflow
        return TokenCountMetadata(
            total_tokens=total_chars // 3,
            system_tokens=system_chars // 3,
            user_tokens=user_chars // 3,
            assistant_tokens=assistant_chars // 3,
            tools_tokens=tool_chars // 3,
            tools_to_call_tokens=tools_to_call_chars // 3,
            other_tokens=0,
        )


def create_claude_sdk_llm(
    model: str = "sonnet",
    max_turns: int = 40,
    tool_executor: Optional[Any] = None,
    **kwargs
) -> ClaudeAgentSDKLLM:
    """
    Factory function to create a Claude Agent SDK LLM instance.

    Args:
        model: Model name (sonnet, opus, haiku)
        max_turns: Maximum conversation turns
        tool_executor: HolmesGPT ToolExecutor for accessing tools
        **kwargs: Additional arguments (ignored for compatibility)

    Returns:
        ClaudeAgentSDKLLM instance
    """
    return ClaudeAgentSDKLLM(
        model=model,
        max_turns=max_turns,
        tool_executor=tool_executor,
    )
