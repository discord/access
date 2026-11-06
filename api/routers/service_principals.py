"""Service principals router.

Reads are open to every authenticated user, except each principal's `subject`,
which only Access admins see; registering, changing, and deleting a service
principal is limited to Access admins.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi_pagination.ext.sqlalchemy import apaginate
from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload, selectinload

from api.auth.dependencies import CurrentUserId
from api.auth.permissions import is_access_admin, require_access_admin
from api.database import DbSession
from api.models import ServicePrincipal, ServicePrincipalOwner
from api.operations import CreateServicePrincipal, DeleteServicePrincipal, ModifyServicePrincipal
from api.pagination import Page, validated
from api.schemas import DeleteMessage
from api.schemas.service_principals import (
    CreateServicePrincipalBody,
    SearchServicePrincipalQuery,
    ServicePrincipalDetail,
    UpdateServicePrincipalBody,
)

router = APIRouter(prefix="/api/service-principals", tags=["service-principals"])


def _load_options() -> tuple:
    return (
        selectinload(ServicePrincipal.owners).options(
            joinedload(ServicePrincipalOwner.active_user),
            joinedload(ServicePrincipalOwner.active_role_group),
        ),
    )


async def _load(db: DbSession, service_principal_id: str) -> ServicePrincipal | None:
    return (
        await db.scalars(
            select(ServicePrincipal)
            .options(*_load_options())
            .where(ServicePrincipal.id == service_principal_id)
            .where(ServicePrincipal.deleted_at.is_(None))
        )
    ).first()


def _detail(service_principal: ServicePrincipal, show_subject: bool) -> ServicePrincipalDetail:
    detail = ServicePrincipalDetail.model_validate(service_principal, from_attributes=True)
    if not show_subject:
        detail.subject = None
    return detail


@router.get("", name="service_principals")
async def list_service_principals(
    db: DbSession,
    current_user_id: CurrentUserId,
    q_args: Annotated[SearchServicePrincipalQuery, Query()],
) -> Page[ServicePrincipalDetail]:
    stmt = (
        select(ServicePrincipal)
        .options(*_load_options())
        .where(ServicePrincipal.deleted_at.is_(None))
        .order_by(func.lower(ServicePrincipal.display_name))
    )
    show_subject = await is_access_admin(db, current_user_id)
    if q_args.q:
        like = f"%{q_args.q}%"
        clauses = [ServicePrincipal.id.like(f"{q_args.q}%"), ServicePrincipal.display_name.ilike(like)]
        if show_subject:
            clauses.append(ServicePrincipal.subject.ilike(like))
        stmt = stmt.where(or_(*clauses))
    page = await apaginate(db, stmt, transformer=validated(ServicePrincipalDetail))
    if not show_subject:
        for item in page.items:
            item.subject = None
    return page


@router.get("/{service_principal_id}", name="service_principal_by_id")
async def get_service_principal(
    service_principal_id: str, db: DbSession, current_user_id: CurrentUserId
) -> ServicePrincipalDetail:
    service_principal = await _load(db, service_principal_id)
    if service_principal is None:
        raise HTTPException(404, "Not Found")
    return _detail(service_principal, await is_access_admin(db, current_user_id))


@router.post("", name="service_principals_create", status_code=201)
async def post_service_principal(
    body: CreateServicePrincipalBody,
    db: DbSession,
    current_user_id: CurrentUserId,
    _admin: Annotated[str, Depends(require_access_admin)],
) -> ServicePrincipalDetail:
    created = await CreateServicePrincipal(
        display_name=body.display_name,
        provider=body.provider,
        subject=body.subject,
        owner_user_ids=body.owner_user_ids,
        owner_role_ids=body.owner_role_ids,
        current_user_id=current_user_id,
    ).execute()
    created_id = created.id
    db.expire_all()
    refreshed = await _load(db, created_id)
    assert refreshed is not None
    return _detail(refreshed, show_subject=True)


@router.put("/{service_principal_id}", name="service_principal_by_id_put")
async def put_service_principal(
    service_principal_id: str,
    body: UpdateServicePrincipalBody,
    db: DbSession,
    current_user_id: CurrentUserId,
    _admin: Annotated[str, Depends(require_access_admin)],
) -> ServicePrincipalDetail:
    if await _load(db, service_principal_id) is None:
        raise HTTPException(404, "Not Found")
    await ModifyServicePrincipal(
        service_principal=service_principal_id,
        display_name=body.display_name,
        subject=body.subject,
        owner_user_ids=body.owner_user_ids,
        owner_role_ids=body.owner_role_ids,
        current_user_id=current_user_id,
    ).execute()
    db.expire_all()
    refreshed = await _load(db, service_principal_id)
    assert refreshed is not None
    return _detail(refreshed, show_subject=True)


@router.delete("/{service_principal_id}", name="service_principal_by_id_delete")
async def delete_service_principal(
    service_principal_id: str,
    db: DbSession,
    current_user_id: CurrentUserId,
    _admin: Annotated[str, Depends(require_access_admin)],
) -> DeleteMessage:
    if await _load(db, service_principal_id) is None:
        raise HTTPException(404, "Not Found")
    await DeleteServicePrincipal(service_principal=service_principal_id, current_user_id=current_user_id).execute()
    return DeleteMessage(deleted=True)
