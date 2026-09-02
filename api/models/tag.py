from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, NamedTuple, Optional

from api.exceptions import InvalidRequestError
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

# Constraints a tag may not carry while opting out of propagation. The reason
# and time-limit constraints degrade gracefully without it -- they still bind
# on the direct path, and a role path still records some reason and some
# duration. A self-add prohibition does not degrade, it inverts: the owner it
# targets adds themselves to a role associated with the tagged group and
# arrives at the same access by a supported path, with nothing recording that a
# separation-of-duties control was in play. So these two settings are not
# independently configurable, and `validate_constraint_propagation` rejects the
# combination at every write.
PROPAGATION_REQUIRED_CONSTRAINT_KEYS: frozenset[str] = frozenset(
    {
        Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY,
        Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY,
    }
)


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
    # An unmanaged role enforces nothing, so nothing should propagate onto it.
    # Gating here rather than at each caller keeps display and enforcement
    # agreeing: every enforcement path already checks `is_managed` separately,
    # and without this the read surface would advertise constraints that
    # nothing applies.
    if not group.is_managed:
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
            # `active_group` filters `deleted_at`, so it is None when the
            # source group has been soft-deleted while the mapping is still
            # active. Skip rather than raise, matching how this module treats
            # every other `active_*` read.
            source_group = mapping.active_group
            if source_group is None or not source_group.is_managed:
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
    a propagated tag contributes only if it has `propagate_to_roles` set, its
    source group is managed, and the role itself is managed.

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


def validate_constraint_propagation(constraints: Optional[dict[str, Any]], propagate_to_roles: Optional[bool]) -> None:
    """Reject a tag whose constraints cannot survive its propagation setting.

    Guards the invariant that a `PROPAGATION_REQUIRED_CONSTRAINT_KEYS`
    constraint is never set on a tag that opts out of reaching roles. Every
    write path calls this, because the two fields can be set independently and
    in either order -- turning propagation off on a tag that already disallows
    self-adds opens the same hole as adding the constraint to a tag that
    already opted out.

    Callers must pass the *merged* result of a partial update, not the request
    body alone: a `PUT` carrying only `propagate_to_roles` is coherent in
    isolation and conflicts only with what is already stored.

    Args:
        constraints: The tag's resulting constraint mapping. None is read as
            empty, matching the column's `{}` server default on a row that has
            not been flushed yet.
        propagate_to_roles: The tag's resulting propagation setting. None is
            read as True, matching the column default -- an unflushed `Tag()`
            has not had it applied, so reading the attribute yields None rather
            than the default it will be given at INSERT.

    Raises:
        InvalidRequestError: If any such constraint is set truthy while
            propagation is off. Names every offending key, since removing one
            of two would still leave the tag invalid, and states the invariant
            rather than which half of it the write happened to change --
            either half may be the one arriving.
    """
    if propagate_to_roles is not False:
        return
    # Only a truthy value is a live constraint; an explicit `False` sets
    # nothing and is as compatible with opting out as omitting the key.
    conflicting = sorted(key for key in PROPAGATION_REQUIRED_CONSTRAINT_KEYS if (constraints or {}).get(key))
    if not conflicting:
        return
    raise InvalidRequestError(
        f"A tag that sets {_join_phrases(conflicting)} must propagate to roles. "
        "Such a constraint only holds if it reaches roles associated with the tagged group: "
        "an owner it blocks could otherwise add themselves to one of those roles and receive "
        "the same access. Enable propagation to roles, or remove the constraint."
    )


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
        message. Names the group itself when no source contributes a truthy
        value -- callers only reach this after the constraint has resolved
        true, so that fallback means the value came from somewhere this
        function cannot attribute.

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


def _constraint_entry(
    constraint_key: str, constraint: TagConstraint, sources: list[ConstraintSource]
) -> Optional[dict[str, Any]]:
    """One constraint's coalesced value and the sources that produced it.

    A source setting a flag to `False` imposes nothing -- the tag form writes
    all four boolean keys on every save, so most tags carry several -- and a
    constraint every source turns off is not in force at all. Both are dropped,
    so a reader is never told a restriction applies when it does not, and a tag
    is never named as the reason for one it explicitly declines to impose.
    `constraint_source_clause` filters the same way for the same reason.

    Discriminated on `is not False` rather than on truthiness: only a boolean
    flag can be switched off, and a falsy *number* is the tightest possible
    limit rather than the absence of one.

    Returns:
        The entry, or None when nothing is left contributing.
    """
    contributing = [source for source in sources if source.value is not False]
    if not contributing:
        return None
    return {
        "constraint": constraint_key,
        "name": constraint.name,
        "value": _fold(constraint, contributing),
        "sources": [
            {
                "tag_id": source.tag.id,
                "tag_name": source.tag.name,
                "origin": source.origin,
                "source_id": source.source_id,
                "source_name": source.source_name,
            }
            for source in contributing
        ],
    }


def effective_constraints(group: OktaGroup) -> list[dict[str, Any]]:
    """Every constraint in force on `group`, with its coalesced value and sources.

    Backs the API response the UI reads, so display and enforcement answer from
    the same code.

    Args:
        group: The group to evaluate. Association reads only happen when this
            is a `RoleGroup`.

    Returns:
        One entry per constraint that anything sets, in `Tag.CONSTRAINTS`
        order, each a mapping of `constraint` (the key), `name` (its display
        name), `value` (coalesced across every source under that constraint's
        own rule), and `sources`. A source names the tag, how it reached the
        group (`origin`), and the app or group it came from -- `source_id` and
        `source_name`, both None for a `DIRECT` origin. A constraint nothing
        sets, and a flag every tag setting it turns off, are both omitted --
        so an untagged group returns an empty list, and so does one whose only
        tag declines every constraint.

    Raises:
        InvalidRequestError: If a relationship this reads was not eager-loaded.
            Provenance means this reads one relationship more than
            `effective_constraint` does -- `OktaGroupTagMap.active_app_tag_mapping`,
            supplied by `group_tag_map_options()` in `api/routers/_eager.py`.
    """
    entries = []
    for constraint_key, constraint in Tag.CONSTRAINTS.items():
        sources = constraint_sources(constraint_key, group, include_provenance=True)
        entry = _constraint_entry(constraint_key, constraint, sources)
        if entry is not None:
            entries.append(entry)
    return entries
