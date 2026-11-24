"""Audit logs for the operations that end access in bulk: `DeleteUser` and `UnmanageGroup`."""

import json
from typing import Any

import pytest
from pytest_mock import MockerFixture
from sqlalchemy import select

from api.config import settings
from api.extensions import Db
from api.models import OktaGroup, OktaUser, OktaUserGroupMember, RoleGroup
from api.operations import DeleteUser, ModifyGroupUsers, ModifyRoleGroups, UnmanageGroup
from api.services import okta


@pytest.fixture(autouse=True)
def _mock_okta(mocker: MockerFixture) -> None:
    # Keep the operations off the network.
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")
    mocker.patch.object(okta, "remove_user_from_group")
    mocker.patch.object(okta, "remove_owner_from_group")


async def _access_owner(db: Db) -> OktaUser:
    owner = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()
    assert owner is not None
    return owner


def _audit_events(caplog: pytest.LogCaptureFixture, event_type: str) -> list[dict[str, Any]]:
    """Return the decoded `access.audit` entries of one event type."""
    entries = [json.loads(r.getMessage()) for r in caplog.records if r.name == "access.audit"]
    return [e for e in entries if e.get("event_type") == event_type]


async def test_delete_user_logs_ended_access(
    db: Db, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup, caplog: pytest.LogCaptureFixture
) -> None:
    db.session.add_all([user, okta_group, role_group])
    await db.session.commit()
    await ModifyGroupUsers(group=okta_group, members_to_add=[user.id], sync_to_okta=False).execute()
    await ModifyGroupUsers(group=role_group, owners_to_add=[user.id], sync_to_okta=False).execute()
    user_id, user_email = user.id, user.email
    okta_group_id, role_group_id = okta_group.id, role_group.id

    with caplog.at_level("INFO", logger="access.audit"):
        # No actor, as the syncer calls it.
        await DeleteUser(user=user_id, sync_to_okta=False).execute()

    [event] = _audit_events(caplog, "USER_DELETE")
    assert event["current_user_id"] is None
    assert event["current_user_email"] is None
    assert event["user"] == {"id": user_id, "email": user_email}
    assert event["memberships_ended"] == [{"user_id": user_id, "group_id": okta_group_id}]
    assert event["ownerships_ended"] == [{"user_id": user_id, "group_id": role_group_id}]


async def test_delete_user_records_actor_when_given(db: Db, user: OktaUser, caplog: pytest.LogCaptureFixture) -> None:
    db.session.add(user)
    await db.session.commit()
    owner = await _access_owner(db)
    owner_id, owner_email = owner.id, owner.email

    with caplog.at_level("INFO", logger="access.audit"):
        await DeleteUser(user=user.id, sync_to_okta=False, current_user_id=owner_id).execute()

    [event] = _audit_events(caplog, "USER_DELETE")
    assert event["current_user_id"] == owner_id
    assert event["current_user_email"] == owner_email
    assert event["memberships_ended"] == []


async def test_delete_user_cleanup_of_deleted_user_uses_its_own_event(
    db: Db, user: OktaUser, okta_group: OktaGroup, caplog: pytest.LogCaptureFixture
) -> None:
    db.session.add_all([user, okta_group])
    await db.session.commit()
    user_id, okta_group_id = user.id, okta_group.id
    await DeleteUser(user=user_id, sync_to_okta=False).execute()
    # A deleted user still holds access, which the syncer's cleanup pass then ends.
    db.session.add(OktaUserGroupMember(user_id=user_id, group_id=okta_group_id))
    await db.session.commit()

    caplog.clear()
    with caplog.at_level("INFO", logger="access.audit"):
        await DeleteUser(user=user_id, sync_to_okta=False).execute()

    assert _audit_events(caplog, "USER_DELETE") == []
    [event] = _audit_events(caplog, "DELETED_USER_ACCESS_END")
    assert event["memberships_ended"] == [{"user_id": user_id, "group_id": okta_group_id}]


async def test_delete_user_cleanup_with_nothing_to_end_logs_nothing(
    db: Db, user: OktaUser, caplog: pytest.LogCaptureFixture
) -> None:
    db.session.add(user)
    await db.session.commit()
    await DeleteUser(user=user.id, sync_to_okta=False).execute()

    caplog.clear()
    with caplog.at_level("INFO", logger="access.audit"):
        await DeleteUser(user=user.id, sync_to_okta=False).execute()

    assert _audit_events(caplog, "USER_DELETE") == []
    assert _audit_events(caplog, "DELETED_USER_ACCESS_END") == []


async def test_unmanage_group_logs_ended_role_access(
    db: Db, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup, caplog: pytest.LogCaptureFixture
) -> None:
    db.session.add_all([user, okta_group, role_group])
    await db.session.commit()
    await ModifyGroupUsers(group=role_group, members_to_add=[user.id], sync_to_okta=False).execute()
    await ModifyRoleGroups(role_group=role_group, groups_to_add=[okta_group.id], sync_to_okta=False).execute()
    user_id, okta_group_id, okta_group_name, role_group_id = user.id, okta_group.id, okta_group.name, role_group.id
    # Group transitions to unmanaged, as the syncer does before UnmanageGroup.
    okta_group.is_managed = False
    await db.session.commit()

    with caplog.at_level("INFO", logger="access.audit"):
        await UnmanageGroup(group=okta_group_id).execute()

    [event] = _audit_events(caplog, "GROUP_UNMANAGE")
    # No actor, as the syncer and the integrity check call it.
    assert event["current_user_id"] is None
    assert event["current_user_email"] is None
    assert {k: event["group"][k] for k in ("id", "name", "type")} == {
        "id": okta_group_id,
        "name": okta_group_name,
        "type": "okta_group",
    }
    assert event["memberships_ended"] == [{"user_id": user_id, "group_id": okta_group_id}]
    assert event["ownerships_ended"] == []
    assert event["role_memberships_ended"] == [{"role_group_id": role_group_id, "group_id": okta_group_id}]
    assert event["role_ownerships_ended"] == []


async def test_unmanage_group_dry_run_logs_nothing(
    db: Db, okta_group: OktaGroup, caplog: pytest.LogCaptureFixture
) -> None:
    okta_group.is_managed = False
    db.session.add(okta_group)
    await db.session.commit()

    with caplog.at_level("INFO", logger="access.audit"):
        await UnmanageGroup(group=okta_group.id).execute(dry_run=True)

    assert _audit_events(caplog, "GROUP_UNMANAGE") == []


async def test_unmanage_group_with_nothing_to_end_logs_nothing(
    db: Db, okta_group: OktaGroup, caplog: pytest.LogCaptureFixture
) -> None:
    okta_group.is_managed = False
    db.session.add(okta_group)
    await db.session.commit()

    with caplog.at_level("INFO", logger="access.audit"):
        await UnmanageGroup(group=okta_group.id).execute()

    assert _audit_events(caplog, "GROUP_UNMANAGE") == []
