"""DeleteApp rejects pending GroupRequests that target the deleted app.

A pending app-group request for a deleted app can never be approved (the app is
gone), and an app owner loses the standing to reject it once the app's owner
group is deleted, so only an Access admin could clear it from approvers' queues.
"""

import pytest
from pytest_mock import MockerFixture
from sqlalchemy import select

from api.config import settings
from api.extensions import Db
from api.models import AccessRequestStatus, App, AppGroup, GroupRequest, OktaUser
from api.operations import CreateGroupRequest, DeleteApp
from api.plugins import get_notification_hook
from api.services import okta
from tests.factories import AppFactory, AppGroupFactory, OktaUserFactory, OktaUserGroupMemberFactory

DELETION_REASON = "Closed because the requested app was deleted"


@pytest.fixture(autouse=True)
def _mock_okta(mocker: MockerFixture) -> None:
    # Keep the app group deletions off the network.
    mocker.patch.object(okta, "delete_group")
    mocker.patch.object(okta, "remove_user_from_group")
    mocker.patch.object(okta, "remove_owner_from_group")


async def _access_owner(db: Db) -> OktaUser:
    owner = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()
    assert owner is not None
    return owner


async def _app_with_owner() -> tuple[App, OktaUser]:
    app_obj = await AppFactory.create_async()
    owner_group = await AppGroupFactory.create_async(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_obj.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        app_id=app_obj.id,
        is_owner=True,
    )
    app_owner = await OktaUserFactory.create_async()
    await OktaUserGroupMemberFactory.create_async(user_id=app_owner.id, group_id=owner_group.id, is_owner=True)
    return app_obj, app_owner


async def _pending_app_group_request(requester: OktaUser, app_obj: App) -> GroupRequest:
    group_request = await CreateGroupRequest(
        requester_user=requester,
        requested_group_name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_obj.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}New",
        requested_group_description="New app group",
        requested_group_type="app_group",
        requested_app_id=app_obj.id,
        request_reason="need an app group",
    ).execute()
    assert group_request is not None
    # Widened local so the precondition check doesn't narrow the attribute type
    # and make the post-refresh REJECTED assertion look non-overlapping to ty.
    created_status: AccessRequestStatus = group_request.status
    assert created_status == AccessRequestStatus.PENDING
    return group_request


async def test_delete_app_by_admin_rejects_pending_group_request(db: Db, user: OktaUser, mocker: MockerFixture) -> None:
    db.session.add(user)
    await db.session.commit()
    app_obj, app_owner = await _app_with_owner()
    group_request = await _pending_app_group_request(user, app_obj)
    admin = await _access_owner(db)
    completed_spy = mocker.patch.object(get_notification_hook(), "access_group_request_completed")

    await DeleteApp(app=app_obj, current_user_id=admin.id).execute()

    await db.session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.REJECTED
    assert group_request.resolved_at is not None
    assert group_request.resolver_user_id == admin.id
    assert group_request.resolution_reason == DELETION_REASON
    assert completed_spy.call_count == 1
    # The app owners were the request's reviewers, so they hear that it closed.
    assert [u.id for u in completed_spy.call_args.kwargs["approvers"]] == [app_owner.id]


async def test_delete_app_by_app_owner_rejects_pending_group_request(db: Db, user: OktaUser) -> None:
    """An app owner may delete their app; the rejection must still land under their name."""
    db.session.add(user)
    await db.session.commit()
    app_obj, app_owner = await _app_with_owner()
    group_request = await _pending_app_group_request(user, app_obj)

    await DeleteApp(app=app_obj, current_user_id=app_owner.id).execute()

    await db.session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.REJECTED
    assert group_request.resolver_user_id == app_owner.id
    assert group_request.resolution_reason == DELETION_REASON


async def test_delete_app_leaves_other_apps_group_requests_pending(db: Db, user: OktaUser) -> None:
    db.session.add(user)
    await db.session.commit()
    deleted_app, _ = await _app_with_owner()
    other_app, _ = await _app_with_owner()
    other_request = await _pending_app_group_request(user, other_app)

    await DeleteApp(app=deleted_app, current_user_id=(await _access_owner(db)).id).execute()

    await db.session.refresh(other_request)
    assert other_request.status == AccessRequestStatus.PENDING
    assert other_request.resolved_at is None
