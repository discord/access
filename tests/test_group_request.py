from datetime import datetime, timedelta, timezone
from typing import Any, cast, Protocol

from factory import Faker
from httpx import AsyncClient
from okta.models import Group
from pytest_mock import MockerFixture
from fastapi import FastAPI

from sqlalchemy import func, or_, select
from api.config import settings
from api.extensions import Db
from api.models import (
    AccessRequestStatus,
    AppGroup,
    AppTagMap,
    GroupRequest,
    OktaGroup,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    OktaGroupTagMap,
    Tag,
)
from api.operations import CreateGroupRequest, ApproveGroupRequest, RejectGroupRequest
from api.plugins import get_notification_hook
from api.services import okta
from tests.factories import (
    OktaUserFactory,
    AppFactory,
    AppGroupFactory,
    OktaGroupFactory,
    RoleGroupFactory,
    TagFactory,
)


# Define a Protocol that includes the pystr method
class FakerWithPyStr(Protocol):
    def pystr(self) -> str: ...


async def test_create_group_request(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    user: OktaUser,
) -> None:
    db.session.add(user)
    await db.session.commit()

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name="Test Group",
        requested_group_description="Test Description",
        requested_group_type="okta_group",
        request_reason="Need this group",
    ).execute()

    assert group_request is not None
    assert group_request.requester_user_id == user.id
    assert group_request.requested_group_name == "Test Group"
    assert group_request.requested_group_description == "Test Description"
    assert group_request.status == AccessRequestStatus.PENDING
    assert group_request.resolved_at is None


async def test_create_app_group_request(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    user: OktaUser,
) -> None:
    db.session.add(user)
    await db.session.commit()

    app_obj = AppFactory.create()
    db.session.add(app_obj)
    await db.session.commit()

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name=f"App-{app_obj.name}-Admins",
        requested_group_description="Admin group for TestApp",
        requested_group_type="app_group",
        requested_app_id=app_obj.id,
        request_reason="Need admin access",
    ).execute()

    assert group_request is not None
    assert group_request.requested_app_id == app_obj.id
    assert group_request.requested_group_type == "app_group"


async def test_create_role_group_request(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    user: OktaUser,
) -> None:
    db.session.add(user)
    await db.session.commit()

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name="Role-Engineering",
        requested_group_description="Engineering role",
        requested_group_type="role_group",
        request_reason="Need engineering role",
    ).execute()

    assert group_request is not None
    assert group_request.requested_group_type == "role_group"


async def test_create_group_request_with_tags(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    user: OktaUser,
    tag: Tag,
) -> None:
    db.session.add(user)
    db.session.add(tag)
    await db.session.commit()

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name="Tagged Group",
        requested_group_description="Group with tags",
        requested_group_type="okta_group",
        requested_group_tags=[tag.id],
        request_reason="Need tagged group",
    ).execute()

    assert group_request is not None
    assert tag.id in group_request.requested_group_tags


async def test_create_group_request_with_ownership_ending_at(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    user: OktaUser,
) -> None:
    db.session.add(user)
    await db.session.commit()

    # Request ownership for 60 days
    requested_ending_at = datetime.now(timezone.utc) + timedelta(days=60)

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name="Time Limited Group",
        requested_group_description="Group with time limit",
        requested_group_type="okta_group",
        requested_ownership_ending_at=requested_ending_at,
        request_reason="Need temporary group",
    ).execute()

    assert group_request is not None
    assert group_request.requested_ownership_ending_at is not None

    # Refresh to get the datetime with proper timezone info from DB
    await db.session.refresh(group_request)

    # Should be close to the requested time (within a few seconds), make both timezone-aware
    stored_time = group_request.requested_ownership_ending_at
    if stored_time.tzinfo is None:
        stored_time = stored_time.replace(tzinfo=timezone.utc)
    assert abs((stored_time - requested_ending_at).total_seconds()) < 5


async def test_create_group_request_tag_limits_ownership_time(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    user: OktaUser,
) -> None:
    # Create a tag that limits ownership to 90 days
    tag = TagFactory.create(
        enabled=True,
        constraints={
            Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: 7776000  # in seconds
        },
    )

    db.session.add(user)
    db.session.add(tag)
    await db.session.commit()

    # Request ownership with no ending time (should be limited to 90 days by tag)
    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name="Tag Limited Group",
        requested_group_description="Group with tag time limit",
        requested_group_type="okta_group",
        requested_group_tags=[tag.id],
        requested_ownership_ending_at=None,
        request_reason="Need group with tag constraint",
    ).execute()

    assert group_request is not None
    assert group_request.requested_ownership_ending_at is not None

    # Refresh to get proper timezone info
    await db.session.refresh(group_request)
    stored_time = group_request.requested_ownership_ending_at
    if stored_time.tzinfo is None:
        stored_time = stored_time.replace(tzinfo=timezone.utc)

    # Should be set to approximately 90 days from now
    expected_ending = datetime.now(timezone.utc) + timedelta(days=90)
    time_diff = abs((stored_time - expected_ending).total_seconds())
    assert time_diff < 5  # Within 5 seconds of expected


async def test_create_group_request_tag_reduces_requested_ownership_time(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    user: OktaUser,
) -> None:
    # Create a tag that limits ownership to 30 days
    tag = TagFactory.create(
        enabled=True,
        constraints={
            Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: 2592000  # in seconds
        },
    )

    db.session.add(user)
    db.session.add(tag)
    await db.session.commit()

    # Request ownership for 90 days (should be reduced to 30 days)
    requested_ending_at = datetime.now(timezone.utc) + timedelta(days=90)

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name="Tag Reduced Group",
        requested_group_description="Group with reduced time",
        requested_group_type="okta_group",
        requested_group_tags=[tag.id],
        requested_ownership_ending_at=requested_ending_at,
        request_reason="Need group with reduced constraint",
    ).execute()

    assert group_request is not None
    assert group_request.requested_ownership_ending_at is not None

    # Refresh to get proper timezone info
    await db.session.refresh(group_request)
    stored_time = group_request.requested_ownership_ending_at
    if stored_time.tzinfo is None:
        stored_time = stored_time.replace(tzinfo=timezone.utc)

    # Should be set to approximately 30 days from now (not 90)
    expected_ending = datetime.now(timezone.utc) + timedelta(days=30)
    time_diff = abs((stored_time - expected_ending).total_seconds())
    assert time_diff < 5  # Within 5 seconds of expected


async def test_approve_group_request_creates_group(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    admin = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()

    db.session.add(user)
    await db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name="New Group",
        requested_group_description="New group description",
        requested_group_type="okta_group",
        request_reason="Need this group",
    ).execute()

    assert group_request is not None

    await ApproveGroupRequest(
        group_request=group_request,
        approver_user=admin,
        approval_reason="Approved",
    ).execute()

    await db.session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.APPROVED
    assert group_request.resolved_at is not None
    assert group_request.resolver_user_id == admin.id
    assert group_request.approved_group_id is not None

    created_group = await db.session.get(OktaGroup, group_request.approved_group_id)
    assert created_group is not None
    assert created_group.name == "New Group"
    assert created_group.description == "New group description"

    ownerships = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == created_group.id)
            .where(OktaUserGroupMember.user_id == user.id)
            .where(OktaUserGroupMember.is_owner.is_(True))
        )
    ).all()
    assert len(ownerships) == 1
    assert ownerships[0].created_reason == f"Group request approved: {group_request.request_reason}"


async def test_approve_group_request_sets_owner_with_ending_time(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    admin = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()

    db.session.add(user)
    await db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    # Request ownership for 60 days
    requested_ending_at = datetime.now(timezone.utc) + timedelta(days=60)

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name="Time Limited Ownership Group",
        requested_group_description="Group with time-limited ownership",
        requested_group_type="okta_group",
        requested_ownership_ending_at=requested_ending_at,
        request_reason="Need temporary ownership",
    ).execute()

    assert group_request is not None

    await ApproveGroupRequest(
        group_request=group_request,
        approver_user=admin,
        approval_reason="Approved",
    ).execute()

    await db.session.refresh(group_request)
    assert group_request.approved_group_id is not None
    created_group = await db.session.get(OktaGroup, group_request.approved_group_id)

    # Check that the requester is an owner with the correct ending time
    ownerships = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == created_group.id)
            .where(OktaUserGroupMember.user_id == user.id)
            .where(OktaUserGroupMember.is_owner.is_(True))
        )
    ).all()
    assert len(ownerships) == 1
    assert ownerships[0].ended_at is not None

    # Make both timezone-aware
    stored_time = ownerships[0].ended_at
    if stored_time.tzinfo is None:
        stored_time = stored_time.replace(tzinfo=timezone.utc)

    # Should be close to the requested time
    time_diff = abs((stored_time - requested_ending_at).total_seconds())
    assert time_diff < 5


