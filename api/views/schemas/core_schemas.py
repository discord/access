from typing import AbstractSet, Any, Dict, List, Optional, Sequence, cast

from flask import current_app
from marshmallow import Schema, ValidationError, fields, utils, validate, validates_schema
from marshmallow.schema import SchemaMeta, SchemaOpts
from marshmallow_sqlalchemy import SQLAlchemyAutoSchema, auto_field
from sqlalchemy.orm import Session
from api.access_config import get_access_config
from api.extensions import db
from api.models import (
    AccessRequest,
    App,
    AppGroup,
    AppTagMap,
    OktaGroup,
    OktaGroupTagMap,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    RoleGroupMap,
    RoleRequest,
    Tag,
)

access_config = get_access_config()


# See https://stackoverflow.com/a/58646612
class OktaUserGroupMemberSchema(SQLAlchemyAutoSchema):
    group = fields.Nested(lambda: PolymorphicGroupSchema)
    active_group = fields.Nested(lambda: PolymorphicGroupSchema)
    user = fields.Nested(lambda: OktaUserSchema)
    active_user = fields.Nested(lambda: OktaUserSchema)
    role_group_mapping = fields.Nested(lambda: RoleGroupMapSchema)
    active_role_group_mapping = fields.Nested(lambda: RoleGroupMapSchema)
    access_request = fields.Nested(lambda: AccessRequestSchema)
    created_actor = fields.Nested(lambda: OktaUserSchema)
    ended_actor = fields.Nested(lambda: OktaUserSchema)

    class Meta:
        fields = (
            "id",
            "user_id",
            "group_id",
            "role_group_map_id",
            "group",
            "active_group",
            "user",
            "active_user",
            "role_group_mapping",
            "active_role_group_mapping",
            "access_request",
            "is_owner",
            "created_at",
            "updated_at",
            "ended_at",
            "created_reason",
            "created_actor_id",
            "ended_actor_id",
            "created_actor",
            "ended_actor",
            "should_expire",
        )
        dump_only = (
            "id",
            "user_id",
            "group_id",
            "role_group_map_id",
            "group",
            "active_group",
            "user",
            "active_user",
            "role_group_mapping",
            "active_role_group_mapping",
            "access_request",
            "is_owner",
            "created_at",
            "updated_at",
            "ended_at",
            "created_reason",
            "created_actor_id",
            "ended_actor_id",
            "created_actor",
            "ended_actor",
            "should_expire",
        )
        model = OktaUserGroupMember
        sqla_session = db.session
        load_instance = True
        include_relationships = True


class OktaUserSchema(SQLAlchemyAutoSchema):
    profile = fields.Dict()
    manager = fields.Nested(
        lambda: OktaUserSchema,
        exclude=(
            "all_group_memberships_and_ownerships",
            "active_group_memberships_and_ownerships",
            "active_group_ownerships",
            "active_group_memberships",
            "manager",
        ),
    )

    def get_attribute(self, obj: OktaUser, key: str, default: Any) -> Any:
        if key == "profile":
            attrs_to_display = current_app.config["USER_DISPLAY_CUSTOM_ATTRIBUTES"].split(",")

            if len(attrs_to_display) == 1 and attrs_to_display[0] == "":
                return {}

            profile = utils.get_value(obj, key, default=default)
            return {key: profile.get(key, None) for key in attrs_to_display}
        return utils.get_value(obj, key, default=default)

    all_group_memberships_and_ownerships = fields.Nested(
        lambda: OktaUserGroupMemberSchema(
            only=(
                "is_owner",
                "created_at",
                "ended_at",
                "group.id",
                "group.type",
                "group.name",
                "group.is_owner",
                "group.is_managed",
                "group.deleted_at",
                "group.app.id",
                "group.app.name",
                "group.app.deleted_at",
                "role_group_mapping.created_at",
                "role_group_mapping.ended_at",
                "role_group_mapping.role_group.id",
                "role_group_mapping.role_group.type",
                "role_group_mapping.role_group.name",
                "role_group_mapping.role_group.deleted_at",
            ),
            many=True,
        ),
    )
    active_group_memberships_and_ownerships = fields.Nested(
        lambda: OktaUserGroupMemberSchema(
            only=(
                "is_owner",
                "created_at",
                "ended_at",
                "active_group.id",
                "active_group.type",
                "active_group.name",
                "active_group.is_owner",
                "active_group.is_managed",
                "active_group.app.id",
                "active_group.app.name",
                "active_role_group_mapping.created_at",
                "active_role_group_mapping.ended_at",
                "active_role_group_mapping.active_role_group.id",
                "active_role_group_mapping.active_role_group.type",
                "active_role_group_mapping.active_role_group.name",
            ),
            many=True,
        ),
    )
    active_group_memberships = fields.Nested(
        lambda: OktaUserGroupMemberSchema(
            only=(
                "created_at",
                "ended_at",
                "active_group.id",
                "active_group.type",
                "active_group.name",
                "active_group.is_owner",
                "active_group.is_managed",
                "active_group.app.id",
                "active_group.app.name",
                "active_role_group_mapping.created_at",
                "active_role_group_mapping.ended_at",
                "active_role_group_mapping.active_role_group.id",
                "active_role_group_mapping.active_role_group.type",
                "active_role_group_mapping.active_role_group.name",
            ),
            many=True,
        ),
    )
    active_group_ownerships = fields.Nested(
        lambda: OktaUserGroupMemberSchema(
            only=(
                "created_at",
                "ended_at",
                "active_group.id",
                "active_group.type",
                "active_group.name",
                "active_group.is_owner",
                "active_group.is_managed",
                "active_group.app.id",
                "active_group.app.name",
                "active_group.active_group_tags.active_tag",
                "active_role_group_mapping.created_at",
                "active_role_group_mapping.ended_at",
                "active_role_group_mapping.active_role_group.id",
                "active_role_group_mapping.active_role_group.type",
                "active_role_group_mapping.active_role_group.name",
            ),
            many=True,
        ),
    )

    class Meta:
        fields = (
            "id",
            "created_at",
            "updated_at",
            "deleted_at",
            "email",
            "first_name",
            "last_name",
            "display_name",
            "profile",
            "manager",
            "all_group_memberships_and_ownerships",
            "active_group_memberships_and_ownerships",
            "active_group_memberships",
            "active_group_ownerships",
        )
        dump_only = (
            "id",
            "created_at",
            "updated_at",
            "deleted_at",
            "email",
            "first_name",
            "last_name",
            "display_name",
            "profile",
            "manager",
            "all_group_memberships_and_ownerships",
            "active_group_memberships_and_ownerships",
            "active_group_memberships",
            "active_group_ownerships",
        )
        model = OktaUser
        sqla_session = db.session
        load_instance = True
        include_relationships = True


