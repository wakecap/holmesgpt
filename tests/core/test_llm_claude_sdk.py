"""
Unit tests for Claude Agent SDK LLM provider.

Tests the ClaudeAgentSDKLLM class that integrates HolmesGPT with
the Claude Agent SDK for Max subscription usage.
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from typing import Dict, Any, Optional


class TestClaudeAgentSDKLLM:
    """Tests for ClaudeAgentSDKLLM class."""

    @pytest.fixture
    def sdk_llm(self):
        """Create a ClaudeAgentSDKLLM instance for testing."""
        # Patch the SDK imports to avoid actual SDK dependency
        with patch("holmes.core.llm_claude_sdk.nest_asyncio"):
            from holmes.core.llm_claude_sdk import ClaudeAgentSDKLLM

            llm = ClaudeAgentSDKLLM(
                model="sonnet",
                max_turns=20,
                permission_mode="bypassPermissions",
            )
            return llm

    def test_initialization(self, sdk_llm):
        """Test SDK LLM initialization with default values."""
        assert sdk_llm.model == "sonnet"
        assert sdk_llm.max_turns == 20
        assert sdk_llm.permission_mode == "bypassPermissions"

    def test_model_name_normalization(self, sdk_llm):
        """Test that various model name formats are normalized correctly."""
        test_cases = [
            ("anthropic/claude-sonnet-4-5-20250929", "sonnet"),
            ("anthropic/claude-opus-4-5-20251101", "opus"),
            ("claude-sonnet-4-5", "sonnet"),
            ("claude-opus-4-5", "opus"),
            ("sonnet", "sonnet"),
            ("opus", "opus"),
            ("haiku", "haiku"),
        ]

        for input_model, expected in test_cases:
            result = sdk_llm._normalize_model_name(input_model)
            assert result == expected, f"Failed for {input_model}: got {result}"

    def test_unknown_model_fallback(self, sdk_llm):
        """Test that unknown model names fall back to extracting base name."""
        # Pattern-based fallback should extract the first part
        result = sdk_llm._normalize_model_name("claude-new-model-v99")
        # Should extract 'claude' as fallback
        assert result is not None

    def test_context_window_size(self, sdk_llm):
        """Test context window size for different models."""
        from holmes.core.llm_claude_sdk import ClaudeAgentSDKLLM

        # Sonnet model
        sonnet_llm = ClaudeAgentSDKLLM(model="sonnet")
        assert sonnet_llm.get_context_window_size() == 200000

        # Opus model
        opus_llm = ClaudeAgentSDKLLM(model="opus")
        assert opus_llm.get_context_window_size() == 200000

        # Haiku model
        haiku_llm = ClaudeAgentSDKLLM(model="haiku")
        assert haiku_llm.get_context_window_size() == 200000

    def test_max_output_tokens(self, sdk_llm):
        """Test max output tokens for different models."""
        from holmes.core.llm_claude_sdk import ClaudeAgentSDKLLM

        # Sonnet should have highest output
        sonnet_llm = ClaudeAgentSDKLLM(model="sonnet")
        assert sonnet_llm.get_maximum_output_token() == 64000

        # Opus has lower max output
        opus_llm = ClaudeAgentSDKLLM(model="opus")
        assert opus_llm.get_maximum_output_token() == 32000

        # Haiku has lowest
        haiku_llm = ClaudeAgentSDKLLM(model="haiku")
        assert haiku_llm.get_maximum_output_token() == 8192


class TestBuildPromptFromMessages:
    """Tests for message-to-prompt conversion."""

    @pytest.fixture
    def sdk_llm(self):
        """Create SDK LLM for testing."""
        with patch("holmes.core.llm_claude_sdk.nest_asyncio"):
            from holmes.core.llm_claude_sdk import ClaudeAgentSDKLLM

            return ClaudeAgentSDKLLM(model="sonnet")

    def test_simple_user_message(self, sdk_llm):
        """Test conversion of simple user message."""
        messages = [{"role": "user", "content": "What pods are failing?"}]

        prompt = sdk_llm._build_prompt_from_messages(messages)

        assert "USER: What pods are failing?" in prompt

    def test_system_message(self, sdk_llm):
        """Test conversion of system message."""
        messages = [
            {"role": "system", "content": "You are a K8s expert."},
            {"role": "user", "content": "Help me debug."},
        ]

        prompt = sdk_llm._build_prompt_from_messages(messages)

        assert "SYSTEM INSTRUCTIONS:" in prompt
        assert "You are a K8s expert." in prompt

    def test_multi_turn_conversation(self, sdk_llm):
        """Test conversion of multi-turn conversation."""
        messages = [
            {"role": "system", "content": "Be helpful."},
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
            {"role": "user", "content": "Follow up"},
        ]

        prompt = sdk_llm._build_prompt_from_messages(messages)

        assert "SYSTEM INSTRUCTIONS:" in prompt
        assert "USER: First question" in prompt
        assert "ASSISTANT: First answer" in prompt
        assert "USER: Follow up" in prompt

    def test_tool_result_message(self, sdk_llm):
        """Test conversion of tool result message."""
        messages = [
            {
                "role": "tool",
                "name": "kubectl_get",
                "content": "pod/nginx Running",
            }
        ]

        prompt = sdk_llm._build_prompt_from_messages(messages)

        assert "TOOL RESULT (kubectl_get):" in prompt
        assert "pod/nginx Running" in prompt

    def test_multimodal_content_list(self, sdk_llm):
        """Test handling of multimodal content as list."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Look at this:"},
                    {"type": "text", "text": "Pod is crashing."},
                ],
            }
        ]

        prompt = sdk_llm._build_prompt_from_messages(messages)

        assert "Look at this:" in prompt
        assert "Pod is crashing." in prompt


