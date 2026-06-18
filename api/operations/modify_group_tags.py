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
        tags_to_add: list[str] = [],
        tags_to_remove: list[str] = [],
        current_user_id: Optional[str],
    ):
        self.group_id = group if isinstance(group, str) else group.id
        self.tag_ids_to_add = tags_to_add
        self.tag_ids_to_remove = tags_to_remove
        self.current_user_id = current_user_id

    def execute(self) -> OktaGroup:
        group = db.session.scalars(
            select(OktaGroup)
            .options(selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]), joinedload(AppGroup.app))
            .where(OktaGroup.deleted_at.is_(None))
            .where(OktaGroup.id == self.group_id)
        ).first()

        tags_to_add = db.session.scalars(
            select(Tag).where(Tag.deleted_at.is_(None)).where(Tag.id.in_(self.tag_ids_to_add))
        ).all()

        tags_to_remove = db.session.scalars(
            select(Tag).where(Tag.deleted_at.is_(None)).where(Tag.id.in_(self.tag_ids_to_remove))
        ).all()

        current_user_id = getattr(
            db.session.scalars(
                select(OktaUser).where(OktaUser.deleted_at.is_(None)).where(OktaUser.id == self.current_user_id)
            ).first(),
            "id",
            None,
        )

        if len(tags_to_add) > 0:
            # Only add tags that are not already associated with the group
            tag_ids_to_add = [t.id for t in tags_to_add]
            existing_tag_maps = db.session.scalars(
                select(OktaGroupTagMap)
                .where(
                    or_(
                        OktaGroupTagMap.ended_at.is_(None),
                        OktaGroupTagMap.ended_at > func.now(),
                    )
                )
                .where(
                    OktaGroupTagMap.group_id == group.id,
                )
                .where(
                    OktaGroupTagMap.tag_id.in_(tag_ids_to_add),
                )
                .where(
                    OktaGroupTagMap.app_tag_map_id.is_(None),
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

            # Handle group time limit constraints when adding tags
            # with time limit contraints to a group
            ModifyGroupsTimeLimit(groups=[group.id], tags=new_tag_ids_to_add).execute()

            db.session.commit()

        if len(tags_to_remove) > 0:
            db.session.execute(
                update(OktaGroupTagMap)
                .where(
                    or_(
                        OktaGroupTagMap.ended_at.is_(None),
                        OktaGroupTagMap.ended_at > func.now(),
                    )
                )
                .where(
                    OktaGroupTagMap.group_id == group.id,
                )
                .where(
                    OktaGroupTagMap.tag_id.in_([t.id for t in tags_to_remove]),
                )
                .where(
                    OktaGroupTagMap.app_tag_map_id.is_(None),
                )
                .values({OktaGroupTagMap.ended_at: func.now()})
                .execution_options(synchronize_session="fetch")
            )
            db.session.commit()

        # Audit log if tags changed
        if len(tags_to_add) > 0 or len(tags_to_remove) > 0:
            email = None
            if current_user_id is not None:
                email = getattr(db.session.get(OktaUser, current_user_id), "email", None)
                audit_group = db.session.scalars(
                    select(OktaGroup)
                    .options(selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]), joinedload(AppGroup.app))
                    .where(OktaGroup.deleted_at.is_(None))
                    .where(OktaGroup.id == group.id)
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

        return group
