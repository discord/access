"""A role-derived grant records both halves of why it exists.

When user U has access to group G because U is a member of role R and R is
associated with G, the `OktaUserGroupMember` materialized in G used to carry
whichever single reason happened to be in hand at the operation that created
it -- the reason U was added to R, or the reason R was attached to G, depending
on which came second. Each is half the justification, and a reader of G's audit
log had no way to tell which half they were looking at.
"""

from pytest_mock import MockerFixture
from sqlalchemy import select

from api.extensions import Db
from api.models import OktaGroup, OktaUser, OktaUserGroupMember, RoleGroup
from api.integrity import verify_and_fix_role_memberships
from api.operations import ModifyGroupUsers, ModifyRoleGroups
from api.operations._derived_reason import (
    MISSING_REASON_PLACEHOLDER,
    ROLE_IN_GROUP_PREFIX,
    USER_IN_ROLE_PREFIX,
    role_derived_reason,
)
from api.services import okta
from tests.factories import OktaUserGroupMemberFactory, RoleGroupMapFactory

USER_IN_ROLE = "Q3 audit rotation"
ROLE_IN_GROUP = "role needs ledger export"
COMPOSED = f"{USER_IN_ROLE_PREFIX}{USER_IN_ROLE}\n{ROLE_IN_GROUP_PREFIX}{ROLE_IN_GROUP}"


async def _derived_row(db: Db, group_id: str, user_id: str) -> OktaUserGroupMember:
    return (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == group_id)
            .where(OktaUserGroupMember.user_id == user_id)
            .where(OktaUserGroupMember.role_group_map_id.is_not(None))
        )
    ).one()


# --- The helper ------------------------------------------------------------


def test_role_derived_reason_composes_both_halves() -> None:
    assert role_derived_reason(USER_IN_ROLE, ROLE_IN_GROUP) == COMPOSED


def test_role_derived_reason_is_empty_when_neither_half_was_given() -> None:
    """Two labels around two blanks is noise, not provenance."""
    assert role_derived_reason("", "") == ""
    assert role_derived_reason(None, None) == ""


def test_role_derived_reason_marks_a_missing_half_rather_than_dropping_it() -> None:
    """A blank half is itself a finding, so it stays visible: dropping the
    label would make a one-sided reason indistinguishable from an
    uncomposed one."""
    assert role_derived_reason(USER_IN_ROLE, "") == (
        f"{USER_IN_ROLE_PREFIX}{USER_IN_ROLE}\n{ROLE_IN_GROUP_PREFIX}{MISSING_REASON_PLACEHOLDER}"
    )
    assert role_derived_reason("", ROLE_IN_GROUP) == (
        f"{USER_IN_ROLE_PREFIX}{MISSING_REASON_PLACEHOLDER}\n{ROLE_IN_GROUP_PREFIX}{ROLE_IN_GROUP}"
    )


def test_role_derived_reason_fits_the_column_when_both_halves_are_long() -> None:
    """Each half can already be column-length on its own, so the composition
    can overflow. Both labels must survive the trim -- a truncation that ate
    the second half would silently restore the one-sided reason this change
    exists to remove."""
    composed = role_derived_reason("a" * 900, "b" * 900)

    assert len(composed) <= OktaUserGroupMember.__table__.c.created_reason.type.length
    assert composed.startswith(USER_IN_ROLE_PREFIX)
    assert ROLE_IN_GROUP_PREFIX in composed
    assert "a" in composed and "b" in composed


def test_role_derived_reason_gives_a_long_half_the_slack_a_short_one_leaves() -> None:
    """Splitting the budget evenly would truncate a long half while a short
    one left room unused."""
    composed = role_derived_reason("a" * 2000, "short")

    assert len(composed) <= OktaUserGroupMember.__table__.c.created_reason.type.length
    assert composed.endswith(f"{ROLE_IN_GROUP_PREFIX}short")
    # Well past an even split of the ~980 available characters.
    assert composed.count("a") > 700


def test_role_derived_reason_leaves_a_short_composition_untouched() -> None:
    assert role_derived_reason("x", "y") == f"{USER_IN_ROLE_PREFIX}x\n{ROLE_IN_GROUP_PREFIX}y"


# --- ModifyRoleGroups: the role is attached to the group second ------------


async def test_attaching_a_role_composes_the_reason_for_existing_role_members(
    db: Db, role_group: RoleGroup, okta_group: OktaGroup, user: OktaUser
) -> None:
    db.session.add_all([user, role_group, okta_group])
    await db.session.commit()

    await ModifyGroupUsers(
        group=role_group.id, members_to_add=[user.id], created_reason=USER_IN_ROLE, sync_to_okta=False
    ).execute()
    await ModifyRoleGroups(
        role_group=role_group.id, groups_to_add=[okta_group.id], created_reason=ROLE_IN_GROUP, sync_to_okta=False
    ).execute()

    assert (await _derived_row(db, okta_group.id, user.id)).created_reason == COMPOSED


