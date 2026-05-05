"""Tags router."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import TypeAdapter
from sqlalchemy import func
from sqlalchemy.orm import selectinload
from starlette.requests import Request

from api.auth.dependencies import CurrentUserId
from api.auth.permissions import require_access_admin
from api.config import settings
from api.database import DbSession
from api.extensions import db as _db
from api.models import Tag
from api.operations import CreateTag, DeleteTag
from api.pagination import paginate
from api.routers._eager import group_tag_map_options
from api.schemas import CreateTagBody, DeleteMessage, TagDetail, UpdateTagBody
from api.schemas._serialize import dump_orm

_TAG_LOAD_OPTIONS = (selectinload(Tag.active_group_tags).options(*group_tag_map_options()),)


def _validate_description(value: Any, field_provided: bool) -> str:
    """Validate `description` against `settings.REQUIRE_DESCRIPTIONS`."""
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
    on bad keys or invalid values."""
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
_adapter = TypeAdapter(TagDetail)


@router.get("", name="tags")
def list_tags(request: Request, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    q = request.query_params.get("q", "")
    query = db.query(Tag).options(*_TAG_LOAD_OPTIONS).filter(Tag.deleted_at.is_(None)).order_by(func.lower(Tag.name))
    if q:
        like = f"%{q}%"
        query = query.filter(Tag.name.ilike(like))
    return paginate(request, query, _adapter)


@router.get("/{tag_id}", name="tag_by_id")
def get_tag(tag_id: str, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    tag = db.query(Tag).options(*_TAG_LOAD_OPTIONS).filter(_db.or_(Tag.id == tag_id, Tag.name == tag_id)).first()
    if tag is None:
        raise HTTPException(404, "Not Found")
    return dump_orm(_adapter, tag)


@router.post("", name="tags_create", status_code=201)
def post_tag(
    body: CreateTagBody,
    db: DbSession,
    current_user_id: CurrentUserId,
    _admin: str = Depends(require_access_admin),
) -> dict[str, Any]:
    if not body.name:
        raise HTTPException(400, "Tag name is required")
    constraints = _validate_constraints(body.constraints or {})
    description = _validate_description(body.description, body.description is not None)
    tag = Tag(
        name=body.name,
        description=description,
        constraints=constraints,
        enabled=body.enabled,
    )
    created = CreateTag(tag=tag, current_user_id=current_user_id).execute()
    refreshed = db.query(Tag).options(*_TAG_LOAD_OPTIONS).filter(Tag.id == created.id).first()
    return dump_orm(_adapter, refreshed or created)


@router.put("/{tag_id}", name="tag_by_id_put")
def put_tag(
    tag_id: str,
    body: UpdateTagBody,
    db: DbSession,
    current_user_id: CurrentUserId,
    _admin: str = Depends(require_access_admin),
) -> dict[str, Any]:
    from sqlalchemy.orm import selectinload

    from api.operations import ModifyGroupsTimeLimit

    tag = db.query(Tag).filter(_db.or_(Tag.id == tag_id, Tag.name == tag_id)).first()
    if tag is None:
        raise HTTPException(404, "Not Found")
    payload = body.model_dump(exclude_unset=True)
    if "constraints" in payload:
        payload["constraints"] = _validate_constraints(payload["constraints"])
    if "description" in payload:
        payload["description"] = _validate_description(payload["description"], True)
    for key in ("name", "description", "constraints", "enabled"):
        if key in payload:
            setattr(tag, key, payload[key])
    db.commit()

    # Re-evaluate time-limit constraints for groups associated with this tag.
    # `ModifyGroupsTimeLimit` commits, expiring objects, so we re-load
    # `refreshed` afterwards before serializing.
    pre_refresh = db.query(Tag).options(selectinload(Tag.active_group_tags)).filter(Tag.id == tag.id).first()
    if pre_refresh is not None and pre_refresh.active_group_tags:
        ModifyGroupsTimeLimit(
            groups=[tm.group_id for tm in pre_refresh.active_group_tags],
            tags=[pre_refresh.id],
        ).execute()

    refreshed = db.query(Tag).options(*_TAG_LOAD_OPTIONS).filter(Tag.id == tag.id).first()
    return dump_orm(_adapter, refreshed or tag)


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