async def test_approve_group_request_tag_limits_owner_ending_time(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    admin = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()

    db.session.add(user)
    await db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    # Create a tag that limits ownership to 30 days
    tag = TagFactory.create(
        enabled=True,
        constraints={
            Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: 2592000  # in seconds
        },
    )
    db.session.add(tag)
    await db.session.commit()

    # Request group with this tag, no ending time specified
    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name="Tag Constrained Group",
        requested_group_description="Group with tag constraint",
        requested_group_type="okta_group",
        requested_group_tags=[tag.id],
        requested_ownership_ending_at=None,
        request_reason="Need group",
    ).execute()

    assert group_request is not None

    # Approver sets resolved ownership ending time to 90 days (should be reduced to 30 by tag)
    requested_90_days = datetime.now(timezone.utc) + timedelta(days=90)
    group_request.resolved_ownership_ending_at = requested_90_days
    await db.session.commit()

    await ApproveGroupRequest(
        group_request=group_request,
        approver_user=admin,
        approval_reason="Approved with time limit",
    ).execute()

    await db.session.refresh(group_request)
    assert group_request.approved_group_id is not None
    created_group = await db.session.get(OktaGroup, group_request.approved_group_id)

    # Check that requester is an owner
    ownerships = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == created_group.id)
            .where(OktaUserGroupMember.user_id == user.id)
            .where(OktaUserGroupMember.is_owner.is_(True))
        )
    ).all()
    assert len(ownerships) == 1
    assert ownerships[0].ended_at is not None

    # Make both timezone-aware
    stored_time = ownerships[0].ended_at
    if stored_time.tzinfo is None:
        stored_time = stored_time.replace(tzinfo=timezone.utc)

    # Should be approximately 30 days from now (not 90)
    expected_ending = datetime.now(timezone.utc) + timedelta(days=30)
    time_diff = abs((stored_time - expected_ending).total_seconds())
    assert time_diff < 5  # Within 5 seconds of expected

    # Should be less than what the approver requested
    assert stored_time < requested_90_days

    # Check that resolved_ownership_ending_at was updated to the coalesced value
    await db.session.refresh(group_request)
    resolved_time = group_request.resolved_ownership_ending_at
    if resolved_time.tzinfo is None:
        resolved_time = resolved_time.replace(tzinfo=timezone.utc)
    assert resolved_time < requested_90_days


async def test_approve_group_request_applies_tags(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
    tag: Tag,
) -> None:
    admin = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()

    db.session.add(user)
    db.session.add(tag)
    await db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name="Tagged Group",
        requested_group_description="Group with tags",
        requested_group_type="okta_group",
        requested_group_tags=[tag.id],
        request_reason="Need tagged group",
    ).execute()

    assert group_request is not None

    # Set resolved tags (approver could modify these)
    group_request.resolved_group_tags = [tag.id]
    await db.session.commit()

    await ApproveGroupRequest(
        group_request=group_request,
        approver_user=admin,
        approval_reason="Approved",
    ).execute()

    await db.session.refresh(group_request)
    assert group_request.approved_group_id is not None
    created_group = await db.session.get(OktaGroup, group_request.approved_group_id)

    # Check that the tag was applied
    tag_mappings = (
        await db.session.scalars(
            select(OktaGroupTagMap)
            .where(OktaGroupTagMap.group_id == created_group.id)
            .where(OktaGroupTagMap.tag_id == tag.id)
            .where(
                or_(
                    OktaGroupTagMap.ended_at.is_(None),
                    OktaGroupTagMap.ended_at > func.now(),
                )
            )
        )
    ).all()
    assert len(tag_mappings) == 1


async def test_approve_group_request_sets_name(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    admin = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()

    db.session.add(user)
    await db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name="Specific Name",
        requested_group_description="Specific description text",
        requested_group_type="okta_group",
        request_reason="Need this name",
    ).execute()

    assert group_request is not None

    await ApproveGroupRequest(
        group_request=group_request,
        approver_user=admin,
        approval_reason="Approved",
    ).execute()

    await db.session.refresh(group_request)
    assert group_request.approved_group_id is not None
    created_group = await db.session.get(OktaGroup, group_request.approved_group_id)
    assert created_group.name == "Specific Name"
    assert created_group.description == "Specific description text"


async def test_approve_group_request_sets_type(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    admin = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()
    app_obj = AppFactory.create()

    db.session.add(user)
    db.session.add(app_obj)
    await db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    # Test OktaGroup
    group_request_okta = await CreateGroupRequest(
        requester_user=user,
        requested_group_name="Regular Group",
        requested_group_description="Description",
        requested_group_type="okta_group",
        request_reason="Need group",
    ).execute()

    assert group_request_okta is not None

    await ApproveGroupRequest(
        group_request=group_request_okta,
        approver_user=admin,
        approval_reason="Approved",
    ).execute()

    await db.session.refresh(group_request_okta)
    assert group_request_okta.status == AccessRequestStatus.APPROVED
    assert group_request_okta.resolver_user_id == admin.id
    assert group_request_okta.approved_group_id is not None

    created_okta_group = await db.session.get(OktaGroup, group_request_okta.approved_group_id)
    assert type(created_okta_group) is OktaGroup

    # Test AppGroup
    group_request_app = await CreateGroupRequest(
        requester_user=user,
        requested_group_name=f"App-{app_obj.name}-Users",
        requested_group_description="App group",
        requested_group_type="app_group",
        requested_app_id=app_obj.id,
        request_reason="Need app group",
    ).execute()

    assert group_request_app is not None

    await ApproveGroupRequest(
        group_request=group_request_app,
        approver_user=admin,
        approval_reason="Approved",
    ).execute()

    await db.session.refresh(group_request_app)
    assert group_request_app.status == AccessRequestStatus.APPROVED
    assert group_request_app.resolver_user_id == admin.id
    assert group_request_app.approved_group_id is not None

    created_app_group = await db.session.get(OktaGroup, group_request_app.approved_group_id)
    assert type(created_app_group) is AppGroup
    # eager-load the joined-inheritance subclass column (lazy IO would raise in async)
    await db.session.refresh(created_app_group, attribute_names=["app_id"])
    assert created_app_group.app_id == app_obj.id

    # Test RoleGroup
    group_request_role = await CreateGroupRequest(
        requester_user=user,
        requested_group_name="Role-Marketing",
        requested_group_description="Role group",
        requested_group_type="role_group",
        request_reason="Need role group",
    ).execute()

    assert group_request_role is not None

    await ApproveGroupRequest(
        group_request=group_request_role,
        approver_user=admin,
        approval_reason="Approved",
    ).execute()

    await db.session.refresh(group_request_role)
    assert group_request_role.status == AccessRequestStatus.APPROVED
    assert group_request_role.resolver_user_id == admin.id
    assert group_request_role.approved_group_id is not None

    created_role_group = await db.session.get(OktaGroup, group_request_role.approved_group_id)
    assert type(created_role_group) is RoleGroup


async def test_app_owner_can_approve_request(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    app_owner = OktaUserFactory.create()
    app_obj = AppFactory.create()

    db.session.add(user)
    db.session.add(app_owner)
    db.session.add(app_obj)
    await db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    # Owner group for the app
    owner_group = AppGroupFactory.create(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_obj.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        app_id=app_obj.id,
        is_owner=True,
    )

    db.session.add(owner_group)
    await db.session.commit()

    # Add app_owner to owner group
    db.session.add(
        OktaUserGroupMember(
            user_id=app_owner.id,
            group_id=owner_group.id,
            is_owner=True,
        )
    )
    await db.session.commit()

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name=f"App-{app_obj.name}-NewGroup",
        requested_group_description="New app group",
        requested_group_type="app_group",
        requested_app_id=app_obj.id,
        request_reason="Need app group",
    ).execute()

    assert group_request is not None

    # App owner should be able to approve
    await ApproveGroupRequest(
        group_request=group_request,
        approver_user=app_owner,
        approval_reason="Approved by app owner",
    ).execute()

    await db.session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.APPROVED
    assert group_request.resolver_user_id == app_owner.id


async def test_app_owner_can_reject_request(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    user: OktaUser,
) -> None:
    app_owner = OktaUserFactory.create()
    app_obj = AppFactory.create()

    db.session.add(user)
    db.session.add(app_owner)
    db.session.add(app_obj)
    await db.session.commit()

    # Create owner group for the app
    owner_group = AppGroupFactory.create(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_obj.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        app_id=app_obj.id,
        is_owner=True,
    )

    db.session.add(owner_group)
    await db.session.commit()

    # Make app_owner an owner of the app via its owner group
    db.session.add(
        OktaUserGroupMember(
            user_id=app_owner.id,
            group_id=owner_group.id,
            is_owner=True,
        )
    )
    await db.session.commit()

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name=f"App-{app_obj.name}-AnotherGroup",
        requested_group_description="Another app group",
        requested_group_type="app_group",
        requested_app_id=app_obj.id,
        request_reason="Need app group",
    ).execute()

    assert group_request is not None

    # App owner should be able to reject
    await RejectGroupRequest(
        group_request=group_request,
        rejection_reason="Rejected by app owner",
        notify_requester=True,
        current_user_id=app_owner.id,
    ).execute()

    await db.session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.REJECTED
    assert group_request.resolver_user_id == app_owner.id


