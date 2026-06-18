from typing import Optional

import logging

from sqlalchemy import func, or_, select, update
from api.context import get_request_context

from api.extensions import db
from api.models import App, AppGroup, AppTagMap, OktaGroupTagMap, OktaUser, Tag
from api.operations import ModifyGroupsTimeLimit
from api.schemas import AuditLogSchema, EventType


class ModifyAppTags:
    def __init__(
        self,
        *,
        app: App | str,
        tags_to_add: list[str] = [],
        tags_to_remove: list[str] = [],
        current_user_id: Optional[str],
    ):
        self.app_id = app if isinstance(app, str) else app.id
        self._tags_to_add_arg = tags_to_add
        self._tags_to_remove_arg = tags_to_remove
        self._current_user_id_arg = current_user_id

    def execute(self) -> App:
        app = db.session.scalars(select(App).where(App.deleted_at.is_(None)).where(App.id == self.app_id)).first()

        tags_to_add = db.session.scalars(
            select(Tag).where(Tag.deleted_at.is_(None)).where(Tag.id.in_(self._tags_to_add_arg))
        ).all()

        tags_to_remove = db.session.scalars(
            select(Tag).where(Tag.deleted_at.is_(None)).where(Tag.id.in_(self._tags_to_remove_arg))
        ).all()

        current_user_id = getattr(
            db.session.scalars(
                select(OktaUser).where(OktaUser.deleted_at.is_(None)).where(OktaUser.id == self._current_user_id_arg)
            ).first(),
            "id",
            None,
        )

        if len(tags_to_add) > 0:
            # Only add tags that are not already associated with this app
            tag_ids_to_add = [t.id for t in tags_to_add]
            existing_tag_maps = db.session.scalars(
                select(AppTagMap)
                .where(
                    or_(
                        AppTagMap.ended_at.is_(None),
                        AppTagMap.ended_at > func.now(),
                    )
                )
                .where(
                    AppTagMap.app_id == app.id,
                )
                .where(
                    AppTagMap.tag_id.in_(tag_ids_to_add),
                )
            ).all()

            new_tag_ids_to_add = set(tag_ids_to_add) - set([m.tag_id for m in existing_tag_maps])

            for tag_id in new_tag_ids_to_add:
                db.session.add(
                    AppTagMap(
                        tag_id=tag_id,
                        app_id=app.id,
                    )
                )
            db.session.commit()

            new_app_tag_maps = db.session.scalars(
                select(AppTagMap)
                .where(AppTagMap.tag_id.in_(new_tag_ids_to_add))
                .where(AppTagMap.app_id == app.id)
                .where(
                    or_(
                        AppTagMap.ended_at.is_(None),
                        AppTagMap.ended_at > func.now(),
                    )
                )
            ).all()

            all_app_groups = db.session.scalars(
                select(AppGroup).where(AppGroup.app_id == app.id).where(AppGroup.deleted_at.is_(None))
            ).all()
            for app_tag_map in new_app_tag_maps:
                for app_group in all_app_groups:
                    db.session.add(
                        OktaGroupTagMap(
                            tag_id=app_tag_map.tag_id,
                            group_id=app_group.id,
                            app_tag_map_id=app_tag_map.id,
                        )
                    )

            # Handle group time limit constraints when adding tags
            # with time limit contraints to an app
            ModifyGroupsTimeLimit(groups=[g.id for g in all_app_groups], tags=new_tag_ids_to_add).execute()

            db.session.commit()

        if len(tags_to_remove) > 0:
            tag_ids_to_remove = [t.id for t in tags_to_remove]
            existing_tag_maps_query = (
                select(AppTagMap)
                .where(AppTagMap.tag_id.in_(tag_ids_to_remove))
                .where(AppTagMap.app_id == app.id)
                .where(
                    or_(
                        AppTagMap.ended_at.is_(None),
                        AppTagMap.ended_at > func.now(),
                    )
                )
            )

            existing_tag_maps = db.session.scalars(existing_tag_maps_query).all()
            db.session.execute(
                update(OktaGroupTagMap)
                .where(
                    or_(
                        OktaGroupTagMap.ended_at.is_(None),
                        OktaGroupTagMap.ended_at > func.now(),
                    )
                )
                .where(
                    OktaGroupTagMap.app_tag_map_id.in_([m.id for m in existing_tag_maps]),
                )
                .values({OktaGroupTagMap.ended_at: func.now()})
                .execution_options(synchronize_session="fetch")
            )

            db.session.execute(
                update(AppTagMap)
                .where(AppTagMap.tag_id.in_(tag_ids_to_remove))
                .where(AppTagMap.app_id == app.id)
                .where(
                    or_(
                        AppTagMap.ended_at.is_(None),
                        AppTagMap.ended_at > func.now(),
                    )
                )
                .values({AppTagMap.ended_at: func.now()})
                .execution_options(synchronize_session="fetch")
            )
            db.session.commit()

        # Audit log if tags changed
        if len(tags_to_add) > 0 or len(tags_to_remove) > 0:
            email = None
            if current_user_id is not None:
                email = getattr(db.session.get(OktaUser, current_user_id), "email", None)

            _ctx = get_request_context()
            logging.getLogger("access.audit").info(
                AuditLogSchema().dumps(
                    {
                        "event_type": EventType.app_modify_tags,
                        "user_agent": _ctx.user_agent if _ctx else None,
                        "ip": _ctx.ip if _ctx else None,
                        "current_user_id": current_user_id,
                        "current_user_email": email,
                        "app": app,
                        "tags_added": tags_to_add,
                        "tags_removed": tags_to_remove,
                    }
                )
            )

        return app
