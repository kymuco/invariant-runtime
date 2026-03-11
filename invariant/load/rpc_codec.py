from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from enum import Enum
from importlib import import_module
from typing import Any

_RPC_KIND_KEY = "__invariant_rpc_kind__"
_RPC_TYPE_KEY = "__invariant_rpc_type__"
_RPC_VALUE_KEY = "__invariant_rpc_value__"


def encode_rpc_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return {
            _RPC_KIND_KEY: "enum",
            _RPC_TYPE_KEY: _type_name(value.__class__),
            _RPC_VALUE_KEY: encode_rpc_value(value.value),
        }

    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if is_dataclass(value) and not isinstance(value, type):
        return {
            _RPC_KIND_KEY: "dataclass",
            _RPC_TYPE_KEY: _type_name(value.__class__),
            _RPC_VALUE_KEY: {
                field.name: encode_rpc_value(getattr(value, field.name))
                for field in fields(value)
            },
        }

    if isinstance(value, list):
        return [encode_rpc_value(item) for item in value]

    if isinstance(value, tuple):
        return {
            _RPC_KIND_KEY: "tuple",
            _RPC_VALUE_KEY: [encode_rpc_value(item) for item in value],
        }

    if isinstance(value, dict):
        return {str(key): encode_rpc_value(item) for key, item in value.items()}

    raise TypeError(
        "isolated RPC payload contains unsupported value "
        f"of type {type(value).__name__}"
    )


def decode_rpc_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, list):
        return [decode_rpc_value(item) for item in value]

    if isinstance(value, dict):
        kind = value.get(_RPC_KIND_KEY)
        if kind is None:
            return {str(key): decode_rpc_value(item) for key, item in value.items()}

        if kind == "enum":
            enum_cls = _load_type(value.get(_RPC_TYPE_KEY))
            if not isinstance(enum_cls, type) or not issubclass(enum_cls, Enum):
                raise TypeError(f"isolated RPC enum type is invalid: {value.get(_RPC_TYPE_KEY)!r}")
            return enum_cls(decode_rpc_value(value.get(_RPC_VALUE_KEY)))

        if kind == "dataclass":
            dataclass_cls = _load_type(value.get(_RPC_TYPE_KEY))
            if not isinstance(dataclass_cls, type) or not is_dataclass(dataclass_cls):
                raise TypeError(f"isolated RPC dataclass type is invalid: {value.get(_RPC_TYPE_KEY)!r}")
            raw_fields = value.get(_RPC_VALUE_KEY)
            if not isinstance(raw_fields, dict):
                raise TypeError("isolated RPC dataclass payload must be a mapping")
            decoded_fields = {
                str(field_name): decode_rpc_value(field_value)
                for field_name, field_value in raw_fields.items()
            }
            return dataclass_cls(**decoded_fields)

        if kind == "tuple":
            raw_items = value.get(_RPC_VALUE_KEY)
            if not isinstance(raw_items, list):
                raise TypeError("isolated RPC tuple payload must be a list")
            return tuple(decode_rpc_value(item) for item in raw_items)

        raise TypeError(f"isolated RPC payload kind is not supported: {kind!r}")

    raise TypeError(
        "isolated RPC decoded unsupported value "
        f"of type {type(value).__name__}"
    )


def assert_rpc_json_serializable(value: Any) -> Any:
    encoded = encode_rpc_value(value)
    json.dumps(encoded, ensure_ascii=False)
    return encoded


def _type_name(tp: type[Any]) -> str:
    return f"{tp.__module__}:{tp.__qualname__}"


def _load_type(type_name: Any) -> Any:
    if not isinstance(type_name, str) or ":" not in type_name:
        raise TypeError(f"isolated RPC type name is invalid: {type_name!r}")
    module_name, qualname = type_name.split(":", 1)
    module = import_module(module_name)
    target: Any = module
    for part in qualname.split("."):
        target = getattr(target, part)
    return target
