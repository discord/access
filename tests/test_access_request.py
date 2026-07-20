from datetime import datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from pytest_mock import MockerFixture
from fastapi import FastAPI

from api.config import settings
from api.extensions import Db
from api.models import (
    AccessRequest,
    AccessRequestStatus,
    App,
    AppGroup,
    OktaGroup,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    Tag,
)
from api.models.access_request import get_all_possible_request_approvers
from api.operations import (
    ApproveAccessRequest,
    CreateAccessRequest,
    ModifyGroupUsers,
    ModifyRoleGroups,
    RejectAccessRequest,
)
from api.plugins import ConditionalAccessResponse, get_notification_hook
from api.services import okta
from tests.factories import (
    AccessRequestFactory,
    AppGroupFactory,
    OktaGroupFactory,
    OktaGroupTagMapFactory,
    OktaUserFactory,
)
from tests.helpers import db_count
from tests.request_factories import CreateAccessRequestBodyFactory, ResolveAccessRequestBodyFactory

SEVEN_DAYS_IN_SECONDS = 7 * 24 * 60 * 60
THREE_DAYS_IN_SECONDS = 3 * 24 * 60 * 60
ONE_DAY_IN_SECONDS = 24 * 60 * 60


async def test_get_access_request(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    okta_group: OktaGroup,
    role_group: RoleGroup,
    user: OktaUser,
    url_for: Any,
) -> None:
    # test 404
    access_request_url = url_for("api-access-requests.access_request_by_id", access_request_id="randomid")
    rep = await client.get(access_request_url)
    assert rep.status_code == 404

    db.session.add(user)
    db.session.add(okta_group)
    db.session.add(role_group)
    await db.session.commit()

    await ModifyRoleGroups(
        role_group=role_group, groups_to_add=[okta_group.id], owner_groups_to_add=[okta_group.id], sync_to_okta=False
    ).execute()

    okta_group_access_request = await CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()
    assert okta_group_access_request is not None

    role_group_access_request = await CreateAccessRequest(
        requester_user=user,
        requested_group=role_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()
    assert role_group_access_request is not None

    # test get okta group access_request
    access_request_url = url_for(
        "api-access-requests.access_request_by_id", access_request_id=okta_group_access_request.id
    )
    rep = await client.get(access_request_url)
    assert rep.status_code == 200

    data = rep.json()
    assert data["requester"]["email"] == user.email
    assert data["requested_group"]["name"] == okta_group.name
    assert data["status"] == okta_group_access_request.status
    assert data["request_reason"] == okta_group_access_request.request_reason
    assert data["request_ownership"] == okta_group_access_request.request_ownership

    access_owner = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()

    add_user_to_group_spy = mocker.patch.object(okta, "add_user_to_group")
    add_owner_to_group_spy = mocker.patch.object(okta, "add_owner_to_group")

    okta_group_access_request = await ApproveAccessRequest(
        access_request=okta_group_access_request, approver_user=access_owner
    ).execute()

    assert add_user_to_group_spy.call_count == 1
    assert add_owner_to_group_spy.call_count == 0

    rep = await client.get(access_request_url)
    assert rep.status_code == 200

    data = rep.json()
    assert data["requester"]["email"] == user.email
    assert data["requested_group"]["name"] == okta_group.name
    assert data["status"] == okta_group_access_request.status
    assert data["request_reason"] == okta_group_access_request.request_reason
    assert data["request_ownership"] == okta_group_access_request.request_ownership

    add_user_to_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()

    access_request_url = url_for(
        "api-access-requests.access_request_by_id", access_request_id=role_group_access_request.id
    )
    rep = await client.get(access_request_url)
    assert rep.status_code == 200

    data = rep.json()
    assert data["requester"]["email"] == user.email
    assert data["requested_group"]["name"] == role_group.name
    assert data["status"] == role_group_access_request.status
    assert data["request_reason"] == role_group_access_request.request_reason
    assert data["request_ownership"] == role_group_access_request.request_ownership

    access_owner = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()

    add_user_to_group_spy = mocker.patch.object(okta, "add_user_to_group")
    add_owner_to_group_spy = mocker.patch.object(okta, "add_owner_to_group")

    role_group_access_request = await ApproveAccessRequest(
        access_request=role_group_access_request, approver_user=access_owner
    ).execute()

    assert add_user_to_group_spy.call_count == 2
    assert add_owner_to_group_spy.call_count == 1

    rep = await client.get(access_request_url)
    assert rep.status_code == 200

    data = rep.json()
    assert data["requester"]["email"] == user.email
    assert data["requested_group"]["name"] == role_group.name
    assert data["status"] == role_group_access_request.status
    assert data["request_reason"] == role_group_access_request.request_reason
    assert data["request_ownership"] == role_group_access_request.request_ownership


async def test_put_access_request(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    access_request: AccessRequest,
    okta_group: OktaGroup,
    user: OktaUser,
    url_for: Any,
) -> None:
    # test 404 — PUT against a non-existent access_request_id with a valid
    # body shape returns 404. (PUT with no body returns 400 because the
    # ResolveAccessRequestBody schema rejects the missing `approved` field.)
    access_request_url = url_for("api-access-requests.access_request_by_id", access_request_id="randomid")
    rep = await client.put(access_request_url, json=ResolveAccessRequestBodyFactory.json(approved=True))
    assert rep.status_code == 404

    db.session.add(user)
    db.session.add(okta_group)
    await db.session.commit()
    access_request.requested_group_id = okta_group.id
    access_request.requester_user_id = user.id
    db.session.add(access_request)
    await db.session.commit()

    # test missing data
    data: dict[str, Any] = {}
    access_request_url = url_for("api-access-requests.access_request_by_id", access_request_id=access_request.id)
    rep = await client.put(access_request_url, json=data)
    assert rep.status_code == 400

    # test update access_request
    add_user_to_group_spy = mocker.patch.object(okta, "add_user_to_group")
    add_owner_to_group_spy = mocker.patch.object(okta, "add_owner_to_group")

    data = ResolveAccessRequestBodyFactory.json(approved=True, reason="test reason")

    rep = await client.put(access_request_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert add_owner_to_group_spy.call_count == 0

    access_owner = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()

    data = rep.json()
    assert data["requester"]["email"] == user.email
    assert data["requested_group"]["name"] == okta_group.name
    assert access_request.status == AccessRequestStatus.APPROVED
    assert data["status"] == access_request.status
    assert data["request_reason"] == access_request.request_reason
    assert data["request_ownership"] == access_request.request_ownership
    assert data["resolver"]["email"] == access_owner.email
    assert data["resolution_reason"] == access_request.resolution_reason

    assert await db_count(db.session, select(OktaUserGroupMember).where(OktaUserGroupMember.ended_at.is_(None))) == 2

    access_request2 = await CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=True,
        request_reason="test reason",
    ).execute()
    assert access_request2 is not None
    access_request = access_request2

    add_user_to_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    data = ResolveAccessRequestBodyFactory.json(approved=False, reason="test reason")

    access_request_url = url_for("api-access-requests.access_request_by_id", access_request_id=access_request.id)
    rep = await client.put(access_request_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0

    access_owner = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()

    data = rep.json()
    assert data["requester"]["email"] == user.email
    assert data["requested_group"]["name"] == okta_group.name
    assert access_request.status == AccessRequestStatus.REJECTED
    assert data["status"] == access_request.status
    assert data["request_reason"] == access_request.request_reason
    assert data["request_ownership"] == access_request.request_ownership
    assert data["resolver"]["email"] == access_owner.email
    assert data["resolution_reason"] == access_request.resolution_reason

    assert await db_count(db.session, select(OktaUserGroupMember).where(OktaUserGroupMember.ended_at.is_(None))) == 2


async def test_put_access_request_by_non_owner(
    client: AsyncClient, app: FastAPI, db: Db, okta_group: OktaGroup, user: OktaUser, url_for: Any
) -> None:
    access_owner = settings.CURRENT_OKTA_USER_EMAIL

    db.session.add(user)
    db.session.add(okta_group)
    await db.session.commit()

    access_request_by_owner = await CreateAccessRequest(
        requester_user=(await db.session.scalars(select(OktaUser).where(OktaUser.email == access_owner))).first(),
        requested_group=okta_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    access_request_by_non_owner = await CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    await db.session.commit()

    assert await db_count(db.session, select(OktaUserGroupMember).where(OktaUserGroupMember.ended_at.is_(None))) == 1

    assert access_request_by_owner is not None
    assert access_request_by_owner.status == AccessRequestStatus.PENDING
    assert access_request_by_non_owner is not None
    assert access_request_by_non_owner.status == AccessRequestStatus.PENDING

    data: dict[str, Any] = {}
    app.state.current_user_email = user.email

    access_request_url = url_for(
        "api-access-requests.access_request_by_id", access_request_id=access_request_by_owner.id
    )
    data = ResolveAccessRequestBodyFactory.json(approved=True, reason="test approval")
    rep = await client.put(access_request_url, json=data)
    assert rep.status_code == 403

    # The failed request's rollback on the shared session expired the
    # identity map; reload before reading ORM attributes.
    await db.session.refresh(access_request_by_non_owner)
    assert access_request_by_non_owner.status == AccessRequestStatus.PENDING
    assert access_request_by_non_owner.resolved_at is None
    assert access_request_by_non_owner.resolver_user_id is None

    assert await db_count(db.session, select(OktaUserGroupMember).where(OktaUserGroupMember.ended_at.is_(None))) == 1

    data = ResolveAccessRequestBodyFactory.json(approved=False, reason="test rejection")

    rep = await client.put(access_request_url, json=data)
    assert rep.status_code == 403

    await db.session.refresh(access_request_by_non_owner)
    assert access_request_by_non_owner.status == AccessRequestStatus.PENDING
    assert access_request_by_non_owner.resolved_at is None
    assert access_request_by_non_owner.resolver_user_id is None

    assert await db_count(db.session, select(OktaUserGroupMember).where(OktaUserGroupMember.ended_at.is_(None))) == 1

    access_request_url = url_for(
        "api-access-requests.access_request_by_id", access_request_id=access_request_by_non_owner.id
    )

    data = ResolveAccessRequestBodyFactory.json(approved=True, reason="test approval")
    rep = await client.put(access_request_url, json=data)
    assert rep.status_code == 403

    await db.session.refresh(access_request_by_non_owner)
    assert access_request_by_non_owner.status == AccessRequestStatus.PENDING
    assert access_request_by_non_owner.resolved_at is None
    assert access_request_by_non_owner.resolver_user_id is None

    assert await db_count(db.session, select(OktaUserGroupMember).where(OktaUserGroupMember.ended_at.is_(None))) == 1

    data = ResolveAccessRequestBodyFactory.json(approved=False, reason="test rejection")

    rep = await client.put(access_request_url, json=data)
    assert rep.status_code == 200

    # Re-fetch: the reject's deferred completion notification expunged the
    # request from the shared session (a plain attribute read would fail).
    access_request_by_non_owner = await db.session.get(AccessRequest, access_request_by_non_owner.id)
    data = rep.json()
    assert data["requester"]["email"] == user.email
    assert data["requested_group"]["name"] == okta_group.name
    assert data["status"] == AccessRequestStatus.REJECTED
    assert data["request_reason"] == access_request_by_non_owner.request_reason
    assert data["request_ownership"] == access_request_by_non_owner.request_ownership
    assert data["resolver"]["email"] == user.email
    assert data["resolution_reason"] == access_request_by_non_owner.resolution_reason

    assert access_request_by_non_owner.resolved_at is not None
    assert access_request_by_non_owner.resolver_user_id == user.id

    assert await db_count(db.session, select(OktaUserGroupMember).where(OktaUserGroupMember.ended_at.is_(None))) == 1


async def test_put_app_group_access_request_by_app_owner(
    client: AsyncClient,
    app: FastAPI,
    db: Db,
    access_app: App,
    app_group: AppGroup,
    user: OktaUser,
    url_for: Any,
) -> None:
    """Regression for the production MissingGreenlet after the async migration (#480).

    Resolving an *app group* request as a non-requester falls through
    `can_manage_group` into `is_app_owner_group_owner`, which reads
    `AppGroup.app_id`. If the requested group is loaded with only its base
    `OktaGroup` columns, that read triggers an implicit lazy load of the
    `app_group` subclass columns — illegal under async SQLAlchemy, so the PUT
    500s instead of resolving. `test_put_access_request_by_non_owner` never
    caught it because a plain `OktaGroup` short-circuits before `app_id` is
    touched; only an `AppGroup` reaches the lazy load."""
    app_owner_user = OktaUserFactory.build()
    app_owner_group = AppGroupFactory.build()

    db.session.add(user)
    db.session.add(app_owner_user)
    db.session.add(access_app)
    await db.session.commit()

    # The requested group is a non-owner group on the app.
    app_group.app_id = access_app.id
    app_group.is_owner = False
    db.session.add(app_group)

    # A separate owner group for the same app; its owners can manage the
    # app's groups without being direct owners of `app_group`.
    app_owner_group.app_id = access_app.id
    app_owner_group.is_owner = True
    db.session.add(app_owner_group)
    await db.session.commit()

    await ModifyGroupUsers(
        group=app_owner_group,
        members_to_add=[],
        owners_to_add=[app_owner_user.id],
        sync_to_okta=False,
    ).execute()

    access_request = await CreateAccessRequest(
        requester_user=user,
        requested_group=app_group,
        request_ownership=False,
        request_reason="please grant",
    ).execute()
    assert access_request is not None
    assert access_request.status == AccessRequestStatus.PENDING

    access_request_id = access_request.id
    app_owner_user_id = app_owner_user.id

    # Evict everything the setup loaded. The HTTP request reuses this session,
    # so a warm identity map would serve the fully-populated AppGroup and hide
    # the missing subclass load. Production gives each request a cold session,
    # where the group arrives with only base columns — `expunge_all` reproduces
    # that so the lazy load of `app_id` fails deterministically pre-fix.
    db.session.expunge_all()

    # Act as the app owner: not the requester, not a direct owner of
    # `app_group`, so the permission gate reaches the app-owner check.
    app.state.current_user_email = app_owner_user.email
    access_request_url = url_for("api-access-requests.access_request_by_id", access_request_id=access_request_id)

    rep = await client.put(
        access_request_url,
        json=ResolveAccessRequestBodyFactory.json(approved=False, reason="already granted elsewhere"),
    )
    assert rep.status_code == 200, rep.text
    assert rep.json()["status"] == AccessRequestStatus.REJECTED

    resolved = (await db.session.scalars(select(AccessRequest).where(AccessRequest.id == access_request_id))).first()
    assert resolved is not None
    assert resolved.status == AccessRequestStatus.REJECTED
    assert resolved.resolver_user_id == app_owner_user_id


async def test_create_access_request(
    app: FastAPI, client: AsyncClient, db: Db, okta_group: OktaGroup, url_for: Any
) -> None:
    # test bad data
    access_requests_url = url_for("api-access-requests.access_requests")
    data: dict[str, Any] = {}
    rep = await client.post(access_requests_url, json=data)
    assert rep.status_code == 400

    db.session.add(okta_group)
    await db.session.commit()

    data = CreateAccessRequestBodyFactory.json(group_id=okta_group.id, group_owner=False, reason="test reason")

    rep = await client.post(access_requests_url, json=data)
    assert rep.status_code == 201

    data = rep.json()
    access_request = await db.session.get(AccessRequest, data["id"])
    access_owner = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()

    assert data["requester"]["email"] == access_owner.email
    assert data["requested_group"]["name"] == okta_group.name
    assert access_request.status == AccessRequestStatus.PENDING
    assert data["status"] == access_request.status
    assert data["request_reason"] == access_request.request_reason
    assert data["request_ownership"] == access_request.request_ownership


async def test_create_access_request_with_rfc822_ending_at(
    app: FastAPI, client: AsyncClient, db: Db, okta_group: OktaGroup, url_for: Any
) -> None:
    """Frontend sends ending_at as RFC822 (e.g. "Sun, 10 May 2026 19:09:02 -0700");
    the router must parse the wire string into a datetime before it hits the
    SQLAlchemy DateTime column."""
    access_requests_url = url_for("api-access-requests.access_requests")
    db.session.add(okta_group)
    await db.session.commit()

    # A literal dict, not CreateAccessRequestBodyFactory: this asserts the
    # router parses the RFC822 wire string, and building through the schema
    # would normalize ending_at to ISO before it ever left the test.
    data = {
        "group_id": okta_group.id,
        "group_owner": False,
        "reason": "test reason",
        "ending_at": "Sun, 10 May 2026 19:09:02 -0700",
    }
    rep = await client.post(access_requests_url, json=data)
    assert rep.status_code == 201, rep.text

    response_data = rep.json()
    access_request = await db.session.get(AccessRequest, response_data["id"])
    assert access_request is not None
    assert access_request.request_ending_at is not None
    # 2026-05-10 19:09:02 -0700 normalizes to 2026-05-11 02:09:02 UTC.
    assert access_request.request_ending_at.year == 2026
    assert access_request.request_ending_at.month == 5
    assert access_request.request_ending_at.day == 11


async def test_get_all_access_request(
    client: AsyncClient, db: Db, okta_group: OktaGroup, user: OktaUser, url_for: Any
) -> None:
    access_requests_url = url_for("api-access-requests.access_requests")
    db.session.add(user)
    db.session.add(okta_group)
    await db.session.commit()

    access_requests = AccessRequestFactory.create_batch(10, requester_user_id=user.id, requested_group_id=okta_group.id)
    db.session.add_all(access_requests)
    await db.session.commit()

    rep = await client.get(access_requests_url)
    assert rep.status_code == 200

    results = rep.json()
    for access_request in access_requests:
        assert any(u["id"] == access_request.id for u in results["items"])

    rep = await client.get(access_requests_url, params={"q": "pend"})
    assert rep.status_code == 200

    results = rep.json()
    for access_request in access_requests:
        assert any(u["id"] == access_request.id for u in results["items"])


async def test_create_access_request_notification(
    app: FastAPI, db: Db, okta_group: OktaGroup, user: OktaUser, mocker: MockerFixture
) -> None:
    db.session.add(user)
    db.session.add(okta_group)
    await db.session.commit()

    await ModifyGroupUsers(
        group=okta_group, members_to_add=[user.id], owners_to_add=[user.id], sync_to_okta=False
    ).execute()

    hook = get_notification_hook()
    request_created_notification_spy = mocker.patch.object(hook, "access_request_created")
    request_completed_notification_spy = mocker.patch.object(hook, "access_request_completed")
    add_membership_spy = mocker.patch.object(okta, "add_user_to_group")

    access_request = await CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    assert access_request is not None
    assert request_created_notification_spy.call_count == 1

    access_owner = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()

    await ApproveAccessRequest(access_request=access_request, approver_user=access_owner).execute()

    assert add_membership_spy.call_count == 1
    assert request_completed_notification_spy.call_count == 1

    access_request.status = AccessRequestStatus.PENDING
    access_request.resolved_at = None
    await db.session.commit()

    add_membership_spy.reset_mock()
    request_completed_notification_spy.reset_mock()

    await RejectAccessRequest(access_request=access_request, current_user_id=access_owner).execute()

    assert add_membership_spy.call_count == 0
    assert request_completed_notification_spy.call_count == 1


async def test_create_app_access_request_notification(
    app: FastAPI, db: Db, access_app: App, app_group: AppGroup, user: OktaUser, mocker: MockerFixture
) -> None:
    # test bad data
    app_owner_user = OktaUserFactory.create()
    app_owner_group = AppGroupFactory.create()

    # Add App
    db.session.add(access_app)

    # Add Users
    db.session.add(app_owner_user)
    db.session.add(user)

    await db.session.commit()

    # Add app group that no one owns
    app_group.app_id = access_app.id
    app_group.is_owner = False
    db.session.add(app_group)

    # Add app owners group
    app_owner_group.app_id = access_app.id
    app_owner_group.is_owner = True
    db.session.add(app_owner_group)

    await db.session.commit()

    # Add app_owner_user to the owner group
    await ModifyGroupUsers(
        group=app_owner_group, members_to_add=[], owners_to_add=[app_owner_user.id], sync_to_okta=False
    ).execute()

    hook = get_notification_hook()
    request_created_notification_spy = mocker.patch.object(hook, "access_request_created")
    request_completed_notification_spy = mocker.patch.object(hook, "access_request_completed")
    add_membership_spy = mocker.patch.object(okta, "add_user_to_group")

    access_request = await CreateAccessRequest(
        requester_user=user,
        requested_group=app_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    assert access_request is not None
    assert request_created_notification_spy.call_count == 1
    _, kwargs = request_created_notification_spy.call_args
    assert kwargs["access_request"] == access_request
    assert kwargs["group"] == app_group
    assert kwargs["requester"] == user

    access_owner = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()

    await ApproveAccessRequest(access_request=access_request, approver_user=access_owner).execute()

    assert add_membership_spy.call_count == 1
    assert request_completed_notification_spy.call_count == 1
    _, kwargs = request_completed_notification_spy.call_args
    assert kwargs["access_request"] == access_request
    assert kwargs["group"] == app_group
    assert kwargs["requester"] == user

    access_request.status = AccessRequestStatus.PENDING
    access_request.resolved_at = None
    await db.session.commit()

    add_membership_spy.reset_mock()
    request_completed_notification_spy.reset_mock()

    await RejectAccessRequest(access_request=access_request, current_user_id=access_owner).execute()

    assert add_membership_spy.call_count == 0
    assert request_completed_notification_spy.call_count == 1
    _, kwargs = request_completed_notification_spy.call_args
    assert kwargs["access_request"] == access_request
    assert kwargs["group"] == app_group
    assert kwargs["requester"] == user


async def test_get_all_possible_request_approvers(app: FastAPI, mocker: MockerFixture, db: Db) -> None:
    access_admin = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()

    users = OktaUserFactory.build_batch(3)
    db.session.add_all(users)
    await db.session.commit()

    mocker.patch(
        "api.models.access_request.get_group_managers",
        return_value=[users[0], users[1]],
    )

    mocker.patch(
        "api.models.access_request.get_app_managers",
        return_value=[users[0], users[2]],
    )

    req = AccessRequest()
    req.requested_group = AppGroupFactory.create()

    approvers = await get_all_possible_request_approvers(req)

    # Assert that the access admin and 3 users are returned with no duplicates
    assert len(approvers) == 4
    assert access_admin in approvers
    assert users[0] in approvers
    assert users[1] in approvers
    assert users[2] in approvers


async def test_resolve_app_access_request_notification(
    app: FastAPI, db: Db, access_app: App, app_group: AppGroup, user: OktaUser, mocker: MockerFixture
) -> None:
    access_admin = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()

    app_owner_user1 = OktaUserFactory.build()
    app_owner_user2 = OktaUserFactory.build()
    app_owner_group = AppGroupFactory.build()

    # Add App
    db.session.add(access_app)

    # Add Users
    db.session.add(app_owner_user1)
    db.session.add(app_owner_user2)
    db.session.add(user)  # Future group owner

    await db.session.commit()

    # Add app group that no one owns
    app_group.app_id = access_app.id
    app_group.is_owner = False
    db.session.add(app_group)

    # Add app owners group
    app_owner_group.app_id = access_app.id
    app_owner_group.is_owner = True
    db.session.add(app_owner_group)

    await db.session.commit()

    # Add app_owner_user to the owner group
    await ModifyGroupUsers(
        group=app_owner_group,
        members_to_add=[],
        owners_to_add=[app_owner_user1.id, app_owner_user2.id],
        sync_to_okta=False,
    ).execute()

    hook = get_notification_hook()
    request_created_notification_spy = mocker.patch.object(hook, "access_request_created")
    request_completed_notification_spy = mocker.patch.object(hook, "access_request_completed")
    add_ownership_spy = mocker.patch.object(okta, "add_owner_to_group")

    access_request = await CreateAccessRequest(
        requester_user=user,
        requested_group=app_group,
        request_ownership=True,
        request_reason="test reason",
    ).execute()

    assert access_request is not None
    assert request_created_notification_spy.call_count == 1
    _, kwargs = request_created_notification_spy.call_args
    assert kwargs["access_request"] == access_request
    assert kwargs["group"] == app_group
    assert kwargs["requester"] == user
    assert len(kwargs["approvers"]) == 2
    assert app_owner_user1 in kwargs["approvers"]
    assert app_owner_user2 in kwargs["approvers"]

    await ApproveAccessRequest(access_request=access_request, approver_user=app_owner_user1).execute()

    assert add_ownership_spy.call_count == 1
    assert request_completed_notification_spy.call_count == 1
    _, kwargs = request_completed_notification_spy.call_args
    assert kwargs["access_request"] == access_request
    assert kwargs["group"] == app_group
    assert kwargs["requester"] == user
    assert len(kwargs["approvers"]) == 4
    assert access_admin in kwargs["approvers"]
    assert app_owner_user1 in kwargs["approvers"]
    assert app_owner_user2 in kwargs["approvers"]
    assert user in kwargs["approvers"]

    # Reset the access request so we can test the reject path
    access_request.status = AccessRequestStatus.PENDING
    access_request.resolved_at = None
    await db.session.commit()

    add_ownership_spy.reset_mock()
    request_completed_notification_spy.reset_mock()

    await RejectAccessRequest(access_request=access_request, current_user_id=app_owner_user1).execute()

    assert add_ownership_spy.call_count == 0
    assert request_completed_notification_spy.call_count == 1
    _, kwargs = request_completed_notification_spy.call_args
    assert kwargs["access_request"] == access_request
    assert kwargs["group"] == app_group
    assert kwargs["requester"] == user
    assert len(kwargs["approvers"]) == 4
    assert access_admin in kwargs["approvers"]
    assert app_owner_user1 in kwargs["approvers"]
    assert app_owner_user2 in kwargs["approvers"]
    assert user in kwargs["approvers"]


async def test_auto_resolve_create_access_request(
    app: FastAPI, db: Db, okta_group: OktaGroup, user: OktaUser, tag: Tag, mocker: MockerFixture
) -> None:
    db.session.add(user)
    db.session.add(okta_group)
    db.session.add(tag)
    await db.session.commit()

    await OktaGroupTagMapFactory.create_async(group_id=okta_group.id, tag_id=tag.id)

    notification_hook = get_notification_hook()
    request_created_notification_spy = mocker.patch.object(notification_hook, "access_request_created")
    request_completed_notification_spy = mocker.patch.object(notification_hook, "access_request_completed")
    # Conditional-access hooks are async; patch the dispatch helper the operation
    # awaits (mock.patch autospecs it to an AsyncMock since it is a coroutine fn).
    request_created_conditional_access_spy = mocker.patch(
        "api.operations.create_access_request.evaluate_conditional_access",
        return_value=[ConditionalAccessResponse(approved=True, reason="Auto-Approved")],
    )
    add_membership_spy = mocker.patch.object(okta, "add_user_to_group")

    access_request = await CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    assert access_request is not None
    # The auto-approval's bulk update expired the resolution columns; reload.
    await db.session.refresh(access_request)
    assert access_request.status == AccessRequestStatus.APPROVED
    assert access_request.resolved_at is not None
    assert access_request.resolver_user_id is None
    assert access_request.resolution_reason == "Auto-Approved"
    assert request_created_notification_spy.call_count == 0
    assert request_completed_notification_spy.call_count == 0
    assert request_created_conditional_access_spy.call_count == 1
    assert add_membership_spy.call_count == 1

    _, kwargs = request_created_conditional_access_spy.call_args
    assert access_request == kwargs["access_request"]
    assert okta_group == kwargs["group"]
    assert user == kwargs["requester"]
    assert len(kwargs["group_tags"]) == 1
    assert tag in kwargs["group_tags"]

    request_created_notification_spy.reset_mock()
    request_completed_notification_spy.reset_mock()
    request_created_conditional_access_spy.reset_mock()
    add_membership_spy.reset_mock()

    request_created_conditional_access_spy = mocker.patch(
        "api.operations.create_access_request.evaluate_conditional_access",
        return_value=[ConditionalAccessResponse(approved=False, reason="Auto-Rejected")],
    )

    access_request = await CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    assert access_request is not None
    await db.session.refresh(access_request)
    assert access_request.status == AccessRequestStatus.REJECTED
    assert access_request.resolved_at is not None
    assert access_request.resolver_user_id is None
    assert access_request.resolution_reason == "Auto-Rejected"
    assert request_created_notification_spy.call_count == 0
    assert request_completed_notification_spy.call_count == 0
    assert request_created_conditional_access_spy.call_count == 1
    assert add_membership_spy.call_count == 0

    _, kwargs = request_created_conditional_access_spy.call_args
    assert access_request == kwargs["access_request"]
    assert okta_group == kwargs["group"]
    assert user == kwargs["requester"]
    assert len(kwargs["group_tags"]) == 1
    assert tag in kwargs["group_tags"]

    request_created_notification_spy.reset_mock()
    request_completed_notification_spy.reset_mock()
    request_created_conditional_access_spy.reset_mock()
    add_membership_spy.reset_mock()

    request_created_conditional_access_spy = mocker.patch(
        "api.operations.create_access_request.evaluate_conditional_access", return_value=[None]
    )

    access_request = await CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    assert access_request is not None
    assert access_request.status == AccessRequestStatus.PENDING
    assert access_request.resolved_at is None
    assert access_request.resolver_user_id is None
    assert access_request.resolution_reason == ""
    assert request_created_notification_spy.call_count == 1
    assert request_completed_notification_spy.call_count == 0
    assert request_created_conditional_access_spy.call_count == 1
    assert add_membership_spy.call_count == 0

    _, kwargs = request_created_conditional_access_spy.call_args
    assert access_request == kwargs["access_request"]
    assert okta_group == kwargs["group"]
    assert user == kwargs["requester"]
    assert len(kwargs["group_tags"]) == 1
    assert tag in kwargs["group_tags"]


async def test_auto_resolve_create_access_request_with_time_limit_constraint_tag(
    app: FastAPI, db: Db, okta_group: OktaGroup, user: OktaUser, tag: Tag, mocker: MockerFixture
) -> None:
    db.session.add(user)
    db.session.add(okta_group)
    tag.constraints = {
        Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: THREE_DAYS_IN_SECONDS,
        Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: THREE_DAYS_IN_SECONDS,
    }
    db.session.add(tag)
    await db.session.commit()

    await OktaGroupTagMapFactory.create_async(group_id=okta_group.id, tag_id=tag.id)

    notification_hook = get_notification_hook()
    request_created_notification_spy = mocker.patch.object(notification_hook, "access_request_created")
    request_completed_notification_spy = mocker.patch.object(notification_hook, "access_request_completed")
    request_created_conditional_access_spy = mocker.patch(
        "api.operations.create_access_request.evaluate_conditional_access",
        return_value=[
            ConditionalAccessResponse(
                approved=True,
                reason="Auto-Approved",
                ending_at=datetime.now() + timedelta(seconds=SEVEN_DAYS_IN_SECONDS),
            ),
        ],
    )
    add_membership_spy = mocker.patch.object(okta, "add_user_to_group")

    access_request = await CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    assert access_request is not None
    # The auto-approval's bulk update expired the resolution columns; reload.
    await db.session.refresh(access_request)
    assert access_request.status == AccessRequestStatus.APPROVED
    assert access_request.resolved_at is not None
    assert access_request.resolver_user_id is None
    assert access_request.resolution_reason == "Auto-Approved"
    assert request_created_notification_spy.call_count == 0
    assert request_completed_notification_spy.call_count == 0
    assert request_created_conditional_access_spy.call_count == 1
    assert add_membership_spy.call_count == 1

    _, kwargs = request_created_conditional_access_spy.call_args
    assert access_request == kwargs["access_request"]
    assert okta_group == kwargs["group"]
    assert user == kwargs["requester"]
    assert len(kwargs["group_tags"]) == 1
    assert tag in kwargs["group_tags"]


async def test_q_search_covers_all_fields_via_http(
    client: AsyncClient, db: Db, okta_group: OktaGroup, user: OktaUser, url_for: Any
) -> None:
    """The `q` search must hit requester email+name, requested group
    name+description, and the request id prefix. Previously only `status`
    and `request_reason` were searched. Seed two requests with disjoint
    requesters and groups so each search must *exclude* the other to pass
    — a regression that returns everything would still match by ID but
    fail the exclusion assertions."""
    target_user = OktaUserFactory.create(email="zelda-target@example.com", first_name="Zelda", last_name="Target")
    other_user = OktaUserFactory.create(email="other-noise@example.com", first_name="Other", last_name="Noise")
    target_group = OktaGroupFactory.create(name="ZeldaTargetGroup", description="zd-desc")
    other_group = OktaGroupFactory.create(name="OtherNoiseGroup", description="on-desc")
    db.session.add_all([target_user, other_user, target_group, other_group])
    await db.session.commit()

    target_ar = await CreateAccessRequest(
        requester_user=target_user,
        requested_group=target_group,
        request_ownership=False,
        request_reason="dummy",
    ).execute()
    other_ar = await CreateAccessRequest(
        requester_user=other_user,
        requested_group=other_group,
        request_ownership=False,
        request_reason="dummy",
    ).execute()
    assert target_ar is not None and other_ar is not None

    requests_url = url_for("api-access-requests.access_requests")

    def ids(rep: Any) -> list[str]:
        return [r["id"] for r in rep.json()["items"]]

    # Requester email substring.
    rep = await client.get(requests_url, params={"q": "zelda-target"})
    assert rep.status_code == 200
    found = ids(rep)
    assert target_ar.id in found and other_ar.id not in found

    # Requester first/last name substring.
    rep = await client.get(requests_url, params={"q": "Zelda"})
    assert rep.status_code == 200
    found = ids(rep)
    assert target_ar.id in found and other_ar.id not in found

    # Requested group name substring.
    rep = await client.get(requests_url, params={"q": "ZeldaTargetGroup"})
    assert rep.status_code == 200
    found = ids(rep)
    assert target_ar.id in found and other_ar.id not in found

    # Requested group description substring.
    rep = await client.get(requests_url, params={"q": "zd-desc"})
    assert rep.status_code == 200
    found = ids(rep)
    assert target_ar.id in found and other_ar.id not in found

    # AccessRequest id prefix.
    rep = await client.get(requests_url, params={"q": target_ar.id[:6]})
    assert rep.status_code == 200
    found = ids(rep)
    assert target_ar.id in found


async def test_post_access_request_403_for_deleted_user(
    client: AsyncClient, db: Db, okta_group: OktaGroup, mock_user: Any, url_for: Any
) -> None:
    """Flask returned 403 (not 404) when current_user_id resolves to a
    soft-deleted user."""
    from datetime import datetime, timezone

    deleted_user = OktaUserFactory.create(deleted_at=datetime.now(timezone.utc))
    db.session.add(deleted_user)
    db.session.add(okta_group)
    await db.session.commit()
    mock_user(deleted_user.id)

    rep = await client.post(
        url_for("api-access-requests.access_requests_create"),
        json=CreateAccessRequestBodyFactory.json(group_id=okta_group.id, group_owner=False, reason="test"),
    )
    assert rep.status_code == 403


async def test_post_access_request_for_role_group_with_associated_groups(
    app: FastAPI, client: AsyncClient, db: Db, url_for: Any
) -> None:
    """POST /api/requests for a RoleGroup that owns and is a member of
    several AppGroups must succeed. Without the polymorphic + tag +
    role-association eager-load on the response refetch, the operation
    walks `active_role_associated_group_*_mappings` row-by-row and either
    N+1's or, on `lazy="raise_on_sql"` relationships, blows up with 500.
    """
    from api.operations import ModifyRoleGroups
    from tests.factories import AppFactory, AppGroupFactory, RoleGroupFactory

    role = RoleGroupFactory.create(name="Role-WithAssociations")
    test_app = AppFactory.create(name="AssocTestApp")
    db.session.add(role)
    db.session.add(test_app)
    await db.session.commit()

    associated_app_groups = [
        AppGroupFactory.create(
            name=f"App-AssocTestApp-Member-{i}",
            app_id=test_app.id,
            is_owner=False,
        )
        for i in range(3)
    ]
    associated_owner_groups = [
        AppGroupFactory.create(
            name=f"App-AssocTestApp-OwnerSlot-{i}",
            app_id=test_app.id,
            is_owner=False,
        )
        for i in range(2)
    ]
    for ag in associated_app_groups + associated_owner_groups:
        db.session.add(ag)
    await db.session.commit()

    await ModifyRoleGroups(
        role_group=role,
        groups_to_add=[g.id for g in associated_app_groups],
        owner_groups_to_add=[g.id for g in associated_owner_groups],
        sync_to_okta=False,
    ).execute()

    rep = await client.post(
        url_for("api-access-requests.access_requests_create"),
        json=CreateAccessRequestBodyFactory.json(group_id=role.id, group_owner=False, reason="want role membership"),
    )
    assert rep.status_code == 201, rep.text
    body = rep.json()
    assert body["requested_group"]["id"] == role.id
    assert body["status"] == AccessRequestStatus.PENDING


async def test_get_access_request_detail_requested_group_for_role_group(
    client: AsyncClient,
    db: Db,
    okta_group: OktaGroup,
    role_group: RoleGroup,
    user: OktaUser,
    url_for: Any,
) -> None:
    """Flask `AccessRequestResource.get()` `only=` retains
    `requested_group.active_group_tags`,
    `requested_group.active_role_associated_group_member_mappings`, and
    `requested_group.active_role_associated_group_owner_mappings` on a
    role-type requested group. The React request-detail page lists the
    role's associated groups inline."""
    db.session.add(user)
    db.session.add(okta_group)
    db.session.add(role_group)
    await db.session.commit()

    # Wire the role to one member-group and one owner-group so both
    # association arrays have content.
    member_group = OktaGroupFactory.create()
    owner_group = OktaGroupFactory.create()
    db.session.add(member_group)
    db.session.add(owner_group)
    await db.session.commit()
    await ModifyRoleGroups(
        role_group=role_group,
        groups_to_add=[member_group.id],
        owner_groups_to_add=[owner_group.id],
        sync_to_okta=False,
    ).execute()

    ar = await CreateAccessRequest(
        requester_user=user,
        requested_group=role_group,
        request_ownership=False,
        request_reason="role-detail rich-ref test",
    ).execute()
    assert ar is not None

    # drop identity-map state staled by the ops above (expire_on_commit=False)
    db.session.expire_all()
    # ... then reload the request before reading its id in this task context.
    await db.session.refresh(ar)

    rep = await client.get(url_for("api-access-requests.access_request_by_id", access_request_id=ar.id))
    assert rep.status_code == 200, rep.text
    data = rep.json()
    rg = data["requested_group"]
    assert rg["type"] == "role_group"
    assert "active_group_tags" in rg
    assert "active_role_associated_group_member_mappings" in rg
    assert "active_role_associated_group_owner_mappings" in rg
    member_ids = [
        m["active_group"]["id"]
        for m in rg["active_role_associated_group_member_mappings"]
        if m.get("active_group") is not None
    ]
    owner_ids = [
        m["active_group"]["id"]
        for m in rg["active_role_associated_group_owner_mappings"]
        if m.get("active_group") is not None
    ]
    assert member_group.id in member_ids
    assert owner_group.id in owner_ids


async def test_get_access_request_detail_requested_group_for_app_group(
    client: AsyncClient,
    db: Db,
    access_app: App,
    app_group: AppGroup,
    user: OktaUser,
    tag: Tag,
    url_for: Any,
) -> None:
    """Flask `AccessRequestResource.get()` `only=` retains
    `requested_group.is_owner`, `requested_group.app`, and
    `requested_group.active_group_tags` on an app-group requested group.
    The React request-detail page renders the app name and any tags
    attached to the group."""
    access_app.app_group_lifecycle_plugin = "noop"
    db.session.add(user)
    db.session.add(access_app)
    await db.session.commit()
    app_group.app_id = access_app.id
    app_group.is_owner = False
    db.session.add(app_group)
    db.session.add(tag)
    await db.session.commit()
    await OktaGroupTagMapFactory.create_async(group_id=app_group.id, tag_id=tag.id)

    ar = await CreateAccessRequest(
        requester_user=user,
        requested_group=app_group,
        request_ownership=False,
        request_reason="app-group rich-ref test",
    ).execute()
    assert ar is not None

    rep = await client.get(url_for("api-access-requests.access_request_by_id", access_request_id=ar.id))
    assert rep.status_code == 200, rep.text
    data = rep.json()
    rg = data["requested_group"]
    assert rg["type"] == "app_group"
    assert "is_owner" in rg and rg["is_owner"] is False
    assert rg["app"] is not None
    assert rg["app"].get("id") == access_app.id
    # `app_group_lifecycle_plugin` parity check (covers the `AppIdRef` fix
    # at the same time, on the embedded path).
    assert rg["app"].get("app_group_lifecycle_plugin") == "noop"
    assert "active_group_tags" in rg
    tag_ids = [entry["active_tag"]["id"] for entry in rg["active_group_tags"] if entry.get("active_tag") is not None]
    assert tag.id in tag_ids


async def test_modify_group_users_notify_false_suppresses_access_request_completion(
    db: Db, okta_group: OktaGroup, user: OktaUser, mocker: MockerFixture
) -> None:
    """`notify=False` suppresses the auto-approved access request's completion DM
    while the request still resolves to APPROVED (notify gates the DM, not the action)."""
    db.session.add_all([okta_group, user])
    await db.session.commit()

    access_request = await CreateAccessRequest(
        requester_user=user, requested_group=okta_group, request_ownership=False, request_reason="please"
    ).execute()
    assert access_request is not None

    mocker.patch.object(okta, "add_user_to_group")
    hook = get_notification_hook()
    completed_spy = mocker.patch.object(hook, "access_request_completed")

    # Adding `user` as a member auto-approves their pending membership request.
    await ModifyGroupUsers(group=okta_group.id, members_to_add=[user.id], sync_to_okta=False, notify=False).execute()

    assert (await db.session.get(AccessRequest, access_request.id)).status == AccessRequestStatus.APPROVED
    assert completed_spy.call_count == 0


async def test_approve_access_request_notify_false_suppresses_completion(
    db: Db, okta_group: OktaGroup, user: OktaUser, mocker: MockerFixture
) -> None:
    """`notify=False` suppresses the completion DM; the request is still APPROVED."""
    db.session.add_all([okta_group, user])
    await db.session.commit()

    access_request = await CreateAccessRequest(
        requester_user=user, requested_group=okta_group, request_ownership=False, request_reason="please"
    ).execute()
    assert access_request is not None

    mocker.patch.object(okta, "add_user_to_group")
    access_owner = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()
    hook = get_notification_hook()
    completed_spy = mocker.patch.object(hook, "access_request_completed")

    await ApproveAccessRequest(access_request=access_request, approver_user=access_owner, notify=False).execute()

    assert (await db.session.get(AccessRequest, access_request.id)).status == AccessRequestStatus.APPROVED
    assert completed_spy.call_count == 0


async def test_reject_access_request_notify_false_suppresses_completion(
    db: Db, okta_group: OktaGroup, user: OktaUser, mocker: MockerFixture
) -> None:
    """`notify=False` suppresses the completion DM; the request is still REJECTED."""
    db.session.add_all([okta_group, user])
    await db.session.commit()

    access_request = await CreateAccessRequest(
        requester_user=user, requested_group=okta_group, request_ownership=False, request_reason="please"
    ).execute()
    assert access_request is not None

    access_owner = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()
    hook = get_notification_hook()
    completed_spy = mocker.patch.object(hook, "access_request_completed")

    await RejectAccessRequest(access_request=access_request, current_user_id=access_owner, notify=False).execute()

    assert (await db.session.get(AccessRequest, access_request.id)).status == AccessRequestStatus.REJECTED
    assert completed_spy.call_count == 0
