import logging

from api.context import get_request_context
from sqlalchemy import func, select
from sqlalchemy.orm import with_polymorphic

from api.extensions import db
from api.models import App, AppGroup, OktaGroup, OktaUser, RoleGroup
from api.plugins.app_group_lifecycle import get_app_group_lifecycle_hook, get_app_group_lifecycle_plugin_to_invoke
from api.services import okta
from api.schemas import AuditLogSchema, EventType


class ModifyGroupDetails:
    """Update a group's name and/or description, sync to Okta, and fire lifecycle hooks."""

    def __init__(
        self,
        *,
        group: OktaGroup,
        name: str | None = None,
        description: str | None = None,
        current_user_id: str | None = None,
        validate_app_group_prefix: bool = True,
    ):
        self.group = group
        self.name = name
        self.description = description
        self.current_user_id = current_user_id
        # Renaming an app group must keep the "App-{app name}-" prefix, except
        # when the rename is part of a group type conversion: converting away
        # from an app group legitimately drops the prefix (ModifyGroupType
        # requires it gone before it will convert).
        self.validate_app_group_prefix = validate_app_group_prefix

    async def execute(self) -> OktaGroup:
        old_name = self.group.name
        old_description = self.group.description or ""

        # Do not allow non-deleted groups with the same name (case-insensitive)
        if self.name is not None and old_name.lower() != self.name.lower():
            existing_group = (
                await db.session.scalars(
                    select(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
                    .where(func.lower(OktaGroup.name) == func.lower(self.name))
                    .where(OktaGroup.deleted_at.is_(None))
                )
            ).first()
            if existing_group is not None:
                raise ValueError("Group already exists with the same name")

        # Enforce the "App-{app name}-" prefix on app group renames. Unchanged
        # names are tolerated so description-only edits of legacy non-conforming
        # groups keep working.
        if (
            self.validate_app_group_prefix
            and self.name is not None
            and self.name != old_name
            and type(self.group) is AppGroup
        ):
            app = (
                await db.session.scalars(select(App).where(App.id == self.group.app_id).where(App.deleted_at.is_(None)))
            ).first()
            if app is None:
                raise ValueError("App for AppGroup does not exist")
            app_group_name_prefix = (
                f"{AppGroup.APP_GROUP_NAME_PREFIX}{app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
            )
            if not self.name.startswith(app_group_name_prefix):
                raise ValueError(
                    'App Group name "{}" should be prefixed with App name. For example: "{}"'.format(
                        self.name, app_group_name_prefix
                    )
                )

        if self.name is not None:
            self.group.name = self.name
        if self.description is not None:
            self.group.description = self.description or ""

        if self.group.deleted_at is None:
            await okta.update_group(self.group.id, self.group.name, self.group.description)
        await db.session.commit()

        # Fire group_updated hook if name or description changed
        if old_name != self.group.name or old_description != self.group.description:
            plugin_id = get_app_group_lifecycle_plugin_to_invoke(self.group)
            if plugin_id is not None:
                try:
                    hook = get_app_group_lifecycle_hook()
                    # sync plugin hook: session-bound, runs on the greenlet bridge
                    await db.session.run_sync(
                        lambda s: hook.group_updated(
                            session=s,
                            group=self.group,
                            old_name=old_name,
                            old_description=old_description,
                            plugin_id=plugin_id,
                        )
                    )
                    await db.session.commit()
                except Exception:
                    logging.getLogger("api").exception(
                        f"Failed to invoke group_updated hook for group {self.group.id} with plugin '{plugin_id}'"
                    )
                    await db.session.rollback()

        # Audit logging, only if group name changed
        if old_name.lower() != self.group.name.lower():
            _ctx = get_request_context()
            email = (
                getattr(await db.session.get(OktaUser, self.current_user_id), "email", None)
                if self.current_user_id is not None
                else None
            )
            logging.getLogger("access.audit").info(
                AuditLogSchema().dumps(
                    {
                        "event_type": EventType.group_modify_name,
                        "user_agent": _ctx.user_agent if _ctx else None,
                        "ip": _ctx.ip if _ctx else None,
                        "current_user_id": self.current_user_id,
                        "current_user_email": email,
                        "group": self.group,
                        "old_group_name": old_name,
                    }
                )
            )

        return self.group
