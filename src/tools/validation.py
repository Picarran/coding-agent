"""Lightweight argument validation against a tool's JSON-schema parameters.

Deliberately minimal (no external JSON-schema dependency): it checks that
required arguments are present and that provided values match their declared
type, producing clear errors the model can act on.
"""
from __future__ import annotations

from typing import Any

_TYPE_CHECKS: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "object": dict,
    "array": list,
}


def validate_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    properties = schema.get("properties", {})
    required = schema.get("required", [])

    for name in required:
        if name not in arguments:
            errors.append(f"missing required argument: {name}")

    for name, value in arguments.items():
        prop = properties.get(name)
        if prop is None or "type" not in prop:
            continue
        expected = prop["type"]
        if expected not in _TYPE_CHECKS:
            continue
        # bool is a subclass of int in Python, so guard it explicitly.
        if expected == "integer" and isinstance(value, bool):
            errors.append(f"argument {name!r}: expected integer, got boolean")
            continue
        if not isinstance(value, _TYPE_CHECKS[expected]):
            errors.append(
                f"argument {name!r}: expected {expected}, got {type(value).__name__}"
            )
    return errors