class OktaGroupSchema(SQLAlchemyAutoSchema):
    name = auto_field(
        required=True,
        validate=validate.And(
            validate.Length(min=1, max=255),
            validate.Regexp(
                f"^{access_config.name_pattern}$",
                error=f"Group {access_config.name_validation_error} Regex to match: /{{regex}}/",
            ),
        ),
    )
    description = auto_field(load_default="", validate=validate.Length(max=1024))

    externally_managed_data = fields.Dict()

    tags_to_add = fields.List(fields.String(validate=validate.Length(equal=20)), load_only=True)
    tags_to_remove = fields.List(fields.String(validate=validate.Length(equal=20)), load_only=True)

    all_user_memberships_and_ownerships = fields.Nested(
        lambda: OktaUserGroupMemberSchema(
            only=(
                "is_owner",
                "created_at",
                "ended_at",
                "user.id",
                "user.email",
                "user.first_name",
                "user.last_name",
                "user.display_name",
                "user.deleted_at" "role_group_mapping.created_at",
                "role_group_mapping.ended_at",
                "role_group_mapping.role_group.id",
                "role_group_mapping.role_group.type",
                "role_group_mapping.role_group.name",
                "role_group_mapping.role_group.deleted_at",
            ),
            many=True,
        ),
    )
    active_user_memberships_and_ownerships = fields.Nested(
        lambda: OktaUserGroupMemberSchema(
            only=(
                "is_owner",
                "created_at",
                "ended_at",
                "active_user.id",
                "active_user.email",
                "active_user.first_name",
                "active_user.last_name",
                "active_user.display_name",
                "active_role_group_mapping.created_at",
                "active_role_group_mapping.ended_at",
                "active_role_group_mapping.active_role_group.id",
                "active_role_group_mapping.active_role_group.type",
                "active_role_group_mapping.active_role_group.name",
            ),
            many=True,
        ),
    )
    active_user_memberships = fields.Nested(
        lambda: OktaUserGroupMemberSchema(
            only=(
                "created_at",
                "ended_at",
                "active_user.id",
                "active_user.email",
                "active_user.first_name",
                "active_user.last_name",
                "active_user.display_name",
                "active_role_group_mapping.created_at",
                "active_role_group_mapping.ended_at",
                "active_role_group_mapping.active_role_group.id",
                "active_role_group_mapping.active_role_group.type",
                "active_role_group_mapping.active_role_group.name",
            ),
            many=True,
        ),
    )
    active_user_ownerships = fields.Nested(
        lambda: OktaUserGroupMemberSchema(
            only=(
                "created_at",
                "ended_at",
                "active_user.id",
                "active_user.email",
                "active_user.first_name",
                "active_user.last_name",
                "active_user.display_name",
                "active_role_group_mapping.created_at",
                "active_role_group_mapping.ended_at",
                "active_role_group_mapping.active_role_group.id",
                "active_role_group_mapping.active_role_group.type",
                "active_role_group_mapping.active_role_group.name",
            ),
            many=True,
        ),
    )
    active_non_role_user_memberships = fields.Nested(
        lambda: OktaUserGroupMemberSchema(
            only=(
                "created_at",
                "ended_at",
                "active_user.id",
                "active_user.email",
                "active_user.first_name",
                "active_user.last_name",
                "active_user.display_name",
            ),
            many=True,
        ),
    )
    active_non_role_user_ownerships = fields.Nested(
        lambda: OktaUserGroupMemberSchema(
            only=(
                "created_at",
                "ended_at",
                "active_user.id",
                "active_user.email",
                "active_user.first_name",
                "active_user.last_name",
                "active_user.display_name",
            ),
            many=True,
        ),
    )

    all_role_mappings = fields.Nested(
        lambda: RoleGroupMapSchema(
            only=(
                "is_owner",
                "created_at",
                "ended_at",
                "role_group.id",
                "role_group.type",
                "role_group.name",
                "role_group.is_managed",
                "role_group.deleted_at",
            ),
            many=True,
        ),
    )
    active_role_mappings = fields.Nested(
        lambda: RoleGroupMapSchema(
            only=(
                "is_owner",
                "created_at",
                "ended_at",
                "active_role_group.id",
                "active_role_group.type",
                "active_role_group.name",
                "active_role_group.is_managed",
            ),
            many=True,
        ),
    )
    active_role_member_mappings = fields.Nested(
        lambda: RoleGroupMapSchema(
            only=(
                "created_at",
                "ended_at",
                "active_role_group.id",
                "active_role_group.type",
                "active_role_group.name",
                "active_role_group.is_managed",
            ),
            many=True,
        ),
    )
    active_role_owner_mappings = fields.Nested(
        lambda: RoleGroupMapSchema(
            only=(
                "created_at",
                "ended_at",
                "active_role_group.id",
                "active_role_group.type",
                "active_role_group.name",
                "active_role_group.is_managed",
            ),
            many=True,
        ),
    )

    all_group_tags = fields.Nested(
        lambda: OktaGroupTagMapSchema(
            only=(
                "created_at",
                "ended_at",
                "tag.id",
                "tag.name",
                "tag.constraints",
                "tag.deleted_at",
            ),
            many=True,
        ),
    )

    active_group_tags = fields.Nested(
        lambda: OktaGroupTagMapSchema(
            only=(
                "created_at",
                "ended_at",
                "active_tag.id",
                "active_tag.name",
                "active_tag.constraints",
                "active_tag.enabled",
            ),
            many=True,
        ),
    )

    class Meta:
        model = OktaGroup
        sqla_session = db.session
        load_instance = True
        include_relationships = True
        fields = (
            "id",
            "type",
            "name",
            "description",
            "is_managed",
            "externally_managed_data",
            "tags_to_add",
            "tags_to_remove",
            "created_at",
            "updated_at",
            "deleted_at",
            "all_user_memberships_and_ownerships",
            "active_user_memberships_and_ownerships",
            "active_user_memberships",
            "active_user_ownerships",
            "active_non_role_user_memberships",
            "active_non_role_user_ownerships",
            "all_role_mappings",
            "active_role_mappings",
            "active_role_member_mappings",
            "active_role_owner_mappings",
            "all_group_tags",
            "active_group_tags",
        )
        dump_only = (
            "id",
            "is_managed",
            "externally_managed_data",
            "created_at",
            "updated_at",
            "deleted_at",
            "all_user_memberships_and_ownerships",
            "active_user_memberships_and_ownerships",
            "active_user_memberships",
            "active_user_ownerships",
            "active_non_role_user_memberships",
            "active_non_role_user_ownerships",
            "all_role_mappings",
            "active_role_mappings",
            "active_role_member_mappings",
            "active_role_owner_mappings",
            "all_group_tags",
            "active_group_tags",
        )
        load_only = (
            "tags_to_add",
            "tags_to_remove",
        )