async def test_wrong_app_owner_cannot_approve_request(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    app_owner = OktaUserFactory.create()
    app_obj = AppFactory.create()
    other_app = AppFactory.create()

    db.session.add(user)
    db.session.add(app_owner)
    db.session.add(app_obj)
    db.session.add(other_app)
    await db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    # Create owner groups for both apps
    owner_group = AppGroupFactory.create(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_obj.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        app_id=app_obj.id,
        is_owner=True,
    )
    other_owner_group = AppGroupFactory.create(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{other_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        app_id=other_app.id,
        is_owner=True,
    )

    db.session.add(owner_group)
    db.session.add(other_owner_group)
    await db.session.commit()

    # Make app_owner an owner of 'app' but not 'other_app'
    db.session.add(
        OktaUserGroupMember(
            user_id=app_owner.id,
            group_id=owner_group.id,
            is_owner=True,
        )
    )
    await db.session.commit()

    # Create request for other_app
    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name=f"App-{other_app.name}-NewGroup",
        requested_group_description="Group for other app",
        requested_group_type="app_group",
        requested_app_id=other_app.id,
        request_reason="Need group for other app",
    ).execute()

    assert group_request is not None

    # Set resolved_app_id
    group_request.resolved_app_id = other_app.id
    await db.session.commit()

    # App owner of 'app' should NOT be able to approve request for 'other_app'
    await ApproveGroupRequest(
        group_request=group_request,
        approver_user=app_owner,
        approval_reason="Should not be allowed",
    ).execute()

    await db.session.refresh(group_request)
    # Request should still be pending (not approved)
    assert group_request.status == AccessRequestStatus.PENDING
    assert group_request.resolved_at is None


async def test_admin_can_reject_request(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    user: OktaUser,
) -> None:
    admin = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()

    db.session.add(user)
    db.session.add(admin)
    await db.session.commit()

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name="Admin Rejected Group",
        requested_group_description="Group rejected by admin",
        requested_group_type="okta_group",
        request_reason="Need group",
    ).execute()

    assert group_request is not None

    await RejectGroupRequest(
        group_request=group_request,
        rejection_reason="Rejected by admin",
        notify_requester=True,
        current_user_id=admin.id,
    ).execute()

    await db.session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.REJECTED
    assert group_request.resolver_user_id == admin.id


async def test_any_user_cannot_reject_request(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    user: OktaUser,
) -> None:
    other_user = OktaUserFactory.create()

    db.session.add(user)
    db.session.add(other_user)
    await db.session.commit()

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name="Other User Can't Reject Group",
        requested_group_description="Group can't be rejected by non-admin user",
        requested_group_type="okta_group",
        request_reason="Need group",
    ).execute()

    assert group_request is not None

    await RejectGroupRequest(
        group_request=group_request,
        rejection_reason="Rejected by other_user",
        notify_requester=True,
        current_user_id=other_user.id,
    ).execute()

    await db.session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.PENDING
    assert group_request.resolved_at is None


async def test_user_can_reject_own_request(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    user: OktaUser,
) -> None:
    db.session.add(user)
    await db.session.commit()

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name="Self Rejected Group",
        requested_group_description="Group I don't want anymore",
        requested_group_type="okta_group",
        request_reason="Changed my mind",
    ).execute()

    assert group_request is not None

    await RejectGroupRequest(
        group_request=group_request,
        rejection_reason="I changed my mind",
        notify_requester=False,
        current_user_id=user.id,
    ).execute()

    await db.session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.REJECTED
    assert group_request.resolver_user_id == user.id


async def test_user_cannot_approve_own_request(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    user: OktaUser,
) -> None:
    db.session.add(user)
    await db.session.commit()

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name="Self Approval Attempt",
        requested_group_description="Trying to approve myself",
        requested_group_type="okta_group",
        request_reason="Want to self-approve",
    ).execute()

    assert group_request is not None

    # Attempt to self-approve should fail
    await ApproveGroupRequest(
        group_request=group_request,
        approver_user=user,
        approval_reason="Self approval",
    ).execute()

    await db.session.refresh(group_request)
    # Should still be pending
    assert group_request.status == AccessRequestStatus.PENDING
    assert group_request.resolved_at is None


async def test_approver_can_modify_group_details(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    admin = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()
    tag = TagFactory.create(enabled=True)
    other_tag = TagFactory.create(enabled=True)

    db.session.add(user)
    db.session.add(tag)
    db.session.add(other_tag)
    await db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name="Original Name",
        requested_group_description="Original description",
        requested_group_type="okta_group",
        requested_group_tags=[tag.id],
        request_reason="Need group",
    ).execute()

    assert group_request is not None

    # Approver modifies the request before approval
    group_request.resolved_group_name = "Modified Name"
    group_request.resolved_group_description = "Modified description"
    group_request.resolved_group_tags = [other_tag.id]
    await db.session.commit()

    await ApproveGroupRequest(
        group_request=group_request,
        approver_user=admin,
        approval_reason="Approved with modifications",
    ).execute()

    await db.session.refresh(group_request)
    assert group_request.approved_group_id is not None
    created_group = await db.session.get(OktaGroup, group_request.approved_group_id)

    # Verify the modified values were used
    assert created_group.name == "Modified Name"
    assert created_group.description == "Modified description"

    # Check tags
    tag_mappings = (
        await db.session.scalars(
            select(OktaGroupTagMap)
            .where(OktaGroupTagMap.group_id == created_group.id)
            .where(
                or_(
                    OktaGroupTagMap.ended_at.is_(None),
                    OktaGroupTagMap.ended_at > func.now(),
                )
            )
        )
    ).all()
    tag_ids = [tm.tag_id for tm in tag_mappings]
    assert other_tag.id in tag_ids
    assert tag.id not in tag_ids


async def test_cannot_approve_already_resolved_request(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    approver_user = OktaUserFactory.create()

    db.session.add(user)
    db.session.add(approver_user)
    await db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name="Already Resolved",
        requested_group_description="Description",
        requested_group_type="okta_group",
        request_reason="Need group",
    ).execute()

    assert group_request is not None

    # First approval
    await ApproveGroupRequest(
        group_request=group_request,
        approver_user=approver_user,
        approval_reason="First approval",
    ).execute()

    await db.session.refresh(group_request)
    first_approval_time = group_request.resolved_at
    first_group_id = group_request.approved_group_id

    # Try to approve again
    await ApproveGroupRequest(
        group_request=group_request,
        approver_user=approver_user,
        approval_reason="Second approval attempt",
    ).execute()

    await db.session.refresh(group_request)
    # Should still have the same resolution details
    assert group_request.resolved_at == first_approval_time
    assert group_request.approved_group_id == first_group_id


