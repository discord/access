from typing import Optional

import logging

from api.context import get_request_context
from sqlalchemy import delete, func, insert, or_, select, update
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
from api.plugins.app_group_lifecycle import AppGroupLifecycleHook, invoke_app_group_lifecycle_hook
from api.schemas import AuditLogSchema, EventType


class ModifyGroupType:
    def __init__(self, *, group: OktaGroup | str, group_changes: OktaGroup, current_user_id: Optional[str]):
        self.group_id = group if isinstance(group, str) else group.id

        self.group_changes = group_changes
        self.current_user_id = current_user_id

    async def execute(self) -> OktaGroup:
        group = (
            await db.session.scalars(
                select(OktaGroup)
                .options(selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]), joinedload(AppGroup.app))
                .where(OktaGroup.deleted_at.is_(None))
                .where(OktaGroup.id == self.group_id)
            )
        ).first()

        current_user_id = getattr(
            (
                await db.session.scalars(
                    select(OktaUser).where(OktaUser.deleted_at.is_(None)).where(OktaUser.id == self.current_user_id)
                )
            ).first(),
            "id",
            None,
        )

        # Update group type if it's being modified
        if type(group) is not type(self.group_changes):
            group_id = group.id
            old_group_type = group.type

            # Clean-up the old child table row
            if type(group) is RoleGroup:
                # Bail if changing away from RoleGroup for a group whose name uses the
                # reserved Role- prefix; non-RoleGroup groups must not carry that prefix
                if type(self.group_changes) is not RoleGroup and group.name.startswith(
                    RoleGroup.ROLE_GROUP_NAME_PREFIX
                ):
                    raise ValueError(
                        "The Role- prefix cannot be used for non-role groups. Please choose a different group name."
                    )

                # End all group attachments to this role and all group memberships via the role grant
                active_role_associated_groups = (
                    await db.session.scalars(
                        select(RoleGroupMap)
                        .where(
                            or_(
                                RoleGroupMap.ended_at.is_(None),
                                RoleGroupMap.ended_at > func.now(),
                            )
                        )
                        .where(RoleGroupMap.role_group_id == group.id)
                    )
                ).all()
                await ModifyRoleGroups(
                    role_group=group,
                    current_user_id=current_user_id,
                    groups_to_remove=[g.group_id for g in active_role_associated_groups if not g.is_owner],
                    owner_groups_to_remove=[g.group_id for g in active_role_associated_groups if g.is_owner],
                ).execute()
                await db.session.commit()

                await db.session.execute(delete(RoleGroup.__table__).where(RoleGroup.__table__.c.id == group_id))
            elif type(group) is AppGroup:
                # Bail if this is the owner group for the app
                # which cannot have its type changed
                if group.is_owner:
                    raise ValueError("Owner app groups cannot have their type modified")

                # Bail if changing away from AppGroup for a group whose name uses the
                # reserved App- prefix; non-AppGroup groups must not carry that prefix
                if type(self.group_changes) is not AppGroup and group.name.startswith(AppGroup.APP_GROUP_NAME_PREFIX):
                    raise ValueError(
                        "The App- prefix cannot be used for non-app groups. Please choose a different group name."
                    )

                # Invoke group_deleted hook before the AppGroup row is removed so the
                # plugin can still access group.app and status values (e.g. to delete
                # the linked GitHub team).
                await invoke_app_group_lifecycle_hook(AppGroupLifecycleHook.GROUP_DELETED, group=group)

                # Remove app tag map for this group that is no longer attached to an app
                await db.session.execute(
                    update(OktaGroupTagMap)
                    .where(
                        or_(
                            OktaGroupTagMap.ended_at.is_(None),
                            OktaGroupTagMap.ended_at > func.now(),
                        )
                    )
                    .where(OktaGroupTagMap.group_id == group.id)
                    .where(OktaGroupTagMap.app_tag_map_id.isnot(None))
                    .values({OktaGroupTagMap.app_tag_map_id: None})
                    .execution_options(synchronize_session="fetch")
                )
                await db.session.commit()

                await db.session.execute(delete(AppGroup.__table__).where(AppGroup.__table__.c.id == group_id))
            # Expunge the session so the changed object is flushed from the ORM
            # See https://stackoverflow.com/a/21792969
            db.session.expunge_all()

            # We've deleted the group child class row group,
            # update the type to the base class type "okta_group"
            group.type = OktaGroup.__mapper_args__["polymorphic_identity"]
            await db.session.commit()

            group = (
                await db.session.scalars(
                    select(OktaGroup).where(OktaGroup.deleted_at.is_(None)).where(OktaGroup.id == group_id)
                )
            ).first()

            # Create new child table row
            if type(self.group_changes) is RoleGroup:
                # Convert any group memberships and ownerships via a role to direct group memberships and ownerships
                active_group_users_from_role = (
                    await db.session.scalars(
                        select(OktaUserGroupMember)
                        .where(
                            or_(
                                OktaUserGroupMember.ended_at.is_(None),
                                OktaUserGroupMember.ended_at > func.now(),
                            )
                        )
                        .where(OktaUserGroupMember.role_group_map_id.is_not(None))
                        .where(OktaUserGroupMember.group_id == group_id)
                    )
                ).all()
                # Add all group memberships and ownerships via a role grant as direct memberships and ownerships
                # Do this in a loop so we can preserve the ended_at value
                for group_user in active_group_users_from_role:
                    if group_user.is_owner:
                        await ModifyGroupUsers(
                            group=group_id,
                            current_user_id=current_user_id,
                            owners_to_add=[group_user.user_id],
                            users_added_ended_at=group_user.ended_at,
                        ).execute()
                    else:
                        await ModifyGroupUsers(
                            group=group_id,
                            current_user_id=current_user_id,
                            members_to_add=[group_user.user_id],
                            users_added_ended_at=group_user.ended_at,
                        ).execute()

                # Remove all group memberships and ownerships via a role grant
                active_role_associated_groups = (
                    await db.session.scalars(
                        select(RoleGroupMap)
                        .where(
                            or_(
                                RoleGroupMap.ended_at.is_(None),
                                RoleGroupMap.ended_at > func.now(),
                            )
                        )
                        .where(RoleGroupMap.group_id == group_id)
                    )
                ).all()
                for role_group_map in active_role_associated_groups:
                    if role_group_map.is_owner:
                        await ModifyRoleGroups(
                            role_group=role_group_map.role_group_id,
                            current_user_id=current_user_id,
                            owner_groups_to_remove=[role_group_map.group_id],
                        ).execute()
                    else:
                        await ModifyRoleGroups(
                            role_group=role_group_map.role_group_id,
                            current_user_id=current_user_id,
                            groups_to_remove=[role_group_map.group_id],
                        ).execute()

                await db.session.execute(insert(RoleGroup.__table__).values(id=group_id))
            elif type(self.group_changes) is AppGroup:
                await db.session.execute(
                    insert(AppGroup.__table__).values(
                        id=group_id,
                        app_id=self.group_changes.app_id,
                    )
                )

            # Update the group type
            group.type = self.group_changes.type
            await db.session.commit()

            # Expunge the session so the changed object is flushed from the ORM
            # See https://stackoverflow.com/a/21792969
            db.session.expunge_all()

            # Add all app tags to this new app group, after we've updated the group type
            if type(self.group_changes) is AppGroup:
                app_tag_maps = (
                    await db.session.scalars(
                        select(AppTagMap)
                        .options(joinedload(AppTagMap.active_tag))
                        .where(
                            or_(
                                AppTagMap.ended_at.is_(None),
                                AppTagMap.ended_at > func.now(),
                            )
                        )
                        .where(
                            AppTagMap.app_id == self.group_changes.app_id,
                        )
                    )
                ).all()
                for app_tag_map in app_tag_maps:
                    db.session.add(
                        OktaGroupTagMap(
                            tag_id=app_tag_map.tag_id,
                            group_id=group_id,
                            app_tag_map_id=app_tag_map.id,
                        )
                    )

                # Handle group time limit constraints when adding tags with time limit contraints to a group
                await ModifyGroupsTimeLimit(
                    groups=[group_id], tags=[tag_map.active_tag.id for tag_map in app_tag_maps]
                ).execute()

            # Return a new lookup for the group
            group = (
                await db.session.scalars(
                    select(OktaGroup)
                    .options(selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]), joinedload(AppGroup.app))
                    .where(OktaGroup.deleted_at.is_(None))
                    .where(OktaGroup.id == group_id)
                )
            ).first()

            # Invoke group_created hook after converting to an AppGroup (symmetric
            # with group_deleted which fires when converting away from AppGroup).
            if type(self.group_changes) is AppGroup:
                await invoke_app_group_lifecycle_hook(AppGroupLifecycleHook.GROUP_CREATED, group=group)

        # Audit logging if type changed
        if group.type != old_group_type:
            email = None
            if current_user_id is not None:
                email = getattr(await db.session.get(OktaUser, current_user_id), "email", None)

            _ctx = get_request_context()
            logging.getLogger("access.audit").info(
                AuditLogSchema().dumps(
                    {
                        "event_type": EventType.group_modify_type,
                        "user_agent": _ctx.user_agent if _ctx else None,
                        "ip": _ctx.ip if _ctx else None,
                        "current_user_id": current_user_id,
                        "current_user_email": email,
                        "group": group,
                        "old_group_type": old_group_type,
                    }
                )
            )

        return group
