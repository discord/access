"""Audit logs for the operations that end access in bulk: `DeleteUser` and `UnmanageGroup`."""

import json
from typing import Any

import pytest
from pytest_mock import MockerFixture
from sqlalchemy import select

from api.config import settings
from api.extensions import Db
from api.models import OktaGroup, OktaUser, RoleGroup
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
    owner = await _access_owner(db)
    owner_id, owner_email = owner.id, owner.email

    with caplog.at_level("INFO", logger="access.audit"):
        await DeleteUser(user=user_id, sync_to_okta=False, current_user_id=owner_id).execute()

    [event] = _audit_events(caplog, "USER_DELETE")
    assert event["current_user_id"] == owner_id
    assert event["current_user_email"] == owner_email
    assert event["user"] == {"id": user_id, "email": user_email}
    assert event["memberships_ended"] == [{"user_id": user_id, "group_id": okta_group_id}]
    assert event["ownerships_ended"] == [{"user_id": user_id, "group_id": role_group_id}]


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
