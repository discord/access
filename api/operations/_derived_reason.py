"""The two-part justification behind a role-derived grant.

When user U has access to group G because U is a member of role R and R is
associated with G, the `OktaUserGroupMember` materialized in G is not the
record of a decision anyone made about G directly. It exists because of two
separate decisions: someone put U in R, and someone attached R to G. Either one
alone is half the story, and a reader of G's audit log could not tell which
half they were looking at.

Each of the three sites that materializes such a row holds one half directly
and can reach the other, but each holds a *different* half -- `ModifyRoleGroups`
knows why the role was attached, `ModifyGroupUsers` knows why the user joined
the role, and `api.integrity` is repairing rows it did not create. Composing
here keeps the stored shape identical across all three.
"""

from __future__ import annotations

from typing import Optional

from api.models import OktaUserGroupMember

USER_IN_ROLE_PREFIX = "User in role because: "
ROLE_IN_GROUP_PREFIX = "Role in group because: "

#: Stands in for a half that was never recorded. A blank half is itself worth
#: seeing -- dropping the label instead would make a one-sided reason
#: indistinguishable from a row written before this composition existed.
MISSING_REASON_PLACEHOLDER = "(no reason given)"

_ELLIPSIS = "…"

# Read off the column rather than restated, so widening `created_reason` widens
# what fits here without a second edit. `TypeEngine.length` is Optional in
# SQLAlchemy's typing and the column declares `Unicode(1024)`, so the fallback
# is unreachable in practice -- it is there so an unbounded column would not
# put `None` into the arithmetic below.
_MAX_LENGTH: int = getattr(OktaUserGroupMember.__table__.c.created_reason.type, "length", None) or 1024


def _trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= len(_ELLIPSIS):
        return text[:limit]
    return text[: limit - len(_ELLIPSIS)] + _ELLIPSIS


def _fit(user_half: str, role_half: str, budget: int) -> tuple[str, str]:
    """Trim the halves to `budget` characters between them.

    Hands each half whatever slack the other leaves, so a short half never
    forces a long one down to an even split. Only when both exceed their share
    is the budget divided.
    """
    if len(user_half) + len(role_half) <= budget:
        return user_half, role_half
    share = budget // 2
    if len(user_half) <= share:
        return user_half, _trim(role_half, budget - len(user_half))
    if len(role_half) <= share:
        return _trim(user_half, budget - len(role_half)), role_half
    return _trim(user_half, share), _trim(role_half, budget - share)


def role_derived_reason(user_in_role_reason: Optional[str], role_in_group_reason: Optional[str]) -> str:
    """Compose the reason stored on a membership a role confers.

    Args:
        user_in_role_reason: Why the user is a member of the role.
        role_in_group_reason: Why the role is associated with the group.

    Returns:
        Both halves on their own labelled lines, trimmed to fit
        `OktaUserGroupMember.created_reason`. Empty when neither half was
        recorded -- two labels around two blanks is noise, not provenance.
    """
    user_half = (user_in_role_reason or "").strip()
    role_half = (role_in_group_reason or "").strip()
    if not user_half and not role_half:
        return ""

    user_half = user_half or MISSING_REASON_PLACEHOLDER
    role_half = role_half or MISSING_REASON_PLACEHOLDER

    # The labels and the separating newline are not negotiable; only the two
    # reasons compete for what is left.
    overhead = len(USER_IN_ROLE_PREFIX) + len(ROLE_IN_GROUP_PREFIX) + len("\n")
    user_half, role_half = _fit(user_half, role_half, _MAX_LENGTH - overhead)

    return f"{USER_IN_ROLE_PREFIX}{user_half}\n{ROLE_IN_GROUP_PREFIX}{role_half}"
