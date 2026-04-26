from datetime import datetime, timedelta, timezone
from typing import cast, Protocol, Any

from factory import Faker
from fastapi.testclient import TestClient
from okta.models import Group
from pytest_mock import MockerFixture
from fastapi import FastAPI

from api.models import (
    AccessRequestStatus,
    AppGroup,
    AppTagMap,
    OktaGroup,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    OktaGroupTagMap,
    Tag,
)
from api.operations import CreateGroupRequest, ApproveGroupRequest, RejectGroupRequest
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


def test_create_group_request(
    app: FastAPI,
    client: TestClient,
    db: Any,
    user: OktaUser,
) -> None:
    db.session.add(user)
    db.session.commit()

    group_request = CreateGroupRequest(
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


def test_create_app_group_request(
    app: FastAPI,
    client: TestClient,
    db: Any,
    user: OktaUser,
) -> None:
    db.session.add(user)
    db.session.commit()

    app_obj = AppFactory.create()
    db.session.add(app_obj)
    db.session.commit()

    group_request = CreateGroupRequest(
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


def test_create_role_group_request(
    app: FastAPI,
    client: TestClient,
    db: Any,
    user: OktaUser,
) -> None:
    db.session.add(user)
    db.session.commit()

    group_request = CreateGroupRequest(
        requester_user=user,
        requested_group_name="Role-Engineering",
        requested_group_description="Engineering role",
        requested_group_type="role_group",
        request_reason="Need engineering role",
    ).execute()

    assert group_request is not None
    assert group_request.requested_group_type == "role_group"


def test_create_group_request_with_tags(
    app: FastAPI,
    client: TestClient,
    db: Any,
    user: OktaUser,
    tag: Tag,
) -> None:
    db.session.add(user)
    db.session.add(tag)
    db.session.commit()

    group_request = CreateGroupRequest(
        requester_user=user,
        requested_group_name="Tagged Group",
        requested_group_description="Group with tags",
        requested_group_type="okta_group",
        requested_group_tags=[tag.id],
        request_reason="Need tagged group",
    ).execute()

    assert group_request is not None
    assert tag.id in group_request.requested_group_tags


def test_create_group_request_with_ownership_ending_at(
    app: FastAPI,
    client: TestClient,
    db: Any,
    user: OktaUser,
) -> None:
    db.session.add(user)
    db.session.commit()

    # Request ownership for 60 days
    requested_ending_at = datetime.now(timezone.utc) + timedelta(days=60)

    group_request = CreateGroupRequest(
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
    db.session.refresh(group_request)

    # Should be close to the requested time (within a few seconds), make both timezone-aware
    stored_time = group_request.requested_ownership_ending_at
    if stored_time.tzinfo is None:
        stored_time = stored_time.replace(tzinfo=timezone.utc)
    assert abs((stored_time - requested_ending_at).total_seconds()) < 5


def test_create_group_request_tag_limits_ownership_time(
    app: FastAPI,
    client: TestClient,
    db: Any,
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
    db.session.commit()

    # Request ownership with no ending time (should be limited to 90 days by tag)
    group_request = CreateGroupRequest(
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
    db.session.refresh(group_request)
    stored_time = group_request.requested_ownership_ending_at
    if stored_time.tzinfo is None:
        stored_time = stored_time.replace(tzinfo=timezone.utc)

    # Should be set to approximately 90 days from now
    expected_ending = datetime.now(timezone.utc) + timedelta(days=90)
    time_diff = abs((stored_time - expected_ending).total_seconds())
    assert time_diff < 5  # Within 5 seconds of expected


def test_create_group_request_tag_reduces_requested_ownership_time(
    app: FastAPI,
    client: TestClient,
    db: Any,
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
    db.session.commit()

    # Request ownership for 90 days (should be reduced to 30 days)
    requested_ending_at = datetime.now(timezone.utc) + timedelta(days=90)

    group_request = CreateGroupRequest(
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
    db.session.refresh(group_request)
    stored_time = group_request.requested_ownership_ending_at
    if stored_time.tzinfo is None:
        stored_time = stored_time.replace(tzinfo=timezone.utc)

    # Should be set to approximately 30 days from now (not 90)
    expected_ending = datetime.now(timezone.utc) + timedelta(days=30)
    time_diff = abs((stored_time - expected_ending).total_seconds())
    assert time_diff < 5  # Within 5 seconds of expected


def test_approve_group_request_creates_group(
    app: FastAPI,
    client: TestClient,
    db: Any,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    admin = OktaUser.query.filter(OktaUser.email == app.config["CURRENT_OKTA_USER_EMAIL"]).first()

    db.session.add(user)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "async_add_user_to_group")
    mocker.patch.object(okta, "async_add_owner_to_group")

    group_request = CreateGroupRequest(
        requester_user=user,
        requested_group_name="New Group",
        requested_group_description="New group description",
        requested_group_type="okta_group",
        request_reason="Need this group",
    ).execute()

    assert group_request is not None

    ApproveGroupRequest(
        group_request=group_request,
        approver_user=admin,
        approval_reason="Approved",
    ).execute()

    db.session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.APPROVED
    assert group_request.resolved_at is not None
    assert group_request.resolver_user_id == admin.id
    assert group_request.approved_group_id is not None

    created_group = db.session.get(OktaGroup, group_request.approved_group_id)
    assert created_group is not None
    assert created_group.name == "New Group"
    assert created_group.description == "New group description"

    ownerships = (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.group_id == created_group.id)
        .filter(OktaUserGroupMember.user_id == user.id)
        .filter(OktaUserGroupMember.is_owner.is_(True))
        .all()
    )
    assert len(ownerships) == 1
    assert ownerships[0].created_reason == f"Group request approved: {group_request.request_reason}"


def test_approve_group_request_sets_owner_with_ending_time(
    app: FastAPI,
    client: TestClient,
    db: Any,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    admin = OktaUser.query.filter(OktaUser.email == app.config["CURRENT_OKTA_USER_EMAIL"]).first()

    db.session.add(user)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "async_add_user_to_group")
    mocker.patch.object(okta, "async_add_owner_to_group")

    # Request ownership for 60 days
    requested_ending_at = datetime.now(timezone.utc) + timedelta(days=60)

    group_request = CreateGroupRequest(
        requester_user=user,
        requested_group_name="Time Limited Ownership Group",
        requested_group_description="Group with time-limited ownership",
        requested_group_type="okta_group",
        requested_ownership_ending_at=requested_ending_at,
        request_reason="Need temporary ownership",
    ).execute()

    assert group_request is not None

    ApproveGroupRequest(
        group_request=group_request,
        approver_user=admin,
        approval_reason="Approved",
    ).execute()

    db.session.refresh(group_request)
    assert group_request.approved_group_id is not None
    created_group = db.session.get(OktaGroup, group_request.approved_group_id)

    # Check that the requester is an owner with the correct ending time
    ownerships = (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.group_id == created_group.id)
        .filter(OktaUserGroupMember.user_id == user.id)
        .filter(OktaUserGroupMember.is_owner.is_(True))
        .all()
    )
    assert len(ownerships) == 1
    assert ownerships[0].ended_at is not None

    # Make both timezone-aware
    stored_time = ownerships[0].ended_at
    if stored_time.tzinfo is None:
        stored_time = stored_time.replace(tzinfo=timezone.utc)

    # Should be close to the requested time
    time_diff = abs((stored_time - requested_ending_at).total_seconds())
    assert time_diff < 5


def test_approve_group_request_tag_limits_owner_ending_time(
    app: FastAPI,
    client: TestClient,
    db: Any,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    admin = OktaUser.query.filter(OktaUser.email == app.config["CURRENT_OKTA_USER_EMAIL"]).first()

    db.session.add(user)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "async_add_user_to_group")
    mocker.patch.object(okta, "async_add_owner_to_group")

    # Create a tag that limits ownership to 30 days
    tag = TagFactory.create(
        enabled=True,
        constraints={
            Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: 2592000  # in seconds
        },
    )
    db.session.add(tag)
    db.session.commit()

    # Request group with this tag, no ending time specified
    group_request = CreateGroupRequest(
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
    db.session.commit()

    ApproveGroupRequest(
        group_request=group_request,
        approver_user=admin,
        approval_reason="Approved with time limit",
    ).execute()

    db.session.refresh(group_request)
    assert group_request.approved_group_id is not None
    created_group = db.session.get(OktaGroup, group_request.approved_group_id)

    # Check that requester is an owner
    ownerships = (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.group_id == created_group.id)
        .filter(OktaUserGroupMember.user_id == user.id)
        .filter(OktaUserGroupMember.is_owner.is_(True))
        .all()
    )
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
    db.session.refresh(group_request)
    resolved_time = group_request.resolved_ownership_ending_at
    if resolved_time.tzinfo is None:
        resolved_time = resolved_time.replace(tzinfo=timezone.utc)
    assert resolved_time < requested_90_days


def test_approve_group_request_applies_tags(
    app: FastAPI,
    client: TestClient,
    db: Any,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
    tag: Tag,
) -> None:
    admin = OktaUser.query.filter(OktaUser.email == app.config["CURRENT_OKTA_USER_EMAIL"]).first()

    db.session.add(user)
    db.session.add(tag)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "async_add_user_to_group")
    mocker.patch.object(okta, "async_add_owner_to_group")

    group_request = CreateGroupRequest(
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
    db.session.commit()

    ApproveGroupRequest(
        group_request=group_request,
        approver_user=admin,
        approval_reason="Approved",
    ).execute()

    db.session.refresh(group_request)
    assert group_request.approved_group_id is not None
    created_group = db.session.get(OktaGroup, group_request.approved_group_id)

    # Check that the tag was applied
    tag_mappings = (
        OktaGroupTagMap.query.filter(OktaGroupTagMap.group_id == created_group.id)
        .filter(OktaGroupTagMap.tag_id == tag.id)
        .filter(
            db.or_(
                OktaGroupTagMap.ended_at.is_(None),
                OktaGroupTagMap.ended_at > db.func.now(),
            )
        )
        .all()
    )
    assert len(tag_mappings) == 1


def test_approve_group_request_sets_name(
    app: FastAPI,
    client: TestClient,
    db: Any,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    admin = OktaUser.query.filter(OktaUser.email == app.config["CURRENT_OKTA_USER_EMAIL"]).first()

    db.session.add(user)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "async_add_user_to_group")
    mocker.patch.object(okta, "async_add_owner_to_group")

    group_request = CreateGroupRequest(
        requester_user=user,
        requested_group_name="Specific Name",
        requested_group_description="Specific description text",
        requested_group_type="okta_group",
        request_reason="Need this name",
    ).execute()

    assert group_request is not None

    ApproveGroupRequest(
        group_request=group_request,
        approver_user=admin,
        approval_reason="Approved",
    ).execute()

    db.session.refresh(group_request)
    assert group_request.approved_group_id is not None
    created_group = db.session.get(OktaGroup, group_request.approved_group_id)
    assert created_group.name == "Specific Name"
    assert created_group.description == "Specific description text"


def test_approve_group_request_sets_type(
    app: FastAPI,
    client: TestClient,
    db: Any,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    admin = OktaUser.query.filter(OktaUser.email == app.config["CURRENT_OKTA_USER_EMAIL"]).first()
    app_obj = AppFactory.create()

    db.session.add(user)
    db.session.add(app_obj)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "async_add_user_to_group")
    mocker.patch.object(okta, "async_add_owner_to_group")

    # Test OktaGroup
    group_request_okta = CreateGroupRequest(
        requester_user=user,
        requested_group_name="Regular Group",
        requested_group_description="Description",
        requested_group_type="okta_group",
        request_reason="Need group",
    ).execute()

    assert group_request_okta is not None

    ApproveGroupRequest(
        group_request=group_request_okta,
        approver_user=admin,
        approval_reason="Approved",
    ).execute()

    db.session.refresh(group_request_okta)
    assert group_request_okta.status == AccessRequestStatus.APPROVED
    assert group_request_okta.resolver_user_id == admin.id
    assert group_request_okta.approved_group_id is not None

    created_okta_group = db.session.get(OktaGroup, group_request_okta.approved_group_id)
    assert type(created_okta_group) is OktaGroup

    # Test AppGroup
    group_request_app = CreateGroupRequest(
        requester_user=user,
        requested_group_name=f"App-{app_obj.name}-Users",
        requested_group_description="App group",
        requested_group_type="app_group",
        requested_app_id=app_obj.id,
        request_reason="Need app group",
    ).execute()

    assert group_request_app is not None

    ApproveGroupRequest(
        group_request=group_request_app,
        approver_user=admin,
        approval_reason="Approved",
    ).execute()

    db.session.refresh(group_request_app)
    assert group_request_app.status == AccessRequestStatus.APPROVED
    assert group_request_app.resolver_user_id == admin.id
    assert group_request_app.approved_group_id is not None

    created_app_group = db.session.get(OktaGroup, group_request_app.approved_group_id)
    assert type(created_app_group) is AppGroup
    assert created_app_group.app_id == app_obj.id

    # Test RoleGroup
    group_request_role = CreateGroupRequest(
        requester_user=user,
        requested_group_name="Role-Marketing",
        requested_group_description="Role group",
        requested_group_type="role_group",
        request_reason="Need role group",
    ).execute()

    assert group_request_role is not None

    ApproveGroupRequest(
        group_request=group_request_role,
        approver_user=admin,
        approval_reason="Approved",
    ).execute()

    db.session.refresh(group_request_role)
    assert group_request_role.status == AccessRequestStatus.APPROVED
    assert group_request_role.resolver_user_id == admin.id
    assert group_request_role.approved_group_id is not None

    created_role_group = db.session.get(OktaGroup, group_request_role.approved_group_id)
    assert type(created_role_group) is RoleGroup


def test_app_owner_can_approve_request(
    app: FastAPI,
    client: TestClient,
    db: Any,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    app_owner = OktaUserFactory.create()
    app_obj = AppFactory.create()

    db.session.add(user)
    db.session.add(app_owner)
    db.session.add(app_obj)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "async_add_user_to_group")
    mocker.patch.object(okta, "async_add_owner_to_group")

    # Owner group for the app
    owner_group = AppGroupFactory.create(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_obj.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        app_id=app_obj.id,
        is_owner=True,
    )

    db.session.add(owner_group)
    db.session.commit()

    # Add app_owner to owner group
    db.session.add(
        OktaUserGroupMember(
            user_id=app_owner.id,
            group_id=owner_group.id,
            is_owner=False,
        )
    )
    db.session.commit()

    group_request = CreateGroupRequest(
        requester_user=user,
        requested_group_name=f"App-{app_obj.name}-NewGroup",
        requested_group_description="New app group",
        requested_group_type="app_group",
        requested_app_id=app_obj.id,
        request_reason="Need app group",
    ).execute()

    assert group_request is not None

    # App owner should be able to approve
    ApproveGroupRequest(
        group_request=group_request,
        approver_user=app_owner,
        approval_reason="Approved by app owner",
    ).execute()

    db.session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.APPROVED
    assert group_request.resolver_user_id == app_owner.id


def test_app_owner_can_reject_request(
    app: FastAPI,
    client: TestClient,
    db: Any,
    user: OktaUser,
) -> None:
    app_owner = OktaUserFactory.create()
    app_obj = AppFactory.create()

    db.session.add(user)
    db.session.add(app_owner)
    db.session.add(app_obj)
    db.session.commit()

    # Create owner group for the app
    owner_group = AppGroupFactory.create(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_obj.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        app_id=app_obj.id,
        is_owner=True,
    )

    db.session.add(owner_group)
    db.session.commit()

    # Make app_owner an owner of the app via its owner group
    db.session.add(
        OktaUserGroupMember(
            user_id=app_owner.id,
            group_id=owner_group.id,
            is_owner=False,
        )
    )
    db.session.commit()

    group_request = CreateGroupRequest(
        requester_user=user,
        requested_group_name=f"App-{app_obj.name}-AnotherGroup",
        requested_group_description="Another app group",
        requested_group_type="app_group",
        requested_app_id=app_obj.id,
        request_reason="Need app group",
    ).execute()

    assert group_request is not None

    # App owner should be able to reject
    RejectGroupRequest(
        group_request=group_request,
        rejection_reason="Rejected by app owner",
        notify_requester=True,
        current_user_id=app_owner.id,
    ).execute()

    db.session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.REJECTED
    assert group_request.resolver_user_id == app_owner.id


def test_wrong_app_owner_cannot_approve_request(
    app: FastAPI,
    client: TestClient,
    db: Any,
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
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "async_add_user_to_group")
    mocker.patch.object(okta, "async_add_owner_to_group")

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
    db.session.commit()

    # Make app_owner an owner of 'app' but not 'other_app'
    db.session.add(
        OktaUserGroupMember(
            user_id=app_owner.id,
            group_id=owner_group.id,
            is_owner=False,
        )
    )
    db.session.commit()

    # Create request for other_app
    group_request = CreateGroupRequest(
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
    db.session.commit()

    # App owner of 'app' should NOT be able to approve request for 'other_app'
    ApproveGroupRequest(
        group_request=group_request,
        approver_user=app_owner,
        approval_reason="Should not be allowed",
    ).execute()

    db.session.refresh(group_request)
    # Request should still be pending (not approved)
    assert group_request.status == AccessRequestStatus.PENDING
    assert group_request.resolved_at is None


def test_admin_can_reject_request(
    app: FastAPI,
    client: TestClient,
    db: Any,
    user: OktaUser,
) -> None:
    admin = OktaUser.query.filter(OktaUser.email == app.config["CURRENT_OKTA_USER_EMAIL"]).first()

    db.session.add(user)
    db.session.add(admin)
    db.session.commit()

    group_request = CreateGroupRequest(
        requester_user=user,
        requested_group_name="Admin Rejected Group",
        requested_group_description="Group rejected by admin",
        requested_group_type="okta_group",
        request_reason="Need group",
    ).execute()

    assert group_request is not None

    RejectGroupRequest(
        group_request=group_request,
        rejection_reason="Rejected by admin",
        notify_requester=True,
        current_user_id=admin.id,
    ).execute()

    db.session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.REJECTED
    assert group_request.resolver_user_id == admin.id


def test_any_user_cannot_reject_request(
    app: FastAPI,
    client: TestClient,
    db: Any,
    user: OktaUser,
) -> None:
    other_user = OktaUserFactory.create()

    db.session.add(user)
    db.session.add(other_user)
    db.session.commit()

    group_request = CreateGroupRequest(
        requester_user=user,
        requested_group_name="Other User Can't Reject Group",
        requested_group_description="Group can't be rejected by non-admin user",
        requested_group_type="okta_group",
        request_reason="Need group",
    ).execute()

    assert group_request is not None

    RejectGroupRequest(
        group_request=group_request,
        rejection_reason="Rejected by other_user",
        notify_requester=True,
        current_user_id=other_user.id,
    ).execute()

    db.session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.PENDING
    assert group_request.resolved_at is None


def test_user_can_reject_own_request(
    app: FastAPI,
    client: TestClient,
    db: Any,
    user: OktaUser,
) -> None:
    db.session.add(user)
    db.session.commit()

    group_request = CreateGroupRequest(
        requester_user=user,
        requested_group_name="Self Rejected Group",
        requested_group_description="Group I don't want anymore",
        requested_group_type="okta_group",
        request_reason="Changed my mind",
    ).execute()

    assert group_request is not None

    RejectGroupRequest(
        group_request=group_request,
        rejection_reason="I changed my mind",
        notify_requester=False,
        current_user_id=user.id,
    ).execute()

    db.session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.REJECTED
    assert group_request.resolver_user_id == user.id


def test_user_cannot_approve_own_request(
    app: FastAPI,
    client: TestClient,
    db: Any,
    user: OktaUser,
) -> None:
    db.session.add(user)
    db.session.commit()

    group_request = CreateGroupRequest(
        requester_user=user,
        requested_group_name="Self Approval Attempt",
        requested_group_description="Trying to approve myself",
        requested_group_type="okta_group",
        request_reason="Want to self-approve",
    ).execute()

    assert group_request is not None

    # Attempt to self-approve should fail
    ApproveGroupRequest(
        group_request=group_request,
        approver_user=user,
        approval_reason="Self approval",
    ).execute()

    db.session.refresh(group_request)
    # Should still be pending
    assert group_request.status == AccessRequestStatus.PENDING
    assert group_request.resolved_at is None


def test_approver_can_modify_group_details(
    app: FastAPI,
    client: TestClient,
    db: Any,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    admin = OktaUser.query.filter(OktaUser.email == app.config["CURRENT_OKTA_USER_EMAIL"]).first()
    tag = TagFactory.create(enabled=True)
    other_tag = TagFactory.create(enabled=True)

    db.session.add(user)
    db.session.add(tag)
    db.session.add(other_tag)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "async_add_user_to_group")
    mocker.patch.object(okta, "async_add_owner_to_group")

    group_request = CreateGroupRequest(
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
    db.session.commit()

    ApproveGroupRequest(
        group_request=group_request,
        approver_user=admin,
        approval_reason="Approved with modifications",
    ).execute()

    db.session.refresh(group_request)
    assert group_request.approved_group_id is not None
    created_group = db.session.get(OktaGroup, group_request.approved_group_id)

    # Verify the modified values were used
    assert created_group.name == "Modified Name"
    assert created_group.description == "Modified description"

    # Check tags
    tag_mappings = (
        OktaGroupTagMap.query.filter(OktaGroupTagMap.group_id == created_group.id)
        .filter(
            db.or_(
                OktaGroupTagMap.ended_at.is_(None),
                OktaGroupTagMap.ended_at > db.func.now(),
            )
        )
        .all()
    )
    tag_ids = [tm.tag_id for tm in tag_mappings]
    assert other_tag.id in tag_ids
    assert tag.id not in tag_ids


def test_cannot_approve_already_resolved_request(
    app: FastAPI,
    client: TestClient,
    db: Any,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    approver_user = OktaUserFactory.create()

    db.session.add(user)
    db.session.add(approver_user)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "async_add_user_to_group")
    mocker.patch.object(okta, "async_add_owner_to_group")

    group_request = CreateGroupRequest(
        requester_user=user,
        requested_group_name="Already Resolved",
        requested_group_description="Description",
        requested_group_type="okta_group",
        request_reason="Need group",
    ).execute()

    assert group_request is not None

    # First approval
    ApproveGroupRequest(
        group_request=group_request,
        approver_user=approver_user,
        approval_reason="First approval",
    ).execute()

    db.session.refresh(group_request)
    first_approval_time = group_request.resolved_at
    first_group_id = group_request.approved_group_id

    # Try to approve again
    ApproveGroupRequest(
        group_request=group_request,
        approver_user=approver_user,
        approval_reason="Second approval attempt",
    ).execute()

    db.session.refresh(group_request)
    # Should still have the same resolution details
    assert group_request.resolved_at == first_approval_time
    assert group_request.approved_group_id == first_group_id


def test_cannot_approve_deleted_requester(
    app: FastAPI,
    client: TestClient,
    db: Any,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    approver_user = OktaUserFactory.create()

    db.session.add(user)
    db.session.add(approver_user)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "async_add_user_to_group")
    mocker.patch.object(okta, "async_add_owner_to_group")

    group_request = CreateGroupRequest(
        requester_user=user,
        requested_group_name="Deleted Requester Group",
        requested_group_description="Requester will be deleted",
        requested_group_type="okta_group",
        request_reason="Need group",
    ).execute()

    assert group_request is not None

    # Delete the requester
    user.deleted_at = db.func.now()
    db.session.commit()

    # Try to approve
    ApproveGroupRequest(
        group_request=group_request,
        approver_user=approver_user,
        approval_reason="Should not work",
    ).execute()

    db.session.refresh(group_request)
    # Should still be pending
    assert group_request.status == AccessRequestStatus.PENDING
    assert group_request.resolved_at is None


def test_app_owner_auto_approves_own_app_group_request(
    app: FastAPI,
    client: TestClient,
    db: Any,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
) -> None:
    app_owner = OktaUserFactory.create()
    app_obj = AppFactory.create()

    db.session.add(app_owner)
    db.session.add(app_obj)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "async_add_user_to_group")
    mocker.patch.object(okta, "async_add_owner_to_group")

    # Create owner group for the app
    owner_group = AppGroupFactory.create(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_obj.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        app_id=app_obj.id,
        is_owner=True,
    )

    db.session.add(owner_group)
    db.session.commit()

    # Make app_owner an owner of the app via its owner group
    db.session.add(
        OktaUserGroupMember(
            user_id=app_owner.id,
            group_id=owner_group.id,
            is_owner=True,
        )
    )
    db.session.commit()

    # App owner creates a group request for their own app
    group_request = CreateGroupRequest(
        requester_user=app_owner,
        requested_group_name=f"App-{app_obj.name}-NewTeam",
        requested_group_description="New team group for the app",
        requested_group_type="app_group",
        requested_app_id=app_obj.id,
        request_reason="Need team group for app",
    ).execute()

    # Verify the request was automatically approved
    assert group_request is not None

    db.session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.APPROVED
    assert group_request.resolved_at is not None
    assert group_request.approved_group_id is not None
    assert group_request.resolution_reason == "Requester owns parent app and can create app groups"

    # Verify the group was created
    created_group = db.session.get(OktaGroup, group_request.approved_group_id)
    assert created_group is not None
    assert type(created_group) is AppGroup
    assert created_group.name == f"App-{app_obj.name}-NewTeam"
    assert created_group.description == "New team group for the app"
    assert created_group.app_id == app_obj.id

    # Verify the requester (app owner) is set as the group owner
    ownerships = (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.group_id == created_group.id)
        .filter(OktaUserGroupMember.user_id == app_owner.id)
        .filter(OktaUserGroupMember.is_owner.is_(True))
        .all()
    )
    assert len(ownerships) == 1
    assert ownerships[0].created_reason == f"Group request approved: {group_request.request_reason}"


def test_app_owner_auto_approves_own_app_group_request_tagged(
    app: FastAPI,
    client: TestClient,
    db: Any,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
) -> None:
    app_owner = OktaUserFactory.create()
    app_obj = AppFactory.create()
    tag = TagFactory.create(enabled=True, constraints={Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY: True})

    db.session.add(app_owner)
    db.session.add(app_obj)
    db.session.add(tag)
    db.session.commit()

    # Apply tag to the app so it cascades to groups created for this app
    app_tag_map = AppTagMap(app_id=app_obj.id, tag_id=tag.id)
    db.session.add(app_tag_map)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "async_add_user_to_group")
    mocker.patch.object(okta, "async_add_owner_to_group")

    # Create owner group for the app
    owner_group = AppGroupFactory.create(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_obj.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        app_id=app_obj.id,
        is_owner=True,
    )

    db.session.add(owner_group)
    db.session.commit()

    # Make app_owner an owner of the app via its owner group
    db.session.add(
        OktaUserGroupMember(
            user_id=app_owner.id,
            group_id=owner_group.id,
            is_owner=True,
        )
    )
    db.session.commit()

    # App owner creates a group request for their own app
    # Include the tag in the request
    group_request = CreateGroupRequest(
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

    db.session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.APPROVED
    assert group_request.resolved_at is not None
    assert group_request.approved_group_id is not None
    assert group_request.resolution_reason == "Requester owns parent app and can create app groups"

    # Verify the group was created
    created_group = db.session.get(OktaGroup, group_request.approved_group_id)
    assert created_group is not None
    assert type(created_group) is AppGroup
    assert created_group.name == f"App-{app_obj.name}-NewTeam"
    assert created_group.description == "New team group for the app"
    assert created_group.app_id == app_obj.id

    # Verify the app owner is *not* set as the group owner due to tags (will own implicitly via app ownership)
    ownerships = (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.group_id == created_group.id)
        .filter(OktaUserGroupMember.user_id == app_owner.id)
        .filter(OktaUserGroupMember.is_owner.is_(True))
        .all()
    )
    assert len(ownerships) == 0

    # Verify the tag was applied to the created group
    tag_mappings = {
        tag_map.tag_id
        for tag_map in (
            OktaGroupTagMap.query.filter(OktaGroupTagMap.group_id == created_group.id)
            .filter(OktaGroupTagMap.tag_id == tag.id)
            .filter(
                db.or_(
                    OktaGroupTagMap.ended_at.is_(None),
                    OktaGroupTagMap.ended_at > db.func.now(),
                )
            )
            .all()
        )
    }

    assert len(tag_mappings) == 1


def test_random_user_cannot_approve_group_request(
    app: FastAPI,
    client: TestClient,
    db: Any,
    user: OktaUser,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
) -> None:
    random_user = OktaUserFactory.create()

    db.session.add(user)
    db.session.add(random_user)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "async_add_user_to_group")
    mocker.patch.object(okta, "async_add_owner_to_group")

    group_request = CreateGroupRequest(
        requester_user=user,
        requested_group_name="Random Approval Attempt",
        requested_group_description="Should not be approvable by random user",
        requested_group_type="okta_group",
        request_reason="Need group",
    ).execute()

    assert group_request is not None

    ApproveGroupRequest(
        group_request=group_request,
        approver_user=random_user,
        approval_reason="Should not work",
    ).execute()

    db.session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.PENDING
    assert group_request.resolved_at is None
    assert group_request.approved_group_id is None


def test_app_owner_cannot_hijack_cross_app_group_via_resolved_name(
    app: FastAPI,
    client: TestClient,
    db: Any,
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
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "async_add_user_to_group")
    mocker.patch.object(okta, "async_add_owner_to_group")

    owner_group = AppGroupFactory.create(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_a.name}"
        f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        app_id=app_a.id,
        is_owner=True,
    )
    db.session.add(owner_group)
    db.session.commit()
    db.session.add(OktaUserGroupMember(user_id=app_owner.id, group_id=owner_group.id, is_owner=False))
    db.session.commit()

    sensitive_group = AppGroupFactory.create(name="App-Finance-Sensitive", app_id=app_b.id)
    db.session.add(sensitive_group)
    db.session.commit()

    group_request = CreateGroupRequest(
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
    db.session.commit()

    ApproveGroupRequest(
        group_request=group_request,
        approver_user=app_owner,
        approval_reason="Approved",
    ).execute()

    db.session.refresh(group_request)
    assert (
        group_request.status == AccessRequestStatus.PENDING
    ), "approval must be blocked when resolved_group_name collides with a pre-existing group"
    assert group_request.resolved_at is None

    hijacked_ownership = (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.group_id == sensitive_group.id)
        .filter(OktaUserGroupMember.user_id == user.id)
        .filter(OktaUserGroupMember.is_owner.is_(True))
        .filter(OktaUserGroupMember.ended_at.is_(None))
        .first()
    )
    assert hijacked_ownership is None


def test_app_owner_cannot_hijack_okta_group_via_resolved_name(
    app: FastAPI,
    client: TestClient,
    db: Any,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    app_owner = OktaUserFactory.create()
    app_obj = AppFactory.create()

    db.session.add(user)
    db.session.add(app_owner)
    db.session.add(app_obj)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "async_add_user_to_group")
    mocker.patch.object(okta, "async_add_owner_to_group")

    owner_group = AppGroupFactory.create(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_obj.name}"
        f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        app_id=app_obj.id,
        is_owner=True,
    )
    db.session.add(owner_group)
    db.session.commit()
    db.session.add(OktaUserGroupMember(user_id=app_owner.id, group_id=owner_group.id, is_owner=False))
    db.session.commit()

    existing_okta_group = OktaGroupFactory.create(name="Okta-Platform-Admins")
    db.session.add(existing_okta_group)
    db.session.commit()

    group_request = CreateGroupRequest(
        requester_user=user,
        requested_group_name=f"App-{app_obj.name}-NewGroup",
        requested_group_description="New group",
        requested_group_type="app_group",
        requested_app_id=app_obj.id,
        request_reason="Need a new group",
    ).execute()
    assert group_request is not None

    group_request.resolved_group_name = existing_okta_group.name
    db.session.commit()

    ApproveGroupRequest(
        group_request=group_request,
        approver_user=app_owner,
        approval_reason="Approved",
    ).execute()

    db.session.refresh(group_request)
    assert (
        group_request.status == AccessRequestStatus.PENDING
    ), "approval must be blocked when resolved_group_name collides with a pre-existing OktaGroup"
    assert group_request.resolved_at is None

    hijacked_ownership = (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.group_id == existing_okta_group.id)
        .filter(OktaUserGroupMember.user_id == user.id)
        .filter(OktaUserGroupMember.is_owner.is_(True))
        .filter(OktaUserGroupMember.ended_at.is_(None))
        .first()
    )
    assert hijacked_ownership is None


