import random
import string
from typing import Optional, TypedDict

from flask import current_app, has_request_context, request
from sqlalchemy import func
from sqlalchemy.orm import with_polymorphic

from api.extensions import db
from api.models import App, AppGroup, AppTagMap, OktaGroup, OktaGroupTagMap, OktaUser, RoleGroup, Tag
from api.models.app_group import app_owners_group_description
from api.operations.create_group import CreateGroup, GroupDict
from api.operations.modify_group_type import ModifyGroupType
from api.operations.modify_group_users import ModifyGroupUsers
from api.operations.modify_role_groups import ModifyRoleGroups
from api.views.schemas import AuditLogSchema, EventType


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

        self.tag_ids = [tag.id for tag in Tag.query.filter(Tag.deleted_at.is_(None)).filter(Tag.id.in_(tags)).all()]

        self.owner_id = getattr(
            OktaUser.query.filter(OktaUser.deleted_at.is_(None)).filter(OktaUser.id == owner_id).first(), "id", None
        )
        self.owner_role_ids = None
        if owner_role_ids is not None:
            self.owner_roles = (
                RoleGroup.query.filter(RoleGroup.id.in_(owner_role_ids)).filter(RoleGroup.deleted_at.is_(None)).all()
            )
            self.owner_role_ids = [role.id for role in self.owner_roles]

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
        self.current_user_id = getattr(
            OktaUser.query.filter(OktaUser.deleted_at.is_(None)).filter(OktaUser.id == current_user_id).first(),
            "id",
            None,
        )

    def execute(self) -> App:
        # Do not allow non-deleted apps with the same name
        existing_app = (
            App.query.filter(func.lower(App.name) == func.lower(self.app.name)).filter(App.deleted_at.is_(None)).first()
        )
        if existing_app is not None:
            return existing_app

        # Audit logging
        email = None
        if self.current_user_id is not None:
            email = getattr(db.session.get(OktaUser, self.current_user_id), "email", None)

        context = has_request_context()

        current_app.logger.info(
            AuditLogSchema().dumps(
                {
                    "event_type": EventType.app_create,
                    "user_agent": request.headers.get("User-Agent") if context else None,
                    "ip": request.headers.get("X-Forwarded-For", request.headers.get("X-Real-IP", request.remote_addr))
                    if context
                    else None,
                    "current_user_id": self.current_user_id,
                    "current_user_email": email,
                    "app": self.app,
                    "owner_id": self.owner_id,
                }
            )
        )

        db.session.add(self.app)
        db.session.commit()

        app_id = self.app.id

        existing_owner_group = (
            db.session.query(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
            .filter(func.lower(OktaGroup.name) == func.lower(self.owner_group_name))
            .filter(OktaGroup.deleted_at.is_(None))
            .first()
        )

        if existing_owner_group is None:
            owner_app_group = AppGroup(
                app_id=app_id,
                is_owner=True,
                name=self.owner_group_name,
                description=app_owners_group_description(self.app.name),
            )
            owner_app_group = CreateGroup(group=owner_app_group, current_user_id=self.current_user_id).execute()
        else:
            group_id = existing_owner_group.id
            if type(existing_owner_group) is not AppGroup:
                ModifyGroupType(
                    group=existing_owner_group,
                    group_changes=AppGroup(app_id=app_id, is_owner=True),
                    current_user_id=self.current_user_id,
                ).execute()
            owner_app_group = AppGroup.query.filter(AppGroup.id == group_id).first()
            owner_app_group.app_id = app_id
            owner_app_group.is_owner = True
            db.session.commit()

        if self.owner_id is not None:
            # Add the app owner to the app owner group as members and owners
            ModifyGroupUsers(
                group=owner_app_group,
                current_user_id=self.current_user_id,
                members_to_add=[self.owner_id],
                owners_to_add=[self.owner_id],
            ).execute()

        if self.owner_role_ids is not None:
            for role_id in self.owner_role_ids:
                ModifyRoleGroups(
                    role_group=role_id,
                    current_user_id=self.current_user_id,
                    groups_to_add=[owner_app_group.id],
                    owner_groups_to_add=[owner_app_group.id],
                ).execute()

        # Find other app groups with the same app name prefix and update them
        other_existing_app_groups = (
            db.session.query(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
            .filter(OktaGroup.name.ilike(f"{self.app_group_prefix}%"))
            .filter(func.lower(OktaGroup.name) != func.lower(self.owner_group_name))
            .filter(OktaGroup.deleted_at.is_(None))
            .all()
        )
        existing_app_group_ids_to_update = []
        for existing_app_group in other_existing_app_groups:
            if type(existing_app_group) is not AppGroup:
                existing_app_group_ids_to_update.append(existing_app_group.id)

        for existing_app_group_id in existing_app_group_ids_to_update:
            ModifyGroupType(
                group=existing_app_group_id,
                group_changes=AppGroup(app_id=app_id, is_owner=False),
                current_user_id=self.current_user_id,
            ).execute()

        # Create any additional app groups for the app
        if self.additional_app_groups is not None:
            for app_group in self.additional_app_groups:
                app_group.app_id = app_id

                existing_group = (
                    db.session.query(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
                    .filter(func.lower(OktaGroup.name) == func.lower(app_group.name))
                    .filter(OktaGroup.deleted_at.is_(None))
                    .first()
                )

                if existing_group is None:
                    CreateGroup(group=app_group, current_user_id=self.current_user_id).execute()
                else:
                    group_id = existing_group.id
                    if type(existing_owner_group) is not AppGroup:
                        ModifyGroupType(
                            group=existing_owner_group,
                            group_changes=AppGroup(app_id=app_id, is_owner=False),
                            current_user_id=self.current_user_id,
                        ).execute()
                    app_group = AppGroup.query.filter(AppGroup.id == group_id).first()
                    app_group.app_id = app_id
                    app_group.is_owner = False
                    db.session.commit()

        if len(self.tag_ids) > 0:
            all_app_groups = (
                AppGroup.query.filter(AppGroup.app_id == app_id).filter(AppGroup.deleted_at.is_(None)).all()
            )

            for tag_id in self.tag_ids:
                db.session.add(
                    AppTagMap(
                        tag_id=tag_id,
                        app_id=app_id,
                    )
                )
            db.session.commit()

            new_app_tag_maps = (
                AppTagMap.query.filter(AppTagMap.app_id == app_id)
                .filter(
                    db.or_(
                        AppTagMap.ended_at.is_(None),
                        AppTagMap.ended_at > db.func.now(),
                    )
                )
                .all()
            )

            for app_tag_map in new_app_tag_maps:
                for app_group in all_app_groups:
                    db.session.add(
                        OktaGroupTagMap(
                            tag_id=app_tag_map.tag_id,
                            group_id=app_group.id,
                            app_tag_map_id=app_tag_map.id,
                        )
                    )
            db.session.commit()

        return db.session.get(App, app_id)

    # Generate a 20 character alphanumeric ID similar to Okta IDs for users and groups
    def __generate_id(self) -> str:
        return "".join(random.choices(string.ascii_letters, k=20))
