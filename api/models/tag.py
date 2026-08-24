from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple, Optional

from api.models.core_models import OktaGroup, RoleGroup, RoleGroupMap, Tag, TagConstraint


def coalesce_constraints(constraint_key: str, tags: list[Tag]) -> Any:
    coalesced_constraint_value = None
    constraint = Tag.CONSTRAINTS[constraint_key]
    for tag in tags:
        if tag.enabled and constraint_key in tag.constraints:
            if coalesced_constraint_value is None:
                coalesced_constraint_value = tag.constraints[constraint_key]
            else:
                coalesced_constraint_value = constraint.coalesce(
                    coalesced_constraint_value, tag.constraints[constraint_key]
                )
    return coalesced_constraint_value


def coalesce_ended_at(
    constraint_key: str, tags: list[Tag], initial_ended_at: Optional[datetime], group_is_managed: bool
) -> Optional[datetime]:
    # Only apply constraints if the group is managed
    if not group_is_managed:
        return initial_ended_at

    # Determine the minimum time allowed for group membership and ownership by current group tags
    seconds_limit = coalesce_constraints(constraint_key=constraint_key, tags=tags)
    if seconds_limit is None:
        return initial_ended_at
    else:
        constraint_ended_at = datetime.now(UTC) + timedelta(seconds=seconds_limit)
        if initial_ended_at is None:
            return constraint_ended_at
        else:
            return min(constraint_ended_at, initial_ended_at.replace(tzinfo=UTC))


# A role confers access upon its MEMBERS. So propagation always feeds the
# role's member-side constraints; an association where the role is an OWNER of
# the group reads that group's owner-side key instead. These three pairs cover
# all six constraints. Membership of this dict is also what decides whether a
# key propagates at all: owner-side keys on a role receive nothing, because
# owning a role does not confer the role's grants.
OWNER_SIDE_COUNTERPART: dict[str, str] = {
    Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY,
    Tag.REQUIRE_MEMBER_REASON_CONSTRAINT_KEY: Tag.REQUIRE_OWNER_REASON_CONSTRAINT_KEY,
    Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY: Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY,
}


class ConstraintSource(NamedTuple):
    tag: Tag
    value: Any
    # "direct" | "app" | "member_association" | "owner_association"
    origin: str
    # For a group-association origin these name the source *group*; for an
    # "app" origin `source_name` is the App's name and `source_id` is None.
    # Hence the deliberately origin-agnostic names.
    source_id: Optional[str]
    source_name: Optional[str]


def _own_tag_sources(constraint_key: str, group: OktaGroup, include_provenance: bool) -> list[ConstraintSource]:
    sources: list[ConstraintSource] = []
    for tag_map in group.active_group_tags:
        tag = tag_map.active_tag
        if not tag.enabled or constraint_key not in tag.constraints:
            continue
        origin = "direct"
        source_name = None
        if include_provenance and tag_map.active_app_tag_mapping is not None:
            origin = "app"
            # For an "app" origin the "source" is an App, not a group -- there's
            # no group to point `source_id` at, but the app's name is still
            # meaningful to surface. `active_app` filters `deleted_at`, so it is
            # None for a soft-deleted app; fall back to no name rather than
            # raising, as every other `active_*` read here does.
            source_name = getattr(tag_map.active_app_tag_mapping.active_app, "name", None)
        sources.append(
            ConstraintSource(
                tag=tag,
                value=tag.constraints[constraint_key],
                origin=origin,
                source_id=None,
                source_name=source_name,
            )
        )
    return sources


def _propagated_sources(constraint_key: str, group: OktaGroup) -> list[ConstraintSource]:
    """Tags reaching `group` because it is a role associated with a tagged group.

    Reads `active_role_associated_group_member_mappings` and
    `active_role_associated_group_owner_mappings`, each joined through
    `active_group` to `active_group_tags` -> `active_tag`. All four are
    `lazy="raise_on_sql"`; callers must eager-load them.
    """
    if type(group) is not RoleGroup:
        return []
    # Owner-side keys never propagate onto a role.
    if constraint_key not in OWNER_SIDE_COUNTERPART:
        return []

    pairs: tuple[tuple[str, str, list[RoleGroupMap]], ...] = (
        ("member_association", constraint_key, group.active_role_associated_group_member_mappings),
        (
            "owner_association",
            OWNER_SIDE_COUNTERPART[constraint_key],
            group.active_role_associated_group_owner_mappings,
        ),
    )

    sources: list[ConstraintSource] = []
    for origin, key, mappings in pairs:
        for mapping in mappings:
            source_group = mapping.active_group
            if not source_group.is_managed:
                continue
            for tag_map in source_group.active_group_tags:
                tag = tag_map.active_tag
                if not tag.enabled or not tag.propagate_to_roles:
                    continue
                if key not in tag.constraints:
                    continue
                sources.append(
                    ConstraintSource(
                        tag=tag,
                        value=tag.constraints[key],
                        origin=origin,
                        source_id=source_group.id,
                        source_name=source_group.name,
                    )
                )
    return sources