def test_app_owner_cannot_hijack_role_group_via_resolved_name(
    app: FastAPI,
    client: TestClient,
    db: Any,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    app_owner = OktaUserFactory.create()
    app_obj = AppFactory.create()

    db.session.add(user)
    db.session.add(app_owner)
    db.session.add(app_obj)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "async_add_user_to_group")
    mocker.patch.object(okta, "async_add_owner_to_group")

    owner_group = AppGroupFactory.create(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_obj.name}"
        f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        app_id=app_obj.id,
        is_owner=True,
    )
    db.session.add(owner_group)
    db.session.commit()
    db.session.add(OktaUserGroupMember(user_id=app_owner.id, group_id=owner_group.id, is_owner=False))
    db.session.commit()

    existing_role_group = RoleGroupFactory.create(name="Role-Security-Engineers")
    db.session.add(existing_role_group)
    db.session.commit()

    group_request = CreateGroupRequest(
        requester_user=user,
        requested_group_name=f"App-{app_obj.name}-NewGroup",
        requested_group_description="New group",
        requested_group_type="app_group",
        requested_app_id=app_obj.id,
        request_reason="Need a new group",
    ).execute()
    assert group_request is not None

    group_request.resolved_group_name = existing_role_group.name
    db.session.commit()

    ApproveGroupRequest(
        group_request=group_request,
        approver_user=app_owner,
        approval_reason="Approved",
    ).execute()

    db.session.refresh(group_request)
    assert (
        group_request.status == AccessRequestStatus.PENDING
    ), "approval must be blocked when resolved_group_name collides with a pre-existing RoleGroup"
    assert group_request.resolved_at is None

    hijacked_ownership = (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.group_id == existing_role_group.id)
        .filter(OktaUserGroupMember.user_id == user.id)
        .filter(OktaUserGroupMember.is_owner.is_(True))
        .filter(OktaUserGroupMember.ended_at.is_(None))
        .first()
    )
    assert hijacked_ownership is None