async def test_cannot_approve_deleted_requester(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    approver_user = OktaUserFactory.create()

    db.session.add(user)
    db.session.add(approver_user)
    await db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name="Deleted Requester Group",
        requested_group_description="Requester will be deleted",
        requested_group_type="okta_group",
        request_reason="Need group",
    ).execute()

    assert group_request is not None

    # Delete the requester
    user.deleted_at = func.now()
    await db.session.commit()

    # Try to approve
    await ApproveGroupRequest(
        group_request=group_request,
        approver_user=approver_user,
        approval_reason="Should not work",
    ).execute()

    await db.session.refresh(group_request)
    # Should still be pending
    assert group_request.status == AccessRequestStatus.PENDING
    assert group_request.resolved_at is None


async def test_app_owner_auto_approves_own_app_group_request(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
) -> None:
    app_owner = OktaUserFactory.create()
    app_obj = AppFactory.create()

    db.session.add(app_owner)
    db.session.add(app_obj)
    await db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    # Create owner group for the app
    owner_group = AppGroupFactory.create(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_obj.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        app_id=app_obj.id,
        is_owner=True,
    )

    db.session.add(owner_group)
    await db.session.commit()

    # Make app_owner an owner of the app via its owner group
    db.session.add(
        OktaUserGroupMember(
            user_id=app_owner.id,
            group_id=owner_group.id,
            is_owner=True,
        )
    )
    await db.session.commit()

    # App owner creates a group request for their own app
    group_request = await CreateGroupRequest(
        requester_user=app_owner,
        requested_group_name=f"App-{app_obj.name}-NewTeam",
        requested_group_description="New team group for the app",
        requested_group_type="app_group",
        requested_app_id=app_obj.id,
        request_reason="Need team group for app",
    ).execute()

    # Verify the request was automatically approved
    assert group_request is not None

    await db.session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.APPROVED
    assert group_request.resolved_at is not None
    assert group_request.approved_group_id is not None
    assert group_request.resolution_reason == "Requester owns parent app and can create app groups"

    # Verify the group was created
    created_group = await db.session.get(OktaGroup, group_request.approved_group_id)
    assert created_group is not None
    assert type(created_group) is AppGroup
    assert created_group.name == f"App-{app_obj.name}-NewTeam"
    assert created_group.description == "New team group for the app"
    # eager-load the joined-inheritance subclass column (lazy IO would raise in async)
    await db.session.refresh(created_group, attribute_names=["app_id"])
    assert created_group.app_id == app_obj.id

    # Verify the requester (app owner) is set as the group owner
    ownerships = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == created_group.id)
            .where(OktaUserGroupMember.user_id == app_owner.id)
            .where(OktaUserGroupMember.is_owner.is_(True))
        )
    ).all()
    assert len(ownerships) == 1
    assert ownerships[0].created_reason == f"Group request approved: {group_request.request_reason}"


async def test_app_owner_auto_approves_own_app_group_request_tagged(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
) -> None:
    app_owner = OktaUserFactory.create()
    app_obj = AppFactory.create()
    tag = TagFactory.create(enabled=True, constraints={Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY: True})

    db.session.add(app_owner)
    db.session.add(app_obj)
    db.session.add(tag)
    await db.session.commit()

    # Apply tag to the app so it cascades to groups created for this app
    app_tag_map = AppTagMap(app_id=app_obj.id, tag_id=tag.id)
    db.session.add(app_tag_map)
    await db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    # Create owner group for the app
    owner_group = AppGroupFactory.create(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_obj.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        app_id=app_obj.id,
        is_owner=True,
    )

    db.session.add(owner_group)
    await db.session.commit()

    # Make app_owner an owner of the app via its owner group
    db.session.add(
        OktaUserGroupMember(
            user_id=app_owner.id,
            group_id=owner_group.id,
            is_owner=True,
        )
    )
    await db.session.commit()

    # App owner creates a group request for their own app
    # Include the tag in the request
    group_request = await CreateGroupRequest(
        requester_user=app_owner,
        requested_group_name=f"App-{app_obj.name}-NewTeam",
        requested_group_description="New team group for the app",
        requested_group_type="app_group",
        requested_app_id=app_obj.id,
        requested_group_tags=[tag.id],  # Include the tag in the request
        request_reason="Need team group for app",
    ).execute()

    # Verify the request was automatically approved
    assert group_request is not None

    await db.session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.APPROVED
    assert group_request.resolved_at is not None
    assert group_request.approved_group_id is not None
    assert group_request.resolution_reason == "Requester owns parent app and can create app groups"

    # Verify the group was created
    created_group = await db.session.get(OktaGroup, group_request.approved_group_id)
    assert created_group is not None
    assert type(created_group) is AppGroup
    assert created_group.name == f"App-{app_obj.name}-NewTeam"
    assert created_group.description == "New team group for the app"
    # eager-load the joined-inheritance subclass column (lazy IO would raise in async)
    await db.session.refresh(created_group, attribute_names=["app_id"])
    assert created_group.app_id == app_obj.id

    # Verify the app owner is *not* set as the group owner due to tags (will own implicitly via app ownership)
    ownerships = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == created_group.id)
            .where(OktaUserGroupMember.user_id == app_owner.id)
            .where(OktaUserGroupMember.is_owner.is_(True))
        )
    ).all()
    assert len(ownerships) == 0

    # Verify the tag was applied to the created group
    tag_mappings = {
        tag_map.tag_id
        for tag_map in (
            (
                await db.session.scalars(
                    select(OktaGroupTagMap)
                    .where(OktaGroupTagMap.group_id == created_group.id)
                    .where(OktaGroupTagMap.tag_id == tag.id)
                    .where(
                        or_(
                            OktaGroupTagMap.ended_at.is_(None),
                            OktaGroupTagMap.ended_at > func.now(),
                        )
                    )
                )
            ).all()
        )
    }

    assert len(tag_mappings) == 1


async def test_random_user_cannot_approve_group_request(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    user: OktaUser,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
) -> None:
    random_user = OktaUserFactory.create()

    db.session.add(user)
    db.session.add(random_user)
    await db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name="Random Approval Attempt",
        requested_group_description="Should not be approvable by random user",
        requested_group_type="okta_group",
        request_reason="Need group",
    ).execute()

    assert group_request is not None

    await ApproveGroupRequest(
        group_request=group_request,
        approver_user=random_user,
        approval_reason="Should not work",
    ).execute()

    await db.session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.PENDING
    assert group_request.resolved_at is None
    assert group_request.approved_group_id is None


async def test_app_owner_cannot_hijack_cross_app_group_via_resolved_name(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    app_owner = OktaUserFactory.create()
    app_a = AppFactory.create()
    app_b = AppFactory.create()

    db.session.add(user)
    db.session.add(app_owner)
    db.session.add(app_a)
    db.session.add(app_b)
    await db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    owner_group = AppGroupFactory.create(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_a.name}"
        f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        app_id=app_a.id,
        is_owner=True,
    )
    db.session.add(owner_group)
    await db.session.commit()
    db.session.add(OktaUserGroupMember(user_id=app_owner.id, group_id=owner_group.id, is_owner=True))
    await db.session.commit()

    sensitive_group = AppGroupFactory.create(name="App-Finance-Sensitive", app_id=app_b.id)
    db.session.add(sensitive_group)
    await db.session.commit()

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name=f"App-{app_a.name}-NewGroup",
        requested_group_description="New group for App A",
        requested_group_type="app_group",
        requested_app_id=app_a.id,
        request_reason="Need a new group",
    ).execute()
    assert group_request is not None

    # attacker overwrites resolved_group_name to target app b sensitive group
    group_request.resolved_group_name = sensitive_group.name
    await db.session.commit()

    await ApproveGroupRequest(
        group_request=group_request,
        approver_user=app_owner,
        approval_reason="Approved",
    ).execute()

    await db.session.refresh(group_request)
    assert (
        group_request.status == AccessRequestStatus.PENDING
    ), "approval must be blocked when resolved_group_name collides with a pre-existing group"
    assert group_request.resolved_at is None

    hijacked_ownership = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == sensitive_group.id)
            .where(OktaUserGroupMember.user_id == user.id)
            .where(OktaUserGroupMember.is_owner.is_(True))
            .where(OktaUserGroupMember.ended_at.is_(None))
        )
    ).first()
    assert hijacked_ownership is None