def _fold(constraint: TagConstraint, sources: list[ConstraintSource]) -> Any:
    """Coalesce `sources`' values pairwise under `constraint.coalesce`.

    Seeded on `is None`, not on truthiness: a first source contributing a falsy
    value (`False`, or a `0`-second time limit) is a real contribution and must
    seed the fold rather than be skipped.
    """
    value = None
    for source in sources:
        value = source.value if value is None else constraint.coalesce(value, source.value)
    return value


def constraint_sources(
    constraint_key: str, group: OktaGroup, *, include_provenance: bool = False
) -> list[ConstraintSource]:
    """Every tag contributing `constraint_key` to `group`, with where it came from.

    `include_provenance=True` additionally reads
    `OktaGroupTagMap.active_app_tag_mapping` to distinguish "direct" from
    "app". Enforcement paths leave it False so they do not have to eager-load
    that relationship.
    """
    return _own_tag_sources(constraint_key, group, include_provenance) + _propagated_sources(constraint_key, group)


def effective_constraint(constraint_key: str, group: OktaGroup) -> Any:
    """Coalesce `constraint_key` across every tag in force on `group`, including
    tags propagated from associated groups when `group` is a role."""
    return _fold(Tag.CONSTRAINTS[constraint_key], constraint_sources(constraint_key, group))


def effective_ended_at(
    constraint_key: str, group: OktaGroup, initial_ended_at: Optional[datetime]
) -> Optional[datetime]:
    """`coalesce_ended_at`, but sourced from the effective constraint set (own
    tags plus anything propagated from associated groups)."""
    if not group.is_managed:
        return initial_ended_at

    seconds_limit = effective_constraint(constraint_key, group)
    if seconds_limit is None:
        return initial_ended_at
    constraint_ended_at = datetime.now(UTC) + timedelta(seconds=seconds_limit)
    if initial_ended_at is None:
        return constraint_ended_at
    return min(constraint_ended_at, initial_ended_at.replace(tzinfo=UTC))


def blocking_source(constraint_key: str, group: OktaGroup) -> Optional[ConstraintSource]:
    """The first source contributing a truthy value for `constraint_key`.

    Used to name the cause in an error message. Sound for the boolean
    constraints because their coalesce is `OR` -- any truthy source on its own
    explains the block.
    """
    for source in constraint_sources(constraint_key, group):
        if source.value:
            return source
    return None


def constraint_source_clause(source: Optional[ConstraintSource]) -> str:
    """Trailing clause naming where a constraint came from."""
    if source is None or source.source_name is None:
        return "due to group tags"
    if source.origin == "member_association":
        return f"due to tags on {source.source_name}, which this role is a member of"
    return f"due to tags on {source.source_name}, which this role owns"


def effective_constraints(group: OktaGroup) -> list[dict[str, Any]]:
    """Every constraint in force on `group`, with its coalesced value and sources.

    Backs both the API response and the UI panel, so display and enforcement
    read the same data. Unlike `effective_constraint`, this reads
    `OktaGroupTagMap.active_app_tag_mapping` for provenance -- callers must
    eager-load it (see `api/routers/_eager.py`).
    """
    entries = []
    for constraint_key, constraint in Tag.CONSTRAINTS.items():
        sources = constraint_sources(constraint_key, group, include_provenance=True)
        if not sources:
            continue
        entries.append(
            {
                "constraint": constraint_key,
                "name": constraint.name,
                "value": _fold(constraint, sources),
                "sources": [
                    {
                        "tag_id": source.tag.id,
                        "tag_name": source.tag.name,
                        "origin": source.origin,
                        "source_id": source.source_id,
                        "source_name": source.source_name,
                    }
                    for source in sources
                ],
            }
        )
    return entries
