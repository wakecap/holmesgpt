"""
MCP Tool Adapter for HolmesGPT

This module converts HolmesGPT Tool objects to Claude Agent SDK MCP format,
enabling HolmesGPT tools to be used with the Claude Agent SDK.
"""

import contextvars
import logging
from typing import Any, Callable, Awaitable, Dict, List, Optional, TYPE_CHECKING

from claude_agent_sdk import tool as sdk_tool, create_sdk_mcp_server, SdkMcpTool

if TYPE_CHECKING:
    from holmes.core.tools import Tool, ToolParameter, StructuredToolResult, ToolInvokeContext
    from holmes.core.llm import LLM

logger = logging.getLogger(__name__)

# Thread-safe and async-safe execution context using contextvars
# This replaces the module-level mutable dict to prevent race conditions
# when multiple HolmesGPT instances execute concurrently
_execution_context: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar(
    "mcp_execution_context",
    default={}
)


def set_execution_context(
    llm: "LLM",
    max_token_count: int,
    tools_by_name: Optional[Dict[str, "Tool"]] = None,
) -> None:
    """
    Set the execution context for MCP tool handlers.

    This stores references that tool handlers need during execution,
    since MCP tools receive simple dict arguments instead of ToolInvokeContext.

    Uses contextvars for thread-safety and async-safety, ensuring each
    concurrent execution has its own isolated context.

    Args:
        llm: The LLM instance for token counting
        max_token_count: Maximum tokens for tool output
        tools_by_name: Dict mapping tool names to Tool instances
    """
    _execution_context.set({
        "llm": llm,
        "max_token_count": max_token_count,
        "tools_by_name": tools_by_name or {},
    })


def clear_execution_context() -> None:
    """Clear the execution context after query completion."""
    _execution_context.set({})


def get_execution_context() -> Dict[str, Any]:
    """Get the current execution context (thread-safe and async-safe)."""
    return _execution_context.get()


def convert_tool_parameter_to_schema(param: "ToolParameter") -> Dict[str, Any]:
    """
    Convert a HolmesGPT ToolParameter to JSON Schema format.

    Args:
        param: HolmesGPT ToolParameter object

    Returns:
        JSON Schema dict for the parameter
    """
    schema: Dict[str, Any] = {}

    # Map HolmesGPT types to JSON Schema types
    type_mapping = {
        "string": "string",
        "str": "string",
        "integer": "integer",
        "int": "integer",
        "number": "number",
        "float": "number",
        "boolean": "boolean",
        "bool": "boolean",
        "array": "array",
        "list": "array",
        "object": "object",
        "dict": "object",
    }

    param_type = param.type.lower()
    json_type = type_mapping.get(param_type, "string")
    schema["type"] = json_type

    # Add description if available
    if param.description:
        schema["description"] = param.description

    # Handle enum values
    if param.enum:
        schema["enum"] = param.enum

    # Handle nested object properties
    if json_type == "object" and param.properties:
        schema["properties"] = {}
        required_props = []
        for prop_name, prop_param in param.properties.items():
            schema["properties"][prop_name] = convert_tool_parameter_to_schema(prop_param)
            if prop_param.required:
                required_props.append(prop_name)
        if required_props:
            schema["required"] = required_props

    # Handle array items schema
    if json_type == "array" and param.items:
        schema["items"] = convert_tool_parameter_to_schema(param.items)

    return schema


def convert_parameters_to_json_schema(
    parameters: Dict[str, "ToolParameter"]
) -> Dict[str, Any]:
    """
    Convert HolmesGPT tool parameters dict to JSON Schema format.

    Args:
        parameters: Dict mapping parameter names to ToolParameter objects

    Returns:
        JSON Schema dict for all parameters
    """
    if not parameters:
        return {"type": "object", "properties": {}}

    properties = {}
    required = []

    for param_name, param in parameters.items():
        properties[param_name] = convert_tool_parameter_to_schema(param)
        if param.required:
            required.append(param_name)

    schema: Dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }

    if required:
        schema["required"] = required

    return schema


def convert_structured_result_to_mcp(result: "StructuredToolResult") -> Dict[str, Any]:
    """
    Convert a HolmesGPT StructuredToolResult to MCP tool response format.

    Args:
        result: HolmesGPT StructuredToolResult from tool execution

    Returns:
        MCP-compatible response dict
    """
    from holmes.core.tools import StructuredToolResultStatus

    # Get the data as string
    data_str = result.get_stringified_data()

    # Build response based on status
    if result.status == StructuredToolResultStatus.ERROR:
        error_msg = result.error or "Tool execution failed"
        return {
            "content": [{
                "type": "text",
                "text": f"Error: {error_msg}\n{data_str}" if data_str else f"Error: {error_msg}"
            }],
            "is_error": True
        }
    elif result.status == StructuredToolResultStatus.NO_DATA:
        return {
            "content": [{
                "type": "text",
                "text": "No data returned from tool execution."
            }]
        }
    elif result.status == StructuredToolResultStatus.APPROVAL_REQUIRED:
        return {
            "content": [{
                "type": "text",
                "text": f"Approval required: {result.error or 'This action requires user approval'}"
            }]
        }
    else:
        # SUCCESS
        return {
            "content": [{
                "type": "text",
                "text": data_str or "Tool executed successfully."
            }]
        }


