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
        self.group = db.session.scalars(
            select(OktaGroup)
            .options(selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]), joinedload(AppGroup.app))
            .where(OktaGroup.deleted_at.is_(None))
            .where(OktaGroup.id == (group if isinstance(group, str) else group.id))
        ).first()

        self.tags_to_add = db.session.scalars(
            select(Tag).where(Tag.deleted_at.is_(None)).where(Tag.id.in_(tags_to_add))
        ).all()

        self.tags_to_remove = db.session.scalars(
            select(Tag).where(Tag.deleted_at.is_(None)).where(Tag.id.in_(tags_to_remove))
        ).all()

        self.current_user_id = getattr(
            db.session.scalars(
                select(OktaUser).where(OktaUser.deleted_at.is_(None)).where(OktaUser.id == current_user_id)
            ).first(),
            "id",
            None,
        )

    def execute(self) -> OktaGroup:
        if len(self.tags_to_add) > 0:
            # Only add tags that are not already associated with the group
            tag_ids_to_add = [t.id for t in self.tags_to_add]
            existing_tag_maps = db.session.scalars(
                select(OktaGroupTagMap)
                .where(
                    or_(
                        OktaGroupTagMap.ended_at.is_(None),
                        OktaGroupTagMap.ended_at > func.now(),
                    )
                )
                .where(
                    OktaGroupTagMap.group_id == self.group.id,
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
                        group_id=self.group.id,
                    )
                )

            # Handle group time limit constraints when adding tags
            # with time limit contraints to a group
            ModifyGroupsTimeLimit(groups=[self.group.id], tags=new_tag_ids_to_add).execute()

            db.session.commit()

        if len(self.tags_to_remove) > 0:
            db.session.execute(
                update(OktaGroupTagMap)
                .where(
                    or_(
                        OktaGroupTagMap.ended_at.is_(None),
                        OktaGroupTagMap.ended_at > func.now(),
                    )
                )
                .where(
                    OktaGroupTagMap.group_id == self.group.id,
                )
                .where(
                    OktaGroupTagMap.tag_id.in_([t.id for t in self.tags_to_remove]),
                )
                .where(
                    OktaGroupTagMap.app_tag_map_id.is_(None),
                )
                .values({OktaGroupTagMap.ended_at: func.now()})
                .execution_options(synchronize_session="fetch")
            )
            db.session.commit()

        # Audit log if tags changed
        if len(self.tags_to_add) > 0 or len(self.tags_to_remove) > 0:
            email = None
            if self.current_user_id is not None:
                email = getattr(db.session.get(OktaUser, self.current_user_id), "email", None)
                group = db.session.scalars(
                    select(OktaGroup)
                    .options(selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]), joinedload(AppGroup.app))
                    .where(OktaGroup.deleted_at.is_(None))
                    .where(OktaGroup.id == self.group.id)
                ).first()

            _ctx = get_request_context()
            logging.getLogger("access.audit").info(
                AuditLogSchema().dumps(
                    {
                        "event_type": EventType.group_modify_tags,
                        "user_agent": _ctx.user_agent if _ctx else None,
                        "ip": _ctx.ip if _ctx else None,
                        "current_user_id": self.current_user_id,
                        "current_user_email": email,
                        "group": group,
                        "tags_added": self.tags_to_add,
                        "tags_removed": self.tags_to_remove,
                    }
                )
            )

        return self.group