class RoleGroupMapSchema(SQLAlchemyAutoSchema):
    role_group = fields.Nested(lambda: RoleGroupSchema)
    active_role_group = fields.Nested(lambda: RoleGroupSchema)

    group = fields.Nested(lambda: PolymorphicGroupSchema)
    active_group = fields.Nested(lambda: PolymorphicGroupSchema)

    all_group_memberships_and_ownerships = fields.Nested(
        lambda: OktaUserGroupMemberSchema(
            only=(
                "is_owner",
                "created_at",
                "ended_at",
                "user.id",
                "user.email",
                "user.first_name",
                "user.last_name",
                "user.display_name",
                "user.deleted_at",
                "group.id",
                "group.type",
                "group.name",
                "group.is_owner",
                "group.is_managed",
                "group.deleted_at",
                "group.app.id",
                "group.app.name",
                "group.app.deleted_at",
            ),
            many=True,
        ),
    )
    active_group_memberships_and_ownerships = fields.Nested(
        lambda: OktaUserGroupMemberSchema(
            only=(
                "is_owner",
                "created_at",
                "ended_at",
                "active_user.id",
                "active_user.email",
                "active_user.first_name",
                "active_user.last_name",
                "active_user.display_name",
                "active_group.id",
                "active_group.type",
                "active_group.name",
                "active_group.is_owner",
                "active_group.is_managed",
                "active_group.app.id",
                "active_group.app.name",
            ),
            many=True,
        ),
    )

    created_actor = fields.Nested(lambda: OktaUserSchema)
    ended_actor = fields.Nested(lambda: OktaUserSchema)

    class Meta:
        fields = (
            "id",
            "group_id",
            "role_group_id",
            "is_owner",
            "created_at",
            "updated_at",
            "ended_at",
            "group",
            "active_group",
            "role_group",
            "active_role_group",
            "all_group_memberships_and_ownerships",
            "active_group_memberships_and_ownerships",
            "created_actor_id",
            "ended_actor_id",
            "created_actor",
            "ended_actor",
            "created_reason",
            "should_expire",
        )
        dump_only = (
            "id",
            "group_id",
            "role_group_id",
            "is_owner",
            "created_at",
            "updated_at",
            "ended_at",
            "group",
            "active_group",
            "role_group",
            "active_role_group",
            "all_group_memberships_and_ownerships",
            "active_group_memberships_and_ownerships",
            "created_actor_id",
            "ended_actor_id",
            "created_actor",
            "ended_actor",
            "created_reason",
            "should_expire",
        )
        model = RoleGroupMap
        sqla_session = db.session
        load_instance = True
        include_relationships = True


