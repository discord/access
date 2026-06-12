from typing import Optional, TypedDict, TypeVar

import logging

from api.context import get_request_context
from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload, selectin_polymorphic, with_polymorphic

from api.extensions import db
from api.models import App, AppGroup, AppTagMap, OktaGroup, OktaGroupTagMap, OktaUser, RoleGroup, Tag
from api.plugins.app_group_lifecycle import get_app_group_lifecycle_hook, get_app_group_lifecycle_plugin_to_invoke
from api.services import okta
from api.schemas import AuditLogSchema, EventType

T = TypeVar("T", bound=OktaGroup)


class GroupDict(TypedDict):
    name: str
    description: str


class CreateGroup:
    def __init__(self, *, group: T | GroupDict, tags: list[str] = [], current_user_id: Optional[str] = None):
        if isinstance(group, dict):
            self.group = OktaGroup(name=group["name"], description=group["description"])
        else:
            self.group = group

        self._tags_arg = tags
        self._current_user_id_arg = current_user_id

    async def _resolve(self) -> None:
        self.tags = (
            await db.session.scalars(select(Tag).where(Tag.deleted_at.is_(None)).where(Tag.id.in_(self._tags_arg)))
        ).all()

        self.current_user_id = getattr(
            (
                await db.session.scalars(
                    select(OktaUser)
                    .where(OktaUser.deleted_at.is_(None))
                    .where(OktaUser.id == self._current_user_id_arg)
                )
            ).first(),
            "id",
            None,
        )

    async def execute(self, *, _group: Optional[T] = None) -> T:
        await self._resolve()
        # Do not allow non-deleted groups with the same name (case-insensitive)
        existing_group = (
            await db.session.scalars(
                select(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
                .where(func.lower(OktaGroup.name) == func.lower(self.group.name))
                .where(OktaGroup.deleted_at.is_(None))
            )
        ).first()
        if existing_group is not None:
            return existing_group

        # Make sure the app exists if we're creating an app group
        if (
            type(self.group) is AppGroup
            and (
                await db.session.scalars(select(App).where(App.id == self.group.app_id).where(App.deleted_at.is_(None)))
            ).first()
            is None
        ):
            raise ValueError("App for AppGroup does not exist")

        okta_group = await okta.create_group(self.group.name, self.group.description)
        if okta_group is None:
            okta_group = (await okta.list_groups(query_params={"q": self.group.name}))[0]
        self.group.id = okta_group.id
        db.session.add(self.group)
        await db.session.commit()

        # If this is an app group, add any app tags
        if type(self.group) is AppGroup:
            app_tag_maps = (
                await db.session.scalars(
                    select(AppTagMap)
                    .where(AppTagMap.app_id == self.group.app_id)
                    .where(
                        or_(
                            AppTagMap.ended_at.is_(None),
                            AppTagMap.ended_at > func.now(),
                        )
                    )
                )
            ).all()

            for app_tag_map in app_tag_maps:
                db.session.add(
                    OktaGroupTagMap(
                        tag_id=app_tag_map.tag_id,
                        group_id=self.group.id,
                        app_tag_map_id=app_tag_map.id,
                    )
                )
            await db.session.commit()

        # Add direct tags
        if len(self.tags) > 0:
            for tag in self.tags:
                db.session.add(
                    OktaGroupTagMap(
                        tag_id=tag.id,
                        group_id=self.group.id,
                    )
                )
            await db.session.commit()

        # Invoke app group lifecycle plugin hook, if configured
        plugin_id = get_app_group_lifecycle_plugin_to_invoke(self.group)
        if plugin_id is not None:
            try:
                hook = get_app_group_lifecycle_hook()
                # sync plugin hook: session-bound, runs on the greenlet bridge
                await db.session.run_sync(
                    lambda s: hook.group_created(session=s, group=self.group, plugin_id=plugin_id)
                )
                await db.session.commit()
            except Exception:
                logging.getLogger("api").exception(
                    f"Failed to invoke group_created hook for group {self.group.id} with plugin '{plugin_id}'"
                )
                await db.session.rollback()

        # Audit logging
        email = None
        if self.current_user_id is not None:
            email = getattr(await db.session.get(OktaUser, self.current_user_id), "email", None)

        group = (
            await db.session.scalars(
                select(OktaGroup)
                .options(selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]), joinedload(AppGroup.app))
                .where(OktaGroup.deleted_at.is_(None))
                .where(OktaGroup.id == self.group.id)
            )
        ).first()

        _ctx = get_request_context()

        logging.getLogger("access.audit").info(
            AuditLogSchema().dumps(
                {
                    "event_type": EventType.group_create,
                    "user_agent": _ctx.user_agent if _ctx else None,
                    "ip": _ctx.ip if _ctx else None,
                    "current_user_id": self.current_user_id,
                    "current_user_email": email,
                    "group": group,
                }
            )
        )

        return self.group
