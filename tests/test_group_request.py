import pytest

from api.extensions import db
from api.models import (
    AccessRequestStatus,
    AppGroup,
    OktaGroup,
    OktaUserGroupMember,
    RoleGroup,
    OktaGroupTagMap,
)
from api.operations import CreateGroupRequest, ApproveGroupRequest, RejectGroupRequest
from tests.factories import (
    OktaUserFactory,
    AppFactory,
    AppGroupFactory,
    TagFactory,
)


def test_create_group_request(db_session):
    """Test creating a basic group request"""
    okta_user = OktaUserFactory.build()
    db_session.add(okta_user)
    db_session.commit()

    group_request = CreateGroupRequest(
        requester_user=okta_user.id,
        requested_group_name="Test Group",
        requested_group_description="Test Description",
        requested_group_type=OktaGroup.__mapper_args__["polymorphic_identity"],
        request_reason="Need this group",
    ).execute()

    assert group_request is not None
    assert group_request.requester_user_id == okta_user.id
    assert group_request.requested_group_name == "Test Group"
    assert group_request.requested_group_description == "Test Description"
    assert group_request.status == AccessRequestStatus.PENDING
    assert group_request.resolved_at is None


def test_create_app_group_request(db_session):
    """Test creating an app group request"""
    okta_user = OktaUserFactory.build()
    db_session.add(okta_user)

    app = AppFactory.build()
    db_session.add(app)
    db_session.commit()

    group_request = CreateGroupRequest(
        requester_user=okta_user.id,
        requested_group_name=f"App-{app.name}-Admins",
        requested_group_description="Admin group for TestApp",
        requested_group_type=AppGroup.__mapper_args__["polymorphic_identity"],
        requested_app_id=app.id,
        request_reason="Need admin access",
    ).execute()

    assert group_request is not None
    assert group_request.requested_app_id == app.id
    assert group_request.requested_group_type == AppGroup.__mapper_args__["polymorphic_identity"]


def test_create_role_group_request(db_session):
    """Test creating a role group request"""
    okta_user = OktaUserFactory.build()
    db_session.add(okta_user)
    db_session.commit()

    group_request = CreateGroupRequest(
        requester_user=okta_user.id,
        requested_group_name="Role-Engineering",
        requested_group_description="Engineering role",
        requested_group_type=RoleGroup.__mapper_args__["polymorphic_identity"],
        request_reason="Need engineering role",
    ).execute()

    assert group_request is not None
    assert group_request.requested_group_type == RoleGroup.__mapper_args__["polymorphic_identity"]


def test_create_group_request_with_tags(db_session):
    """Test creating a group request with tags"""
    okta_user = OktaUserFactory.build()
    db_session.add(okta_user)

    tag = TagFactory.build(enabled=True)
    db_session.add(tag)
    db_session.commit()

    group_request = CreateGroupRequest(
        requester_user=okta_user.id,
        requested_group_name="Tagged Group",
        requested_group_description="Group with tags",
        requested_group_type=OktaGroup.__mapper_args__["polymorphic_identity"],
        requested_group_tags=[tag.id],
        request_reason="Need tagged group",
    ).execute()

    assert group_request is not None
    assert tag.id in group_request.requested_group_tags


def test_approve_group_request_creates_group(db_session):
    """Test that approving a group request creates the group"""
    okta_user = OktaUserFactory.build()
    approver_user = OktaUserFactory.build()
    db_session.add(okta_user)
    db_session.add(approver_user)
    db_session.commit()

    group_request = CreateGroupRequest(
        requester_user=okta_user.id,
        requested_group_name="New Group",
        requested_group_description="New group description",
        requested_group_type=OktaGroup.__mapper_args__["polymorphic_identity"],
        request_reason="Need this group",
    ).execute()

    ApproveGroupRequest(
        group_request=group_request,
        approver_user=approver_user.id,
        approval_reason="Approved",
    ).execute()

    db_session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.APPROVED
    assert group_request.resolved_at is not None
    assert group_request.resolver_user_id == approver_user.id
    assert group_request.approved_group_id is not None

    # Verify the group was created
    created_group = db_session.get(OktaGroup, group_request.approved_group_id)
    assert created_group is not None
    assert created_group.name == "New Group"
    assert created_group.description == "New group description"


