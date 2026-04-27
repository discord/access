"""Tags router."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import TypeAdapter
from sqlalchemy import func
from starlette.requests import Request

from api.auth.dependencies import CurrentUserId
from api.auth.permissions import require_access_admin
from api.config import settings
from api.database import DbSession
from api.extensions import db as _db
from api.models import Tag
from api.operations import CreateTag, DeleteTag
from api.pagination import paginate
from api.schemas import DeleteMessage, TagOut
from api.schemas._serialize import safe_dump


def _validate_description(value: Any, field_provided: bool) -> str:
    """Mirror the Marshmallow `context_aware_description_field` semantics."""
    if not field_provided:
        if settings.REQUIRE_DESCRIPTIONS:
            raise HTTPException(400, "Description is required.")
        return ""
    if value == "" and settings.REQUIRE_DESCRIPTIONS:
        raise HTTPException(400, "Description must be between 1 and 1024 characters")
    if value is None or value == "":
        if settings.REQUIRE_DESCRIPTIONS:
            raise HTTPException(400, "Description is required.")
        return ""
    if not isinstance(value, str):
        raise HTTPException(400, "Description must be a string")
    if len(value) > 1024:
        raise HTTPException(400, "Description must be 1024 characters or less")
    return value


def _validate_constraints(constraints: Any) -> dict[str, Any]:
    """Validate tag constraints against `Tag.CONSTRAINTS`. Raises HTTPException
    on bad keys or invalid values, mirroring the legacy Marshmallow validator."""
    if constraints is None:
        return {}
    if not isinstance(constraints, dict):
        raise HTTPException(400, "Constraints must be an object")
    valid: dict[str, Any] = {}
    for key, value in constraints.items():
        if key not in Tag.CONSTRAINTS:
            raise HTTPException(400, f"Unknown constraint: {key}")
        if not Tag.CONSTRAINTS[key].validator(value):
            raise HTTPException(400, f"Invalid value for constraint {key}: {value!r}")
        valid[key] = value
    return valid

router = APIRouter(prefix="/api/tags", tags=["tags"])
_adapter = TypeAdapter(TagOut)


@router.get("", name="tags")
def list_tags(request: Request, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    q = request.query_params.get("q", "")
    query = db.query(Tag).filter(Tag.deleted_at.is_(None)).order_by(func.lower(Tag.name))
    if q:
        like = f"%{q}%"
        query = query.filter(Tag.name.ilike(like))
    return paginate(request, query, _adapter)


@router.get("/{tag_id}", name="tag_by_id")
def get_tag(tag_id: str, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    tag = db.query(Tag).filter(_db.or_(Tag.id == tag_id, Tag.name == tag_id)).first()
    if tag is None:
        raise HTTPException(404, "Not Found")
    return safe_dump(_adapter, tag)


@router.post("", name="tags_create", status_code=201)
def post_tag(
    body: dict[str, Any] | None = Body(default=None),
    db: DbSession = None,  # type: ignore[assignment]
    _admin: str = Depends(require_access_admin),
    current_user_id: CurrentUserId = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    body = body or {}
    name = body.get("name", "")
    if not name or not isinstance(name, str):
        raise HTTPException(400, "Tag name is required")
    constraints = _validate_constraints(body.get("constraints") or {})
    description = _validate_description(body.get("description"), "description" in body)
    tag = Tag(
        name=name,
        description=description,
        constraints=constraints,
        enabled=body.get("enabled", True),
    )
    created = CreateTag(tag=tag, current_user_id=current_user_id).execute()
    return safe_dump(_adapter, created)


@router.put("/{tag_id}", name="tag_by_id_put")
def put_tag(
    tag_id: str,
    body: dict[str, Any] | None = Body(default=None),
    db: DbSession = None,  # type: ignore[assignment]
    _admin: str = Depends(require_access_admin),
    current_user_id: CurrentUserId = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    from sqlalchemy.orm import selectinload

    from api.operations import ModifyGroupsTimeLimit

    tag = db.query(Tag).filter(_db.or_(Tag.id == tag_id, Tag.name == tag_id)).first()
    if tag is None:
        raise HTTPException(404, "Not Found")
    body = body or {}
    if "constraints" in body:
        body["constraints"] = _validate_constraints(body["constraints"])
    if "description" in body:
        body["description"] = _validate_description(body["description"], True)
    for key in ("name", "description", "constraints", "enabled"):
        if key in body:
            setattr(tag, key, body[key])
    db.commit()

    # Re-evaluate time-limit constraints for groups associated with this tag
    refreshed = (
        db.query(Tag)
        .options(selectinload(Tag.active_group_tags))
        .filter(Tag.id == tag.id)
        .first()
    )
    if refreshed is not None and refreshed.active_group_tags:
        ModifyGroupsTimeLimit(
            groups=[tm.group_id for tm in refreshed.active_group_tags],
            tags=[refreshed.id],
        ).execute()

    return safe_dump(_adapter, refreshed or tag)


@router.delete("/{tag_id}", name="tag_by_id_delete")
def delete_tag(
    tag_id: str,
    db: DbSession,
    _admin: str = Depends(require_access_admin),
    current_user_id: CurrentUserId = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    tag = db.query(Tag).filter(_db.or_(Tag.id == tag_id, Tag.name == tag_id)).first()
    if tag is None:
        raise HTTPException(404, "Not Found")
    DeleteTag(tag=tag, current_user_id=current_user_id).execute()
    return DeleteMessage(deleted=True).model_dump()