class RoleGroupSchema(SQLAlchemyAutoSchema):
    name = auto_field(
        required=True,
        validate=validate.And(
            validate.Length(min=1, max=255),
            validate.Regexp(
                f"^{RoleGroup.ROLE_GROUP_NAME_PREFIX}{access_config.name_pattern}$",
                error=f"Role {access_config.name_validation_error} Regex to match: /{{regex}}/",
            ),
        ),
    )
    description = auto_field(load_default="", validate=validate.Length(max=1024))

    externally_managed_data = fields.Dict()

    tags_to_add = fields.List(fields.String(validate=validate.Length(equal=20)), load_only=True)
    tags_to_remove = fields.List(fields.String(validate=validate.Length(equal=20)), load_only=True)

    all_user_memberships_and_ownerships = fields.Nested(
        lambda: OktaUserGroupMemberSchema(
            only=(
                "is_owner",
                "created_at",
                "ended_at",
                "user.id",
                "user.email",
                "user.first_name",
                "user.last_name",
                "user.display_name",
                "user.deleted_at",
            ),
            many=True,
        ),
    )
    active_user_memberships_and_ownerships = fields.Nested(
        lambda: OktaUserGroupMemberSchema(
            only=(
                "is_owner",
                "created_at",
                "ended_at",
                "active_user.id",
                "active_user.email",
                "active_user.first_name",
                "active_user.last_name",
                "active_user.display_name",
            ),
            many=True,
        ),
    )
    active_user_memberships = fields.Nested(
        lambda: OktaUserGroupMemberSchema(
            only=(
                "created_at",
                "ended_at",
                "active_user.id",
                "active_user.email",
                "active_user.first_name",
                "active_user.last_name",
                "active_user.display_name",
            ),
            many=True,
        ),
    )
    active_user_ownerships = fields.Nested(
        lambda: OktaUserGroupMemberSchema(
            only=(
                "created_at",
                "ended_at",
                "active_user.id",
                "active_user.email",
                "active_user.first_name",
                "active_user.last_name",
                "active_user.display_name",
            ),
            many=True,
        ),
    )

    all_role_associated_group_mappings = fields.Nested(
        lambda: RoleGroupMapSchema(
            only=(
                "is_owner",
                "created_at",
                "ended_at",
                "group.id",
                "group.type",
                "group.name",
                "group.deleted_at",
                "group.app.id",
                "group.app.name",
                "group.app.deleted_at",
            ),
            many=True,
        ),
    )
    active_role_associated_group_mappings = fields.Nested(
        lambda: RoleGroupMapSchema(
            only=(
                "is_owner",
                "created_at",
                "ended_at",
                "active_group.id",
                "active_group.type",
                "active_group.name",
                "active_group.app.id",
                "active_group.app.name",
            ),
            many=True,
        ),
    )
    active_role_associated_group_member_mappings = fields.Nested(
        lambda: RoleGroupMapSchema(
            only=(
                "created_at",
                "ended_at",
                "active_group.id",
                "active_group.type",
                "active_group.name",
                "active_group.active_group_tags",
                "active_group.app.id",
                "active_group.app.name",
            ),
            many=True,
        ),
    )
    active_role_associated_group_owner_mappings = fields.Nested(
        lambda: RoleGroupMapSchema(
            only=(
                "created_at",
                "ended_at",
                "active_group.id",
                "active_group.type",
                "active_group.name",
                "active_group.active_group_tags",
                "active_group.app.id",
                "active_group.app.name",
            ),
            many=True,
        ),
    )
    all_group_tags = fields.Nested(
        lambda: OktaGroupTagMapSchema(
            only=(
                "created_at",
                "ended_at",
                "tag.id",
                "tag.name",
                "tag.constraints",
                "tag.deleted_at",
            ),
            many=True,
        ),
    )
    active_group_tags = fields.Nested(
        lambda: OktaGroupTagMapSchema(
            only=(
                "created_at",
                "ended_at",
                "active_tag.id",
                "active_tag.name",
                "active_tag.constraints",
                "active_tag.enabled",
            ),
            many=True,
        ),
    )

    class Meta:
        model = RoleGroup
        sqla_session = db.session
        load_instance = True
        include_relationships = True
        fields = (
            "id",
            "type",
            "name",
            "description",
            "is_managed",
            "externally_managed_data",
            "tags_to_add",
            "tags_to_remove",
            "created_at",
            "updated_at",
            "deleted_at",
            "all_user_memberships_and_ownerships",
            "active_user_memberships_and_ownerships",
            "active_user_memberships",
            "active_user_ownerships",
            "all_role_associated_group_mappings",
            "active_role_associated_group_mappings",
            "active_role_associated_group_member_mappings",
            "active_role_associated_group_owner_mappings",
            "all_group_tags",
            "active_group_tags",
        )
        dump_only = (
            "id",
            "is_managed",
            "externally_managed_data",
            "created_at",
            "updated_at",
            "deleted_at",
            "all_user_memberships_and_ownerships",
            "active_user_memberships_and_ownerships",
            "active_user_memberships",
            "active_user_ownerships",
            "all_role_associated_group_mappings",
            "active_role_associated_group_mappings",
            "active_role_associated_group_member_mappings",
            "active_role_associated_group_owner_mappings",
            "all_group_tags",
            "active_group_tags",
        )
        load_only = (
            "tags_to_add",
            "tags_to_remove",
        )


