"""Shared SQLAlchemy eager-load option helpers.

These mirror the field set of `OktaUserGroupMemberDetail`, `RoleGroupMapDetail`,
`OktaGroupTagMapDetail`, and `AppTagMapDetail` so any router returning those
shapes can re-use the same loader and stay in sync with the schema. Pydantic
runs in strict `from_attributes=True` mode (`api/schemas/_serialize.py`), so
every relationship the response schema reads must be eager-loaded; otherwise
SQLAlchemy raises `InvalidRequestError` on the `lazy="raise_on_sql"`
relationships.
"""

from __future__ import annotations

from sqlalchemy.orm import joinedload, selectin_polymorphic, selectinload

from api.models import (
    AppGroup,
    AppTagMap,
    OktaGroup,
    OktaGroupTagMap,
    OktaUserGroupMember,
    RoleGroup,
    RoleGroupMap,
)


def polymorphic_group_options() -> tuple:
    """Polymorphic loader for an `OktaGroup` relationship: pulls the
    `AppGroup`/`RoleGroup` subclass attributes plus `AppGroup.app`."""
    return (
        selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
        joinedload(AppGroup.app),
    )


def user_group_member_options() -> tuple:
    """Eager-load every relationship `OktaUserGroupMemberDetail` reads."""
    return (
        joinedload(OktaUserGroupMember.user),
        joinedload(OktaUserGroupMember.active_user),
        selectinload(OktaUserGroupMember.group).options(*polymorphic_group_options()),
        selectinload(OktaUserGroupMember.active_group).options(*polymorphic_group_options()),
        joinedload(OktaUserGroupMember.role_group_mapping).options(
            joinedload(RoleGroupMap.role_group),
            joinedload(RoleGroupMap.active_role_group),
        ),
        joinedload(OktaUserGroupMember.active_role_group_mapping).options(
            joinedload(RoleGroupMap.role_group),
            joinedload(RoleGroupMap.active_role_group),
        ),
    )


def role_group_map_options() -> tuple:
    """Eager-load every relationship `RoleGroupMapDetail` reads."""
    return (
        joinedload(RoleGroupMap.role_group),
        joinedload(RoleGroupMap.active_role_group),
        selectinload(RoleGroupMap.group).options(*polymorphic_group_options()),
        selectinload(RoleGroupMap.active_group).options(*polymorphic_group_options()),
    )


def group_tag_map_options() -> tuple:
    """Eager-load every relationship `OktaGroupTagMapDetail` reads."""
    return (
        joinedload(OktaGroupTagMap.active_app_tag_mapping).joinedload(AppTagMap.active_tag),
        joinedload(OktaGroupTagMap.active_tag),
        selectinload(OktaGroupTagMap.active_group).options(*polymorphic_group_options()),
    )
