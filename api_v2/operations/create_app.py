import logging
import random
import string
from typing import Optional, TypedDict

from fastapi import Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, with_polymorphic

from api_v2.models import App, AppGroup, AppTagMap, OktaGroup, OktaGroupTagMap, OktaUser, RoleGroup, Tag
from api_v2.models.app_group import app_owners_group_description
from api_v2.operations.create_group import CreateGroup, GroupDict
from api_v2.operations.modify_group_type import ModifyGroupType
from api_v2.operations.modify_group_users import ModifyGroupUsers
from api_v2.operations.modify_role_groups import ModifyRoleGroups
from api_v2.schemas import AuditAppSummary, AuditEventType, AuditLogRead

logger = logging.getLogger(__name__)


class AppDict(TypedDict):
    name: str
    description: str


class CreateApp:
    def __init__(
        self,
        db: Session,
        *,
        app: App | AppDict,
        tags: list[str] = [],
        owner_id: Optional[str] = None,
        owner_role_ids: Optional[list[str]] = None,
        additional_app_groups: Optional[list[AppGroup] | list[GroupDict]] = None,
        current_user_id: Optional[str] = None,
        request: Optional[Request] = None,
    ):
        self.db = db
        self.request = request

        id = self._generate_id()
        if isinstance(app, dict):
            self.app = App(id=id, name=app["name"], description=app["description"])
        else:
            app.id = id
            self.app = app

        self.tag_ids = [
            tag.id for tag in self.db.query(Tag).filter(Tag.deleted_at.is_(None)).filter(Tag.id.in_(tags)).all()
        ]

        self.owner_id = getattr(
            self.db.query(OktaUser).filter(OktaUser.deleted_at.is_(None)).filter(OktaUser.id == owner_id).first(),
            "id",
            None,
        )
        self.owner_role_ids = None
        if owner_role_ids is not None:
            self.owner_roles = (
                self.db.query(RoleGroup)
                .filter(RoleGroup.id.in_(owner_role_ids))
                .filter(RoleGroup.deleted_at.is_(None))
                .all()
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
            self.db.query(OktaUser)
            .filter(OktaUser.deleted_at.is_(None))
            .filter(OktaUser.id == current_user_id)
            .first(),
            "id",
            None,
        )

    def _log_audit_event(self) -> None:
        """Log audit event for app creation."""
        email = None
        if self.current_user_id is not None:
            email = getattr(self.db.get(OktaUser, self.current_user_id), "email", None)

        # Build audit data
        audit_data = {
            "event_type": AuditEventType.APP_CREATE,
            "user_agent": None,
            "ip": None,
            "current_user_id": self.current_user_id,
            "current_user_email": email,
            "app": AuditAppSummary(id=self.app.id, name=self.app.name),
            "owner_id": self.owner_id,
        }

        if self.request:
            audit_data["user_agent"] = self.request.headers.get("User-Agent")
            audit_data["ip"] = (
                self.request.headers.get("X-Forwarded-For")
                or self.request.headers.get("X-Real-IP")
                or self.request.client.host
                if self.request.client
                else None
            )

        audit_log = AuditLogRead(**audit_data)
        logger.info(audit_log.model_dump_json(exclude_none=True))

    def execute(self) -> App:
        # Do not allow non-deleted apps with the same name
        existing_app = (
            self.db.query(App)
            .filter(func.lower(App.name) == func.lower(self.app.name))
            .filter(App.deleted_at.is_(None))
            .first()
        )
        if existing_app is not None:
            return existing_app

        # Audit logging
        self._log_audit_event()

        self.db.add(self.app)
        self.db.commit()

        app_id = self.app.id

        existing_owner_group = (
            self.db.query(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
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
            owner_app_group = CreateGroup(
                self.db,
                group=owner_app_group,
                current_user_id=self.current_user_id,
                request=self.request,
            ).execute()
        else:
            group_id = existing_owner_group.id
            if type(existing_owner_group) is not AppGroup:
                ModifyGroupType(
                    self.db,
                    group=existing_owner_group,
                    group_changes=AppGroup(app_id=app_id, is_owner=True),
                    current_user_id=self.current_user_id,
                    request=self.request,
                ).execute()
            owner_app_group = self.db.query(AppGroup).filter(AppGroup.id == group_id).first()
            owner_app_group.app_id = app_id
            owner_app_group.is_owner = True
            self.db.commit()

        if self.owner_id is not None:
            # Add the app owner to the app owner group as members and owners
            ModifyGroupUsers(
                self.db,
                group=owner_app_group,
                current_user_id=self.current_user_id,
                members_to_add=[self.owner_id],
                owners_to_add=[self.owner_id],
                request=self.request,
            ).execute()

        if self.owner_role_ids is not None:
            for role_id in self.owner_role_ids:
                ModifyRoleGroups(
                    self.db,
                    role_group=role_id,
                    current_user_id=self.current_user_id,
                    groups_to_add=[owner_app_group.id],
                    owner_groups_to_add=[owner_app_group.id],
                    request=self.request,
                ).execute()

        # Find other app groups with the same app name prefix and update them
        other_existing_app_groups = (
            self.db.query(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
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
                self.db,
                group=existing_app_group_id,
                group_changes=AppGroup(app_id=app_id, is_owner=False),
                current_user_id=self.current_user_id,
                request=self.request,
            ).execute()

        # Create any additional app groups for the app
        if self.additional_app_groups is not None:
            for app_group in self.additional_app_groups:
                app_group.app_id = app_id

                existing_group = (
                    self.db.query(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
                    .filter(func.lower(OktaGroup.name) == func.lower(app_group.name))
                    .filter(OktaGroup.deleted_at.is_(None))
                    .first()
                )

                if existing_group is None:
                    CreateGroup(
                        self.db,
                        group=app_group,
                        current_user_id=self.current_user_id,
                        request=self.request,
                    ).execute()
                else:
                    group_id = existing_group.id
                    if type(existing_group) is not AppGroup:
                        ModifyGroupType(
                            self.db,
                            group=existing_group,
                            group_changes=AppGroup(app_id=app_id, is_owner=False),
                            current_user_id=self.current_user_id,
                            request=self.request,
                        ).execute()
                    app_group = self.db.query(AppGroup).filter(AppGroup.id == group_id).first()
                    app_group.app_id = app_id
                    app_group.is_owner = False
                    self.db.commit()

        if len(self.tag_ids) > 0:
            all_app_groups = (
                self.db.query(AppGroup).filter(AppGroup.app_id == app_id).filter(AppGroup.deleted_at.is_(None)).all()
            )

            for tag_id in self.tag_ids:
                self.db.add(
                    AppTagMap(
                        tag_id=tag_id,
                        app_id=app_id,
                    )
                )
            self.db.commit()

            new_app_tag_maps = (
                self.db.query(AppTagMap)
                .filter(AppTagMap.app_id == app_id)
                .filter(
                    or_(
                        AppTagMap.ended_at.is_(None),
                        AppTagMap.ended_at > func.now(),
                    )
                )
                .all()
            )

            for app_tag_map in new_app_tag_maps:
                for app_group in all_app_groups:
                    self.db.add(
                        OktaGroupTagMap(
                            tag_id=app_tag_map.tag_id,
                            group_id=app_group.id,
                            app_tag_map_id=app_tag_map.id,
                        )
                    )
            self.db.commit()

        return self.db.get(App, app_id)

    # Generate a 20 character alphanumeric ID similar to Okta IDs for users and groups
    def _generate_id(self) -> str:
        return "".join(random.choices(string.ascii_letters, k=20))