class AppGroupSchema(SQLAlchemyAutoSchema):
    name = auto_field(
        required=True,
        validate=validate.And(
            validate.Length(min=1, max=255),
            validate.Regexp(
                f"^{AppGroup.APP_GROUP_NAME_PREFIX}{access_config.name_pattern}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{access_config.name_pattern}$",
                error=f"Group {access_config.name_validation_error} Regex to match: /{{regex}}/",
            ),
        ),
    )
    description = auto_field(load_default="", validate=validate.Length(max=1024))

    externally_managed_data = fields.Dict()

    @validates_schema
    def validate_app_group(self, data: Dict[str, Any], **kwargs: Any) -> None:
        app = App.query.filter(App.id == data["app_id"]).filter(App.deleted_at.is_(None)).first()
        if app is None:
            raise ValidationError('Specified App with app_id: "{}" does not exist'.format(data["app_id"]))
        # app_groups should have app name prepended always
        app_group_name_prefix = f"{AppGroup.APP_GROUP_NAME_PREFIX}{app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
        if not data["name"].startswith(app_group_name_prefix):
            raise ValidationError(
                'App Group name "{}" should be prefixed with App name. For example: "{}"'.format(
                    data["name"], app_group_name_prefix
                )
            )

    app_id = auto_field(required=True, validate=validate.Length(equal=20))
    app = fields.Nested(lambda: AppSchema(only=("id", "name", "deleted_at")))

    tags_to_add = fields.List(fields.String(validate=validate.Length(equal=20)), load_only=True)
    tags_to_remove = fields.List(fields.String(validate=validate.Length(equal=20)), load_only=True)

    all_user_memberships_and_ownerships = fields.Nested(
        lambda: OktaUserGroupMemberSchema(
            only=(
                "is_owner",
                "created_at",
                "ended_at",
                "user.id",
                "user.email",
                "user.first_name",
                "user.last_name",
                "user.display_name",
                "user.deleted_at",
                "role_group_mapping.created_at",
                "role_group_mapping.ended_at",
                "role_group_mapping.role_group.id",
                "role_group_mapping.role_group.type",
                "role_group_mapping.role_group.name",
                "role_group_mapping.role_group.deleted_at",
            ),
            many=True,
        ),
    )
    active_user_memberships_and_ownerships = fields.Nested(
        lambda: OktaUserGroupMemberSchema(
            only=(
                "is_owner",
                "created_at",
                "ended_at",
                "active_user.id",
                "active_user.email",
                "active_user.first_name",
                "active_user.last_name",
                "active_user.display_name",
                "active_role_group_mapping.created_at",
                "active_role_group_mapping.ended_at",
                "active_role_group_mapping.active_role_group.id",
                "active_role_group_mapping.active_role_group.type",
                "active_role_group_mapping.active_role_group.name",
            ),
            many=True,
        ),
    )
    active_user_memberships = fields.Nested(
        lambda: OktaUserGroupMemberSchema(
            only=(
                "created_at",
                "ended_at",
                "active_user.id",
                "active_user.email",
                "active_user.first_name",
                "active_user.last_name",
                "active_user.display_name",
                "active_role_group_mapping.created_at",
                "active_role_group_mapping.ended_at",
                "active_role_group_mapping.active_role_group.id",
                "active_role_group_mapping.active_role_group.type",
                "active_role_group_mapping.active_role_group.name",
            ),
            many=True,
        ),
    )
    active_user_ownerships = fields.Nested(
        lambda: OktaUserGroupMemberSchema(
            only=(
                "created_at",
                "ended_at",
                "active_user.id",
                "active_user.email",
                "active_user.first_name",
                "active_user.last_name",
                "active_user.display_name",
                "active_role_group_mapping.created_at",
                "active_role_group_mapping.ended_at",
                "active_role_group_mapping.active_role_group.id",
                "active_role_group_mapping.active_role_group.type",
                "active_role_group_mapping.active_role_group.name",
            ),
            many=True,
        ),
    )
    active_non_role_user_memberships = fields.Nested(
        lambda: OktaUserGroupMemberSchema(
            only=(
                "created_at",
                "ended_at",
                "active_user.id",
                "active_user.email",
                "active_user.first_name",
                "active_user.last_name",
                "active_user.display_name",
            ),
            many=True,
        ),
    )
    active_non_role_user_ownerships = fields.Nested(
        lambda: OktaUserGroupMemberSchema(
            only=(
                "created_at",
                "ended_at",
                "active_user.id",
                "active_user.email",
                "active_user.first_name",
                "active_user.last_name",
                "active_user.display_name",
            ),
            many=True,
        ),
    )

    all_role_mappings = fields.Nested(
        lambda: RoleGroupMapSchema(
            only=(
                "is_owner",
                "created_at",
                "ended_at",
                "role_group.id",
                "role_group.type",
                "role_group.name",
                "role_group.is_managed",
                "role_group.deleted_at",
            ),
            many=True,
        ),
    )
    active_role_mappings = fields.Nested(
        lambda: RoleGroupMapSchema(
            only=(
                "is_owner",
                "created_at",
                "ended_at",
                "active_role_group.id",
                "active_role_group.type",
                "active_role_group.name",
                "active_role_group.is_managed",
            ),
            many=True,
        ),
    )
    active_role_member_mappings = fields.Nested(
        lambda: RoleGroupMapSchema(
            only=(
                "created_at",
                "ended_at",
                "active_role_group.id",
                "active_role_group.type",
                "active_role_group.name",
                "active_role_group.is_managed",
            ),
            many=True,
        ),
    )
    active_role_owner_mappings = fields.Nested(
        lambda: RoleGroupMapSchema(
            only=(
                "created_at",
                "ended_at",
                "active_role_group.id",
                "active_role_group.type",
                "active_role_group.name",
                "active_role_group.is_managed",
            ),
            many=True,
        ),
    )
    all_group_tags = fields.Nested(
        lambda: OktaGroupTagMapSchema(
            only=(
                "created_at",
                "ended_at",
                "tag.id",
                "tag.name",
                "tag.constraints",
                "tag.deleted_at",
                "app_tag_mapping.created_at",
                "app_tag_mapping.ended_at",
            ),
            many=True,
        ),
    )
    active_group_tags = fields.Nested(
        lambda: OktaGroupTagMapSchema(
            only=(
                "created_at",
                "ended_at",
                "active_tag.id",
                "active_tag.name",
                "active_tag.constraints",
                "active_app_tag_mapping.created_at",
                "active_app_tag_mapping.ended_at",
                "active_tag.enabled",
            ),
            many=True,
        ),
    )

    class Meta:
        model = AppGroup
        sqla_session = db.session
        load_instance = True
        include_relationships = True
        fields = (
            "id",
            "type",
            "name",
            "description",
            "is_managed",
            "externally_managed_data",
            "is_owner",
            "app",
            "app_id",
            "tags_to_add",
            "tags_to_remove",
            "created_at",
            "updated_at",
            "deleted_at",
            "all_user_memberships_and_ownerships",
            "active_user_memberships_and_ownerships",
            "active_user_memberships",
            "active_user_ownerships",
            "active_non_role_user_memberships",
            "active_non_role_user_ownerships",
            "all_role_mappings",
            "active_role_mappings",
            "active_role_member_mappings",
            "active_role_owner_mappings",
            "all_group_tags",
            "active_group_tags",
        )
        dump_only = (
            "id",
            "is_managed",
            "externally_managed_data",
            "is_owner",
            "app",
            "created_at",
            "updated_at",
            "deleted_at",
            "all_user_memberships_and_ownerships",
            "active_user_memberships_and_ownerships",
            "active_user_memberships",
            "active_user_ownerships",
            "active_non_role_user_memberships",
            "active_non_role_user_ownerships",
            "all_role_mappings",
            "active_role_mappings",
            "active_role_member_mappings",
            "active_role_owner_mappings",
            "all_group_tags",
            "active_group_tags",
        )
        load_only = (
            "tags_to_add",
            "tags_to_remove",
        )