async def test_app_owner_cannot_hijack_okta_group_via_resolved_name(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    app_owner = OktaUserFactory.create()
    app_obj = AppFactory.create()

    db.session.add(user)
    db.session.add(app_owner)
    db.session.add(app_obj)
    await db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    owner_group = AppGroupFactory.create(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_obj.name}"
        f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        app_id=app_obj.id,
        is_owner=True,
    )
    db.session.add(owner_group)
    await db.session.commit()
    db.session.add(OktaUserGroupMember(user_id=app_owner.id, group_id=owner_group.id, is_owner=True))
    await db.session.commit()

    existing_okta_group = OktaGroupFactory.create(name="Okta-Platform-Admins")
    db.session.add(existing_okta_group)
    await db.session.commit()

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name=f"App-{app_obj.name}-NewGroup",
        requested_group_description="New group",
        requested_group_type="app_group",
        requested_app_id=app_obj.id,
        request_reason="Need a new group",
    ).execute()
    assert group_request is not None

    group_request.resolved_group_name = existing_okta_group.name
    await db.session.commit()

    await ApproveGroupRequest(
        group_request=group_request,
        approver_user=app_owner,
        approval_reason="Approved",
    ).execute()

    await db.session.refresh(group_request)
    assert (
        group_request.status == AccessRequestStatus.PENDING
    ), "approval must be blocked when resolved_group_name collides with a pre-existing OktaGroup"
    assert group_request.resolved_at is None

    hijacked_ownership = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == existing_okta_group.id)
            .where(OktaUserGroupMember.user_id == user.id)
            .where(OktaUserGroupMember.is_owner.is_(True))
            .where(OktaUserGroupMember.ended_at.is_(None))
        )
    ).first()
    assert hijacked_ownership is None


async def test_app_owner_cannot_hijack_role_group_via_resolved_name(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    app_owner = OktaUserFactory.create()
    app_obj = AppFactory.create()

    db.session.add(user)
    db.session.add(app_owner)
    db.session.add(app_obj)
    await db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    owner_group = AppGroupFactory.create(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_obj.name}"
        f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        app_id=app_obj.id,
        is_owner=True,
    )
    db.session.add(owner_group)
    await db.session.commit()
    db.session.add(OktaUserGroupMember(user_id=app_owner.id, group_id=owner_group.id, is_owner=True))
    await db.session.commit()

    existing_role_group = RoleGroupFactory.create(name="Role-Security-Engineers")
    db.session.add(existing_role_group)
    await db.session.commit()

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name=f"App-{app_obj.name}-NewGroup",
        requested_group_description="New group",
        requested_group_type="app_group",
        requested_app_id=app_obj.id,
        request_reason="Need a new group",
    ).execute()
    assert group_request is not None

    group_request.resolved_group_name = existing_role_group.name
    await db.session.commit()

    await ApproveGroupRequest(
        group_request=group_request,
        approver_user=app_owner,
        approval_reason="Approved",
    ).execute()

    await db.session.refresh(group_request)
    assert (
        group_request.status == AccessRequestStatus.PENDING
    ), "approval must be blocked when resolved_group_name collides with a pre-existing RoleGroup"
    assert group_request.resolved_at is None

    hijacked_ownership = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == existing_role_group.id)
            .where(OktaUserGroupMember.user_id == user.id)
            .where(OktaUserGroupMember.is_owner.is_(True))
            .where(OktaUserGroupMember.ended_at.is_(None))
        )
    ).first()
    assert hijacked_ownership is None


async def test_app_owner_cannot_hijack_group_via_resolved_name_case_insensitive(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    app_owner = OktaUserFactory.create()
    app_a = AppFactory.create()
    app_b = AppFactory.create()

    db.session.add(user)
    db.session.add(app_owner)
    db.session.add(app_a)
    db.session.add(app_b)
    await db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    owner_group = AppGroupFactory.create(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_a.name}"
        f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        app_id=app_a.id,
        is_owner=True,
    )
    db.session.add(owner_group)
    await db.session.commit()
    db.session.add(OktaUserGroupMember(user_id=app_owner.id, group_id=owner_group.id, is_owner=True))
    await db.session.commit()

    sensitive_group = AppGroupFactory.create(name="App-Finance-Sensitive", app_id=app_b.id)
    db.session.add(sensitive_group)
    await db.session.commit()

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name=f"App-{app_a.name}-NewGroup",
        requested_group_description="New group for App A",
        requested_group_type="app_group",
        requested_app_id=app_a.id,
        request_reason="Need a new group",
    ).execute()
    assert group_request is not None

    # case-insensitive lookup should still collides
    group_request.resolved_group_name = sensitive_group.name.upper()
    await db.session.commit()

    await ApproveGroupRequest(
        group_request=group_request,
        approver_user=app_owner,
        approval_reason="Approved",
    ).execute()

    await db.session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.PENDING, "case-insensitive name collision must also be blocked"
    assert group_request.resolved_at is None

    hijacked_ownership = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == sensitive_group.id)
            .where(OktaUserGroupMember.user_id == user.id)
            .where(OktaUserGroupMember.is_owner.is_(True))
            .where(OktaUserGroupMember.ended_at.is_(None))
        )
    ).first()
    assert hijacked_ownership is None


async def test_cannot_approve_okta_group_with_reserved_app_owners_name(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    app_owner = OktaUserFactory.create()
    app_obj = AppFactory.create()

    db.session.add(user)
    db.session.add(app_owner)
    db.session.add(app_obj)
    await db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    owner_group = AppGroupFactory.create(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_obj.name}"
        f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        app_id=app_obj.id,
        is_owner=True,
    )
    db.session.add(owner_group)
    await db.session.commit()
    db.session.add(OktaUserGroupMember(user_id=app_owner.id, group_id=owner_group.id, is_owner=True))
    await db.session.commit()

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name=f"App-{app_obj.name}-NewGroup",
        requested_group_description="New group",
        requested_group_type="app_group",
        requested_app_id=app_obj.id,
        request_reason="Need a new group",
    ).execute()
    assert group_request is not None

    group_request.resolved_group_name = "App-Payments-Owners"
    group_request.resolved_group_type = "okta_group"
    await db.session.commit()

    await ApproveGroupRequest(
        group_request=group_request,
        approver_user=app_owner,
        approval_reason="Approved",
    ).execute()

    await db.session.refresh(group_request)
    assert (
        group_request.status == AccessRequestStatus.PENDING
    ), "approval must be blocked when resolved_group_name uses the reserved App- prefix for a non-app_group type"
    assert group_request.resolved_at is None

    assert (
        await db.session.scalars(
            select(OktaGroup).where(OktaGroup.name == "App-Payments-Owners").where(OktaGroup.deleted_at.is_(None))
        )
    ).first() is None


async def test_cannot_approve_role_group_with_reserved_app_owners_name(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    app_owner = OktaUserFactory.create()
    app_obj = AppFactory.create()

    db.session.add(user)
    db.session.add(app_owner)
    db.session.add(app_obj)
    await db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    owner_group = AppGroupFactory.create(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_obj.name}"
        f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        app_id=app_obj.id,
        is_owner=True,
    )
    db.session.add(owner_group)
    await db.session.commit()
    db.session.add(OktaUserGroupMember(user_id=app_owner.id, group_id=owner_group.id, is_owner=True))
    await db.session.commit()

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name=f"App-{app_obj.name}-NewGroup",
        requested_group_description="New group",
        requested_group_type="app_group",
        requested_app_id=app_obj.id,
        request_reason="Need a new group",
    ).execute()
    assert group_request is not None

    group_request.resolved_group_name = "App-Payments-Owners"
    group_request.resolved_group_type = "role_group"
    await db.session.commit()

    await ApproveGroupRequest(
        group_request=group_request,
        approver_user=app_owner,
        approval_reason="Approved",
    ).execute()

    await db.session.refresh(group_request)
    assert (
        group_request.status == AccessRequestStatus.PENDING
    ), "approval must be blocked when resolved_group_name uses the reserved App- prefix for a non-app_group type"
    assert group_request.resolved_at is None

    assert (
        await db.session.scalars(
            select(OktaGroup).where(OktaGroup.name == "App-Payments-Owners").where(OktaGroup.deleted_at.is_(None))
        )
    ).first() is None


