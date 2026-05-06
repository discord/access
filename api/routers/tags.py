"""Tags router."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import TypeAdapter
from sqlalchemy import func, nullsfirst
from sqlalchemy.orm import selectinload
from starlette.requests import Request

from api.auth.dependencies import CurrentUserId
from api.auth.permissions import require_access_admin
from api.context import get_request_context
from api.database import DbSession
from api.extensions import db as _db
from api.models import OktaUser, Tag
from api.operations import CreateTag, DeleteTag
from api.pagination import paginate
from api.routers._eager import group_tag_map_options
from api.schemas import (
    AuditLogSchema,
    CreateTagBody,
    DeleteMessage,
    EventType,
    SearchTagPaginationQuery,
    TagDetail,
    TagListItem,
    UpdateTagBody,
)
from api.schemas._serialize import dump_orm

_TAG_LOAD_OPTIONS = (selectinload(Tag.active_group_tags).options(*group_tag_map_options()),)


router = APIRouter(prefix="/api/tags", tags=["tags"])
_adapter = TypeAdapter(TagDetail)
_list_adapter = TypeAdapter(TagListItem)


@router.get("", name="tags")
def list_tags(
    request: Request,
    db: DbSession,
    current_user_id: CurrentUserId,
    q_args: Annotated[SearchTagPaginationQuery, Query()],
) -> dict[str, Any]:
    query = db.query(Tag).filter(Tag.deleted_at.is_(None)).order_by(func.lower(Tag.name))
    if q_args.q:
        like = f"%{q_args.q}%"
        query = query.filter(_db.or_(Tag.name.ilike(like), Tag.description.ilike(like)))
    return paginate(request, query, _list_adapter, extract=lambda: (q_args.page, q_args.per_page))


@router.get("/{tag_id}", name="tag_by_id")
def get_tag(tag_id: str, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    # `nullsfirst(deleted_at.desc())` makes an active row beat a soft-deleted
    # row that shares the same name. Without the order, `.first()` may return
    # an old deleted tag and 404 on a name that still exists.
    tag = (
        db.query(Tag)
        .options(*_TAG_LOAD_OPTIONS)
        .filter(_db.or_(Tag.id == tag_id, Tag.name == tag_id))
        .order_by(nullsfirst(Tag.deleted_at.desc()))
        .first()
    )
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
    # Reject duplicates by name. Without this, CreateTag.execute() silently
    # returns the existing tag and the endpoint replies 201 with the wrong
    # row, which clients don't expect.
    existing = (
        db.query(Tag).filter(func.lower(Tag.name) == func.lower(body.name)).filter(Tag.deleted_at.is_(None)).first()
    )
    if existing is not None:
        raise HTTPException(400, "Tag already exists with the same name")

    tag = Tag(
        name=body.name,
        description=body.description if body.description is not None else "",
        constraints=body.constraints or {},
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

    tag = (
        db.query(Tag)
        .filter(Tag.deleted_at.is_(None))
        .filter(_db.or_(Tag.id == tag_id, Tag.name == tag_id))
        .first()
    )
    if tag is None:
        raise HTTPException(404, "Not Found")
    payload = body.model_dump(exclude_unset=True)

    # Snapshot pre-mutation state for the `tag_modify` audit log emitted
    # below. Built as a detached `Tag()` so the audit projection treats it
    # as an ORM-shaped object without holding a session reference.
    old_tag = Tag(
        name=tag.name,
        description=tag.description,
        constraints=tag.constraints,
        enabled=tag.enabled,
    )

    # Reject renames that collide with another existing tag (case-insensitive).
    new_name = payload.get("name")
    if new_name and new_name.lower() != tag.name.lower():
        collision = (
            db.query(Tag)
            .filter(Tag.id != tag.id)
            .filter(func.lower(Tag.name) == func.lower(new_name))
            .filter(Tag.deleted_at.is_(None))
            .first()
        )
        if collision is not None:
            raise HTTPException(400, "Tag already exists with the same name")

    if "constraints" in payload and payload["constraints"] is None:
        payload["constraints"] = {}
    if "description" in payload and payload["description"] is None:
        payload["description"] = ""
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

    email = getattr(db.get(OktaUser, current_user_id), "email", None) if current_user_id else None
    _ctx = get_request_context()
    logging.getLogger("access.audit").info(
        AuditLogSchema().dumps(
            {
                "event_type": EventType.tag_modify,
                "user_agent": _ctx.user_agent if _ctx else None,
                "ip": _ctx.ip if _ctx else None,
                "current_user_id": current_user_id,
                "current_user_email": email,
                "tag": refreshed or tag,
                "old_tag": old_tag,
            }
        )
    )

    return dump_orm(_adapter, refreshed or tag)


@router.delete("/{tag_id}", name="tag_by_id_delete")
def delete_tag(
    tag_id: str,
    db: DbSession,
    _admin: str = Depends(require_access_admin),
    current_user_id: CurrentUserId = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    tag = (
        db.query(Tag)
        .filter(Tag.deleted_at.is_(None))
        .filter(_db.or_(Tag.id == tag_id, Tag.name == tag_id))
        .first()
    )
    if tag is None:
        raise HTTPException(404, "Not Found")
    DeleteTag(tag=tag, current_user_id=current_user_id).execute()
    return DeleteMessage(deleted=True).model_dump()