class InitialAppGroupSchema(Schema):
    name = fields.String(
        required=True,
        validate=validate.And(
            validate.Length(min=1, max=255),
            validate.Regexp(
                f"^{AppGroup.APP_GROUP_NAME_PREFIX}{access_config.name_pattern}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{access_config.name_pattern}$",
                error=f"Group {access_config.name_validation_error} Regex to match: /{{regex}}/",
            ),
        ),
    )
    description = fields.String(load_default="", validate=validate.Length(max=1024))


class AppSchema(SQLAlchemyAutoSchema):
    name = auto_field(
        required=True,
        validate=validate.And(
            validate.Length(min=1, max=255),
            validate.Regexp(
                f"^{access_config.name_pattern}$",
                error=f"App {access_config.name_validation_error} Regex to match: /{{regex}}/",
            ),
        ),
    )
    description = auto_field(validate=validate.Length(max=1024))

    initial_owner_id = fields.String(validate=validate.Length(min=1, max=255), load_only=True)
    initial_owner_role_ids = fields.List(fields.String(validate=validate.Length(equal=20)), load_only=True)
    initial_additional_app_groups = fields.Nested(lambda: InitialAppGroupSchema, many=True, load_only=True)

    tags_to_add = fields.List(fields.String(validate=validate.Length(equal=20)), load_only=True)
    tags_to_remove = fields.List(fields.String(validate=validate.Length(equal=20)), load_only=True)

    app_groups = fields.Nested(lambda: AppGroupSchema, many=True)
    active_app_groups = fields.Nested(lambda: AppGroupSchema, many=True)
    active_owner_app_groups = fields.Nested(lambda: AppGroupSchema, many=True)
    active_non_owner_app_groups = fields.Nested(lambda: AppGroupSchema, many=True)

    all_app_tags = fields.Nested(
        lambda: AppTagMapSchema(
            only=(
                "created_at",
                "ended_at",
                "tag.id",
                "tag.name",
                "tag.constraints",
                "tag.deleted_at",
            ),
            many=True,
        ),
    )
    active_app_tags = fields.Nested(
        lambda: AppTagMapSchema(
            only=(
                "created_at",
                "ended_at",
                "active_tag.id",
                "active_tag.name",
                "active_tag.constraints",
                "active_tag.enabled",
            ),
            many=True,
        ),
    )

    class Meta:
        fields = (
            "id",
            "created_at",
            "updated_at",
            "deleted_at",
            "name",
            "description",
            "initial_owner_id",
            "initial_owner_role_ids",
            "initial_additional_app_groups",
            "tags_to_add",
            "tags_to_remove",
            "app_groups",
            "active_app_groups",
            "active_owner_app_groups",
            "active_non_owner_app_groups",
            "all_app_tags",
            "active_app_tags",
        )
        dump_only = (
            "id",
            "created_at",
            "updated_at",
            "deleted_at",
            "app_groups",
            "active_app_groups",
            "active_owner_app_groups",
            "active_non_owner_app_groups",
            "all_app_tags",
            "active_app_tags",
        )
        load_only = (
            "initial_owner_id",
            "initial_owner_role_ids",
            "initial_additional_app_groups",
            "tags_to_add",
            "tags_to_remove",
        )
        model = App
        sqla_session = db.session
        load_instance = True
        include_relationships = True


class PolymorphicGroupSchemaMeta(SchemaMeta):
    # Include all fields from all group schemas as get_declared_fields
    @classmethod
    def get_declared_fields(
        mcs,
        klass: type,
        cls_fields: list,  # type: ignore[type-arg]
        inherited_fields: list,  # type: ignore[type-arg]
        dict_cls: type = dict,
    ) -> Any:
        fields = dict_cls()
        for group_schema in klass().TYPE_TO_GROUP_SCHEMA_MAP.values():
            # For some reason some fields not apart of the schema have None values, so filter them out
            fields.update({k: v for k, v in group_schema._declared_fields.items() if v is not None})
        return fields


