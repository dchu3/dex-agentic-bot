"""Convert MCP tool schemas to Gemini function declarations."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import google.generativeai as genai


def mcp_type_to_gemini_type(mcp_type: str) -> genai.protos.Type:
    """Convert MCP/JSON Schema type to Gemini proto type."""
    type_map = {
        "string": genai.protos.Type.STRING,
        "number": genai.protos.Type.NUMBER,
        "integer": genai.protos.Type.INTEGER,
        "boolean": genai.protos.Type.BOOLEAN,
        "array": genai.protos.Type.ARRAY,
        "object": genai.protos.Type.OBJECT,
    }
    return type_map.get(mcp_type, genai.protos.Type.STRING)


def convert_json_schema_to_gemini_schema(
    schema: Dict[str, Any], depth: int = 0
) -> genai.protos.Schema:
    """Recursively convert JSON Schema to Gemini Schema proto."""
    if depth > 5:
        return genai.protos.Schema(type=genai.protos.Type.STRING)

    schema_type = schema.get("type", "string")

    # Handle arrays
    if schema_type == "array":
        items_schema = schema.get("items", {"type": "string"})
        return genai.protos.Schema(
            type=genai.protos.Type.ARRAY,
            items=convert_json_schema_to_gemini_schema(items_schema, depth + 1),
        )

    # Handle objects
    if schema_type == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])

        gemini_properties = {}
        for prop_name, prop_schema in properties.items():
            gemini_properties[prop_name] = convert_json_schema_to_gemini_schema(
                prop_schema, depth + 1
            )

        return genai.protos.Schema(
            type=genai.protos.Type.OBJECT,
            properties=gemini_properties,
            required=required if required else None,
        )

    # Handle primitives
    gemini_type = mcp_type_to_gemini_type(schema_type)
    kwargs: Dict[str, Any] = {"type": gemini_type}

    if "description" in schema:
        kwargs["description"] = schema["description"]

    if "enum" in schema:
        kwargs["enum"] = schema["enum"]

    return genai.protos.Schema(**kwargs)


def mcp_tool_to_gemini_function(
    client_name: str, tool: Dict[str, Any]
) -> Optional[genai.protos.FunctionDeclaration]:
    """Convert a single MCP tool to a Gemini FunctionDeclaration."""
    try:
        tool_name = tool.get("name")
        if not tool_name:
            return None

        # Namespace the function name: client_method
        full_name = f"{client_name}_{tool_name}"
        description = tool.get("description", f"Call {client_name}.{tool_name}")

        # Get input schema
        input_schema = tool.get("inputSchema", {})

        # Convert to Gemini parameters schema
        if input_schema and input_schema.get("properties"):
            parameters = convert_json_schema_to_gemini_schema(input_schema)
        else:
            parameters = None

        return genai.protos.FunctionDeclaration(
            name=full_name,
            description=description,
            parameters=parameters,
        )
    except Exception:
        return None


def convert_mcp_tools_to_gemini(
    client_name: str, tools: List[Dict[str, Any]]
) -> List[genai.protos.FunctionDeclaration]:
    """Convert a list of MCP tools to Gemini function declarations."""
    declarations = []
    for tool in tools:
        declaration = mcp_tool_to_gemini_function(client_name, tool)
        if declaration:
            declarations.append(declaration)
    return declarations


def parse_function_call_name(name: str) -> tuple[str, str]:
    """Parse a namespaced function name into (client, method)."""
    parts = name.split("_", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return "", name
