"""Tool schema for structured LLM output via Anthropic tool_use."""

from __future__ import annotations

import json

from meeting_minutes.models import StructuredMinutesResponse


def _inline_refs(schema: dict) -> dict:
    """Inline $defs references for Anthropic tool_use compatibility."""
    defs = schema.pop("$defs", {})
    if not defs:
        return schema

    schema_str = json.dumps(schema)
    for def_name, def_schema in defs.items():
        ref = f'{{"$ref": "#/$defs/{def_name}"}}'
        replacement = json.dumps(def_schema)
        schema_str = schema_str.replace(ref, replacement)
    return json.loads(schema_str)


def get_minutes_tool_schema() -> dict:
    """Get the JSON schema for StructuredMinutesResponse, suitable for Anthropic tool_use."""
    schema = StructuredMinutesResponse.model_json_schema()
    return _inline_refs(schema)


def get_tool_definition() -> dict:
    """Get the complete Anthropic tool definition."""
    return {
        "name": "record_meeting_minutes",
        "description": (
            "Record the structured meeting minutes extracted from the transcript. "
            "Fill in every field that can be determined from the transcript. "
            "Leave fields as null or empty arrays if the information is not available."
        ),
        "input_schema": get_minutes_tool_schema(),
    }