class PolymorphicGroupSchema(Schema, metaclass=PolymorphicGroupSchemaMeta):
    TYPE_TO_GROUP_SCHEMA_MAP = {
        "okta_group": OktaGroupSchema,
        "role_group": RoleGroupSchema,
        "app_group": AppGroupSchema,
    }

    def load(
        self,
        data: Any,
        *args: Any,
        session: Optional[Session] = None,
        instance: Optional[Any] = None,
        transient: Optional[bool] = False,
        **kwargs: Any,
    ) -> Any:
        if "type" not in data:
            raise ValidationError(f"type not given, expecting one of {self.TYPE_TO_GROUP_SCHEMA_MAP.keys()}")

        group_type = data["type"]
        if group_type in self.TYPE_TO_GROUP_SCHEMA_MAP:
            group_class = self.TYPE_TO_GROUP_SCHEMA_MAP[group_type]
            return group_class(
                only=self._polymorphic_fields_intersection(group_class, self.only),
                exclude=self._polymorphic_fields_intersection(group_class, self.exclude),
                load_only=self._polymorphic_fields_intersection(group_class, self.load_only),
                dump_only=self._polymorphic_fields_intersection(group_class, self.dump_only),
            ).load(data, session=session, instance=instance, transient=transient)
        else:
            raise ValidationError(f"Unexpected group type, expecting one of {self.TYPE_TO_GROUP_SCHEMA_MAP.keys()}")
        raise ValidationError(f"Unable to validate with: {self.TYPE_TO_GROUP_SCHEMA_MAP}")

    def dump(self, obj: Any, *args: Any, many: Optional[bool] = None) -> List[Dict[str, Any]] | Dict[str, Any]:
        many = self.many if many is None else bool(many)

        if many:
            return [self._serialize(d) for d in obj]
        else:
            return self._serialize(obj)

    def _serialize(self, obj: Any, *args: Any, **kwargs: Any) -> Any:
        group_type = obj.type
        if group_type in self.TYPE_TO_GROUP_SCHEMA_MAP:
            group_class = self.TYPE_TO_GROUP_SCHEMA_MAP[group_type]
            return group_class(
                only=self._polymorphic_fields_intersection(group_class, self.only),
                exclude=self._polymorphic_fields_intersection(group_class, self.exclude),
                load_only=self._polymorphic_fields_intersection(group_class, self.load_only),
                dump_only=self._polymorphic_fields_intersection(group_class, self.dump_only),
            ).dump(obj)
        else:
            raise ValidationError(f"Unexpected group type, expecting one of {self.TYPE_TO_GROUP_SCHEMA_MAP.keys()}")
        raise ValidationError(f"Unable to validate with: {self.TYPE_TO_GROUP_SCHEMA_MAP}")

    def _polymorphic_fields_intersection(
        self, group_class: type[Schema], input_fields: Optional[Sequence[str] | AbstractSet[str]]
    ) -> Optional[Sequence[str] | AbstractSet[str]]:
        if input_fields is None:
            return None

        fields = set(cast(SchemaOpts, group_class.Meta).fields)
        return set(filter(lambda field: field.split(".")[0] in fields, input_fields))


class AccessRequestSchema(SQLAlchemyAutoSchema):
    requester = fields.Nested(lambda: OktaUserSchema)
    active_requester = fields.Nested(lambda: OktaUserSchema)
    requested_group = fields.Nested(lambda: PolymorphicGroupSchema)
    active_requested_group = fields.Nested(lambda: PolymorphicGroupSchema)

    resolver = fields.Nested(lambda: OktaUserSchema)
    active_resolver = fields.Nested(lambda: OktaUserSchema)

    approved_membership = fields.Nested(
        lambda: OktaUserGroupMemberSchema(
            only=(
                "is_owner",
                "ended_at",
            )
        ),
    )

    class Meta:
        model = AccessRequest
        sqla_session = db.session
        load_instance = True
        include_relationships = True
        fields = (
            "id",
            "created_at",
            "updated_at",
            "resolved_at",
            "status",
            "requester",
            "active_requester",
            "requested_group",
            "requested_group.app",
            "active_requested_group",
            "request_ownership",
            "request_reason",
            "request_ending_at",
            "resolver",
            "active_resolver",
            "resolution_reason",
            "approved_membership",
            "approval_ending_at",
        )
        dump_only = (
            "id",
            "created_at",
            "updated_at",
            "resolved_at",
            "status",
            "requester",
            "active_requester",
            "requested_group",
            "requested_group.app",
            "active_requested_group",
            "request_ownership",
            "request_reason",
            "request_ending_at",
            "resolver",
            "active_resolver",
            "resolution_reason",
            "approved_membership",
            "approval_ending_at",
        )


class RoleRequestSchema(SQLAlchemyAutoSchema):
    requester = fields.Nested(lambda: OktaUserSchema)
    active_requester = fields.Nested(lambda: OktaUserSchema)
    requester_role = fields.Nested(lambda: RoleGroupSchema)
    active_requester_role = fields.Nested(lambda: RoleGroupSchema)
    requested_group = fields.Nested(lambda: PolymorphicGroupSchema)
    active_requested_group = fields.Nested(lambda: PolymorphicGroupSchema)

    resolver = fields.Nested(lambda: OktaUserSchema)
    active_resolver = fields.Nested(lambda: OktaUserSchema)

    approved_membership = fields.Nested(
        lambda: RoleGroupMapSchema(
            only=(
                "is_owner",
                "ended_at",
            )
        ),
    )

    class Meta:
        model = RoleRequest
        sqla_session = db.session
        load_instance = True
        include_relationships = True
        fields = (
            "id",
            "created_at",
            "updated_at",
            "resolved_at",
            "status",
            "requester",
            "active_requester",
            "requester_role",
            "active_requester_role",
            "requested_group",
            "requested_group.app",
            "active_requested_group",
            "request_ownership",
            "request_reason",
            "request_ending_at",
            "resolver",
            "active_resolver",
            "resolution_reason",
            "approved_membership",
            "approval_ending_at",
        )
        dump_only = (
            "id",
            "created_at",
            "updated_at",
            "resolved_at",
            "status",
            "requester",
            "active_requester",
            "requester_role",
            "active_requester_role",
            "requested_group",
            "requested_group.app",
            "active_requested_group",
            "request_ownership",
            "request_reason",
            "request_ending_at",
            "resolver",
            "active_resolver",
            "resolution_reason",
            "approved_membership",
            "approval_ending_at",
        )


