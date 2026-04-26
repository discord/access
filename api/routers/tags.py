"""Tags router."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import TypeAdapter
from sqlalchemy import func
from starlette.requests import Request

from api.auth.dependencies import CurrentUserId
from api.auth.permissions import require_access_admin
from api.database import DbSession
from api.extensions import db as _db
from api.models import Tag
from api.operations import CreateTag, DeleteTag
from api.pagination import paginate
from api.schemas import DeleteMessage, TagOut

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
    return _adapter.dump_python(_adapter.validate_python(tag, from_attributes=True), mode="json")


@router.post("", name="tags_create", status_code=201)
def post_tag(
    body: dict[str, Any] = Body(...),
    db: DbSession = None,  # type: ignore[assignment]
    _admin: str = Depends(require_access_admin),
    current_user_id: CurrentUserId = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    tag = Tag(
        name=body.get("name", ""),
        description=body.get("description", ""),
        constraints=body.get("constraints") or {},
        enabled=body.get("enabled", True),
    )
    created = CreateTag(tag=tag, current_user_id=current_user_id).execute()
    return _adapter.dump_python(_adapter.validate_python(created, from_attributes=True), mode="json")


@router.put("/{tag_id}", name="tag_by_id_put")
def put_tag(
    tag_id: str,
    body: dict[str, Any] = Body(...),
    db: DbSession = None,  # type: ignore[assignment]
    _admin: str = Depends(require_access_admin),
    current_user_id: CurrentUserId = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    tag = db.query(Tag).filter(_db.or_(Tag.id == tag_id, Tag.name == tag_id)).first()
    if tag is None:
        raise HTTPException(404, "Not Found")
    for key in ("name", "description", "constraints", "enabled"):
        if key in body:
            setattr(tag, key, body[key])
    db.commit()
    return _adapter.dump_python(_adapter.validate_python(tag, from_attributes=True), mode="json")


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