def test_app_owner_cannot_hijack_group_via_resolved_name_case_insensitive(
    app: FastAPI,
    client: TestClient,
    db: Any,
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
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "async_add_user_to_group")
    mocker.patch.object(okta, "async_add_owner_to_group")

    owner_group = AppGroupFactory.create(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_a.name}"
        f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        app_id=app_a.id,
        is_owner=True,
    )
    db.session.add(owner_group)
    db.session.commit()
    db.session.add(OktaUserGroupMember(user_id=app_owner.id, group_id=owner_group.id, is_owner=False))
    db.session.commit()

    sensitive_group = AppGroupFactory.create(name="App-Finance-Sensitive", app_id=app_b.id)
    db.session.add(sensitive_group)
    db.session.commit()

    group_request = CreateGroupRequest(
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
    db.session.commit()

    ApproveGroupRequest(
        group_request=group_request,
        approver_user=app_owner,
        approval_reason="Approved",
    ).execute()

    db.session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.PENDING, "case-insensitive name collision must also be blocked"
    assert group_request.resolved_at is None

    hijacked_ownership = (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.group_id == sensitive_group.id)
        .filter(OktaUserGroupMember.user_id == user.id)
        .filter(OktaUserGroupMember.is_owner.is_(True))
        .filter(OktaUserGroupMember.ended_at.is_(None))
        .first()
    )
    assert hijacked_ownership is None


