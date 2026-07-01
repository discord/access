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

from sqlalchemy.orm import joinedload, noload, selectin_polymorphic, selectinload
from sqlalchemy.orm.attributes import set_committed_value

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
        joinedload(OktaUserGroupMember.created_actor),
        joinedload(OktaUserGroupMember.ended_actor),
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


def _role_group_map_actor_and_role_options() -> tuple:
    """The subset of `role_group_map_options()`/`role_group_map_options_for_own_group()`
    that's identical between them: the role side and the actor columns.
    They only disagree on how to load `.group`/`.active_group`."""
    return (
        joinedload(RoleGroupMap.role_group),
        joinedload(RoleGroupMap.active_role_group),
        joinedload(RoleGroupMap.created_actor),
        joinedload(RoleGroupMap.ended_actor),
    )


def role_group_map_options() -> tuple:
    """Eager-load every relationship `RoleGroupMapDetail` reads."""
    return (
        *_role_group_map_actor_and_role_options(),
        selectinload(RoleGroupMap.group).options(*polymorphic_group_options()),
        selectinload(RoleGroupMap.active_group).options(*polymorphic_group_options()),
    )


def role_group_map_options_for_own_group() -> tuple:
    """Eager-load every relationship `RoleGroupMapDetail` reads, for mappings
    reached via `OktaGroup.active_role_member_mappings` /
    `active_role_owner_mappings`.

    On those two relationships `RoleGroupMap.group_id` is always the id of
    the group being loaded -- the mapping is *about* that group. Eagerly
    re-selecting `.group`/`.active_group` there re-fetches the exact
    `app_group`/`app` row the caller already has, once per mapping. Skip
    the query here and use `bind_role_group_map_own_groups` after load to
    stamp both attributes from the already-loaded group instead.
    """
    return (
        *_role_group_map_actor_and_role_options(),
        noload(RoleGroupMap.group),
        noload(RoleGroupMap.active_group),
    )


def bind_role_group_map_own_groups(group: OktaGroup) -> None:
    """Stamp `.group`/`.active_group` on `group.active_role_member_mappings`
    and `.active_role_owner_mappings` -- the two `RoleGroupMap` collections
    loaded via `role_group_map_options_for_own_group` -- without hitting the
    database. See that function's docstring for why this is correct."""
    active_group = group if group.deleted_at is None else None
    for mapping in (*group.active_role_member_mappings, *group.active_role_owner_mappings):
        set_committed_value(mapping, "group", group)
        set_committed_value(mapping, "active_group", active_group)


def group_tag_map_options() -> tuple:
    """Eager-load every relationship `OktaGroupTagMapDetail` reads.

    `AppTagMapDetail.active_app` is hydrated here too because the same
    schema is reused inside `TagDetail.active_app_tags` — the route-side
    loaders for tag detail rely on this helper indirectly through the
    forward-ref chain.
    """
    return (
        joinedload(OktaGroupTagMap.active_app_tag_mapping).options(
            joinedload(AppTagMap.active_tag),
            joinedload(AppTagMap.active_app),
        ),
        joinedload(OktaGroupTagMap.active_tag),
        selectinload(OktaGroupTagMap.active_group).options(*polymorphic_group_options()),
    )