def create_mcp_tool_handler(holmes_tool: "Tool") -> Callable[..., Awaitable[Dict[str, Any]]]:
    """
    Create an async MCP tool handler function for a HolmesGPT Tool.

    This creates a closure that wraps the HolmesGPT tool's _invoke method
    and converts the result to MCP format.

    Args:
        holmes_tool: The HolmesGPT Tool to wrap

    Returns:
        Async handler function compatible with MCP that returns Dict[str, Any]
    """
    async def handler(args: Dict[str, Any]) -> Dict[str, Any]:
        """MCP tool handler that wraps HolmesGPT tool execution."""
        from holmes.core.tools import ToolInvokeContext
        import uuid

        # Get execution context
        ctx = get_execution_context()
        llm = ctx.get("llm")
        max_token_count = ctx.get("max_token_count", 50000)

        # Create a minimal ToolInvokeContext
        # Note: We don't have a real tool_call_id in MCP context
        tool_call_id = str(uuid.uuid4())

        invoke_context = ToolInvokeContext(
            tool_number=None,
            user_approved=True,  # SDK handles permissions
            llm=llm,
            max_token_count=max_token_count,
            tool_call_id=tool_call_id,
            tool_name=holmes_tool.name,
        )

        logger.info(f"MCP executing tool: {holmes_tool.name} with params: {list(args.keys())}")

        try:
            # Execute the HolmesGPT tool
            result = holmes_tool.invoke(args, invoke_context)

            # Convert to MCP format
            mcp_response = convert_structured_result_to_mcp(result)

            logger.debug(f"MCP tool {holmes_tool.name} completed with status: {result.status}")

            return mcp_response

        except Exception as e:
            logger.error(f"MCP tool {holmes_tool.name} failed: {e}", exc_info=True)
            return {
                "content": [{
                    "type": "text",
                    "text": f"Tool execution error: {str(e)}"
                }],
                "is_error": True
            }

    return handler


def convert_tool_to_mcp(holmes_tool: "Tool") -> SdkMcpTool:
    """
    Convert a HolmesGPT Tool to an SDK MCP tool using the @tool decorator.

    Args:
        holmes_tool: HolmesGPT Tool instance to convert

    Returns:
        SdkMcpTool instance configured for the HolmesGPT tool
    """
    # Convert parameters to JSON Schema
    input_schema = convert_parameters_to_json_schema(holmes_tool.parameters)

    # Create the handler
    handler = create_mcp_tool_handler(holmes_tool)

    # Use the @tool decorator to create the MCP tool
    mcp_tool = sdk_tool(
        name=holmes_tool.name,
        description=holmes_tool.description,
        input_schema=input_schema
    )(handler)

    return mcp_tool


def create_holmes_mcp_server(
    tools: List["Tool"],
    server_name: str = "holmes",
    version: str = "1.0.0"
):
    """
    Create an SDK MCP server from a list of HolmesGPT tools.

    Args:
        tools: List of HolmesGPT Tool instances
        server_name: Name for the MCP server
        version: Version string for the server

    Returns:
        McpSdkServerConfig that can be passed to ClaudeAgentOptions
    """
    # Convert all tools to MCP format
    mcp_tools = []
    for holmes_tool in tools:
        try:
            mcp_tool = convert_tool_to_mcp(holmes_tool)
            mcp_tools.append(mcp_tool)
            logger.debug(f"Converted tool '{holmes_tool.name}' to MCP format")
        except Exception as e:
            logger.warning(f"Failed to convert tool '{holmes_tool.name}' to MCP: {e}")
            continue

    logger.info(f"Created MCP server '{server_name}' with {len(mcp_tools)} tools")

    # Create the SDK MCP server
    return create_sdk_mcp_server(
        name=server_name,
        version=version,
        tools=mcp_tools
    )


def get_allowed_tools_list(
    tools: List["Tool"],
    server_name: str = "holmes"
) -> List[str]:
    """
    Get the list of allowed tool names in MCP format for ClaudeAgentOptions.

    MCP tool names follow the pattern: mcp__{server_name}__{tool_name}

    Args:
        tools: List of HolmesGPT Tool instances
        server_name: Name of the MCP server

    Returns:
        List of MCP tool names for allowed_tools option
    """
    return [f"mcp__{server_name}__{tool.name}" for tool in tools]