# TODO: Test that requester is set as owner of created group
def test_approve_group_request_sets_requester_as_owner(db_session):
    """Test that approving a group request sets the requester as an owner"""
    okta_user = OktaUserFactory.build()
    approver_user = OktaUserFactory.build()
    db_session.add(okta_user)
    db_session.add(approver_user)
    db_session.commit()

    group_request = CreateGroupRequest(
        requester_user=okta_user.id,
        requested_group_name="Owned Group",
        requested_group_description="Group with owner",
        requested_group_type=OktaGroup.__mapper_args__["polymorphic_identity"],
        request_reason="Need ownership",
    ).execute()

    ApproveGroupRequest(
        group_request=group_request,
        approver_user=approver_user.id,
        approval_reason="Approved",
    ).execute()

    db_session.refresh(group_request)
    created_group = db_session.get(OktaGroup, group_request.approved_group_id)

    # Check that the requester is an owner
    ownerships = (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.group_id == created_group.id)
        .filter(OktaUserGroupMember.user_id == okta_user.id)
        .filter(OktaUserGroupMember.is_owner.is_(True))
        .all()
    )
    assert len(ownerships) == 1
    assert ownerships[0].created_reason == group_request.request_reason


# TODO: Test that tags are applied to created group
def test_approve_group_request_applies_tags(db_session):
    """Test that approving a group request applies the requested tags"""
    okta_user = OktaUserFactory.build()
    approver_user = OktaUserFactory.build()
    db_session.add(okta_user)
    db_session.add(approver_user)

    tag = TagFactory.build(enabled=True)
    db_session.add(tag)
    db_session.commit()

    group_request = CreateGroupRequest(
        requester_user=okta_user.id,
        requested_group_name="Tagged Group",
        requested_group_description="Group with tags",
        requested_group_type=OktaGroup.__mapper_args__["polymorphic_identity"],
        requested_group_tags=[tag.id],
        request_reason="Need tagged group",
    ).execute()

    # Set resolved tags (approver could modify these)
    group_request.resolved_group_tags = [tag.id]
    db_session.commit()

    ApproveGroupRequest(
        group_request=group_request,
        approver_user=approver_user.id,
        approval_reason="Approved",
    ).execute()

    db_session.refresh(group_request)
    created_group = db_session.get(OktaGroup, group_request.approved_group_id)

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


# TODO: Test that group name is set correctly
def test_approve_group_request_sets_name(db_session):
    """Test that the created group has the correct name"""
    okta_user = OktaUserFactory.build()
    approver_user = OktaUserFactory.build()
    db_session.add(okta_user)
    db_session.add(approver_user)
    db_session.commit()

    group_request = CreateGroupRequest(
        requester_user=okta_user.id,
        requested_group_name="Specific Name",
        requested_group_description="Description",
        requested_group_type=OktaGroup.__mapper_args__["polymorphic_identity"],
        request_reason="Need this name",
    ).execute()

    ApproveGroupRequest(
        group_request=group_request,
        approver_user=approver_user.id,
        approval_reason="Approved",
    ).execute()

    db_session.refresh(group_request)
    created_group = db_session.get(OktaGroup, group_request.approved_group_id)
    assert created_group.name == "Specific Name"


