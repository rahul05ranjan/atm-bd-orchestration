from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime, timedelta
from types import UnionType
from typing import Any, Literal, get_args, get_origin, get_type_hints

from .result import Err, Error, Ok, ParseFailed, Result

_ERRORS = {
    "command_failed": "CommandFailed", "timeout": "Timeout", "rejected": "Rejected", "unreachable": "Unreachable",
    "parse_failed": "ParseFailed", "not_found": "NotFound", "config_invalid": "ConfigInvalid",
    "lock_held": "LockHeld", "io_failed": "IoFailed",
}


def to_json(obj: Any) -> str:
    """Encode contract values with UTC timestamps and explicit Result envelopes."""
    return json.dumps(_encode(obj), sort_keys=True, separators=(",", ":"))


def _encode(obj: Any) -> Any:
    if isinstance(obj, Ok):
        return {"ok": _encode(obj.value)}
    if isinstance(obj, Err):
        return {"err": _encode(obj.error)}
    if isinstance(obj, datetime):
        if obj.tzinfo is None:
            raise ValueError("naive datetime")
        return obj.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if is_dataclass(obj):
        return {field.name: _encode(getattr(obj, field.name)) for field in fields(obj)}
    if isinstance(obj, tuple):
        return [_encode(value) for value in obj]
    if isinstance(obj, Mapping):
        return {str(key): _encode(value) for key, value in obj.items()}
    return obj


def from_json(cls: type[Any], text: str) -> Result[Any]:
    """Decode a contract value; malformed input is data, never an exception."""
    try:
        return Ok(_decode(cls, json.loads(text), "$"))
    except (TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
        return Err(ParseFailed("parse_failed", "$", str(error), "correct the JSON contract value"))


def _decode(cls: Any, value: Any, path: str) -> Any:
    if cls is Any:
        return value
    origin, args = get_origin(cls), get_args(cls)
    if cls is datetime:
        if not isinstance(value, str) or not value.endswith("Z"):
            raise ValueError(f"{path}: expected UTC Z datetime")
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
        return parsed.replace(tzinfo=UTC)
    if origin is Literal:
        if value not in args:
            raise ValueError(f"{path}: expected one of {args}")
        return value
    if origin in (tuple,):
        if not isinstance(value, list):
            raise ValueError(f"{path}: expected array")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_decode(args[0], item, f"{path}[{index}]") for index, item in enumerate(value))
        if len(value) != len(args):
            raise ValueError(f"{path}: expected {len(args)} items")
        return tuple(_decode(item_type, item, f"{path}[{index}]") for index, (item_type, item) in enumerate(zip(args, value)))
    if origin in (dict, Mapping) or cls is Mapping:
        if not isinstance(value, dict):
            raise ValueError(f"{path}: expected object")
        value_type = args[1] if len(args) == 2 else Any
        return {key: _decode(value_type, item, f"{path}.{key}") for key, item in value.items()}
    if origin in (UnionType,) or str(origin) == "typing.Union":
        for option in args:
            if option is type(None) and value is None:
                return None
            try:
                return _decode(option, value, path)
            except (TypeError, ValueError, KeyError):
                continue
        raise ValueError(f"{path}: does not match any permitted type")
    if cls is Ok or origin is Ok:
        if not isinstance(value, dict) or set(value) != {"ok"}:
            raise ValueError(f"{path}: expected ok envelope")
        return Ok(_decode(args[0] if args else Any, value["ok"], f"{path}.ok"))
    if cls is Err:
        if not isinstance(value, dict) or set(value) != {"err"}:
            raise ValueError(f"{path}: expected err envelope")
        return Err(_decode_error(value["err"], f"{path}.err"))
    if cls is Error:
        return _decode_error(value, path)
    if is_dataclass(cls):
        if not isinstance(value, dict):
            raise ValueError(f"{path}: expected object")
        hints = get_type_hints(cls)
        decoded = {}
        for field in fields(cls):
            if field.name not in value:
                raise KeyError(f"{path}.{field.name}: missing field")
            decoded[field.name] = _decode(hints[field.name], value[field.name], f"{path}.{field.name}")
        return cls(**decoded)
    if cls in (str, int, bool, float):
        if type(value) is not cls:
            raise ValueError(f"{path}: expected {cls.__name__}")
        return value
    if cls is type(None):
        if value is not None:
            raise ValueError(f"{path}: expected null")
        return None
    return value


def _decode_error(value: Any, path: str) -> Error:
    if not isinstance(value, dict) or not isinstance(value.get("kind"), str):
        raise TypeError(f"{path}: error kind missing")
    from . import result
    name = _ERRORS.get(value["kind"])
    if name is None:
        raise ValueError(f"{path}.kind: unknown error kind")
    return _decode(getattr(result, name), value, path)


def snapshot_stale(snapshot: Any, now: datetime) -> bool:
    return now - snapshot.generated_at > timedelta(seconds=snapshot.interval_s * 2)
