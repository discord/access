from datetime import UTC, datetime, timedelta
from enum import StrEnum
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


class ConstraintOrigin(StrEnum):
    """How a tag reaches the group a constraint is being evaluated for.

    A `StrEnum` so it serializes as the bare string it always was: the JSON
    contract is unchanged, but the OpenAPI schema now carries the value set,
    and the generated TypeScript client gets a literal union instead of
    `string`.
    """

    #: Applied to the group itself.
    DIRECT = "direct"
    #: Inherited from the group's app.
    APP = "app"
    #: Reaches a role because the role is a member of the tagged group.
    MEMBER_ASSOCIATION = "member_association"
    #: Reaches a role because the role owns the tagged group.
    OWNER_ASSOCIATION = "owner_association"


class ConstraintSource(NamedTuple):
    """One tag contributing a value for a constraint, and where it came from."""

    tag: Tag
    value: Any
    origin: ConstraintOrigin
    #: The App for an `APP` origin, the source group for an association origin,
    #: and None for a `DIRECT` one. Named without "group" because it is not
    #: always a group.
    source_id: Optional[str]
    source_name: Optional[str]


def _own_tag_sources(constraint_key: str, group: OktaGroup, include_provenance: bool) -> list[ConstraintSource]:
    """Tags carrying `constraint_key` that sit on `group` itself, directly or via its app."""
    sources: list[ConstraintSource] = []
    for tag_map in group.active_group_tags:
        tag = tag_map.active_tag
        if not tag.enabled or constraint_key not in tag.constraints:
            continue
        origin = ConstraintOrigin.DIRECT
        source_id = None
        source_name = None
        if include_provenance and tag_map.active_app_tag_mapping is not None:
            origin = ConstraintOrigin.APP
            # `active_app` filters `deleted_at`, so it is None for a
            # soft-deleted app; fall back to no source rather than raising, as
            # every other `active_*` read here does.
            app = tag_map.active_app_tag_mapping.active_app
            source_id = getattr(app, "id", None)
            source_name = getattr(app, "name", None)
        sources.append(
            ConstraintSource(
                tag=tag,
                value=tag.constraints[constraint_key],
                origin=origin,
                source_id=source_id,
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

    # One entry per association direction: the key read on the source group
    # differs, but the handling is otherwise identical.
    association_axes: tuple[tuple[ConstraintOrigin, str, list[RoleGroupMap]], ...] = (
        (
            ConstraintOrigin.MEMBER_ASSOCIATION,
            constraint_key,
            group.active_role_associated_group_member_mappings,
        ),
        (
            ConstraintOrigin.OWNER_ASSOCIATION,
            OWNER_SIDE_COUNTERPART[constraint_key],
            group.active_role_associated_group_owner_mappings,
        ),
    )

    sources: list[ConstraintSource] = []
    for origin, key, mappings in association_axes:
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
    """Collect every tag contributing a value for `constraint_key` to `group`.

    Covers tags on the group itself and, when `group` is a role, tags reaching
    it from the groups it is associated with. Only enabled tags contribute, and
    a propagated tag contributes only if it has `propagate_to_roles` set and
    its source group is managed.

    Args:
        constraint_key: A key from `Tag.CONSTRAINTS`.
        group: The group to evaluate. Association reads only happen when this
            is a `RoleGroup`.
        include_provenance: Whether to distinguish `APP` from `DIRECT` origins.
            Doing so reads `OktaGroupTagMap.active_app_tag_mapping`, so
            enforcement paths leave this False and avoid having to eager-load
            it. Display paths set it True.

    Returns:
        Every contributing source, unordered and not deduplicated -- one entry
        per tag per origin. Empty if nothing sets the constraint.

    Raises:
        InvalidRequestError: If a relationship this reads was not eager-loaded.
            All of them are `lazy="raise_on_sql"`; see
            `api/routers/_eager.py`.
    """
    return _own_tag_sources(constraint_key, group, include_provenance) + _propagated_sources(constraint_key, group)


def effective_constraint(constraint_key: str, group: OktaGroup) -> Any:
    """Resolve the value of `constraint_key` actually in force on `group`.

    Coalesces every contributing tag under that constraint's own rule -- the
    minimum for time limits, logical OR for flags -- across the group's own
    tags and anything reaching it from an associated group.

    Args:
        constraint_key: A key from `Tag.CONSTRAINTS`.
        group: The group to evaluate.

    Returns:
        The coalesced value, or None if no tag sets this constraint.

    Raises:
        InvalidRequestError: If a relationship this reads was not eager-loaded.
    """
    return _fold(Tag.CONSTRAINTS[constraint_key], constraint_sources(constraint_key, group))


def effective_ended_at(
    constraint_key: str, group: OktaGroup, initial_ended_at: Optional[datetime]
) -> Optional[datetime]:
    """Apply a time-limit constraint to a proposed access end date.

    Caps `initial_ended_at` at the limit `constraint_key` resolves to for
    `group`, so a grant can never outlive what the tags in force allow. An
    indefinite grant becomes bounded; an already-shorter one is left alone.

    Args:
        constraint_key: `MEMBER_TIME_LIMIT_CONSTRAINT_KEY` or
            `OWNER_TIME_LIMIT_CONSTRAINT_KEY`.
        group: The group the access is being granted on.
        initial_ended_at: The requested end date, or None for indefinite.

    Returns:
        The capped end date, or `initial_ended_at` unchanged when no limit
        applies or `group` is unmanaged (unmanaged groups enforce nothing).

    Raises:
        InvalidRequestError: If a relationship this reads was not eager-loaded.
    """
    if not group.is_managed:
        return initial_ended_at

    seconds_limit = effective_constraint(constraint_key, group)
    if seconds_limit is None:
        return initial_ended_at
    constraint_ended_at = datetime.now(UTC) + timedelta(seconds=seconds_limit)
    if initial_ended_at is None:
        return constraint_ended_at
    return min(constraint_ended_at, initial_ended_at.replace(tzinfo=UTC))


def _source_phrase(source: ConstraintSource) -> str:
    """Name one source the way it should read inside an error message."""
    if source.origin == ConstraintOrigin.MEMBER_ASSOCIATION:
        return f"tags on {source.source_name}, which this role is a member of"
    if source.origin == ConstraintOrigin.OWNER_ASSOCIATION:
        return f"tags on {source.source_name}, which this role owns"
    return "tags on this group"


def _join_phrases(phrases: list[str]) -> str:
    """Join phrases as prose: "a", "a and b", "a, b and c"."""
    if len(phrases) == 1:
        return phrases[0]
    return f"{', '.join(phrases[:-1])} and {phrases[-1]}"


def constraint_source_clause(constraint_key: str, group: OktaGroup) -> str:
    """Build a trailing clause naming why `constraint_key` blocks an action.

    Names every source imposing the constraint, not just one. These are the
    boolean constraints, whose coalesce is logical OR, so each truthy source
    blocks independently -- resolving one would not unblock the action, and a
    message naming a single source would send someone to fix the wrong thing.

    Args:
        constraint_key: A key from `Tag.CONSTRAINTS`.
        group: The group whose action is being blocked.

    Returns:
        A clause beginning "due to ...", suitable for appending to an error
        message. Falls back to naming the group itself when no source carries
        a name.

    Raises:
        InvalidRequestError: If a relationship this reads was not eager-loaded.
    """
    phrases: list[str] = []
    for source in constraint_sources(constraint_key, group):
        if not source.value:
            continue
        phrase = _source_phrase(source)
        # Two tags on one source group produce the same phrase; say it once.
        if phrase not in phrases:
            phrases.append(phrase)
    if not phrases:
        return "due to tags on this group"
    return f"due to {_join_phrases(phrases)}"


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