class TagSchema(SQLAlchemyAutoSchema):
    name = auto_field(
        required=True,
        validate=validate.And(
            validate.Length(min=1, max=255),
            validate.Regexp(
                f"^{access_config.name_pattern}$",
                error=f"Tag {access_config.name_validation_error} Regex to match: /{{regex}}/",
            ),
        ),
    )
    description = auto_field(load_default="", validate=validate.Length(max=1024))

    def validate_constraints(value) -> bool:
        if not isinstance(value, dict):
            raise ValidationError("Constraints must be a dictionary")
        for key, val in value.items():
            if not isinstance(key, str):
                raise ValidationError("Constraint keys must be strings")
            if key not in Tag.CONSTRAINTS:
                raise ValidationError(
                    f"Constraint key must be one of {', '.join([k for k,_ in Tag.CONSTRAINTS.items()])}"
                )
            constraint = Tag.CONSTRAINTS[key]
            if not constraint.validator(val):
                raise ValidationError(f"Constraint value must be {constraint.description}")
        return True

    constraints = fields.Dict(validate=validate_constraints)

    all_group_tags = fields.Nested(
        lambda: OktaGroupTagMapSchema(
            only=(
                "created_at",
                "ended_at",
                "group.id",
                "group.type",
                "group.name",
                "group.is_owner",
                "group.is_managed",
                "group.deleted_at",
                "group.app.id",
                "group.app.name",
                "group.app.deleted_at",
                "app_tag_mapping.created_at",
                "app_tag_mapping.ended_at",
                "app_tag_mapping.app.id",
                "app_tag_mapping.app.name",
                "app_tag_mapping.app.deleted_at",
            ),
            many=True,
        ),
    )

    active_group_tags = fields.Nested(
        lambda: OktaGroupTagMapSchema(
            only=(
                "created_at",
                "ended_at",
                "active_group.id",
                "active_group.type",
                "active_group.name",
                "active_group.description",
                "active_group.is_owner",
                "active_group.is_managed",
                "active_group.app.id",
                "active_group.app.name",
                "active_app_tag_mapping.created_at",
                "active_app_tag_mapping.ended_at",
                "active_app_tag_mapping.app.id",
                "active_app_tag_mapping.app.name",
            ),
            many=True,
        ),
    )

    all_app_tags = fields.Nested(
        lambda: AppTagMapSchema(
            only=(
                "created_at",
                "ended_at",
                "app.id",
                "app.name",
                "app.deleted_at",
            ),
            many=True,
        ),
    )

    active_app_tags = fields.Nested(
        lambda: AppTagMapSchema(
            only=("created_at", "ended_at", "active_app.id", "active_app.name", "active_app.description"),
            many=True,
        ),
    )

    class Meta:
        model = Tag
        sqla_session = db.session
        include_relationships = True
        load_instance = True
        fields = (
            "id",
            "name",
            "description",
            "enabled",
            "constraints",
            "created_at",
            "updated_at",
            "deleted_at",
            "all_group_tags",
            "active_group_tags",
            "all_app_tags",
            "active_app_tags",
        )
        dump_only = (
            "id",
            "created_at",
            "updated_at",
            "deleted_at",
            "all_group_tags",
            "active_group_tags",
            "all_app_tags",
            "active_app_tags",
        )


class OktaGroupTagMapSchema(SQLAlchemyAutoSchema):
    tag = fields.Nested(lambda: TagSchema)
    active_tag = fields.Nested(lambda: TagSchema)
    enabled_active_tag = fields.Nested(lambda: TagSchema)

    group = fields.Nested(lambda: PolymorphicGroupSchema)
    active_group = fields.Nested(lambda: PolymorphicGroupSchema)

    app_tag_mapping = fields.Nested(lambda: AppTagMapSchema)
    active_app_tag_mapping = fields.Nested(lambda: AppTagMapSchema)

    class Meta:
        model = OktaGroupTagMap
        sqla_session = db.session
        include_relationships = True
        load_instance = True
        fields = (
            "id",
            "tag_id",
            "group_id",
            "app_tag_map_id",
            "created_at",
            "updated_at",
            "ended_at",
            "tag",
            "active_tag",
            "enabled_active_tag",
            "group",
            "active_group",
            "app_tag_mapping",
            "active_app_tag_mapping",
        )
        dump_only = (
            "id",
            "tag_id",
            "group_id",
            "app_tag_map_id",
            "created_at",
            "updated_at",
            "ended_at",
            "tag",
            "active_tag",
            "enabled_active_tag",
            "group",
            "active_group",
            "app_tag_mapping",
            "active_app_tag_mapping",
        )


class AppTagMapSchema(SQLAlchemyAutoSchema):
    tag = fields.Nested(lambda: TagSchema)
    active_tag = fields.Nested(lambda: TagSchema)
    enabled_active_tag = fields.Nested(lambda: TagSchema)

    app = fields.Nested(lambda: AppSchema)
    active_app = fields.Nested(lambda: AppSchema)

    group_tag_mappings = fields.Nested(lambda: OktaGroupTagMapSchema, many=True)
    active_group_tag_mappings = fields.Nested(lambda: OktaGroupTagMapSchema, many=True)

    class Meta:
        model = AppTagMap
        sqla_session = db.session
        include_relationships = True
        load_instance = True
        fields = (
            "id",
            "tag_id",
            "app_id",
            "created_at",
            "updated_at",
            "ended_at",
            "tag",
            "active_tag",
            "enabled_active_tag",
            "app",
            "active_app",
            "group_tag_mapping",
            "active_group_tag_mappings",
        )
        dump_only = (
            "id",
            "tag_id",
            "app_id",
            "created_at",
            "updated_at",
            "ended_at",
            "tag",
            "active_tag",
            "enabled_active_tag",
            "app",
            "active_app",
            "group_tag_mapping",
            "active_group_tag_mappings",
        )
