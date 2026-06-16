"""Map internal snake_case payloads to UI-facing camelCase (BFF /api/v1)."""

from __future__ import annotations

import re
from typing import Any


def snake_to_camel(name: str) -> str:
    parts = name.split("_")
    if not parts:
        return name
    return parts[0] + "".join(p[:1].upper() + p[1:] if p else "" for p in parts[1:])


def dict_keys_to_camel(value: Any) -> Any:
    if isinstance(value, list):
        return [dict_keys_to_camel(v) for v in value]
    if isinstance(value, dict):
        return {snake_to_camel(str(k)): dict_keys_to_camel(v) for k, v in value.items()}
    return value


_CAMEL_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")


def camel_to_snake(name: str) -> str:
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return _CAMEL_BOUNDARY.sub(r"\1_\2", s1).lower()


def ui_json_dict_to_snake(d: dict[str, Any]) -> dict[str, Any]:
    """Normalize a JSON object keyed in camelCase (or mixed) to snake_case keys (one level)."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        if not isinstance(k, str):
            continue
        sk = camel_to_snake(k.strip()) if k.strip() else k
        out[sk] = v
    return out
