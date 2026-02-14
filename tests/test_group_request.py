import pytest
from datetime import datetime, timedelta, timezone
from typing import cast, Protocol

from factory import Faker
from flask import Flask
from flask.testing import FlaskClient
from flask_sqlalchemy import SQLAlchemy
from okta.models import Group
from pytest_mock import MockerFixture

from api.models import (
    AccessRequestStatus,
    App,
    AppGroup,
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
    TagFactory,
)


# Define a Protocol that includes the pystr method
class FakerWithPyStr(Protocol):
    def pystr(self) -> str: ...


def test_create_group_request(
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
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
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
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
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
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
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
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
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
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
    # Should be close to the requested time (within a few seconds)
    # Make both timezone-aware for comparison
    stored_time = group_request.requested_ownership_ending_at
    if stored_time.tzinfo is None:
        stored_time = stored_time.replace(tzinfo=timezone.utc)
    assert abs((stored_time - requested_ending_at).total_seconds()) < 5


def test_create_group_request_tag_limits_ownership_time(
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
    user: OktaUser,
) -> None:
    # Create a tag that limits ownership to 90 days (7776000 seconds)
    tag = TagFactory.create(
        enabled=True,
        constraints={
            Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: 7776000  # 90 days in seconds
        }
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
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
    user: OktaUser,
) -> None:
    # Create a tag that limits ownership to 30 days (2592000 seconds)
    tag = TagFactory.create(
        enabled=True,
        constraints={
            Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: 2592000  # 30 days in seconds
        }
    )

    db.session.add(user)
    db.session.add(tag)
    db.session.commit()

    # Request ownership for 90 days (should be reduced to 30 days by tag)
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
    
    # Should be less than the originally requested time
    assert stored_time < requested_ending_at


def test_approve_group_request_creates_group(
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    """Test that approving a group request creates the group"""
    approver_user = OktaUserFactory.create()

    db.session.add(user)
    db.session.add(approver_user)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )

    group_request = CreateGroupRequest(
        requester_user=user,
        requested_group_name="New Group",
        requested_group_description="New group description",
        requested_group_type="okta_group",
        request_reason="Need this group",
    ).execute()

    ApproveGroupRequest(
        group_request=group_request,
        approver_user=approver_user,
        approval_reason="Approved",
    ).execute()

    db.session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.APPROVED
    assert group_request.resolved_at is not None
    assert group_request.resolver_user_id == approver_user.id
    assert group_request.approved_group_id is not None

    # Verify the group was created
    created_group = db.session.get(OktaGroup, group_request.approved_group_id)
    assert created_group is not None
    assert created_group.name == "New Group"
    assert created_group.description == "New group description"


def test_approve_group_request_sets_requester_as_owner(
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    """Test that approving a group request sets the requester as an owner"""
    approver_user = OktaUserFactory.create()

    db.session.add(user)
    db.session.add(approver_user)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )

    group_request = CreateGroupRequest(
        requester_user=user,
        requested_group_name="Owned Group",
        requested_group_description="Group with owner",
        requested_group_type="okta_group",
        request_reason="Need ownership",
    ).execute()

    ApproveGroupRequest(
        group_request=group_request,
        approver_user=approver_user,
        approval_reason="Approved",
    ).execute()

    db.session.refresh(group_request)
    created_group = db.session.get(OktaGroup, group_request.approved_group_id)

    # Check that the requester is an owner
    ownerships = (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.group_id == created_group.id)
        .filter(OktaUserGroupMember.user_id == user.id)
        .filter(OktaUserGroupMember.is_owner.is_(True))
        .all()
    )
    assert len(ownerships) == 1
    assert ownerships[0].created_reason == group_request.request_reason


def test_approve_group_request_sets_owner_with_ending_time(
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    """Test that approving a group request with ownership ending time sets the owner with the correct ending time"""
    approver_user = OktaUserFactory.create()

    db.session.add(user)
    db.session.add(approver_user)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )

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

    ApproveGroupRequest(
        group_request=group_request,
        approver_user=approver_user,
        approval_reason="Approved",
    ).execute()

    db.session.refresh(group_request)
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
    
    # Make both timezone-aware for comparison
    stored_time = ownerships[0].ended_at
    if stored_time.tzinfo is None:
        stored_time = stored_time.replace(tzinfo=timezone.utc)
    
    # Should be close to the requested time (within a few seconds)
    time_diff = abs((stored_time - requested_ending_at).total_seconds())
    assert time_diff < 5


