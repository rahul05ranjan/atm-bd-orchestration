from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import MISSING, fields, is_dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from .result import (
    Err,
    Error,
    Ok,
    ParseFailed,
    Result,
)
from .types import Snapshot

_ERROR_TYPES = get_args(Error)
_ERRORS: dict[str, type[Any]] = {
    get_args(get_type_hints(error_type)["kind"])[0]: error_type
    for error_type in _ERROR_TYPES
}


def to_json(obj: Any) -> str:
    """Encode contract values with UTC second-precision timestamps and Result envelopes.

    Datetime microseconds are discarded; naive datetimes raise ValueError.
    """
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


class _DecodeError(Exception):
    """A decode mismatch at a field path; converted to ParseFailed at the from_json boundary."""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


_FIX = "correct the JSON contract value"
_MAX_JSON_DEPTH = 128


def _exceeds_depth_limit(text: str) -> bool:
    depth = 0
    quoted = False
    escaped = False
    for character in text:
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in "[{":
            depth += 1
            if depth > _MAX_JSON_DEPTH:
                return True
        elif character in "]}":
            depth -= 1
    return False


def from_json(cls: type[Any], text: str) -> Result[Any]:
    """Decode up to 128 JSON container levels; malformed input is data, never an exception."""
    if _exceeds_depth_limit(text):
        return Err(ParseFailed("parse_failed", "$", "value nested too deeply", _FIX))
    try:
        return Ok(_decode(cls, json.loads(text), "$"))
    except _DecodeError as error:
        return Err(ParseFailed("parse_failed", error.path, str(error), _FIX))
    except json.JSONDecodeError as error:
        return Err(ParseFailed("parse_failed", "$", f"invalid JSON: {error}", _FIX))
    except RecursionError:
        return Err(ParseFailed("parse_failed", "$", "value nested too deeply", _FIX))


def _decode(cls: Any, value: Any, path: str) -> Any:
    if cls is Any:
        return value
    origin, args = get_origin(cls), get_args(cls)
    if isinstance(cls, type) and issubclass(cls, StrEnum):
        try:
            return cls(value)
        except (ValueError, TypeError):
            raise _DecodeError(path, f"unknown {cls.__name__} value {value!r}") from None
    if cls is datetime:
        if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value) is None:
            raise _DecodeError(path, "expected UTC datetime YYYY-MM-DDTHH:MM:SSZ")
        try:
            parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError as error:
            raise _DecodeError(path, f"invalid datetime: {error}") from None
        return parsed.replace(tzinfo=UTC)
    if origin is Literal:
        if not isinstance(value, str) or value not in args:
            raise _DecodeError(path, f"expected one of {args}")
        return value
    if origin is tuple:
        if not isinstance(value, list):
            raise _DecodeError(path, "expected array")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_decode(args[0], item, f"{path}[{index}]") for index, item in enumerate(value))
        if len(value) != len(args):
            raise _DecodeError(path, f"expected {len(args)} items")
        return tuple(_decode(item_type, item, f"{path}[{index}]") for index, (item_type, item) in enumerate(zip(args, value)))
    if origin in (dict, Mapping) or cls is Mapping:
        if not isinstance(value, dict):
            raise _DecodeError(path, "expected object")
        value_type = args[1] if len(args) == 2 else Any
        return {key: _decode(value_type, item, f"{path}.{key}") for key, item in value.items()}
    if origin is UnionType or origin is Union:
        return _decode_union(args, value, path)
    if cls is Ok or origin is Ok:
        if not isinstance(value, dict) or set(value) != {"ok"}:
            raise _DecodeError(path, "expected ok envelope")
        return Ok(_decode(args[0] if args else Any, value["ok"], f"{path}.ok"))
    if cls is Err:
        if not isinstance(value, dict) or set(value) != {"err"}:
            raise _DecodeError(path, "expected err envelope")
        return Err(_decode_error(value["err"], f"{path}.err"))
    if is_dataclass(cls) and isinstance(cls, type):
        return _decode_dataclass(cls, value, path)
    if cls in (str, int, bool, float):
        if type(value) is not cls:
            raise _DecodeError(path, f"expected {cls.__name__}")
        return value
    if cls is type(None):
        if value is not None:
            raise _DecodeError(path, "expected null")
        return None
    raise _DecodeError(path, f"unsupported annotation {cls!r}")


def _decode_dataclass(cls: type[Any], value: Any, path: str) -> Any:
    if not isinstance(value, dict):
        raise _DecodeError(path, "expected object")
    hints = get_type_hints(cls)
    names = [field.name for field in fields(cls)]
    unknown = sorted(set(value) - set(names))
    if unknown:
        raise _DecodeError(f"{path}.{unknown[0]}", f"unknown field (unknown: {', '.join(unknown)})")
    decoded = {}
    for field in fields(cls):
        name = field.name
        if name not in value:
            if field.default is not MISSING:
                decoded[name] = field.default
                continue
            if field.default_factory is not MISSING:
                decoded[name] = field.default_factory()
                continue
            raise _DecodeError(f"{path}.{name}", "missing field")
        raw = value[name]
        migrate = field.metadata.get("json_migration")
        if migrate is not None:
            raw = migrate(raw, value, field.metadata)
        decoded[name] = _decode(hints[name], raw, f"{path}.{name}")
    return cls(**decoded)


def _decode_union(options: tuple[Any, ...], value: Any, path: str) -> Any:
    if value is None and type(None) in options:
        return None
    present = tuple(option for option in options if option is not type(None))
    if present and all(option in _ERROR_TYPES for option in present):
        return _decode_error(value, path)
    failures: list[_DecodeError] = []
    for option in present:
        try:
            return _decode(option, value, path)
        except _DecodeError as error:
            failures.append(error)
    if not failures:
        raise _DecodeError(path, "expected null")
    # The alternative that matched deepest names the field that is actually wrong.
    deepest = max(failures, key=lambda error: len(error.path))
    if deepest.path == path:
        raise _DecodeError(path, "does not match any permitted type: " + "; ".join(e.message for e in failures))
    raise deepest


def _decode_error(value: Any, path: str) -> Error:
    if not isinstance(value, dict) or not isinstance(value.get("kind"), str):
        raise _DecodeError(f"{path}.kind", "error kind missing")
    error_type = _ERRORS.get(value["kind"])
    if error_type is None:
        raise _DecodeError(f"{path}.kind", f"unknown error kind {value['kind']!r}")
    return _decode(error_type, value, path)


def snapshot_stale(snapshot: Snapshot, now: datetime) -> bool:
    """The one staleness rule: now - generated_at > 2 * interval_s."""
    return now - snapshot.generated_at > timedelta(seconds=snapshot.interval_s * 2)