class TestTokenCounting:
    """Tests for token estimation."""

    @pytest.fixture
    def sdk_llm(self):
        """Create SDK LLM for testing."""
        with patch("holmes.core.llm_claude_sdk.nest_asyncio"):
            from holmes.core.llm_claude_sdk import ClaudeAgentSDKLLM

            return ClaudeAgentSDKLLM(model="sonnet")

    def test_basic_token_count(self, sdk_llm):
        """Test basic token counting estimation."""
        messages = [
            {"role": "user", "content": "Hello world"},  # 11 chars
        ]

        result = sdk_llm.count_tokens(messages)

        # With 3 chars per token estimate: 11 // 3 = 3
        assert result.total_tokens >= 3
        assert result.user_tokens >= 3

    def test_token_count_with_tools(self, sdk_llm):
        """Test token counting includes tool definitions."""
        messages = [{"role": "user", "content": "Test"}]
        tools = [
            {
                "function": {
                    "name": "test_tool",
                    "description": "A test tool for demonstration purposes",
                }
            }
        ]

        result = sdk_llm.count_tokens(messages, tools)

        # Should include tool definition overhead
        assert result.tools_to_call_tokens > 0

    def test_token_count_by_role(self, sdk_llm):
        """Test that tokens are correctly attributed to roles."""
        messages = [
            {"role": "system", "content": "System message here"},
            {"role": "user", "content": "User question"},
            {"role": "assistant", "content": "Assistant response"},
        ]

        result = sdk_llm.count_tokens(messages)

        assert result.system_tokens > 0
        assert result.user_tokens > 0
        assert result.assistant_tokens > 0


class TestSetToolExecutor:
    """Tests for tool executor integration."""

    @pytest.fixture
    def sdk_llm(self):
        """Create SDK LLM for testing."""
        with patch("holmes.core.llm_claude_sdk.nest_asyncio"):
            from holmes.core.llm_claude_sdk import ClaudeAgentSDKLLM

            return ClaudeAgentSDKLLM(model="sonnet")

    def test_set_tool_executor(self, sdk_llm):
        """Test setting tool executor loads tools."""
        mock_tool = MagicMock()
        mock_tool.name = "test_tool"

        mock_executor = MagicMock()
        mock_executor.tools_by_name = {"test_tool": mock_tool}

        sdk_llm.set_tool_executor(mock_executor)

        assert sdk_llm.tool_executor == mock_executor
        assert len(sdk_llm._tools) == 1
        assert sdk_llm._tools[0] == mock_tool

    def test_set_tools_directly(self, sdk_llm):
        """Test setting tools directly."""
        mock_tools = [MagicMock(), MagicMock()]

        sdk_llm.set_tools(mock_tools)

        assert len(sdk_llm._tools) == 2


class TestCompletion:
    """Tests for completion method (mocked SDK calls)."""

    @pytest.fixture
    def sdk_llm(self):
        """Create SDK LLM for testing."""
        with patch("holmes.core.llm_claude_sdk.nest_asyncio"):
            from holmes.core.llm_claude_sdk import ClaudeAgentSDKLLM

            return ClaudeAgentSDKLLM(model="sonnet")

    @pytest.mark.asyncio
    async def test_completion_returns_model_response(self, sdk_llm):
        """Test that completion returns proper ModelResponse format."""
        # Mock the SDK execution
        with patch.object(
            sdk_llm, "_execute_sdk_query", new_callable=AsyncMock
        ) as mock_execute:
            mock_execute.return_value = "Investigation complete. No issues found."

            messages = [{"role": "user", "content": "Check pod status"}]

            # Mock asyncio to run synchronously
            with patch("asyncio.get_event_loop") as mock_loop:
                mock_loop.return_value.run_until_complete = lambda coro: (
                    "Investigation complete. No issues found."
                )

                response = sdk_llm.completion(messages)

            # Verify response format
            assert hasattr(response, "choices")
            assert len(response.choices) > 0
            assert response.choices[0].message.content is not None
            # Key: tool_calls should be None to signal completion
            assert response.choices[0].message.tool_calls is None

    def test_completion_no_tool_calls_returned(self, sdk_llm):
        """Test that completion never returns tool_calls (SDK handles internally)."""
        with patch.object(
            sdk_llm, "_execute_sdk_query", new_callable=AsyncMock
        ) as mock_execute:
            mock_execute.return_value = "Done"

            with patch("asyncio.get_event_loop") as mock_loop:
                mock_loop.return_value.run_until_complete = lambda coro: "Done"

                response = sdk_llm.completion([{"role": "user", "content": "Test"}])

            # This is critical: no tool_calls means HolmesGPT won't try to execute tools
            assert response.choices[0].message.tool_calls is None
            assert response.choices[0].finish_reason == "stop"


class TestFactoryFunction:
    """Tests for create_claude_sdk_llm factory function."""

    def test_factory_creates_instance(self):
        """Test factory function creates proper instance."""
        with patch("holmes.core.llm_claude_sdk.nest_asyncio"):
            from holmes.core.llm_claude_sdk import create_claude_sdk_llm

            llm = create_claude_sdk_llm(
                model="opus",
                max_turns=30,
            )

            assert llm.model == "opus"
            assert llm.max_turns == 30

    def test_factory_with_tool_executor(self):
        """Test factory function accepts tool executor."""
        with patch("holmes.core.llm_claude_sdk.nest_asyncio"):
            from holmes.core.llm_claude_sdk import create_claude_sdk_llm

            mock_executor = MagicMock()
            mock_executor.tools_by_name = {}

            llm = create_claude_sdk_llm(
                model="sonnet",
                tool_executor=mock_executor,
            )

            assert llm.tool_executor == mock_executor