def test_approve_group_request_tag_limits_owner_ending_time(
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    """Test that tag ownership constraints reduce the resolved ownership ending time during approval"""
    approver_user = OktaUserFactory.create()

    db.session.add(user)
    db.session.add(approver_user)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )

    # Create a tag that limits ownership to 30 days (2592000 seconds)
    tag = TagFactory.create(
        enabled=True,
        constraints={
            Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: 2592000  # 30 days in seconds
        }
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

    assert group_request is not None, "CreateGroupRequest should return a GroupRequest object"

    # Approver sets resolved ownership ending time to 90 days (should be reduced to 30 by tag)
    requested_90_days = datetime.now(timezone.utc) + timedelta(days=90)
    group_request.resolved_ownership_ending_at = requested_90_days
    db.session.commit()

    ApproveGroupRequest(
        group_request=group_request,
        approver_user=approver_user,
        approval_reason="Approved with time limit",
    ).execute()

    db.session.refresh(group_request)
    created_group = db.session.get(OktaGroup, group_request.approved_group_id)

    # Check that the requester is an owner
    ownerships = (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.group_id == created_group.id)
        .filter(OktaUserGroupMember.user_id == user.id)
        .filter(OktaUserGroupMember.is_owner.is_(True))
        .all()
    )
    assert len(ownerships) == 1
    assert ownerships[0].ended_at is not None
    
    # Make both timezone-aware for comparison
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
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
    tag: Tag,
) -> None:
    """Test that approving a group request applies the requested tags"""
    approver_user = OktaUserFactory.create()

    db.session.add(user)
    db.session.add(tag)
    db.session.add(approver_user)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )

    group_request = CreateGroupRequest(
        requester_user=user,
        requested_group_name="Tagged Group",
        requested_group_description="Group with tags",
        requested_group_type="okta_group",
        requested_group_tags=[tag.id],
        request_reason="Need tagged group",
    ).execute()

    # Set resolved tags (approver could modify these)
    group_request.resolved_group_tags = [tag.id]
    db.session.commit()

    ApproveGroupRequest(
        group_request=group_request,
        approver_user=approver_user,
        approval_reason="Approved",
    ).execute()

    db.session.refresh(group_request)
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
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    """Test that the created group has the correct name"""
    approver_user = OktaUserFactory.create()

    db.session.add(user)
    db.session.add(approver_user)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )

    group_request = CreateGroupRequest(
        requester_user=user,
        requested_group_name="Specific Name",
        requested_group_description="Description",
        requested_group_type="okta_group",
        request_reason="Need this name",
    ).execute()

    ApproveGroupRequest(
        group_request=group_request,
        approver_user=approver_user,
        approval_reason="Approved",
    ).execute()

    db.session.refresh(group_request)
    created_group = db.session.get(OktaGroup, group_request.approved_group_id)
    assert created_group.name == "Specific Name"


def test_approve_group_request_sets_type(
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    approver_user = OktaUserFactory.create()
    app_obj = AppFactory.create()

    db.session.add(user)
    db.session.add(approver_user)
    db.session.add(app_obj)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )

    # Test OktaGroup
    group_request_okta = CreateGroupRequest(
        requester_user=user,
        requested_group_name="Regular Group",
        requested_group_description="Description",
        requested_group_type="okta_group",
        request_reason="Need group",
    ).execute()

    ApproveGroupRequest(
        group_request=group_request_okta,
        approver_user=approver_user,
        approval_reason="Approved",
    ).execute()

    db.session.refresh(group_request_okta)
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

    ApproveGroupRequest(
        group_request=group_request_app,
        approver_user=approver_user,
        approval_reason="Approved",
    ).execute()

    db.session.refresh(group_request_app)
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

    ApproveGroupRequest(
        group_request=group_request_role,
        approver_user=approver_user,
        approval_reason="Approved",
    ).execute()

    db.session.refresh(group_request_role)
    created_role_group = db.session.get(OktaGroup, group_request_role.approved_group_id)
    assert type(created_role_group) is RoleGroup


