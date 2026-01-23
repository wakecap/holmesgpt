"""
Unit tests for MCP tool adapter module.

Tests conversion of HolmesGPT tools to Claude Agent SDK MCP format.
"""

import pytest
from unittest.mock import MagicMock, patch
from typing import Dict, Any, Optional

# Test the MCP tools module
# Note: Tests use mocks to avoid importing claude_agent_sdk directly


class MockToolParameter:
    """Mock ToolParameter for testing without full HolmesGPT dependency."""

    def __init__(
        self,
        type: str = "string",
        description: Optional[str] = None,
        required: bool = True,
        enum: Optional[list] = None,
        properties: Optional[Dict] = None,
        items: Optional["MockToolParameter"] = None,
    ):
        self.type = type
        self.description = description
        self.required = required
        self.enum = enum
        self.properties = properties
        self.items = items


class MockTool:
    """Mock Tool for testing."""

    def __init__(
        self,
        name: str,
        description: str,
        parameters: Optional[Dict[str, MockToolParameter]] = None,
    ):
        self.name = name
        self.description = description
        self.parameters = parameters or {}

    def invoke(self, params: Dict, context) -> Any:
        """Mock invoke that returns a simple result."""
        from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=f"Executed {self.name} with {params}",
        )


class TestConvertToolParameterToSchema:
    """Tests for convert_tool_parameter_to_schema function."""

    def test_string_parameter(self):
        """Test conversion of string parameter."""
        from holmes.core.mcp_tools import convert_tool_parameter_to_schema

        param = MockToolParameter(type="string", description="A test string")
        schema = convert_tool_parameter_to_schema(param)

        assert schema["type"] == "string"
        assert schema["description"] == "A test string"

    def test_integer_parameter(self):
        """Test conversion of integer parameter."""
        from holmes.core.mcp_tools import convert_tool_parameter_to_schema

        param = MockToolParameter(type="integer", description="Count")
        schema = convert_tool_parameter_to_schema(param)

        assert schema["type"] == "integer"
        assert schema["description"] == "Count"

    def test_boolean_parameter(self):
        """Test conversion of boolean parameter."""
        from holmes.core.mcp_tools import convert_tool_parameter_to_schema

        param = MockToolParameter(type="boolean", description="Flag")
        schema = convert_tool_parameter_to_schema(param)

        assert schema["type"] == "boolean"

    def test_array_parameter(self):
        """Test conversion of array parameter with items schema."""
        from holmes.core.mcp_tools import convert_tool_parameter_to_schema

        items = MockToolParameter(type="string", description="Item")
        param = MockToolParameter(type="array", description="List of items", items=items)
        schema = convert_tool_parameter_to_schema(param)

        assert schema["type"] == "array"
        assert "items" in schema
        assert schema["items"]["type"] == "string"

    def test_enum_parameter(self):
        """Test conversion of parameter with enum values."""
        from holmes.core.mcp_tools import convert_tool_parameter_to_schema

        param = MockToolParameter(
            type="string",
            description="Status",
            enum=["active", "inactive", "pending"],
        )
        schema = convert_tool_parameter_to_schema(param)

        assert schema["type"] == "string"
        assert schema["enum"] == ["active", "inactive", "pending"]

    def test_object_parameter(self):
        """Test conversion of object parameter with nested properties."""
        from holmes.core.mcp_tools import convert_tool_parameter_to_schema

        props = {
            "name": MockToolParameter(type="string", required=True),
            "age": MockToolParameter(type="integer", required=False),
        }
        param = MockToolParameter(
            type="object",
            description="User object",
            properties=props,
        )
        schema = convert_tool_parameter_to_schema(param)

        assert schema["type"] == "object"
        assert "properties" in schema
        assert schema["properties"]["name"]["type"] == "string"
        assert schema["properties"]["age"]["type"] == "integer"
        assert schema["required"] == ["name"]

    def test_type_mapping_variants(self):
        """Test that various type name variants are mapped correctly."""
        from holmes.core.mcp_tools import convert_tool_parameter_to_schema

        # Test common aliases
        test_cases = [
            ("str", "string"),
            ("int", "integer"),
            ("float", "number"),
            ("bool", "boolean"),
            ("list", "array"),
            ("dict", "object"),
        ]

        for input_type, expected_type in test_cases:
            param = MockToolParameter(type=input_type)
            schema = convert_tool_parameter_to_schema(param)
            assert schema["type"] == expected_type, f"Failed for {input_type}"


class TestConvertParametersToJsonSchema:
    """Tests for convert_parameters_to_json_schema function."""

    def test_empty_parameters(self):
        """Test conversion of empty parameters dict."""
        from holmes.core.mcp_tools import convert_parameters_to_json_schema

        schema = convert_parameters_to_json_schema({})

        assert schema["type"] == "object"
        assert schema["properties"] == {}

    def test_multiple_parameters(self):
        """Test conversion of multiple parameters."""
        from holmes.core.mcp_tools import convert_parameters_to_json_schema

        params = {
            "namespace": MockToolParameter(
                type="string", description="K8s namespace", required=True
            ),
            "limit": MockToolParameter(
                type="integer", description="Max results", required=False
            ),
        }
        schema = convert_parameters_to_json_schema(params)

        assert schema["type"] == "object"
        assert "namespace" in schema["properties"]
        assert "limit" in schema["properties"]
        assert schema["required"] == ["namespace"]


