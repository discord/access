"""Apps router."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import TypeAdapter
from sqlalchemy import func
from sqlalchemy.orm import joinedload, selectinload
from starlette.requests import Request

from api.auth.dependencies import CurrentUserId
from api.auth.permissions import (
    require_access_admin_or_app_creator,
    require_app_owner_or_access_admin_for_app,
)
from api.database import DbSession
from api.extensions import db as _db
from api.models import App
from api.pagination import paginate
from api.schemas import AppOut, AppSummary, DeleteMessage

from fastapi import Depends

router = APIRouter(prefix="/api/apps", tags=["apps"])
_adapter = TypeAdapter(AppOut)
_summary_adapter = TypeAdapter(AppSummary)


@router.get("", name="apps")
def list_apps(request: Request, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    q = request.query_params.get("q", "")
    query = db.query(App).filter(App.deleted_at.is_(None)).order_by(func.lower(App.name))
    if q:
        like = f"%{q}%"
        query = query.filter(_db.or_(App.name.ilike(like), App.description.ilike(like)))
    return paginate(request, query, _summary_adapter)


@router.get("/{app_id}", name="app_by_id")
def get_app(app_id: str, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    app = (
        db.query(App)
        .options(selectinload(App.active_app_tags))
        .filter(_db.or_(App.id == app_id, App.name == app_id))
        .first()
    )
    if app is None:
        raise HTTPException(404, "Not Found")
    return _adapter.dump_python(_adapter.validate_python(app, from_attributes=True), mode="json")


@router.post("", name="apps_create", status_code=201)
def post_app(
    body: dict[str, Any] = Body(...),
    db: DbSession = None,  # type: ignore[assignment]
    current_user_id: str = Depends(require_access_admin_or_app_creator),
) -> dict[str, Any]:
    from api.operations import CreateApp

    app_obj = App(name=body.get("name", ""), description=body.get("description", ""))
    created = CreateApp(
        app=app_obj,
        owner_id=body.get("initial_owner_id"),
        owner_role_ids=body.get("initial_owner_role_ids", []),
        additional_app_groups=body.get("initial_additional_app_groups", []),
        tags=body.get("tags_to_add", []),
        current_user_id=current_user_id,
    ).execute()
    refreshed = db.query(App).options(selectinload(App.active_app_tags)).filter(App.id == created.id).first()
    return _adapter.dump_python(_adapter.validate_python(refreshed, from_attributes=True), mode="json")


@router.put("/{app_id}", name="app_by_id_put")
def put_app(
    app_id: str,
    body: dict[str, Any] = Body(...),
    db: DbSession = None,  # type: ignore[assignment]
    app_obj=Depends(require_app_owner_or_access_admin_for_app),
    current_user_id: CurrentUserId = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    from api.operations import ModifyAppTags

    if "tags_to_add" in body or "tags_to_remove" in body:
        ModifyAppTags(
            app=app_obj,
            tags_to_add=body.get("tags_to_add", []),
            tags_to_remove=body.get("tags_to_remove", []),
            current_user_id=current_user_id,
        ).execute()
    refreshed = db.query(App).options(selectinload(App.active_app_tags)).filter(App.id == app_obj.id).first()
    return _adapter.dump_python(_adapter.validate_python(refreshed, from_attributes=True), mode="json")


@router.delete("/{app_id}", name="app_by_id_delete")
def delete_app(
    app_id: str,
    db: DbSession,
    app_obj=Depends(require_app_owner_or_access_admin_for_app),
    current_user_id: CurrentUserId = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    from api.operations import DeleteApp

    DeleteApp(app=app_obj, current_user_id=current_user_id).execute()
    return DeleteMessage(deleted=True).model_dump()