def test_approve_group_request_sets_description(
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
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

    group_request = CreateGroupRequest(
        requester_user=user,
        requested_group_name="Named Group",
        requested_group_description="Specific description text",
        requested_group_type="okta_group",
        request_reason="Need description",
    ).execute()

    ApproveGroupRequest(
        group_request=group_request,
        approver_user=approver_user,
        approval_reason="Approved",
    ).execute()

    db.session.refresh(group_request)
    created_group = db.session.get(OktaGroup, group_request.approved_group_id)
    assert created_group.description == "Specific description text"


def test_app_owner_can_approve_request(
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    """Test that an app owner can approve a group request for their app"""
    app_owner_user = OktaUserFactory.create()
    app_obj = AppFactory.create()

    db.session.add(user)
    db.session.add(app_owner_user)
    db.session.add(app_obj)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )

    # Create owner group for the app
    owner_group = AppGroupFactory.create(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_obj.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        app_id=app_obj.id,
        is_owner=True,
    )

    db.session.add(owner_group)
    db.session.commit()

    # Make app_owner_user an owner of the app via its owner group
    db.session.add(
        OktaUserGroupMember(
            user_id=app_owner_user.id,
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

    # App owner should be able to approve
    ApproveGroupRequest(
        group_request=group_request,
        approver_user=app_owner_user,
        approval_reason="Approved by app owner",
    ).execute()

    db.session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.APPROVED
    assert group_request.resolver_user_id == app_owner_user.id


def test_app_owner_can_reject_request(
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
    user: OktaUser,
) -> None:
    """Test that an app owner can reject a group request for their app"""
    app_owner_user = OktaUserFactory.create()
    app_obj = AppFactory.create()

    db.session.add(user)
    db.session.add(app_owner_user)
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

    # Make app_owner_user an owner of the app via its owner group
    db.session.add(
        OktaUserGroupMember(
            user_id=app_owner_user.id,
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

    # App owner should be able to reject
    RejectGroupRequest(
        group_request=group_request,
        rejection_reason="Rejected by app owner",
        notify_requester=True,
        current_user_id=app_owner_user.id,
    ).execute()

    db.session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.REJECTED
    assert group_request.resolver_user_id == app_owner_user.id


def test_wrong_app_owner_cannot_approve_request(
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    """Test that an app owner cannot approve a request for a different app"""
    app_owner_user = OktaUserFactory.create()
    app_obj = AppFactory.create()
    other_app = AppFactory.create()

    db.session.add(user)
    db.session.add(app_owner_user)
    db.session.add(app_obj)
    db.session.add(other_app)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )

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

    # Make app_owner_user an owner of 'app' but not 'other_app'
    db.session.add(
        OktaUserGroupMember(
            user_id=app_owner_user.id,
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

    # Set resolved_app_id (normally done in the PUT handler)
    group_request.resolved_app_id = other_app.id
    db.session.commit()

    # App owner of 'app' should NOT be able to approve request for 'other_app'
    result = ApproveGroupRequest(
        group_request=group_request,
        approver_user=app_owner_user,
        approval_reason="Should not be allowed",
    ).execute()

    db.session.refresh(group_request)
    # Request should still be pending (not approved)
    assert group_request.status == AccessRequestStatus.PENDING
    assert group_request.resolved_at is None


def test_admin_can_approve_request(
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    """Test that an admin can approve any group request"""
    admin_user = OktaUserFactory.create()

    db.session.add(user)
    db.session.add(admin_user)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )

    group_request = CreateGroupRequest(
        requester_user=user,
        requested_group_name="Admin Approved Group",
        requested_group_description="Group approved by admin",
        requested_group_type="okta_group",
        request_reason="Need group",
    ).execute()

    ApproveGroupRequest(
        group_request=group_request,
        approver_user=admin_user,
        approval_reason="Approved by admin",
    ).execute()

    db.session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.APPROVED
    assert group_request.resolver_user_id == admin_user.id


def test_admin_can_reject_request(
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
    user: OktaUser,
) -> None:
    """Test that an admin can reject any group request"""
    admin_user = OktaUserFactory.create()

    db.session.add(user)
    db.session.add(admin_user)
    db.session.commit()

    group_request = CreateGroupRequest(
        requester_user=user,
        requested_group_name="Admin Rejected Group",
        requested_group_description="Group rejected by admin",
        requested_group_type="okta_group",
        request_reason="Need group",
    ).execute()

    RejectGroupRequest(
        group_request=group_request,
        rejection_reason="Rejected by admin",
        notify_requester=True,
        current_user_id=admin_user.id,
    ).execute()

    db.session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.REJECTED
    assert group_request.resolver_user_id == admin_user.id


def test_user_can_reject_own_request(
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
    user: OktaUser,
) -> None:
    """Test that a user can reject their own group request"""
    db.session.add(user)
    db.session.commit()

    group_request = CreateGroupRequest(
        requester_user=user,
        requested_group_name="Self Rejected Group",
        requested_group_description="Group I don't want anymore",
        requested_group_type="okta_group",
        request_reason="Changed my mind",
    ).execute()

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
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
    user: OktaUser,
) -> None:
    """Test that a user cannot approve their own group request"""
    db.session.add(user)
    db.session.commit()

    group_request = CreateGroupRequest(
        requester_user=user,
        requested_group_name="Self Approval Attempt",
        requested_group_description="Trying to approve myself",
        requested_group_type="okta_group",
        request_reason="Want to self-approve",
    ).execute()

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
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    """Test that an approver can modify the group name, description, and tags before approval"""
    approver_user = OktaUserFactory.create()
    tag = TagFactory.create(enabled=True)
    other_tag = TagFactory.create(enabled=True)

    db.session.add(user)
    db.session.add(approver_user)
    db.session.add(tag)
    db.session.add(other_tag)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )

    group_request = CreateGroupRequest(
        requester_user=user,
        requested_group_name="Original Name",
        requested_group_description="Original description",
        requested_group_type="okta_group",
        requested_group_tags=[tag.id],
        request_reason="Need group",
    ).execute()

    # Approver modifies the request before approval
    group_request.resolved_group_name = "Modified Name"
    group_request.resolved_group_description = "Modified description"
    group_request.resolved_group_tags = [other_tag.id]
    db.session.commit()

    ApproveGroupRequest(
        group_request=group_request,
        approver_user=approver_user,
        approval_reason="Approved with modifications",
    ).execute()

    db.session.refresh(group_request)
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


def test_reject_group_request(
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
    user: OktaUser,
) -> None:
    """Test rejecting a group request"""
    approver_user = OktaUserFactory.create()

    db.session.add(user)
    db.session.add(approver_user)
    db.session.commit()

    group_request = CreateGroupRequest(
        requester_user=user,
        requested_group_name="Rejected Group",
        requested_group_description="This will be rejected",
        requested_group_type="okta_group",
        request_reason="Need group",
    ).execute()

    RejectGroupRequest(
        group_request=group_request,
        rejection_reason="Not needed",
        notify_requester=True,
        current_user_id=approver_user.id,
    ).execute()

    db.session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.REJECTED
    assert group_request.resolved_at is not None
    assert group_request.resolver_user_id == approver_user.id
    assert group_request.resolution_reason == "Not needed"
    assert group_request.approved_group_id is None


def test_cannot_approve_already_resolved_request(
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    """Test that an already resolved request cannot be approved again"""
    approver_user = OktaUserFactory.create()

    db.session.add(user)
    db.session.add(approver_user)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )

    group_request = CreateGroupRequest(
        requester_user=user,
        requested_group_name="Already Resolved",
        requested_group_description="Description",
        requested_group_type="okta_group",
        request_reason="Need group",
    ).execute()

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
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
) -> None:
    """Test that a request cannot be approved if the requester is deleted"""
    approver_user = OktaUserFactory.create()

    db.session.add(user)
    db.session.add(approver_user)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )

    group_request = CreateGroupRequest(
        requester_user=user,
        requested_group_name="Deleted Requester Group",
        requested_group_description="Requester will be deleted",
        requested_group_type="okta_group",
        request_reason="Need group",
    ).execute()

    assert group_request is not None, "CreateGroupRequest should return a GroupRequest object"

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
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
) -> None:
    """Test that when an app owner creates an app group request, it is automatically approved,
    the group is created, and the requester is set as the group owner"""
    app_owner_user = OktaUserFactory.create()
    app_obj = AppFactory.create()

    db.session.add(app_owner_user)
    db.session.add(app_obj)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )

    # Create owner group for the app
    owner_group = AppGroupFactory.create(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_obj.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        app_id=app_obj.id,
        is_owner=True,
    )

    db.session.add(owner_group)
    db.session.commit()

    # Make app_owner_user an owner of the app via its owner group
    db.session.add(
        OktaUserGroupMember(
            user_id=app_owner_user.id,
            group_id=owner_group.id,
            is_owner=False,
        )
    )
    db.session.commit()

    # App owner creates a group request for their own app
    group_request = CreateGroupRequest(
        requester_user=app_owner_user,
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
        .filter(OktaUserGroupMember.user_id == app_owner_user.id)
        .filter(OktaUserGroupMember.is_owner.is_(True))
        .all()
    )
    assert len(ownerships) == 1
    assert ownerships[0].created_reason == group_request.request_reason
