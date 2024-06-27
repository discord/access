from typing import Optional

from flask import current_app, has_request_context, request
from sqlalchemy import delete, insert
from sqlalchemy.orm import joinedload, selectin_polymorphic

from api.extensions import db
from api.models import (
    AppGroup,
    AppTagMap,
    OktaGroup,
    OktaGroupTagMap,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    RoleGroupMap,
)
from api.operations.modify_group_users import ModifyGroupUsers
from api.operations.modify_groups_time_limit import ModifyGroupsTimeLimit
from api.operations.modify_role_groups import ModifyRoleGroups
from api.views.schemas import AuditLogSchema, EventType


class ModifyGroupType:
    def __init__(self, *, group: OktaGroup | str, group_changes: OktaGroup, current_user_id: Optional[str]):
        self.group = (
            db.session.query(OktaGroup)
            .options(selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]), joinedload(AppGroup.app))
            .filter(OktaGroup.deleted_at.is_(None))
            .filter(OktaGroup.id == (group if isinstance(group, str) else group.id))
            .first()
        )

        self.group_changes = group_changes
        self.current_user_id = getattr(
            OktaUser.query.filter(OktaUser.deleted_at.is_(None)).filter(OktaUser.id == current_user_id).first(),
            "id",
            None,
        )

    def execute(self) -> OktaGroup:
        # Update group type if it's being modified
        if type(self.group) is not type(self.group_changes):
            group_id = self.group.id
            old_group_type = self.group.type

            # Clean-up the old child table row
            if type(self.group) is RoleGroup:
                # End all group attachments to this role and all group memberships via the role grant
                active_role_associated_groups = RoleGroupMap.query.filter(
                    db.or_(
                        RoleGroupMap.ended_at.is_(None),
                        RoleGroupMap.ended_at > db.func.now(),
                    )
                ).filter(RoleGroupMap.role_group_id == self.group.id)
                ModifyRoleGroups(
                    role_group=self.group,
                    current_user_id=self.current_user_id,
                    groups_to_remove=[g.group_id for g in active_role_associated_groups if not g.is_owner],
                    owner_groups_to_remove=[g.group_id for g in active_role_associated_groups if g.is_owner],
                ).execute()
                db.session.commit()

                db.session.execute(delete(RoleGroup.__table__).where(RoleGroup.__table__.c.id == group_id))
            elif type(self.group) is AppGroup:
                # Bail if this is the owner group for the app
                # which cannot have its type changed
                if self.group.is_owner:
                    raise ValueError("Owner app groups cannot have their type modified")

                # Remove app tag map for this group that is no longer attached to an app
                OktaGroupTagMap.query.filter(
                    db.or_(
                        OktaGroupTagMap.ended_at.is_(None),
                        OktaGroupTagMap.ended_at > db.func.now(),
                    )
                ).filter(OktaGroupTagMap.group_id == self.group.id).filter(
                    OktaGroupTagMap.app_tag_map_id.isnot(None)
                ).update(
                    {OktaGroupTagMap.app_tag_map_id: None},
                    synchronize_session="fetch",
                )
                db.session.commit()

                db.session.execute(delete(AppGroup.__table__).where(AppGroup.__table__.c.id == group_id))
            # Expunge the session so the changed object is flushed from the ORM
            # See https://stackoverflow.com/a/21792969
            db.session.expunge_all()

            # We've deleted the group child class row group,
            # update the type to the base class type "okta_group"
            self.group.type = OktaGroup.__mapper_args__["polymorphic_identity"]
            db.session.commit()

            self.group = OktaGroup.query.filter(OktaGroup.deleted_at.is_(None)).filter(OktaGroup.id == group_id).first()

            # Create new child table row
            if type(self.group_changes) is RoleGroup:
                # Convert any group memberships and ownerships via a role to direct group memberships and ownerships
                active_group_users_from_role = (
                    OktaUserGroupMember.query.filter(
                        db.or_(
                            OktaUserGroupMember.ended_at.is_(None),
                            OktaUserGroupMember.ended_at > db.func.now(),
                        )
                    )
                    .filter(OktaUserGroupMember.role_group_map_id.is_not(None))
                    .filter(OktaUserGroupMember.group_id == group_id)
                    .all()
                )
                # Add all group memberships and ownerships via a role grant as direct memberships and ownerships
                # Do this in a loop so we can preserve the ended_at value
                for group_user in active_group_users_from_role:
                    if group_user.is_owner:
                        ModifyGroupUsers(
                            group=group_id,
                            current_user_id=self.current_user_id,
                            owners_to_add=[group_user.user_id],
                            users_added_ended_at=group_user.ended_at,
                        ).execute()
                    else:
                        ModifyGroupUsers(
                            group=group_id,
                            current_user_id=self.current_user_id,
                            members_to_add=[group_user.user_id],
                            users_added_ended_at=group_user.ended_at,
                        ).execute()

                # Remove all group memberships and ownerships via a role grant
                active_role_associated_groups = (
                    RoleGroupMap.query.filter(
                        db.or_(
                            RoleGroupMap.ended_at.is_(None),
                            RoleGroupMap.ended_at > db.func.now(),
                        )
                    )
                    .filter(RoleGroupMap.group_id == group_id)
                    .all()
                )
                for role_group_map in active_role_associated_groups:
                    if role_group_map.is_owner:
                        ModifyRoleGroups(
                            role_group=role_group_map.role_group_id,
                            current_user_id=self.current_user_id,
                            owner_groups_to_remove=[role_group_map.group_id],
                        ).execute()
                    else:
                        ModifyRoleGroups(
                            role_group=role_group_map.role_group_id,
                            current_user_id=self.current_user_id,
                            groups_to_remove=[role_group_map.group_id],
                        ).execute()

                db.session.execute(insert(RoleGroup.__table__).values(id=group_id))
            elif type(self.group_changes) is AppGroup:
                db.session.execute(
                    insert(AppGroup.__table__).values(
                        id=group_id,
                        app_id=self.group_changes.app_id,
                    )
                )

            # Update the group type
            self.group.type = self.group_changes.type
            db.session.commit()

            # Expunge the session so the changed object is flushed from the ORM
            # See https://stackoverflow.com/a/21792969
            db.session.expunge_all()

            # Add all app tags to this new app group, after we've updated the group type
            if type(self.group_changes) is AppGroup:
                app_tag_maps = (
                    AppTagMap.query.options(joinedload(AppTagMap.active_tag))
                    .filter(
                        db.or_(
                            AppTagMap.ended_at.is_(None),
                            AppTagMap.ended_at > db.func.now(),
                        )
                    )
                    .filter(
                        AppTagMap.app_id == self.group_changes.app_id,
                    )
                    .all()
                )
                for app_tag_map in app_tag_maps:
                    db.session.add(
                        OktaGroupTagMap(
                            tag_id=app_tag_map.tag_id,
                            group_id=group_id,
                            app_tag_map_id=app_tag_map.id,
                        )
                    )

                # Handle group time limit constraints when adding tags with time limit contraints to a group
                ModifyGroupsTimeLimit(
                    groups=[group_id], tags=[tag_map.active_tag.id for tag_map in app_tag_maps]
                ).execute()

            # Return a new lookup for the group
            self.group = (
                db.session.query(OktaGroup)
                .options(selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]), joinedload(AppGroup.app))
                .filter(OktaGroup.deleted_at.is_(None))
                .filter(OktaGroup.id == group_id)
                .first()
            )

        # Audit logging if type changed
        if self.group.type != old_group_type:
            email = None
            if self.current_user_id is not None:
                email = getattr(db.session.get(OktaUser, self.current_user_id), "email", None)

            context = has_request_context()
            current_app.logger.info(
                AuditLogSchema().dumps(
                    {
                        "event_type": EventType.group_modify_type,
                        "user_agent": request.headers.get("User-Agent") if context else None,
                        "ip": request.headers.get(
                            "X-Forwarded-For", request.headers.get("X-Real-IP", request.remote_addr)
                        )
                        if context
                        else None,
                        "current_user_id": self.current_user_id,
                        "current_user_email": email,
                        "group": self.group,
                        "old_group_type": old_group_type,
                    }
                )
            )

        return self.group
