"""
Claude CLI LLM Provider for HolmesGPT

This module provides an LLM implementation that uses the Claude CLI instead of
the Anthropic HTTP API. This allows using your Claude Max subscription instead
of paying for API credits.

The Claude CLI handles the agentic loop internally, so we delegate tool execution
to it rather than managing the loop ourselves.
"""

import json
import logging
import os
import subprocess
from typing import Any, Dict, List, Optional, Type, Union

from litellm.litellm_core_utils.streaming_handler import CustomStreamWrapper
from litellm.types.utils import (
    Choices,
    Message,
    ModelResponse,
    Usage,
)
from pydantic import BaseModel

from holmes.core.llm import LLM, TokenCountMetadata

logger = logging.getLogger(__name__)

# Model mapping from HolmesGPT model names to Claude CLI model names
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


class ClaudeCLILLM(LLM):
    """
    LLM implementation using Claude CLI.

    This uses your Claude Max subscription instead of API credits.
    The Claude CLI handles tool execution internally.
    """

    def __init__(
        self,
        model: str = "sonnet",
        timeout: int = 300,
        allowed_tools: Optional[List[str]] = None,
    ):
        """
        Initialize the Claude CLI LLM.

        Args:
            model: Model name (sonnet, opus, haiku) or full Anthropic model name
            timeout: Timeout in seconds for CLI execution
            allowed_tools: List of tools to allow (default: ["Bash"] for kubectl)
        """
        self.model = self._normalize_model_name(model)
        self.timeout = timeout
        self.allowed_tools = allowed_tools or ["Bash"]

        # Verify Claude CLI is available
        self._check_cli_available()

    def _normalize_model_name(self, model: str) -> str:
        """Convert various model name formats to CLI-compatible names."""
        return MODEL_MAPPING.get(model, model.split("/")[-1].split("-")[0])

    def _check_cli_available(self) -> None:
        """Verify Claude CLI is installed and accessible."""
        try:
            result = subprocess.run(
                ["claude", "--help"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode != 0:
                logger.warning(f"Claude CLI check returned non-zero: {result.stderr}")
        except FileNotFoundError:
            raise RuntimeError(
                "Claude CLI not found. Please install it: https://claude.ai/code"
            )
        except Exception as e:
            raise RuntimeError(f"Error checking Claude CLI: {e}")

    def _build_prompt_from_messages(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """
        Convert HolmesGPT messages format to a single prompt for Claude CLI.

        Args:
            messages: List of message dicts with 'role' and 'content'
            tools: Available tools (used to generate tool descriptions)

        Returns:
            Single prompt string
        """
        prompt_parts = []

        # Process messages
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
                # Tool results from previous calls
                tool_name = msg.get("name", "tool")
                prompt_parts.append(f"TOOL RESULT ({tool_name}):\n{content}\n")

        # Add tool descriptions if provided
        if tools:
            tools_desc = self._format_tools_description(tools)
            prompt_parts.insert(0, f"AVAILABLE TOOLS:\n{tools_desc}\n\n")

        return "\n".join(prompt_parts)

    def _format_tools_description(self, tools: List[Dict[str, Any]]) -> str:
        """Format tool definitions for the prompt."""
        descriptions = []
        for tool in tools:
            func = tool.get("function", {})
            name = func.get("name", "unknown")
            desc = func.get("description", "No description")
            params = func.get("parameters", {})

            param_desc = ""
            if params.get("properties"):
                param_list = []
                for pname, pinfo in params["properties"].items():
                    param_list.append(f"  - {pname}: {pinfo.get('description', 'No description')}")
                param_desc = "\n" + "\n".join(param_list)

            descriptions.append(f"- {name}: {desc}{param_desc}")

        return "\n".join(descriptions)

    def _execute_claude_cli(self, prompt: str) -> str:
        """
        Execute Claude CLI with the given prompt.

        Args:
            prompt: The prompt to send to Claude

        Returns:
            Claude's response text
        """
        # Build command
        cmd = [
            "claude",
            "-p", prompt,
            "--model", self.model,
            "--output-format", "text",
        ]

        # Add allowed tools
        if self.allowed_tools:
            cmd.extend(["--allowedTools", ",".join(self.allowed_tools)])

        # Set up environment
        env = os.environ.copy()
        env["USE_GKE_GCLOUD_AUTH_PLUGIN"] = "True"

        logger.info(f"Executing Claude CLI with model={self.model}, tools={self.allowed_tools}")
        logger.debug(f"Prompt length: {len(prompt)} chars")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=env,
            )

            if result.returncode != 0:
                error_msg = result.stderr or "Unknown error"
                logger.error(f"Claude CLI error (code {result.returncode}): {error_msg}")
                raise RuntimeError(f"Claude CLI failed: {error_msg}")

            response = result.stdout.strip()
            logger.debug(f"Claude CLI response length: {len(response)} chars")

            return response

        except subprocess.TimeoutExpired:
            logger.error(f"Claude CLI timed out after {self.timeout}s")
            raise RuntimeError(f"Claude CLI timed out after {self.timeout} seconds")

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
        Execute completion using Claude CLI.

        This method converts the HolmesGPT message format to a prompt,
        executes it via Claude CLI (which handles tools internally),
        and returns the result in the expected ModelResponse format.

        Note: Claude CLI handles the entire agentic loop internally,
        so we always return a final response without tool_calls.
        """
        # Build prompt from messages
        prompt = self._build_prompt_from_messages(messages, tools)

        # Execute via CLI
        response_text = self._execute_claude_cli(prompt)

        # Build ModelResponse in LiteLLM-compatible format
        # Since Claude CLI handles tools internally, we return the final result
        # without tool_calls - this signals to HolmesGPT that investigation is complete
        model_response = ModelResponse(
            id="claude-cli-response",
            created=0,
            model=f"claude-cli/{self.model}",
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

        Since we don't have direct access to Claude's tokenizer via CLI,
        we use a rough estimate of 4 characters per token.
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

        # Add tools description if provided
        tools_to_call_chars = 0
        if tools:
            tools_desc = self._format_tools_description(tools)
            tools_to_call_chars = len(tools_desc)
            total_chars += tools_to_call_chars

        # Convert to tokens (rough estimate: 4 chars per token)
        return TokenCountMetadata(
            total_tokens=total_chars // 4,
            system_tokens=system_chars // 4,
            user_tokens=user_chars // 4,
            assistant_tokens=assistant_chars // 4,
            tools_tokens=tool_chars // 4,
            tools_to_call_tokens=tools_to_call_chars // 4,
            other_tokens=0,
        )


def create_claude_cli_llm(
    model: str = "sonnet",
    timeout: int = 300,
    **kwargs
) -> ClaudeCLILLM:
    """
    Factory function to create a Claude CLI LLM instance.

    Args:
        model: Model name (sonnet, opus, haiku)
        timeout: Timeout in seconds
        **kwargs: Additional arguments (ignored for compatibility)

    Returns:
        ClaudeCLILLM instance
    """
    return ClaudeCLILLM(model=model, timeout=timeout)