# TODO: Test that group type is set correctly
def test_approve_group_request_sets_type(db_session):
    """Test that the created group has the correct type"""
    okta_user = OktaUserFactory.build()
    approver_user = OktaUserFactory.build()
    db_session.add(okta_user)
    db_session.add(approver_user)

    app = AppFactory.build()
    db_session.add(app)
    db_session.commit()

    # Test OktaGroup
    group_request_okta = CreateGroupRequest(
        requester_user=okta_user.id,
        requested_group_name="Regular Group",
        requested_group_description="Description",
        requested_group_type=OktaGroup.__mapper_args__["polymorphic_identity"],
        request_reason="Need group",
    ).execute()

    ApproveGroupRequest(
        group_request=group_request_okta,
        approver_user=approver_user.id,
        approval_reason="Approved",
    ).execute()

    db_session.refresh(group_request_okta)
    created_okta_group = db_session.get(OktaGroup, group_request_okta.approved_group_id)
    assert type(created_okta_group) is OktaGroup

    # Test AppGroup
    group_request_app = CreateGroupRequest(
        requester_user=okta_user.id,
        requested_group_name=f"App-{app.name}-Users",
        requested_group_description="App group",
        requested_group_type=AppGroup.__mapper_args__["polymorphic_identity"],
        requested_app_id=app.id,
        request_reason="Need app group",
    ).execute()

    ApproveGroupRequest(
        group_request=group_request_app,
        approver_user=approver_user.id,
        approval_reason="Approved",
    ).execute()

    db_session.refresh(group_request_app)
    created_app_group = db_session.get(OktaGroup, group_request_app.approved_group_id)
    assert type(created_app_group) is AppGroup
    assert created_app_group.app_id == app.id

    # Test RoleGroup
    group_request_role = CreateGroupRequest(
        requester_user=okta_user.id,
        requested_group_name="Role-Marketing",
        requested_group_description="Role group",
        requested_group_type=RoleGroup.__mapper_args__["polymorphic_identity"],
        request_reason="Need role group",
    ).execute()

    ApproveGroupRequest(
        group_request=group_request_role,
        approver_user=approver_user.id,
        approval_reason="Approved",
    ).execute()

    db_session.refresh(group_request_role)
    created_role_group = db_session.get(OktaGroup, group_request_role.approved_group_id)
    assert type(created_role_group) is RoleGroup


# TODO: Test that group description is set correctly
def test_approve_group_request_sets_description(db_session):
    """Test that the created group has the correct description"""
    okta_user = OktaUserFactory.build()
    approver_user = OktaUserFactory.build()
    db_session.add(okta_user)
    db_session.add(approver_user)
    db_session.commit()

    group_request = CreateGroupRequest(
        requester_user=okta_user.id,
        requested_group_name="Named Group",
        requested_group_description="Specific description text",
        requested_group_type=OktaGroup.__mapper_args__["polymorphic_identity"],
        request_reason="Need description",
    ).execute()

    ApproveGroupRequest(
        group_request=group_request,
        approver_user=approver_user.id,
        approval_reason="Approved",
    ).execute()

    db_session.refresh(group_request)
    created_group = db_session.get(OktaGroup, group_request.approved_group_id)
    assert created_group.description == "Specific description text"


# TODO: Test that app owners can approve group creation for apps they own
def test_app_owner_can_approve_request(db_session):
    """Test that an app owner can approve a group request for their app"""
    okta_user = OktaUserFactory.build()
    app_owner_user = OktaUserFactory.build()
    db_session.add(okta_user)
    db_session.add(app_owner_user)

    app = AppFactory.build()
    db_session.add(app)
    db_session.commit()

    # Create owner group for the app
    owner_group = AppGroupFactory.build(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        app_id=app.id,
        is_owner=True,
    )
    db_session.add(owner_group)
    db_session.commit()

    # Make app_owner_user an owner of the app via its owner group
    db_session.add(
        OktaUserGroupMember(
            user_id=app_owner_user.id,
            group_id=owner_group.id,
            is_owner=False,
        )
    )
    db_session.commit()

    group_request = CreateGroupRequest(
        requester_user=okta_user.id,
        requested_group_name=f"App-{app.name}-NewGroup",
        requested_group_description="New app group",
        requested_group_type=AppGroup.__mapper_args__["polymorphic_identity"],
        requested_app_id=app.id,
        request_reason="Need app group",
    ).execute()

    # App owner should be able to approve
    ApproveGroupRequest(
        group_request=group_request,
        approver_user=app_owner_user.id,
        approval_reason="Approved by app owner",
    ).execute()

    db_session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.APPROVED
    assert group_request.resolver_user_id == app_owner_user.id


