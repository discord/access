"""Failures in the Okta/notification task fan-out are logged, not swallowed.

The fan-out operations drain their `asyncio.create_task` lists through
`api.operations._fan_out.drain_fan_out_tasks`, which logs each failed task
with operation context instead of dropping the exception (the old
`asyncio.wait` idiom) — and never fails the request, because the local DB
state is committed before the tasks are awaited.
"""

import asyncio
import logging
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pytest_mock import MockerFixture
from sqlalchemy import select

from api.extensions import Db
from api.models import OktaGroup, OktaUser, OktaUserGroupMember
from api.operations import DeleteGroup
from api.operations._fan_out import drain_fan_out_tasks
from api.services import okta
from tests.helpers import db_count


async def test_drain_logs_failures_and_awaits_all_tasks(caplog: pytest.LogCaptureFixture) -> None:
    completed = []

    async def _ok() -> None:
        completed.append("ok")

    async def _boom() -> None:
        raise RuntimeError("fan-out task exploded")

    tasks = [asyncio.create_task(_boom()), asyncio.create_task(_ok())]
    with caplog.at_level(logging.WARNING, logger="api"):
        await drain_fan_out_tasks(tasks, "UnitTestOp for thing thing-1")

    # The failure neither propagated nor cancelled the sibling task.
    assert completed == ["ok"]
    failures = [r for r in caplog.records if "UnitTestOp for thing thing-1" in r.getMessage()]
    assert len(failures) == 1
    # WARNING, not ERROR: the syncer reconciles these, so they must not surface as Sentry noise.
    assert failures[0].levelno == logging.WARNING
    assert failures[0].exc_info is not None
    assert "fan-out task exploded" in str(failures[0].exc_info[1])


async def test_drain_no_tasks_is_a_noop(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="api"):
        await drain_fan_out_tasks([], "UnitTestOp for nothing")
    assert not [r for r in caplog.records if "UnitTestOp" in r.getMessage()]


async def test_request_cancellation_does_not_cancel_in_flight_tasks() -> None:
    # Cancelling the awaiting request (client disconnect, timeout, SIGTERM on a
    # rolling deploy) must NOT tear down the in-flight Okta calls — the DB change
    # they trail is already committed. `asyncio.gather` would cancel them;
    # `asyncio.wait` (what the helper uses) leaves them running.
    started = asyncio.Event()
    finished: list[bool] = []

    async def _slow() -> None:
        started.set()
        await asyncio.sleep(0.05)
        finished.append(True)

    task = asyncio.create_task(_slow())
    drain = asyncio.create_task(drain_fan_out_tasks([task], "CancelTest for x"))
    await started.wait()  # _slow is running and drain is now awaiting the tasks
    drain.cancel()
    with pytest.raises(asyncio.CancelledError):
        await drain

    # The in-flight task was left alone and runs to completion.
    await task
    assert finished == [True]
    assert not task.cancelled()


async def test_failing_okta_call_is_logged_and_does_not_fail_request(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    user: OktaUser,
    okta_group: OktaGroup,
    url_for: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    db.session.add(okta_group)
    db.session.add(user)
    await db.session.commit()

    mocker.patch.object(okta, "add_user_to_group", side_effect=Exception("okta add blew up"))
    add_owner_spy = mocker.patch.object(okta, "add_owner_to_group")
    mocker.patch.object(okta, "remove_user_from_group")
    mocker.patch.object(okta, "remove_owner_from_group")

    data: dict[str, Any] = {
        "members_to_add": [user.id],
        "owners_to_add": [user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=okta_group.id)
    with caplog.at_level(logging.WARNING, logger="api"):
        rep = await client.put(group_url, json=data)

    # The Okta failure does not fail the request - the membership committed.
    assert rep.status_code == 200
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.user_id == user.id)
            .where(OktaUserGroupMember.group_id == okta_group.id)
            .where(OktaUserGroupMember.ended_at.is_(None)),
        )
        == 2
    )
    # The sibling owner-add task still ran.
    assert add_owner_spy.call_count == 1

    failures = [r for r in caplog.records if f"ModifyGroupUsers for group {okta_group.id}" in r.getMessage()]
    assert len(failures) == 1
    assert failures[0].levelno == logging.WARNING
    assert failures[0].exc_info is not None
    assert "okta add blew up" in str(failures[0].exc_info[1])


async def test_failing_okta_delete_is_logged_and_group_still_deleted(
    db: Db,
    mocker: MockerFixture,
    okta_group: OktaGroup,
    caplog: pytest.LogCaptureFixture,
) -> None:
    db.session.add(okta_group)
    await db.session.commit()
    group_id = okta_group.id

    mocker.patch.object(okta, "delete_group", side_effect=Exception("okta delete blew up"))
    mocker.patch.object(okta, "remove_user_from_group")
    mocker.patch.object(okta, "remove_owner_from_group")

    with caplog.at_level(logging.WARNING, logger="api"):
        await DeleteGroup(group=group_id).execute()

    # The local soft-delete committed despite the Okta failure.
    db.session.expire_all()
    deleted_group = await db.session.get(OktaGroup, group_id)
    assert deleted_group is not None
    assert deleted_group.deleted_at is not None

    failures = [r for r in caplog.records if f"DeleteGroup for group {group_id}" in r.getMessage()]
    assert len(failures) == 1
    assert failures[0].levelno == logging.WARNING
    assert failures[0].exc_info is not None
    assert "okta delete blew up" in str(failures[0].exc_info[1])