async def test_cannot_approve_okta_group_with_any_reserved_app_prefix(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    app_owner = OktaUserFactory.create()
    app_obj = AppFactory.create()

    db.session.add(user)
    db.session.add(app_owner)
    db.session.add(app_obj)
    await db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    owner_group = AppGroupFactory.create(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_obj.name}"
        f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        app_id=app_obj.id,
        is_owner=True,
    )
    db.session.add(owner_group)
    await db.session.commit()
    db.session.add(OktaUserGroupMember(user_id=app_owner.id, group_id=owner_group.id, is_owner=True))
    await db.session.commit()

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name=f"App-{app_obj.name}-NewGroup",
        requested_group_description="New group",
        requested_group_type="app_group",
        requested_app_id=app_obj.id,
        request_reason="Need a new group",
    ).execute()
    assert group_request is not None

    # Non-Owners suffix: rule is broader than just App-*-Owners
    group_request.resolved_group_name = "App-Payments-Members"
    group_request.resolved_group_type = "okta_group"
    await db.session.commit()

    await ApproveGroupRequest(
        group_request=group_request,
        approver_user=app_owner,
        approval_reason="Approved",
    ).execute()

    await db.session.refresh(group_request)
    assert (
        group_request.status == AccessRequestStatus.PENDING
    ), "approval must be blocked for any non-app_group resolved_group_name that starts with the App- prefix"
    assert group_request.resolved_at is None

    assert (
        await db.session.scalars(
            select(OktaGroup).where(OktaGroup.name == "App-Payments-Members").where(OktaGroup.deleted_at.is_(None))
        )
    ).first() is None


async def test_cannot_approve_app_group_request_with_owners_group_name(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    app_owner = OktaUserFactory.create()
    app_obj = AppFactory.create()

    db.session.add(user)
    db.session.add(app_owner)
    db.session.add(app_obj)
    await db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    owner_group = AppGroupFactory.create(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_obj.name}"
        f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        app_id=app_obj.id,
        is_owner=True,
    )
    db.session.add(owner_group)
    await db.session.commit()
    db.session.add(OktaUserGroupMember(user_id=app_owner.id, group_id=owner_group.id, is_owner=True))
    await db.session.commit()

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name=f"App-{app_obj.name}-NewGroup",
        requested_group_description="New group",
        requested_group_type="app_group",
        requested_app_id=app_obj.id,
        request_reason="Need a new group",
    ).execute()
    assert group_request is not None

    owners_group_name = (
        f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_obj.name}"
        f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}"
    )
    group_request.resolved_group_name = owners_group_name
    group_request.resolved_group_type = "app_group"
    await db.session.commit()

    await ApproveGroupRequest(
        group_request=group_request,
        approver_user=app_owner,
        approval_reason="Approved",
    ).execute()

    await db.session.refresh(group_request)
    assert (
        group_request.status == AccessRequestStatus.PENDING
    ), "approval must be blocked when resolved_group_name matches the App-*-Owners pattern even for app_group type"
    assert group_request.resolved_at is None

    assert (
        await db.session.scalars(
            select(OktaGroup)
            .where(OktaGroup.name == owners_group_name)
            .where(OktaGroup.deleted_at.is_(None))
            .where(OktaGroup.id != owner_group.id)
        )
    ).first() is None


async def test_cannot_approve_non_role_group_request_with_role_prefix(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    admin = OktaUserFactory.create()
    db.session.add(user)
    db.session.add(admin)
    await db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    access_admin_group = RoleGroupFactory.create(name="App-Access-Owners")
    db.session.add(access_admin_group)
    await db.session.commit()
    db.session.add(OktaUserGroupMember(user_id=admin.id, group_id=access_admin_group.id, is_owner=False))
    await db.session.commit()

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name=f"{RoleGroup.ROLE_GROUP_NAME_PREFIX}Admins",
        requested_group_description="A role group",
        requested_group_type="role_group",
        request_reason="Need a role",
    ).execute()
    assert group_request is not None

    # Override the resolved name/type to use the Role- prefix with a non-role type
    group_request.resolved_group_name = f"{RoleGroup.ROLE_GROUP_NAME_PREFIX}Admins"
    group_request.resolved_group_type = "okta_group"
    await db.session.commit()

    await ApproveGroupRequest(
        group_request=group_request,
        approver_user=admin,
        approval_reason="Approved",
    ).execute()

    await db.session.refresh(group_request)
    assert (
        group_request.status == AccessRequestStatus.PENDING
    ), "approval must be blocked for any non-role_group resolved_group_name that starts with the Role- prefix"
    assert group_request.resolved_at is None


async def test_group_request_list_filters_via_http(client: AsyncClient, db: Db, url_for: Any) -> None:
    """`status`, `requester_user_id`, `requested_group_type`,
    `requested_app_id` and `q` each narrow /api/group-requests. Seed two
    requests of different types/requesters/apps so each filter must
    *exclude* the other to pass — a regression that returns everything
    would still match by ID."""
    from api.operations import CreateGroupRequest

    target_user = OktaUserFactory.create()
    other_user = OktaUserFactory.create()
    target_app = AppFactory.create()
    db.session.add_all([target_user, other_user, target_app])
    await db.session.commit()

    target_gr = await CreateGroupRequest(
        requester_user=target_user,
        requested_group_name="ZelaTargetOktaGroup",
        requested_group_description="zela target desc",
        requested_group_type="okta_group",
        requested_app_id=None,
        requested_group_tags=[],
        requested_ownership_ending_at=None,
        request_reason="please",
    ).execute()
    other_gr = await CreateGroupRequest(
        requester_user=other_user,
        requested_group_name=f"App-{target_app.name}-OtherDistinctApp",
        requested_group_description="distinct app group desc",
        requested_group_type="app_group",
        requested_app_id=target_app.id,
        requested_group_tags=[],
        requested_ownership_ending_at=None,
        request_reason="please",
    ).execute()
    assert target_gr is not None and other_gr is not None

    list_url = url_for("api-group-requests.group_requests")

    def ids(rep: Any) -> list[str]:
        return [r["id"] for r in rep.json()["items"]]

    rep = await client.get(list_url, params={"status": "PENDING"})
    assert rep.status_code == 200
    assert {target_gr.id, other_gr.id}.issubset(set(ids(rep)))

    rep = await client.get(list_url, params={"requester_user_id": target_user.id})
    assert rep.status_code == 200
    found = ids(rep)
    assert target_gr.id in found and other_gr.id not in found

    rep = await client.get(list_url, params={"requested_group_type": "okta_group"})
    assert rep.status_code == 200
    found = ids(rep)
    assert target_gr.id in found and other_gr.id not in found

    rep = await client.get(list_url, params={"requested_app_id": target_app.id})
    assert rep.status_code == 200
    found = ids(rep)
    assert other_gr.id in found and target_gr.id not in found

    rep = await client.get(list_url, params={"q": "ZelaTargetOktaGroup"})
    assert rep.status_code == 200
    found = ids(rep)
    assert target_gr.id in found and other_gr.id not in found