# TODO: Test that app owners can reject group creation for apps they own
def test_app_owner_can_reject_request(db_session):
    """Test that an app owner can reject a group request for their app"""
    okta_user = OktaUserFactory.build()
    app_owner_user = OktaUserFactory.build()
    db_session.add(okta_user)
    db_session.add(app_owner_user)

    app = AppFactory.build()
    db_session.add(app)
    db_session.commit()

    # Create owner group for the app
    owner_group = AppGroupFactory.build(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        app_id=app.id,
        is_owner=True,
    )
    db_session.add(owner_group)
    db_session.commit()

    # Make app_owner_user an owner of the app via its owner group
    db_session.add(
        OktaUserGroupMember(
            user_id=app_owner_user.id,
            group_id=owner_group.id,
            is_owner=False,
        )
    )
    db_session.commit()

    group_request = CreateGroupRequest(
        requester_user=okta_user.id,
        requested_group_name=f"App-{app.name}-AnotherGroup",
        requested_group_description="Another app group",
        requested_group_type=AppGroup.__mapper_args__["polymorphic_identity"],
        requested_app_id=app.id,
        request_reason="Need app group",
    ).execute()

    # App owner should be able to reject
    RejectGroupRequest(
        group_request=group_request,
        rejection_reason="Rejected by app owner",
        notify_requester=True,
        current_user_id=app_owner_user.id,
    ).execute()

    db_session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.REJECTED
    assert group_request.resolver_user_id == app_owner_user.id


