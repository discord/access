import random
import string
from typing import Optional, TypedDict

import logging

from api.context import get_request_context
from sqlalchemy import func, or_, select
from sqlalchemy.orm import with_polymorphic

from api.extensions import db
from api.models import App, AppGroup, AppTagMap, OktaGroup, OktaGroupTagMap, OktaUser, RoleGroup, Tag
from api.models.app_group import app_owners_group_description
from api.operations.create_group import CreateGroup, GroupDict
from api.operations.modify_group_type import ModifyGroupType
from api.operations.modify_group_users import ModifyGroupUsers
from api.operations.modify_role_groups import ModifyRoleGroups
from api.schemas import AuditLogSchema, EventType


class AppDict(TypedDict):
    name: str
    description: str


class CreateApp:
    def __init__(
        self,
        *,
        app: App | AppDict,
        tags: list[str] = [],
        owner_id: Optional[str] = None,
        owner_role_ids: Optional[list[str]] = None,
        additional_app_groups: Optional[list[AppGroup] | list[GroupDict]] = None,
        current_user_id: Optional[str] = None,
    ):
        id = self.__generate_id()
        if isinstance(app, dict):
            self.app = App(id=id, name=app["name"], description=app["description"])
        else:
            app.id = id
            self.app = app

        self.tag_ids = tags
        self.owner_id = owner_id
        self.owner_role_ids = owner_role_ids

        self.app_group_prefix = (
            f"{AppGroup.APP_GROUP_NAME_PREFIX}{self.app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
        )
        self.owner_group_name = f"{self.app_group_prefix}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}"
        self.additional_app_groups = []
        if additional_app_groups is not None:
            for group in additional_app_groups:
                name = ""
                description = ""
                if isinstance(group, dict):
                    name = group["name"]
                    description = group.get("description", "")
                else:
                    name = group.name
                    description = group.description
                if not name.startswith(self.app_group_prefix):
                    name = f"{self.app_group_prefix}{name}"
                if name == self.owner_group_name:
                    continue
                self.additional_app_groups.append(AppGroup(is_owner=False, name=name, description=description))
        self.current_user_id = current_user_id

    async def execute(self) -> App:
        tag_ids = [
            tag.id
            for tag in (
                await db.session.scalars(select(Tag).where(Tag.deleted_at.is_(None)).where(Tag.id.in_(self.tag_ids)))
            ).all()
        ]

        owner_id = getattr(
            (
                await db.session.scalars(
                    select(OktaUser).where(OktaUser.deleted_at.is_(None)).where(OktaUser.id == self.owner_id)
                )
            ).first(),
            "id",
            None,
        )
        owner_role_ids: Optional[list[str]] = None
        if self.owner_role_ids is not None:
            owner_roles = (
                await db.session.scalars(
                    select(RoleGroup).where(RoleGroup.id.in_(self.owner_role_ids)).where(RoleGroup.deleted_at.is_(None))
                )
            ).all()
            owner_role_ids = [role.id for role in owner_roles]

        current_user_id = getattr(
            (
                await db.session.scalars(
                    select(OktaUser).where(OktaUser.deleted_at.is_(None)).where(OktaUser.id == self.current_user_id)
                )
            ).first(),
            "id",
            None,
        )

        # Do not allow non-deleted apps with the same name
        existing_app = (
            await db.session.scalars(
                select(App).where(func.lower(App.name) == func.lower(self.app.name)).where(App.deleted_at.is_(None))
            )
        ).first()
        if existing_app is not None:
            return existing_app

        # Audit logging
        email = None
        if current_user_id is not None:
            email = getattr(await db.session.get(OktaUser, current_user_id), "email", None)

        _ctx = get_request_context()

        logging.getLogger("access.audit").info(
            AuditLogSchema().dumps(
                {
                    "event_type": EventType.app_create,
                    "user_agent": _ctx.user_agent if _ctx else None,
                    "ip": _ctx.ip if _ctx else None,
                    "current_user_id": current_user_id,
                    "current_user_email": email,
                    "app": self.app,
                    "owner_id": owner_id,
                }
            )
        )

        db.session.add(self.app)
        await db.session.commit()

        app_id = self.app.id

        existing_owner_group = (
            await db.session.scalars(
                select(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
                .where(func.lower(OktaGroup.name) == func.lower(self.owner_group_name))
                .where(OktaGroup.deleted_at.is_(None))
            )
        ).first()

        if existing_owner_group is None:
            owner_app_group = AppGroup(
                app_id=app_id,
                is_owner=True,
                name=self.owner_group_name,
                description=app_owners_group_description(self.app.name),
            )
            owner_app_group = await CreateGroup(group=owner_app_group, current_user_id=current_user_id).execute()
        else:
            group_id = existing_owner_group.id
            if type(existing_owner_group) is not AppGroup:
                await ModifyGroupType(
                    group=existing_owner_group,
                    group_changes=AppGroup(app_id=app_id, is_owner=True),
                    current_user_id=current_user_id,
                ).execute()
            owner_app_group = (await db.session.scalars(select(AppGroup).where(AppGroup.id == group_id))).first()
            owner_app_group.app_id = app_id
            owner_app_group.is_owner = True
            await db.session.commit()

        if owner_id is not None:
            # Add the app owner to the app owner group as members and owners
            await ModifyGroupUsers(
                group=owner_app_group,
                current_user_id=current_user_id,
                members_to_add=[owner_id],
                owners_to_add=[owner_id],
            ).execute()

        if owner_role_ids is not None:
            for role_id in owner_role_ids:
                await ModifyRoleGroups(
                    role_group=role_id,
                    current_user_id=current_user_id,
                    groups_to_add=[owner_app_group.id],
                    owner_groups_to_add=[owner_app_group.id],
                ).execute()

        # Find other app groups with the same app name prefix and update them
        other_existing_app_groups = (
            await db.session.scalars(
                select(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
                .where(OktaGroup.name.ilike(f"{self.app_group_prefix}%"))
                .where(func.lower(OktaGroup.name) != func.lower(self.owner_group_name))
                .where(OktaGroup.deleted_at.is_(None))
            )
        ).all()
        existing_app_group_ids_to_update = []
        for existing_app_group in other_existing_app_groups:
            if type(existing_app_group) is not AppGroup:
                existing_app_group_ids_to_update.append(existing_app_group.id)

        for existing_app_group_id in existing_app_group_ids_to_update:
            await ModifyGroupType(
                group=existing_app_group_id,
                group_changes=AppGroup(app_id=app_id, is_owner=False),
                current_user_id=current_user_id,
            ).execute()

        # Create any additional app groups for the app
        if self.additional_app_groups is not None:
            for app_group in self.additional_app_groups:
                app_group.app_id = app_id

                existing_group = (
                    await db.session.scalars(
                        select(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
                        .where(func.lower(OktaGroup.name) == func.lower(app_group.name))
                        .where(OktaGroup.deleted_at.is_(None))
                    )
                ).first()

                if existing_group is None:
                    await CreateGroup(group=app_group, current_user_id=current_user_id).execute()
                else:
                    group_id = existing_group.id
                    if type(existing_group) is not AppGroup:
                        await ModifyGroupType(
                            group=existing_group,
                            group_changes=AppGroup(app_id=app_id, is_owner=False),
                            current_user_id=current_user_id,
                        ).execute()
                    app_group = (await db.session.scalars(select(AppGroup).where(AppGroup.id == group_id))).first()
                    app_group.app_id = app_id
                    app_group.is_owner = False
                    await db.session.commit()

        if len(tag_ids) > 0:
            all_app_groups = (
                await db.session.scalars(
                    select(AppGroup).where(AppGroup.app_id == app_id).where(AppGroup.deleted_at.is_(None))
                )
            ).all()

            for tag_id in tag_ids:
                db.session.add(
                    AppTagMap(
                        tag_id=tag_id,
                        app_id=app_id,
                    )
                )
            await db.session.commit()

            new_app_tag_maps = (
                await db.session.scalars(
                    select(AppTagMap)
                    .where(AppTagMap.app_id == app_id)
                    .where(
                        or_(
                            AppTagMap.ended_at.is_(None),
                            AppTagMap.ended_at > func.now(),
                        )
                    )
                )
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
            await db.session.commit()

        return await db.session.get(App, app_id)

    # Generate a 20 character alphanumeric ID similar to Okta IDs for users and groups
    def __generate_id(self) -> str:
        return "".join(random.choices(string.ascii_letters, k=20))
