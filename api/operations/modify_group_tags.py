from typing import Optional

import logging

from sqlalchemy import func, or_, select, update
from api.context import get_request_context
from sqlalchemy.orm import joinedload, selectin_polymorphic

from api.extensions import db
from api.models import AppGroup, OktaGroup, OktaGroupTagMap, OktaUser, RoleGroup, Tag
from api.operations.modify_groups_time_limit import ModifyGroupsTimeLimit
from api.schemas import AuditLogSchema, EventType


class ModifyGroupTags:
    def __init__(
        self,
        *,
        group: OktaGroup | str,
        tags_to_add: Optional[list[str]] = None,
        tags_to_remove: Optional[list[str]] = None,
        current_user_id: Optional[str],
    ):
        self.group_id = group if isinstance(group, str) else group.id
        self.tag_ids_to_add = tags_to_add or []
        self.tag_ids_to_remove = tags_to_remove or []
        self.current_user_id = current_user_id

    async def execute(self) -> OktaGroup:
        group = await self._fetch_group()
        tags_to_add = await self._fetch_tags(self.tag_ids_to_add)
        tags_to_remove = await self._fetch_tags(self.tag_ids_to_remove)
        current_user_id = await self._resolve_current_user_id()

        if tags_to_add:
            await self._add_tags(group, tags_to_add)
        if tags_to_remove:
            await self._remove_tags(group, tags_to_remove)
        if tags_to_add or tags_to_remove:
            await self._write_audit_log(group, tags_to_add, tags_to_remove, current_user_id)

        return group

    async def _fetch_group(self) -> OktaGroup:
        group = (
            await db.session.scalars(
                select(OktaGroup)
                .options(
                    selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
                    joinedload(AppGroup.app),
                )
                .where(OktaGroup.deleted_at.is_(None))
                .where(OktaGroup.id == self.group_id)
            )
        ).first()
        assert group is not None
        return group

    async def _fetch_tags(self, tag_ids: list[str]) -> list[Tag]:
        if not tag_ids:
            return []
        return (
            await db.session.scalars(
                select(Tag)
                .where(Tag.deleted_at.is_(None))
                .where(Tag.id.in_(tag_ids))
            )
        ).all()

    async def _resolve_current_user_id(self) -> Optional[str]:
        if self.current_user_id is None:
            return None
        user = (
            await db.session.scalars(
                select(OktaUser)
                .where(OktaUser.deleted_at.is_(None))
                .where(OktaUser.id == self.current_user_id)
            )
        ).first()
        return getattr(user, "id", None)

    async def _add_tags(self, group: OktaGroup, tags_to_add: list[Tag]) -> None:
        tag_ids_to_add = [t.id for t in tags_to_add]
        existing_tag_maps = (
            await db.session.scalars(
                select(OktaGroupTagMap)
                .where(
                    or_(
                        OktaGroupTagMap.ended_at.is_(None),
                        OktaGroupTagMap.ended_at > func.now(),
                    )
                )
                .where(OktaGroupTagMap.group_id == group.id)
                .where(OktaGroupTagMap.tag_id.in_(tag_ids_to_add))
                .where(OktaGroupTagMap.app_tag_map_id.is_(None))
            )
        ).all()

        new_tag_ids_to_add = set(tag_ids_to_add) - set([m.tag_id for m in existing_tag_maps])

        for tag_id in new_tag_ids_to_add:
            db.session.add(
                OktaGroupTagMap(
                    tag_id=tag_id,
                    group_id=group.id,
                )
            )

        await ModifyGroupsTimeLimit(groups=[group.id], tags=new_tag_ids_to_add).execute()
        await db.session.commit()

    async def _remove_tags(self, group: OktaGroup, tags_to_remove: list[Tag]) -> None:
        await db.session.execute(
            update(OktaGroupTagMap)
            .where(
                or_(
                    OktaGroupTagMap.ended_at.is_(None),
                    OktaGroupTagMap.ended_at > func.now(),
                )
            )
            .where(OktaGroupTagMap.group_id == group.id)
            .where(OktaGroupTagMap.tag_id.in_([t.id for t in tags_to_remove]))
            .where(OktaGroupTagMap.app_tag_map_id.is_(None))
            .values({OktaGroupTagMap.ended_at: func.now()})
            .execution_options(synchronize_session="fetch")
        )
        await db.session.commit()

    async def _write_audit_log(
        self,
        group: OktaGroup,
        tags_to_add: list[Tag],
        tags_to_remove: list[Tag],
        current_user_id: Optional[str],
    ) -> None:
        email = None
        audit_group = group
        if current_user_id is not None:
            email = getattr(await db.session.get(OktaUser, current_user_id), "email", None)
            audit_group = (
                await db.session.scalars(
                    select(OktaGroup)
                    .options(
                        selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
                        joinedload(AppGroup.app),
                    )
                    .where(OktaGroup.deleted_at.is_(None))
                    .where(OktaGroup.id == group.id)
                )
            ).first()

        _ctx = get_request_context()
        logging.getLogger("access.audit").info(
            AuditLogSchema().dumps(
                {
                    "event_type": EventType.group_modify_tags,
                    "user_agent": _ctx.user_agent if _ctx else None,
                    "ip": _ctx.ip if _ctx else None,
                    "current_user_id": current_user_id,
                    "current_user_email": email,
                    "group": audit_group,
                    "tags_added": tags_to_add,
                    "tags_removed": tags_to_remove,
                }
            )
        )