# TODO: Test that app owner that is not the owner for the requested app group cannot approve
def test_wrong_app_owner_cannot_approve_request(db_session):
    """Test that an app owner cannot approve a request for a different app"""
    okta_user = OktaUserFactory.build()
    app_owner_user = OktaUserFactory.build()
    db_session.add(okta_user)
    db_session.add(app_owner_user)

    app = AppFactory.build()
    other_app = AppFactory.build()
    db_session.add(app)
    db_session.add(other_app)
    db_session.commit()

    # Create owner groups for both apps
    owner_group = AppGroupFactory.build(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        app_id=app.id,
        is_owner=True,
    )
    other_owner_group = AppGroupFactory.build(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{other_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        app_id=other_app.id,
        is_owner=True,
    )
    db_session.add(owner_group)
    db_session.add(other_owner_group)
    db_session.commit()

    # Make app_owner_user an owner of 'app' but not 'other_app'
    db_session.add(
        OktaUserGroupMember(
            user_id=app_owner_user.id,
            group_id=owner_group.id,
            is_owner=False,
        )
    )
    db_session.commit()

    # Create request for other_app
    group_request = CreateGroupRequest(
        requester_user=okta_user.id,
        requested_group_name=f"App-{other_app.name}-NewGroup",
        requested_group_description="Group for other app",
        requested_group_type=AppGroup.__mapper_args__["polymorphic_identity"],
        requested_app_id=other_app.id,
        request_reason="Need group for other app",
    ).execute()

    # Set resolved_app_id (normally done in the PUT handler)
    group_request.resolved_app_id = other_app.id
    db_session.commit()

    # App owner of 'app' should NOT be able to approve request for 'other_app'
    result = ApproveGroupRequest(
        group_request=group_request,
        approver_user=app_owner_user.id,
        approval_reason="Should not be allowed",
    ).execute()

    db_session.refresh(group_request)
    # Request should still be pending (not approved)
    assert group_request.status == AccessRequestStatus.PENDING
    assert group_request.resolved_at is None


# TODO: Test that admins can approve requests
def test_admin_can_approve_request(db_session):
    """Test that an admin can approve any group request"""
    okta_user = OktaUserFactory.build()
    admin_user = OktaUserFactory.build()
    db_session.add(okta_user)
    db_session.add(admin_user)
    db_session.commit()

    group_request = CreateGroupRequest(
        requester_user=okta_user.id,
        requested_group_name="Admin Approved Group",
        requested_group_description="Group approved by admin",
        requested_group_type=OktaGroup.__mapper_args__["polymorphic_identity"],
        request_reason="Need group",
    ).execute()

    ApproveGroupRequest(
        group_request=group_request,
        approver_user=admin_user.id,
        approval_reason="Approved by admin",
    ).execute()

    db_session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.APPROVED
    assert group_request.resolver_user_id == admin_user.id


# TODO: Test that admins can reject requests
def test_admin_can_reject_request(db_session):
    """Test that an admin can reject any group request"""
    okta_user = OktaUserFactory.build()
    admin_user = OktaUserFactory.build()
    db_session.add(okta_user)
    db_session.add(admin_user)
    db_session.commit()

    group_request = CreateGroupRequest(
        requester_user=okta_user.id,
        requested_group_name="Admin Rejected Group",
        requested_group_description="Group rejected by admin",
        requested_group_type=OktaGroup.__mapper_args__["polymorphic_identity"],
        request_reason="Need group",
    ).execute()

    RejectGroupRequest(
        group_request=group_request,
        rejection_reason="Rejected by admin",
        notify_requester=True,
        current_user_id=admin_user.id,
    ).execute()

    db_session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.REJECTED
    assert group_request.resolver_user_id == admin_user.id


# TODO: Test that users can reject their own requests
def test_user_can_reject_own_request(db_session):
    """Test that a user can reject their own group request"""
    okta_user = OktaUserFactory.build()
    db_session.add(okta_user)
    db_session.commit()

    group_request = CreateGroupRequest(
        requester_user=okta_user.id,
        requested_group_name="Self Rejected Group",
        requested_group_description="Group I don't want anymore",
        requested_group_type=OktaGroup.__mapper_args__["polymorphic_identity"],
        request_reason="Changed my mind",
    ).execute()

    RejectGroupRequest(
        group_request=group_request,
        rejection_reason="I changed my mind",
        notify_requester=False,
        current_user_id=okta_user.id,
    ).execute()

    db_session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.REJECTED
    assert group_request.resolver_user_id == okta_user.id


def test_user_cannot_approve_own_request(db_session):
    """Test that a user cannot approve their own group request"""
    okta_user = OktaUserFactory.build()
    db_session.add(okta_user)
    db_session.commit()

    group_request = CreateGroupRequest(
        requester_user=okta_user.id,
        requested_group_name="Self Approval Attempt",
        requested_group_description="Trying to approve myself",
        requested_group_type=OktaGroup.__mapper_args__["polymorphic_identity"],
        request_reason="Want to self-approve",
    ).execute()

    # Attempt to self-approve should fail
    ApproveGroupRequest(
        group_request=group_request,
        approver_user=okta_user.id,
        approval_reason="Self approval",
    ).execute()

    db_session.refresh(group_request)
    # Should still be pending
    assert group_request.status == AccessRequestStatus.PENDING
    assert group_request.resolved_at is None


# TODO: Test that approver can change group name/description/tags
def test_approver_can_modify_group_details(db_session):
    """Test that an approver can modify the group name, description, and tags before approval"""
    okta_user = OktaUserFactory.build()
    approver_user = OktaUserFactory.build()
    db_session.add(okta_user)
    db_session.add(approver_user)

    tag = TagFactory.build(enabled=True)
    other_tag = TagFactory.build(enabled=True)
    db_session.add(tag)
    db_session.add(other_tag)
    db_session.commit()

    group_request = CreateGroupRequest(
        requester_user=okta_user.id,
        requested_group_name="Original Name",
        requested_group_description="Original description",
        requested_group_type=OktaGroup.__mapper_args__["polymorphic_identity"],
        requested_group_tags=[tag.id],
        request_reason="Need group",
    ).execute()

    # Approver modifies the request before approval
    group_request.resolved_group_name = "Modified Name"
    group_request.resolved_group_description = "Modified description"
    group_request.resolved_group_tags = [other_tag.id]
    db_session.commit()

    ApproveGroupRequest(
        group_request=group_request,
        approver_user=approver_user.id,
        approval_reason="Approved with modifications",
    ).execute()

    db_session.refresh(group_request)
    created_group = db_session.get(OktaGroup, group_request.approved_group_id)

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


def test_reject_group_request(db_session):
    """Test rejecting a group request"""
    okta_user = OktaUserFactory.build()
    approver_user = OktaUserFactory.build()
    db_session.add(okta_user)
    db_session.add(approver_user)
    db_session.commit()

    group_request = CreateGroupRequest(
        requester_user=okta_user.id,
        requested_group_name="Rejected Group",
        requested_group_description="This will be rejected",
        requested_group_type=OktaGroup.__mapper_args__["polymorphic_identity"],
        request_reason="Need group",
    ).execute()

    RejectGroupRequest(
        group_request=group_request,
        rejection_reason="Not needed",
        notify_requester=True,
        current_user_id=approver_user.id,
    ).execute()

    db_session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.REJECTED
    assert group_request.resolved_at is not None
    assert group_request.resolver_user_id == approver_user.id
    assert group_request.resolution_reason == "Not needed"
    assert group_request.approved_group_id is None


def test_cannot_approve_already_resolved_request(db_session):
    """Test that an already resolved request cannot be approved again"""
    okta_user = OktaUserFactory.build()
    approver_user = OktaUserFactory.build()
    db_session.add(okta_user)
    db_session.add(approver_user)
    db_session.commit()

    group_request = CreateGroupRequest(
        requester_user=okta_user.id,
        requested_group_name="Already Resolved",
        requested_group_description="Description",
        requested_group_type=OktaGroup.__mapper_args__["polymorphic_identity"],
        request_reason="Need group",
    ).execute()

    # First approval
    ApproveGroupRequest(
        group_request=group_request,
        approver_user=approver_user.id,
        approval_reason="First approval",
    ).execute()

    db_session.refresh(group_request)
    first_approval_time = group_request.resolved_at
    first_group_id = group_request.approved_group_id

    # Try to approve again
    ApproveGroupRequest(
        group_request=group_request,
        approver_user=approver_user.id,
        approval_reason="Second approval attempt",
    ).execute()

    db_session.refresh(group_request)
    # Should still have the same resolution details
    assert group_request.resolved_at == first_approval_time
    assert group_request.approved_group_id == first_group_id


def test_cannot_approve_deleted_requester(db_session):
    """Test that a request cannot be approved if the requester is deleted"""
    okta_user = OktaUserFactory.build()
    approver_user = OktaUserFactory.build()
    db_session.add(okta_user)
    db_session.add(approver_user)
    db_session.commit()

    group_request = CreateGroupRequest(
        requester_user=okta_user.id,
        requested_group_name="Deleted Requester Group",
        requested_group_description="Requester will be deleted",
        requested_group_type=OktaGroup.__mapper_args__["polymorphic_identity"],
        request_reason="Need group",
    ).execute()

    # Delete the requester
    okta_user.deleted_at = db.func.now()
    db_session.commit()

    # Try to approve
    ApproveGroupRequest(
        group_request=group_request,
        approver_user=approver_user.id,
        approval_reason="Should not work",
    ).execute()

    db_session.refresh(group_request)
    # Should still be pending
    assert group_request.status == AccessRequestStatus.PENDING
    assert group_request.resolved_at is None