async def test_post_group_request_validation_via_http(
    client: AsyncClient, db: Db, user: OktaUser, mock_user: Any, url_for: Any
) -> None:
    """POST /api/group-requests pre-validates: deleted requester → 403,
    unknown tag → 400, and `app_group` without `requested_app_id` → 400
    (the Pydantic discriminator catches it before the handler runs)."""
    from datetime import datetime, timezone

    create_url = url_for("api-group-requests.group_requests_create")

    # (a) Deleted requester → 403
    deleted = OktaUserFactory.create(deleted_at=datetime.now(timezone.utc))
    db.session.add(deleted)
    await db.session.commit()
    mock_user(deleted.id)
    rep = await client.post(
        create_url,
        json={
            "requested_group_name": "Foo",
            "requested_group_description": "x",
            "requested_group_type": "okta_group",
            "requested_group_tags": [],
        },
    )
    assert rep.status_code == 403
    mock_user(None)  # restore default

    # (b) Unknown tag → 400
    db.session.add(user)
    await db.session.commit()
    mock_user(user.id)
    rep = await client.post(
        create_url,
        json={
            "requested_group_name": "Foo",
            "requested_group_description": "x",
            "requested_group_type": "okta_group",
            "requested_group_tags": ["tag-does-not-exist"],
        },
    )
    assert rep.status_code == 400
    assert "tags not found" in rep.text

    # (c) app_group missing requested_app_id → 400 (Pydantic discriminator
    # rejects the missing required field on _AppGroupRequestBody).
    rep = await client.post(
        create_url,
        json={
            "requested_group_name": "Foo",
            "requested_group_description": "x",
            "requested_group_type": "app_group",
        },
    )
    assert rep.status_code == 400


async def test_post_group_request_app_id_must_exist_unknown(
    client: AsyncClient, db: Db, user: OktaUser, mock_user: Any, url_for: Any
) -> None:
    """`requested_app_id` that does not match any App row → 404 "App not found".
    The router must verify the app exists before invoking
    `CreateGroupRequest`."""
    db.session.add(user)
    await db.session.commit()
    mock_user(user.id)
    rep = await client.post(
        url_for("api-group-requests.group_requests_create"),
        json={
            "requested_group_name": "Foo",
            "requested_group_description": "x",
            "requested_group_type": "app_group",
            "requested_app_id": "nonexistent-app-id",
        },
    )
    assert rep.status_code == 404
    assert "App not found" in rep.text


async def test_post_group_request_app_id_must_exist_deleted(
    client: AsyncClient, db: Db, user: OktaUser, mock_user: Any, url_for: Any
) -> None:
    """`requested_app_id` pointing at a soft-deleted App → 404 (the resource
    queries `App.deleted_at.is_(None)` before accepting the request)."""
    db.session.add(user)
    deleted_app = AppFactory.create(name="DeletedApp", deleted_at=datetime.now(timezone.utc))
    db.session.add(deleted_app)
    await db.session.commit()
    mock_user(user.id)
    rep = await client.post(
        url_for("api-group-requests.group_requests_create"),
        json={
            "requested_group_name": "Foo",
            "requested_group_description": "x",
            "requested_group_type": "app_group",
            "requested_app_id": deleted_app.id,
        },
    )
    assert rep.status_code == 404
    assert "App not found" in rep.text


async def test_post_group_request_tag_ids_must_be_undeleted(
    client: AsyncClient, db: Db, user: OktaUser, mock_user: Any, url_for: Any
) -> None:
    """A soft-deleted tag id must not be accepted — the router filters
    `Tag.deleted_at.is_(None)` before counting matches against the
    requested tag list."""
    from tests.factories import TagFactory

    db.session.add(user)
    deleted_tag = TagFactory.create(name="DeletedTag", deleted_at=datetime.now(timezone.utc))
    db.session.add(deleted_tag)
    await db.session.commit()
    mock_user(user.id)
    rep = await client.post(
        url_for("api-group-requests.group_requests_create"),
        json={
            "requested_group_name": "Foo",
            "requested_group_description": "x",
            "requested_group_type": "okta_group",
            "requested_group_tags": [deleted_tag.id],
        },
    )
    assert rep.status_code == 400
    assert "tags not found" in rep.text


async def test_put_group_request_ignores_legacy_resolution_reason_alias_via_http(
    client: AsyncClient, db: Db, user: OktaUser, url_for: Any
) -> None:
    """PUT /api/group-requests/{id} reads `reason` from the body (matching
    the access-request and role-request resolve endpoints). The legacy
    `resolution_reason` key is silently dropped, leaving the persisted
    resolution_reason empty."""
    db.session.add(user)
    await db.session.commit()

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name="ResolveAliasOktaGroup",
        requested_group_description="alias drop test",
        requested_group_type="okta_group",
        request_reason="please",
    ).execute()
    assert group_request is not None

    resolve_url = url_for("api-group-requests.group_request_by_id_put", group_request_id=group_request.id)
    rep = await client.put(resolve_url, json={"approved": False, "resolution_reason": "should be dropped"})
    assert rep.status_code == 200

    # Re-fetch: the resolve's deferred completion notification expunged the
    # request from the shared session (a plain refresh would fail — detached).
    group_request = await db.session.get(GroupRequest, group_request.id)
    assert group_request.status == AccessRequestStatus.REJECTED
    assert group_request.resolution_reason == ""


async def test_put_group_request_persists_reason_via_http(
    client: AsyncClient, db: Db, user: OktaUser, url_for: Any
) -> None:
    """PUT /api/group-requests/{id} stores the body's `reason` verbatim on
    the resolved request's `resolution_reason` column (the DB column name
    is preserved; only the request-body key was renamed)."""
    db.session.add(user)
    await db.session.commit()

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name="ResolveResolutionReasonOktaGroup",
        requested_group_description="reason test",
        requested_group_type="okta_group",
        request_reason="please",
    ).execute()
    assert group_request is not None

    resolve_url = url_for("api-group-requests.group_request_by_id_put", group_request_id=group_request.id)
    rep = await client.put(resolve_url, json={"approved": False, "reason": "duplicate work"})
    assert rep.status_code == 200

    # Re-fetch: the resolve's deferred completion notification expunged the
    # request from the shared session (a plain refresh would fail — detached).
    group_request = await db.session.get(GroupRequest, group_request.id)
    assert group_request.status == AccessRequestStatus.REJECTED
    assert group_request.resolution_reason == "duplicate work"


async def test_put_group_request_app_owner_cannot_escalate_to_role_group(
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
    mock_user: Any,
    url_for: Any,
) -> None:
    """An app owner authorized to approve an `app_group` request
    against their own app must not be able to escalate the resolution into
    creating a group for another app or group type by supplying for eg.
    `resolved_group_type="role_group"` in the PUT body. Only Access admins
    can mint vanilla and role groups via the normal POST /api/groups path,
    so the group-request PUT must not become a backdoor that bypasses that
    admin check.
    """
    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    # Alice is the app owner of Foo. She is NOT an Access admin.
    alice = OktaUserFactory.create()
    foo_app = AppFactory.create()
    db.session.add(user)
    db.session.add(alice)
    db.session.add(foo_app)
    await db.session.commit()

    owner_group = AppGroupFactory.create(
        name=(
            f"{AppGroup.APP_GROUP_NAME_PREFIX}{foo_app.name}"
            f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}"
        ),
        app_id=foo_app.id,
        is_owner=True,
    )
    db.session.add(owner_group)
    await db.session.commit()
    # is_owner=True makes Alice a *manager* of app Foo
    db.session.add(OktaUserGroupMember(user_id=alice.id, group_id=owner_group.id, is_owner=True))
    await db.session.commit()

    # Regular user files an app_group request against Foo. Alice is a valid
    # approver for this request because she owns Foo.
    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name=f"App-{foo_app.name}-Members",
        requested_group_description="legit app group",
        requested_group_type="app_group",
        requested_app_id=foo_app.id,
        request_reason="need access",
    ).execute()
    assert group_request is not None

    # Alice approves, but flips resolved_group_type to role_group and names
    # the group with the Role- prefix. resolved_app_id stays on Foo so the
    # in-op authz check in ApproveGroupRequest (which only looks at
    # resolved_app_id) still passes.
    mock_user(alice.id)
    resolve_url = url_for("api-group-requests.group_request_by_id_put", group_request_id=group_request.id)
    rep = await client.put(
        resolve_url,
        json={
            "approved": True,
            "resolved_group_type": "role_group",
            "resolved_group_name": "Role-evil",
            "resolution_reason": "lgtm",
        },
    )

    assert rep.status_code == 403
    # No RoleGroup should be created since Alice is only an
    # app owner but not an admin
    await db.session.refresh(group_request)
    role_evil = (
        await db.session.scalars(select(RoleGroup).where(func.lower(OktaGroup.name) == func.lower("Role-evil")))
    ).first()
    assert role_evil is None, (
        "App owner was able to escalate an app_group request into creating a RoleGroup "
        f"(PUT returned {rep.status_code}, request status={group_request.status})."
    )
    assert group_request.status == AccessRequestStatus.PENDING
    assert group_request.approved_group_id is None