class TestExecutionContext:
    """Tests for execution context management (thread-safe with contextvars)."""

    def test_set_and_get_context(self):
        """Test setting and getting execution context."""
        from holmes.core.mcp_tools import (
            set_execution_context,
            get_execution_context,
            clear_execution_context,
        )

        mock_llm = MagicMock()
        mock_tools = {"test_tool": MagicMock()}

        set_execution_context(
            llm=mock_llm,
            max_token_count=50000,
            tools_by_name=mock_tools,
        )

        ctx = get_execution_context()
        assert ctx["llm"] == mock_llm
        assert ctx["max_token_count"] == 50000
        assert ctx["tools_by_name"] == mock_tools

        # Cleanup
        clear_execution_context()

    def test_clear_context(self):
        """Test clearing execution context."""
        from holmes.core.mcp_tools import (
            set_execution_context,
            get_execution_context,
            clear_execution_context,
        )

        mock_llm = MagicMock()
        set_execution_context(llm=mock_llm, max_token_count=50000)

        clear_execution_context()
        ctx = get_execution_context()

        assert ctx == {}

    def test_context_isolation_async(self):
        """Test that contexts are isolated between async tasks."""
        import asyncio
        from holmes.core.mcp_tools import (
            set_execution_context,
            get_execution_context,
            clear_execution_context,
        )

        async def task_1():
            mock_llm = MagicMock()
            mock_llm.name = "task1_llm"
            set_execution_context(llm=mock_llm, max_token_count=1000)
            await asyncio.sleep(0.01)
            ctx = get_execution_context()
            return ctx.get("max_token_count")

        async def task_2():
            mock_llm = MagicMock()
            mock_llm.name = "task2_llm"
            set_execution_context(llm=mock_llm, max_token_count=2000)
            await asyncio.sleep(0.01)
            ctx = get_execution_context()
            return ctx.get("max_token_count")

        async def run_tasks():
            # Run both tasks concurrently
            results = await asyncio.gather(task_1(), task_2())
            return results

        results = asyncio.run(run_tasks())

        # Each task should see its own context value
        assert 1000 in results
        assert 2000 in results

        # Cleanup
        clear_execution_context()


class TestGetAllowedToolsList:
    """Tests for get_allowed_tools_list function."""

    def test_allowed_tools_format(self):
        """Test that allowed tools list has correct MCP format."""
        from holmes.core.mcp_tools import get_allowed_tools_list

        tools = [
            MockTool(name="kubectl_get", description="Get K8s resources"),
            MockTool(name="prometheus_query", description="Query Prometheus"),
        ]

        allowed = get_allowed_tools_list(tools, server_name="holmes")

        assert "mcp__holmes__kubectl_get" in allowed
        assert "mcp__holmes__prometheus_query" in allowed
        assert len(allowed) == 2

    def test_custom_server_name(self):
        """Test that custom server name is used in tool names."""
        from holmes.core.mcp_tools import get_allowed_tools_list

        tools = [MockTool(name="test_tool", description="Test")]

        allowed = get_allowed_tools_list(tools, server_name="custom_server")

        assert allowed[0] == "mcp__custom_server__test_tool"


class TestConvertStructuredResultToMcp:
    """Tests for convert_structured_result_to_mcp function."""

    def test_success_result(self):
        """Test conversion of successful tool result."""
        from holmes.core.mcp_tools import convert_structured_result_to_mcp
        from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus

        result = StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data="Command executed successfully",
        )

        mcp_response = convert_structured_result_to_mcp(result)

        assert "content" in mcp_response
        assert mcp_response["content"][0]["type"] == "text"
        assert "Command executed successfully" in mcp_response["content"][0]["text"]
        assert "is_error" not in mcp_response or not mcp_response.get("is_error")

    def test_error_result(self):
        """Test conversion of error tool result."""
        from holmes.core.mcp_tools import convert_structured_result_to_mcp
        from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus

        result = StructuredToolResult(
            status=StructuredToolResultStatus.ERROR,
            error="Connection refused",
        )

        mcp_response = convert_structured_result_to_mcp(result)

        assert mcp_response["is_error"] is True
        assert "Error" in mcp_response["content"][0]["text"]
        assert "Connection refused" in mcp_response["content"][0]["text"]

    def test_no_data_result(self):
        """Test conversion of no-data tool result."""
        from holmes.core.mcp_tools import convert_structured_result_to_mcp
        from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus

        result = StructuredToolResult(
            status=StructuredToolResultStatus.NO_DATA,
        )

        mcp_response = convert_structured_result_to_mcp(result)

        assert "No data returned" in mcp_response["content"][0]["text"]

    def test_approval_required_result(self):
        """Test conversion of approval-required tool result."""
        from holmes.core.mcp_tools import convert_structured_result_to_mcp
        from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus

        result = StructuredToolResult(
            status=StructuredToolResultStatus.APPROVAL_REQUIRED,
            error="Requires admin approval",
        )

        mcp_response = convert_structured_result_to_mcp(result)

        assert "Approval required" in mcp_response["content"][0]["text"]