def test_cannot_approve_okta_group_with_reserved_app_owners_name(
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    app_owner = OktaUserFactory.create()
    app_obj = AppFactory.create()

    db.session.add(user)
    db.session.add(app_owner)
    db.session.add(app_obj)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "async_add_user_to_group")
    mocker.patch.object(okta, "async_add_owner_to_group")

    owner_group = AppGroupFactory.create(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_obj.name}"
        f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        app_id=app_obj.id,
        is_owner=True,
    )
    db.session.add(owner_group)
    db.session.commit()
    db.session.add(OktaUserGroupMember(user_id=app_owner.id, group_id=owner_group.id, is_owner=False))
    db.session.commit()

    group_request = CreateGroupRequest(
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
    db.session.commit()

    ApproveGroupRequest(
        group_request=group_request,
        approver_user=app_owner,
        approval_reason="Approved",
    ).execute()

    db.session.refresh(group_request)
    assert (
        group_request.status == AccessRequestStatus.PENDING
    ), "approval must be blocked when resolved_group_name uses the reserved App- prefix for a non-app_group type"
    assert group_request.resolved_at is None

    assert (
        OktaGroup.query.filter(OktaGroup.name == "App-Payments-Owners").filter(OktaGroup.deleted_at.is_(None)).first()
        is None
    )


def test_cannot_approve_role_group_with_reserved_app_owners_name(
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    app_owner = OktaUserFactory.create()
    app_obj = AppFactory.create()

    db.session.add(user)
    db.session.add(app_owner)
    db.session.add(app_obj)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "async_add_user_to_group")
    mocker.patch.object(okta, "async_add_owner_to_group")

    owner_group = AppGroupFactory.create(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_obj.name}"
        f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        app_id=app_obj.id,
        is_owner=True,
    )
    db.session.add(owner_group)
    db.session.commit()
    db.session.add(OktaUserGroupMember(user_id=app_owner.id, group_id=owner_group.id, is_owner=False))
    db.session.commit()

    group_request = CreateGroupRequest(
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
    db.session.commit()

    ApproveGroupRequest(
        group_request=group_request,
        approver_user=app_owner,
        approval_reason="Approved",
    ).execute()

    db.session.refresh(group_request)
    assert (
        group_request.status == AccessRequestStatus.PENDING
    ), "approval must be blocked when resolved_group_name uses the reserved App- prefix for a non-app_group type"
    assert group_request.resolved_at is None

    assert (
        OktaGroup.query.filter(OktaGroup.name == "App-Payments-Owners").filter(OktaGroup.deleted_at.is_(None)).first()
        is None
    )


def test_cannot_approve_okta_group_with_any_reserved_app_prefix(
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    app_owner = OktaUserFactory.create()
    app_obj = AppFactory.create()

    db.session.add(user)
    db.session.add(app_owner)
    db.session.add(app_obj)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "async_add_user_to_group")
    mocker.patch.object(okta, "async_add_owner_to_group")

    owner_group = AppGroupFactory.create(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_obj.name}"
        f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        app_id=app_obj.id,
        is_owner=True,
    )
    db.session.add(owner_group)
    db.session.commit()
    db.session.add(OktaUserGroupMember(user_id=app_owner.id, group_id=owner_group.id, is_owner=False))
    db.session.commit()

    group_request = CreateGroupRequest(
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
    db.session.commit()

    ApproveGroupRequest(
        group_request=group_request,
        approver_user=app_owner,
        approval_reason="Approved",
    ).execute()

    db.session.refresh(group_request)
    assert (
        group_request.status == AccessRequestStatus.PENDING
    ), "approval must be blocked for any non-app_group resolved_group_name that starts with the App- prefix"
    assert group_request.resolved_at is None

    assert (
        OktaGroup.query.filter(OktaGroup.name == "App-Payments-Members").filter(OktaGroup.deleted_at.is_(None)).first()
        is None
    )


def test_cannot_approve_app_group_request_with_owners_group_name(
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    app_owner = OktaUserFactory.create()
    app_obj = AppFactory.create()

    db.session.add(user)
    db.session.add(app_owner)
    db.session.add(app_obj)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "async_add_user_to_group")
    mocker.patch.object(okta, "async_add_owner_to_group")

    owner_group = AppGroupFactory.create(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_obj.name}"
        f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        app_id=app_obj.id,
        is_owner=True,
    )
    db.session.add(owner_group)
    db.session.commit()
    db.session.add(OktaUserGroupMember(user_id=app_owner.id, group_id=owner_group.id, is_owner=False))
    db.session.commit()

    group_request = CreateGroupRequest(
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
    db.session.commit()

    ApproveGroupRequest(
        group_request=group_request,
        approver_user=app_owner,
        approval_reason="Approved",
    ).execute()

    db.session.refresh(group_request)
    assert (
        group_request.status == AccessRequestStatus.PENDING
    ), "approval must be blocked when resolved_group_name matches the App-*-Owners pattern even for app_group type"
    assert group_request.resolved_at is None

    assert (
        OktaGroup.query.filter(OktaGroup.name == owners_group_name)
        .filter(OktaGroup.deleted_at.is_(None))
        .filter(OktaGroup.id != owner_group.id)
        .first()
        is None
    )


def test_cannot_approve_non_role_group_request_with_role_prefix(
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    admin = OktaUserFactory.create()
    db.session.add(user)
    db.session.add(admin)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "async_add_user_to_group")
    mocker.patch.object(okta, "async_add_owner_to_group")

    access_admin_group = RoleGroupFactory.create(name="App-Access-Owners")
    db.session.add(access_admin_group)
    db.session.commit()
    db.session.add(OktaUserGroupMember(user_id=admin.id, group_id=access_admin_group.id, is_owner=False))
    db.session.commit()

    group_request = CreateGroupRequest(
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
    db.session.commit()

    ApproveGroupRequest(
        group_request=group_request,
        approver_user=admin,
        approval_reason="Approved",
    ).execute()

    db.session.refresh(group_request)
    assert (
        group_request.status == AccessRequestStatus.PENDING
    ), "approval must be blocked for any non-role_group resolved_group_name that starts with the Role- prefix"
    assert group_request.resolved_at is None