async def test_attaching_a_role_as_owner_composes_the_reason(
    db: Db, role_group: RoleGroup, okta_group: OktaGroup, user: OktaUser
) -> None:
    """The ownership branch materializes its own rows and needs the same
    composition; only the member branch would be covered otherwise."""
    db.session.add_all([user, role_group, okta_group])
    await db.session.commit()

    await ModifyGroupUsers(
        group=role_group.id, members_to_add=[user.id], created_reason=USER_IN_ROLE, sync_to_okta=False
    ).execute()
    await ModifyRoleGroups(
        role_group=role_group.id, owner_groups_to_add=[okta_group.id], created_reason=ROLE_IN_GROUP, sync_to_okta=False
    ).execute()

    derived = await _derived_row(db, okta_group.id, user.id)
    assert derived.is_owner is True
    assert derived.created_reason == COMPOSED


async def test_attaching_a_role_leaves_the_direct_role_membership_reason_alone(
    db: Db, role_group: RoleGroup, okta_group: OktaGroup, user: OktaUser
) -> None:
    """Only the derived row tells a two-part story. U's membership of R is a
    direct grant with a single reason and must stay verbatim."""
    db.session.add_all([user, role_group, okta_group])
    await db.session.commit()

    await ModifyGroupUsers(
        group=role_group.id, members_to_add=[user.id], created_reason=USER_IN_ROLE, sync_to_okta=False
    ).execute()
    await ModifyRoleGroups(
        role_group=role_group.id, groups_to_add=[okta_group.id], created_reason=ROLE_IN_GROUP, sync_to_okta=False
    ).execute()

    direct = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == role_group.id)
            .where(OktaUserGroupMember.user_id == user.id)
        )
    ).one()
    assert direct.created_reason == USER_IN_ROLE


# --- ModifyGroupUsers: the user joins the role second ----------------------


async def test_adding_a_user_to_an_attached_role_composes_the_reason(
    db: Db, role_group: RoleGroup, okta_group: OktaGroup, user: OktaUser
) -> None:
    """The mirror image of the case above: here the role association already
    exists and supplies the half the operation does not hold."""
    db.session.add_all([user, role_group, okta_group])
    await db.session.commit()

    await ModifyRoleGroups(
        role_group=role_group.id, groups_to_add=[okta_group.id], created_reason=ROLE_IN_GROUP, sync_to_okta=False
    ).execute()
    await ModifyGroupUsers(
        group=role_group.id, members_to_add=[user.id], created_reason=USER_IN_ROLE, sync_to_okta=False
    ).execute()

    assert (await _derived_row(db, okta_group.id, user.id)).created_reason == COMPOSED


async def test_adding_a_user_to_a_group_directly_keeps_a_single_reason(
    db: Db, okta_group: OktaGroup, user: OktaUser
) -> None:
    """A direct grant has one half and must not gain the two-part shape."""
    db.session.add_all([user, okta_group])
    await db.session.commit()

    await ModifyGroupUsers(
        group=okta_group.id, members_to_add=[user.id], created_reason="direct grant", sync_to_okta=False
    ).execute()

    membership = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == okta_group.id)
            .where(OktaUserGroupMember.user_id == user.id)
        )
    ).one()
    assert membership.created_reason == "direct grant"


# --- The integrity repair job ----------------------------------------------


async def test_repairing_a_missing_role_membership_records_both_halves(
    db: Db, mocker: MockerFixture, role_group: RoleGroup, okta_group: OktaGroup, user: OktaUser
) -> None:
    """`verify_and_fix_role_memberships` materializes rows the operations
    failed to write, and recorded no reason at all for them. Both halves are
    on hand there -- the user's role membership and the association mapping --
    so a repaired row carries the same provenance as one written normally."""
    db.session.add_all([user, role_group, okta_group])
    await OktaUserGroupMemberFactory.create_async(user_id=user.id, group_id=role_group.id, created_reason=USER_IN_ROLE)
    role_group_map = await RoleGroupMapFactory.create_async(
        role_group_id=role_group.id, group_id=okta_group.id, is_owner=False, created_reason=ROLE_IN_GROUP
    )
    await db.session.commit()

    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    await verify_and_fix_role_memberships()

    repaired = (
        await db.session.scalars(
            select(OktaUserGroupMember).where(OktaUserGroupMember.role_group_map_id == role_group_map.id)
        )
    ).one()
    assert repaired.created_reason == COMPOSED
