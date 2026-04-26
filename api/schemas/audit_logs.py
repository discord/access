"""Audit log shaping.

Operations emit JSON-line audit logs via `AuditLogSchema().dumps(payload)`.
The legacy implementation was a Marshmallow schema with deeply nested
`fields.Nested` projections; here we provide a drop-in replacement that
serializes ORM objects to simple dicts using a small set of well-known
fields per type. The resulting JSON is structurally compatible with the
legacy format.
"""
from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class EventType(Enum):
    access_approve = "ACCESS_APPROVE"
    access_create = "ACCESS_CREATE"
    access_reject = "ACCESS_REJECT"
    app_create = "APP_CREATE"
    app_delete = "APP_DELETE"
    app_modify_name = "APP_MODIFY_NAME"
    app_modify_plugin = "APP_MODIFY_PLUGIN"
    app_modify_tags = "APP_MODIFY_TAG"
    group_create = "GROUP_CREATE"
    group_delete = "GROUP_DELETE"
    group_modify_name = "GROUP_MODIFY_NAME"
    group_modify_plugin = "GROUP_MODIFY_PLUGIN"
    group_modify_type = "GROUP_MODIFY_TYPE"
    group_modify_tags = "GROUP_MODIFY_TAG"
    group_modify_users = "GROUP_MODIFY_USER"
    group_request_approve = "GROUP_REQUEST_APPROVE"
    group_request_create = "GROUP_REQUEST_CREATE"
    group_request_reject = "GROUP_REQUEST_REJECT"
    role_group_modify = "ROLE_GROUP_MODIFY"
    role_request_approve = "ROLE_REQUEST_APPROVE"
    role_request_create = "ROLE_REQUEST_CREATE"
    role_request_reject = "ROLE_REQUEST_REJECT"
    tag_create = "TAG_CREATE"
    tag_modify = "TAG_MODIFY"
    tag_delete = "TAG_DELETE"


