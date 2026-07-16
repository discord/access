"""Tags router."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from sqlalchemy import func, nullsfirst, or_, select
from sqlalchemy.orm import joinedload, selectinload
from starlette.requests import Request

from api.auth.dependencies import CurrentUserId
from api.auth.permissions import require_access_admin
from api.context import get_request_context
from api.database import DbSession
from api.models import AppTagMap, OktaUser, Tag
from api.operations import CreateTag, DeleteTag
from fastapi_pagination.ext.sqlalchemy import apaginate

from api.pagination import Page, validated
from api.routers._eager import group_tag_map_options
from api.schemas import (
    TagListItem,
    AuditLogSchema,
    CreateTagBody,
    DeleteMessage,
    EventType,
    SearchTagQuery,
    TagDetail,
    UpdateTagBody,
)

_TAG_LOAD_OPTIONS = (
    selectinload(Tag.active_group_tags).options(*group_tag_map_options()),
    selectinload(Tag.active_app_tags).options(
        joinedload(AppTagMap.active_app),
        joinedload(AppTagMap.active_tag),
    ),
)


router = APIRouter(prefix="/api/tags", tags=["tags"])


@router.get("", name="tags")
async def list_tags(
    request: Request,
    db: DbSession,
    current_user_id: CurrentUserId,
    q_args: Annotated[SearchTagQuery, Query()],
) -> Page[TagListItem]:
    stmt = select(Tag).where(Tag.deleted_at.is_(None)).order_by(func.lower(Tag.name))
    if q_args.q:
        like = f"%{q_args.q}%"
        stmt = stmt.where(or_(Tag.name.ilike(like), Tag.description.ilike(like)))
    return await apaginate(db, stmt, transformer=validated(TagListItem))


@router.get("/{tag_id}", name="tag_by_id")
async def get_tag(tag_id: str, db: DbSession, current_user_id: CurrentUserId) -> TagDetail:
    # `nullsfirst(deleted_at.desc())` makes an active row beat a soft-deleted
    # row that shares the same name. Without the order, `.first()` may return
    # an old deleted tag and 404 on a name that still exists.
    tag = (
        await db.scalars(
            select(Tag)
            .options(*_TAG_LOAD_OPTIONS)
            .where(or_(Tag.id == tag_id, Tag.name == tag_id))
            .order_by(nullsfirst(Tag.deleted_at.desc()))
        )
    ).first()
    if tag is None:
        raise HTTPException(404, "Not Found")
    return TagDetail.model_validate(tag, from_attributes=True)


@router.post("", name="tags_create", status_code=201)
async def post_tag(
    body: CreateTagBody,
    db: DbSession,
    current_user_id: CurrentUserId,
    _admin: str = Depends(require_access_admin),
) -> TagDetail:
    # Reject duplicates by name. Without this, CreateTag.execute() silently
    # returns the existing tag and the endpoint replies 201 with the wrong
    # row, which clients don't expect.
    existing = (
        await db.scalars(
            select(Tag).where(func.lower(Tag.name) == func.lower(body.name)).where(Tag.deleted_at.is_(None))
        )
    ).first()
    if existing is not None:
        raise HTTPException(400, "Tag already exists with the same name")

    tag = Tag(
        name=body.name,
        description=body.description if body.description is not None else "",
        constraints=body.constraints or {},
        enabled=body.enabled,
    )
    created = await CreateTag(tag=tag, current_user_id=current_user_id).execute()
    # Drop cached ORM state so the response reflects what the operation
    # committed (expire_on_commit=False keeps pre-operation state otherwise).
    created_id = created.id
    db.expire_all()
    refreshed = (await db.scalars(select(Tag).options(*_TAG_LOAD_OPTIONS).where(Tag.id == created_id))).first()
    return TagDetail.model_validate(refreshed or created, from_attributes=True)


@router.put("/{tag_id}", name="tag_by_id_put")
async def put_tag(
    tag_id: str,
    body: UpdateTagBody,
    db: DbSession,
    current_user_id: CurrentUserId,
    _admin: str = Depends(require_access_admin),
) -> TagDetail:
    from sqlalchemy.orm import selectinload

    from api.operations import ModifyGroupsTimeLimit

    tag = (
        await db.scalars(select(Tag).where(Tag.deleted_at.is_(None)).where(or_(Tag.id == tag_id, Tag.name == tag_id)))
    ).first()
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
            await db.scalars(
                select(Tag)
                .where(Tag.id != tag.id)
                .where(func.lower(Tag.name) == func.lower(new_name))
                .where(Tag.deleted_at.is_(None))
            )
        ).first()
        if collision is not None:
            raise HTTPException(400, "Tag already exists with the same name")

    if "constraints" in payload and payload["constraints"] is None:
        payload["constraints"] = {}
    if "description" in payload and payload["description"] is None:
        payload["description"] = ""
    for key in ("name", "description", "constraints", "enabled"):
        if key in payload:
            setattr(tag, key, payload[key])
    await db.commit()

    # Re-evaluate time-limit constraints for groups associated with this tag.
    # `ModifyGroupsTimeLimit` commits, expiring objects, so we re-load
    # `refreshed` afterwards before serializing.
    pre_refresh = (
        await db.scalars(select(Tag).options(selectinload(Tag.active_group_tags)).where(Tag.id == tag.id))
    ).first()
    if pre_refresh is not None and pre_refresh.active_group_tags:
        await ModifyGroupsTimeLimit(
            groups=[tm.group_id for tm in pre_refresh.active_group_tags],
            tags=[pre_refresh.id],
        ).execute()

    # Drop cached ORM state so the response reflects what the operation
    # committed (expire_on_commit=False keeps pre-operation state otherwise).
    tag_id_val = tag.id
    db.expire_all()
    refreshed = (await db.scalars(select(Tag).options(*_TAG_LOAD_OPTIONS).where(Tag.id == tag_id_val))).first()

    email = getattr(await db.get(OktaUser, current_user_id), "email", None) if current_user_id else None
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

    return TagDetail.model_validate(refreshed or tag, from_attributes=True)


@router.delete("/{tag_id}", name="tag_by_id_delete")
async def delete_tag(
    tag_id: str,
    db: DbSession,
    current_user_id: CurrentUserId,
    _admin: str = Depends(require_access_admin),
) -> DeleteMessage:
    tag = (
        await db.scalars(select(Tag).where(Tag.deleted_at.is_(None)).where(or_(Tag.id == tag_id, Tag.name == tag_id)))
    ).first()
    if tag is None:
        raise HTTPException(404, "Not Found")
    await DeleteTag(tag=tag, current_user_id=current_user_id).execute()
    return DeleteMessage(deleted=True)
