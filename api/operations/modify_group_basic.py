from flask import current_app
from sqlalchemy import func
from sqlalchemy.orm import with_polymorphic

from api.extensions import db
from api.models import AppGroup, OktaGroup, RoleGroup
from api.plugins.app_group_lifecycle import get_app_group_lifecycle_hook, get_app_group_lifecycle_plugin_to_invoke
from api.services import okta


class ModifyGroupBasic:
    """Update a group's name and/or description, sync to Okta, and fire lifecycle hooks."""

    def __init__(self, *, group: OktaGroup, name: str, description: str):
        self.group = group
        self.name = name
        self.description = description or ""

    def execute(self) -> OktaGroup:
        old_name = self.group.name
        old_description = self.group.description or ""

        # Do not allow non-deleted groups with the same name (case-insensitive)
        if old_name.lower() != self.name.lower():
            existing_group = (
                db.session.query(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
                .filter(func.lower(OktaGroup.name) == func.lower(self.name))
                .filter(OktaGroup.deleted_at.is_(None))
                .first()
            )
            if existing_group is not None:
                raise ValueError("Group already exists with the same name")

        self.group.name = self.name
        self.group.description = self.description

        okta.update_group(self.group.id, self.group.name, self.group.description)
        db.session.commit()

        # Fire group_updated hook if name or description changed
        if old_name != self.group.name or old_description != self.group.description:
            plugin_id = get_app_group_lifecycle_plugin_to_invoke(self.group)
            if plugin_id is not None:
                try:
                    hook = get_app_group_lifecycle_hook()
                    hook.group_updated(
                        session=db.session,
                        group=self.group,
                        old_name=old_name,
                        old_description=old_description,
                        plugin_id=plugin_id,
                    )
                    db.session.commit()
                except Exception:
                    current_app.logger.exception(
                        f"Failed to invoke group_updated hook for group {self.group.id}"
                        f" with plugin '{plugin_id}'"
                    )
                    db.session.rollback()

        return self.group