async def test_put_group_request_app_owner_cannot_escalate_to_other_app(
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
    mock_user: Any,
    url_for: Any,
) -> None:
    """Non-admin app owner of Foo cannot approve an app_group request filed
    against Foo by flipping `resolved_app_id` to a different app (Bar) they
    don't own. Mirror of the type-escalation case for the app_id branch.
    """
    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    alice = OktaUserFactory.create()
    foo_app = AppFactory.create()
    bar_app = AppFactory.create()
    db.session.add(user)
    db.session.add(alice)
    db.session.add(foo_app)
    db.session.add(bar_app)
    await db.session.commit()

    foo_owner_group = AppGroupFactory.create(
        name=(
            f"{AppGroup.APP_GROUP_NAME_PREFIX}{foo_app.name}"
            f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}"
        ),
        app_id=foo_app.id,
        is_owner=True,
    )
    db.session.add(foo_owner_group)
    await db.session.commit()
    db.session.add(OktaUserGroupMember(user_id=alice.id, group_id=foo_owner_group.id, is_owner=True))
    await db.session.commit()

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name=f"App-{foo_app.name}-Members",
        requested_group_description="legit app group",
        requested_group_type="app_group",
        requested_app_id=foo_app.id,
        request_reason="need access",
    ).execute()
    assert group_request is not None

    mock_user(alice.id)
    resolve_url = url_for("api-group-requests.group_request_by_id_put", group_request_id=group_request.id)
    rep = await client.put(
        resolve_url,
        json={
            "approved": True,
            "resolved_app_id": bar_app.id,
            "resolved_group_name": f"App-{bar_app.name}-Members",
            "resolution_reason": "lgtm",
        },
    )

    assert rep.status_code == 403
    await db.session.refresh(group_request)
    # the 403 request's rollback expired bar_app; reload it before reading .id
    await db.session.refresh(bar_app)
    bar_group = (
        await db.session.scalars(
            select(AppGroup).where(AppGroup.app_id == bar_app.id).where(AppGroup.deleted_at.is_(None))
        )
    ).first()
    assert bar_group is None, "App owner of Foo escalated approval into creating a group attached to Bar."
    assert group_request.status == AccessRequestStatus.PENDING
    assert group_request.approved_group_id is None


async def test_approve_group_request_op_blocks_type_mismatch_for_non_admin(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    """Direct-op coverage for the mirror check in ApproveGroupRequest. The
    router-level test exercises the HTTP gate; this one bypasses the router
    so the in-op no-op path is what's being asserted on. With a mismatched
    resolved_group_type and a non-admin approver, the request must stay
    PENDING and no group may be created.
    """
    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    alice = OktaUserFactory.create()
    foo_app = AppFactory.create()
    db.session.add(user)
    db.session.add(alice)
    db.session.add(foo_app)
    await db.session.commit()

    foo_owner_group = AppGroupFactory.create(
        name=(
            f"{AppGroup.APP_GROUP_NAME_PREFIX}{foo_app.name}"
            f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}"
        ),
        app_id=foo_app.id,
        is_owner=True,
    )
    db.session.add(foo_owner_group)
    await db.session.commit()
    db.session.add(OktaUserGroupMember(user_id=alice.id, group_id=foo_owner_group.id, is_owner=True))
    await db.session.commit()

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name=f"App-{foo_app.name}-Members",
        requested_group_description="legit app group",
        requested_group_type="app_group",
        requested_app_id=foo_app.id,
        request_reason="need access",
    ).execute()
    assert group_request is not None

    # Persist the resolved_* overrides directly on the row, simulating a
    # caller that bypasses the router (e.g. another internal operation).
    group_request.resolved_group_type = "role_group"
    group_request.resolved_group_name = "Role-evil"
    await db.session.commit()

    await ApproveGroupRequest(
        group_request=group_request,
        approver_user=alice,
        approval_reason="lgtm",
    ).execute()

    await db.session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.PENDING
    assert group_request.approved_group_id is None
    role_evil = (
        await db.session.scalars(select(RoleGroup).where(func.lower(OktaGroup.name) == func.lower("Role-evil")))
    ).first()
    assert role_evil is None


async def test_approve_app_group_request_with_non_conforming_resolved_name_is_rejected(
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
    url_for: Any,
) -> None:
    """Approving an app group request whose resolved name lacks the
    "App-{app name}-" prefix is a 400 and leaves the request pending."""
    app_obj = AppFactory.create()
    db.session.add_all([user, app_obj])
    await db.session.commit()
    app_name = app_obj.name

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name=f"App-{app_name}-NewGroup",
        requested_group_description="New group",
        requested_group_type="app_group",
        requested_app_id=app_obj.id,
        request_reason="Need a new group",
    ).execute()
    assert group_request is not None

    resolve_url = url_for("api-group-requests.group_request_by_id_put", group_request_id=group_request.id)
    rep = await client.put(resolve_url, json={"approved": True, "reason": "ok", "resolved_group_name": "Whatever"})
    assert rep.status_code == 400
    assert rep.json()["detail"] == (
        f'App Group name "Whatever" should be prefixed with App name. For example: "App-{app_name}-"'
    )

    await db.session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.PENDING
    assert group_request.approved_group_id is None

    # A conforming resolved name approves cleanly
    rep = await client.put(
        resolve_url,
        json={"approved": True, "reason": "ok", "resolved_group_name": f"App-{app_name}-RenamedGroup"},
    )
    assert rep.status_code == 200

    # Re-fetch instead of reusing `group_request` — the approve's deferred
    # completion notification expunged it from the session (so a refresh would
    # fail), and mypy also narrows `status` to PENDING from the assert above.
    approved_request = await db.session.get(GroupRequest, group_request.id)
    assert approved_request is not None
    assert approved_request.status == AccessRequestStatus.APPROVED
    created_group = await db.session.get(OktaGroup, approved_request.approved_group_id)
    assert created_group.name == f"App-{app_name}-RenamedGroup"


async def test_approve_group_request_notify_false_suppresses_completion(
    db: Db, user: OktaUser, mocker: MockerFixture
) -> None:
    """`notify=False` suppresses the group-request completion DM; still APPROVED."""
    db.session.add(user)
    await db.session.commit()

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name="Notify Gate Group",
        requested_group_description="d",
        requested_group_type="okta_group",
        request_reason="please",
    ).execute()
    assert group_request is not None

    mocker.patch.object(okta, "create_group", side_effect=lambda name, desc: Group({"id": "00gNOTIFYGATEGRP001"}))
    mocker.patch.object(okta, "add_owner_to_group")
    admin = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()
    hook = get_notification_hook()
    completed_spy = mocker.patch.object(hook, "access_group_request_completed")

    await ApproveGroupRequest(group_request=group_request, approver_user=admin, notify=False).execute()

    assert (await db.session.get(GroupRequest, group_request.id)).status == AccessRequestStatus.APPROVED
    assert completed_spy.call_count == 0


async def test_reject_group_request_notify_false_suppresses_completion(
    db: Db, user: OktaUser, mocker: MockerFixture
) -> None:
    """`notify=False` suppresses the group-request completion DM; still REJECTED."""
    db.session.add(user)
    await db.session.commit()

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name="Notify Gate Group",
        requested_group_description="d",
        requested_group_type="okta_group",
        request_reason="please",
    ).execute()
    assert group_request is not None

    admin = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()
    hook = get_notification_hook()
    completed_spy = mocker.patch.object(hook, "access_group_request_completed")

    await RejectGroupRequest(group_request=group_request, current_user_id=admin, notify=False).execute()

    assert (await db.session.get(GroupRequest, group_request.id)).status == AccessRequestStatus.REJECTED
    assert completed_spy.call_count == 0