def _attr(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _serialize_value(key: str, value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.strftime("%a, %d %b %Y %H:%M:%S %z") if value.tzinfo else value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [_serialize_value(key, v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize_value(k, v) for k, v in value.items()}
    # Heuristic per key
    if key in ("group", "active_group"):
        return _serialize_group(value)
    if key in ("requester", "resolver", "user"):
        return _serialize_user_id_email(value)
    if key == "role":
        return _serialize_role(value)
    if key == "request":
        return _serialize_access_request(value)
    if key == "role_request":
        return _serialize_role_request(value)
    if key == "group_request":
        return _serialize_group_request(value)
    if key == "app":
        return _serialize_app(value)
    if key in ("tag", "old_tag"):
        return _serialize_tag(value)
    if key in ("tags_added", "tags_removed"):
        return [_serialize_tag(v) for v in value] if isinstance(value, list) else _serialize_tag(value)
    # Fall back to common attribute set
    return _serialize_obj_common(value)


def _serialize_group(g: Any) -> Optional[dict]:
    if g is None:
        return None
    out: dict = {
        "id": _attr(g, "id"),
        "name": _attr(g, "name"),
        "type": _attr(g, "type"),
    }
    app = _attr(g, "app")
    if app is not None:
        out["app"] = {"id": _attr(app, "id"), "name": _attr(app, "name")}
    plugin_data = _attr(g, "plugin_data")
    if plugin_data is not None:
        out["plugin_data"] = plugin_data
    return out


def _serialize_user_id_email(u: Any) -> Optional[dict]:
    if u is None:
        return None
    return {"id": _attr(u, "id"), "email": _attr(u, "email")}


def _serialize_role(r: Any) -> Optional[dict]:
    if r is None:
        return None
    return {"id": _attr(r, "id"), "name": _attr(r, "name")}


def _serialize_app(a: Any) -> Optional[dict]:
    if a is None:
        return None
    return {
        "id": _attr(a, "id"),
        "name": _attr(a, "name"),
        "app_group_lifecycle_plugin": _attr(a, "app_group_lifecycle_plugin"),
        "plugin_data": _attr(a, "plugin_data"),
    }


def _serialize_tag(t: Any) -> Optional[dict]:
    if t is None:
        return None
    return {
        "id": _attr(t, "id"),
        "name": _attr(t, "name"),
        "constraints": _attr(t, "constraints"),
        "enabled": _attr(t, "enabled"),
    }


def _serialize_access_request(r: Any) -> Optional[dict]:
    if r is None:
        return None
    return {
        "id": _attr(r, "id"),
        "request_reason": _attr(r, "request_reason"),
        "request_ending_at": _serialize_value("request_ending_at", _attr(r, "request_ending_at")),
        "request_ownership": _attr(r, "request_ownership"),
        "resolution_reason": _attr(r, "resolution_reason"),
        "approval_ending_at": _serialize_value("approval_ending_at", _attr(r, "approval_ending_at")),
    }


def _serialize_role_request(r: Any) -> Optional[dict]:
    if r is None:
        return None
    requester_role = _attr(r, "requester_role")
    return {
        "id": _attr(r, "id"),
        "requester_role": {
            "id": _attr(requester_role, "id"),
            "name": _attr(requester_role, "name"),
        }
        if requester_role is not None
        else None,
        "request_reason": _attr(r, "request_reason"),
        "request_ending_at": _serialize_value("request_ending_at", _attr(r, "request_ending_at")),
        "request_ownership": _attr(r, "request_ownership"),
        "resolution_reason": _attr(r, "resolution_reason"),
        "approval_ending_at": _serialize_value("approval_ending_at", _attr(r, "approval_ending_at")),
    }


def _serialize_group_request(r: Any) -> Optional[dict]:
    if r is None:
        return None
    return {
        "id": _attr(r, "id"),
        "requested_group_name": _attr(r, "requested_group_name"),
        "requested_group_type": _attr(r, "requested_group_type"),
        "requested_app_id": _attr(r, "requested_app_id"),
        "requested_ownership_ending_at": _serialize_value(
            "requested_ownership_ending_at", _attr(r, "requested_ownership_ending_at")
        ),
        "request_reason": _attr(r, "request_reason"),
        "resolved_ownership_ending_at": _serialize_value(
            "resolved_ownership_ending_at", _attr(r, "resolved_ownership_ending_at")
        ),
        "resolution_reason": _attr(r, "resolution_reason"),
    }


def _serialize_obj_common(obj: Any) -> Any:
    """Last-resort: serialize via __dict__ filtering out SQLAlchemy internals."""
    if hasattr(obj, "__dict__"):
        return {
            k: _serialize_value(k, v)
            for k, v in obj.__dict__.items()
            if not k.startswith("_")
        }
    return str(obj)


# Keys whose None/empty value should be retained verbatim in the output
_ALLOWED_NULL_KEYS = {
    "groups_added_ending_at",
    "current_user_id",
    "current_user_email",
    "old_app_group_lifecycle_plugin",
}


class AuditLogSchema:
    """Drop-in replacement for the Marshmallow `AuditLogSchema().dumps(...)`.

    Accepts a `dict` payload of mixed primitives + ORM objects and returns a
    JSON string. The output is structurally compatible with the legacy schema
    but field ordering and exact bytes may differ — audit log consumers
    should parse the JSON, not match it as a string.

    Accepts but ignores legacy `exclude=`, `only=`, `partial=` Marshmallow
    constructor kwargs.
    """

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def dumps(self, data: dict[str, Any], **_: Any) -> str:
        out: dict[str, Any] = {}
        for key, value in data.items():
            serialized = _serialize_value(key, value)
            if serialized is None and key not in _ALLOWED_NULL_KEYS:
                continue
            out[key] = serialized
        return json.dumps(out, default=str)
