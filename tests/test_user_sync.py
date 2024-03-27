from typing import List

from flask_sqlalchemy import SQLAlchemy
from pytest_mock import MockerFixture
from sqlalchemy.orm import Session

from api.models import AccessRequest, AccessRequestStatus, OktaGroup, OktaUser, OktaUserGroupMember, RoleGroup
from api.operations import CreateAccessRequest
from api.services import okta
from api.services.okta_service import User, UserSchema
from api.syncer import sync_users
from tests.factories import UserFactory, UserSchemaFactory


def test_user_sync_no_changes(db: SQLAlchemy, mocker: MockerFixture) -> None:
    initial_users_in_okta = UserFactory.create_batch(3)

    initial_db_users = seed_db(db, initial_users_in_okta)

    new_db_users = run_sync(db, mocker, initial_users_in_okta)

    for i in range(len(initial_users_in_okta)):
        assert okta_users_are_equal(
            get_user_by_id(initial_db_users, initial_users_in_okta[i].id),
            get_user_by_id(new_db_users, initial_users_in_okta[i].id),
        )


def test_user_sync_updates_fields(db: SQLAlchemy, mocker: MockerFixture) -> None:
    initial_users_in_okta = UserFactory.create_batch(1)

    _ = seed_db(db, initial_users_in_okta)

    initial_users_in_okta[0].profile.login = "changed"

    new_db_users = run_sync(db, mocker, initial_users_in_okta)

    assert get_user_by_id(new_db_users, initial_users_in_okta[0].id).email == "changed"


def test_user_sync_updates_deleted_user(db: SQLAlchemy, mocker: MockerFixture, okta_group: OktaGroup, role_group: RoleGroup) -> None:
    initial_users_in_okta = UserFactory.create_batch(1)

    initial_db_users = seed_db(db, initial_users_in_okta)

    # Add a managed group membership and ownership to the user to be deleted
    db.session.add(okta_group)
    db.session.commit()
    db.session.commit()
    managed_membership = OktaUserGroupMember(
        user_id=initial_users_in_okta[0].id, group_id=okta_group.id, ended_at=None, is_owner=False
    )
    db.session.add(managed_membership)
    managed_ownership = OktaUserGroupMember(
        user_id=initial_users_in_okta[0].id, group_id=okta_group.id, ended_at=None, is_owner=True
    )
    db.session.add(managed_ownership)
    db.session.commit()

    # Add a unmanaged group membership and ownership to the user to be deleted
    role_group.is_managed = False
    db.session.add(role_group)
    db.session.commit()
    db.session.commit()
    unmanaged_membership = OktaUserGroupMember(
        user_id=initial_users_in_okta[0].id, group_id=role_group.id, ended_at=None, is_owner=False
    )
    db.session.add(unmanaged_membership)
    unmanaged_ownership = OktaUserGroupMember(
        user_id=initial_users_in_okta[0].id, group_id=role_group.id, ended_at=None, is_owner=True
    )
    db.session.add(unmanaged_ownership)
    db.session.commit()

    # Create AccessRequest to test it gets rejected
    access_request = CreateAccessRequest(
        requester_user=initial_db_users[0],
        requested_group=okta_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    initial_users_in_okta[0].status = "DEPROVISIONED"
    initial_users_in_okta[0].status_changed = "2022-06-02 11:54:51.724560"

    delete_membership_spy = mocker.patch.object(okta, "async_remove_user_from_group")
    delete_ownership_spy = mocker.patch.object(okta, "async_remove_owner_from_group")

    new_db_users = run_sync(db, mocker, initial_users_in_okta)

    deleted_user = get_user_by_id(new_db_users, initial_users_in_okta[0].id)
    assert str(deleted_user.deleted_at) == initial_users_in_okta[0].status_changed
    assert db.session.get(OktaUser, initial_users_in_okta[0].id).deleted_at is not None
    assert access_request is not None
    assert db.session.get(AccessRequest, access_request.id).status == AccessRequestStatus.REJECTED
    assert db.session.get(OktaUserGroupMember, managed_membership.id).ended_at is not None
    assert db.session.get(OktaUserGroupMember, managed_ownership.id).ended_at is not None
    assert db.session.get(OktaUserGroupMember, unmanaged_membership.id).ended_at is not None
    assert db.session.get(OktaUserGroupMember, unmanaged_ownership.id).ended_at is not None
    # Should only be called once each for the managed group
    assert delete_membership_spy.call_count == 1
    assert delete_ownership_spy.call_count == 1


def test_user_sync_deletes_disappearing_user(db: SQLAlchemy, mocker: MockerFixture, okta_group: OktaGroup, role_group: RoleGroup) -> None:
    initial_users_in_okta = UserFactory.create_batch(1)

    initial_db_users = seed_db(db, initial_users_in_okta)

    # Add a managed group membership and ownership to the user to be deleted
    db.session.add(okta_group)
    db.session.commit()
    db.session.commit()
    managed_membership = OktaUserGroupMember(
        user_id=initial_users_in_okta[0].id, group_id=okta_group.id, ended_at=None, is_owner=False
    )
    db.session.add(managed_membership)
    managed_ownership = OktaUserGroupMember(
        user_id=initial_users_in_okta[0].id, group_id=okta_group.id, ended_at=None, is_owner=True
    )
    db.session.add(managed_ownership)
    db.session.commit()

    # Add a unmanaged group membership and ownership to the user to be deleted
    role_group.is_managed = False
    db.session.add(role_group)
    db.session.commit()
    db.session.commit()
    unmanaged_membership = OktaUserGroupMember(
        user_id=initial_users_in_okta[0].id, group_id=role_group.id, ended_at=None, is_owner=False
    )
    db.session.add(unmanaged_membership)
    unmanaged_ownership = OktaUserGroupMember(
        user_id=initial_users_in_okta[0].id, group_id=role_group.id, ended_at=None, is_owner=True
    )
    db.session.add(unmanaged_ownership)
    db.session.commit()

    # Create AccessRequest to test it gets rejected
    access_request = CreateAccessRequest(
        requester_user=initial_db_users[0],
        requested_group=okta_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    delete_membership_spy = mocker.patch.object(okta, "async_remove_user_from_group")
    delete_ownership_spy = mocker.patch.object(okta, "async_remove_owner_from_group")

    assert (
        get_user_by_id(initial_db_users, initial_users_in_okta[0].id).deleted_at is None
    )

    new_db_users = run_sync(db, mocker, [])

    assert len(initial_db_users) == len(new_db_users)
    assert (
        get_user_by_id(new_db_users, initial_users_in_okta[0].id).deleted_at is not None
    )
    assert db.session.get(OktaUser, initial_users_in_okta[0].id).deleted_at is not None
    assert access_request is not None
    assert db.session.get(AccessRequest, access_request.id).status == AccessRequestStatus.REJECTED
    assert db.session.get(OktaUserGroupMember, managed_membership.id).ended_at is not None
    assert db.session.get(OktaUserGroupMember, managed_ownership.id).ended_at is not None
    assert db.session.get(OktaUserGroupMember, unmanaged_membership.id).ended_at is not None
    assert db.session.get(OktaUserGroupMember, unmanaged_ownership.id).ended_at is not None
    # Should never be called as the user no longer exists in Okta
    assert delete_membership_spy.call_count == 0
    assert delete_ownership_spy.call_count == 0

def test_user_sync_ends_memberships_for_previously_deleted_user(db: SQLAlchemy, mocker: MockerFixture, okta_group: OktaGroup, role_group: RoleGroup) -> None:
    initial_users_in_okta = UserFactory.create_batch(1)

    seed_db(db, initial_users_in_okta)

    # Mark the user as deleted in Access
    db.session.get(OktaUser, initial_users_in_okta[0].id).deleted_at = db.func.now()

    # Add a managed group membership and ownership to the user to be deleted
    db.session.add(okta_group)
    db.session.commit()
    db.session.commit()
    managed_membership = OktaUserGroupMember(
        user_id=initial_users_in_okta[0].id, group_id=okta_group.id, ended_at=None, is_owner=False
    )
    db.session.add(managed_membership)
    managed_ownership = OktaUserGroupMember(
        user_id=initial_users_in_okta[0].id, group_id=okta_group.id, ended_at=None, is_owner=True
    )
    db.session.add(managed_ownership)
    db.session.commit()

    # Add a unmanaged group membership and ownership to the user to be deleted
    role_group.is_managed = False
    db.session.add(role_group)
    db.session.commit()
    db.session.commit()
    unmanaged_membership = OktaUserGroupMember(
        user_id=initial_users_in_okta[0].id, group_id=role_group.id, ended_at=None, is_owner=False
    )
    db.session.add(unmanaged_membership)
    unmanaged_ownership = OktaUserGroupMember(
        user_id=initial_users_in_okta[0].id, group_id=role_group.id, ended_at=None, is_owner=True
    )
    db.session.add(unmanaged_ownership)
    db.session.commit()

    initial_users_in_okta[0].status = "DEPROVISIONED"
    initial_users_in_okta[0].status_changed = "2022-06-02 11:54:51.724560"

    delete_membership_spy = mocker.patch.object(okta, "async_remove_user_from_group")
    delete_ownership_spy = mocker.patch.object(okta, "async_remove_owner_from_group")

    new_db_users = run_sync(db, mocker, initial_users_in_okta)

    deleted_user = get_user_by_id(new_db_users, initial_users_in_okta[0].id)
    assert str(deleted_user.deleted_at) == initial_users_in_okta[0].status_changed
    assert db.session.get(OktaUser, initial_users_in_okta[0].id).deleted_at is not None
    assert db.session.get(OktaUserGroupMember, managed_membership.id).ended_at is not None
    assert db.session.get(OktaUserGroupMember, managed_ownership.id).ended_at is not None
    assert db.session.get(OktaUserGroupMember, unmanaged_membership.id).ended_at is not None
    assert db.session.get(OktaUserGroupMember, unmanaged_ownership.id).ended_at is not None
    # Should only be called once each for the managed group
    assert delete_membership_spy.call_count == 1
    assert delete_ownership_spy.call_count == 1


def seed_db(db: SQLAlchemy, users: List[User]) -> List[OktaUser]:
    with Session(db.engine) as session:
        session.add_all([User(u).update_okta_user(OktaUser(), {}) for u in users])
        session.commit()
        return session.query(OktaUser).all()


def run_sync(db: SQLAlchemy, mocker: MockerFixture, okta_users: List[User]) -> List[OktaUser]:
    schema = UserSchemaFactory.create()
    with Session(db.engine) as session:
        mocker.patch.object(
            okta, "list_users", return_value=[User(u) for u in okta_users]
        )
        mocker.patch.object(okta, "get_user_schema", return_value=UserSchema(schema))
        sync_users()
        return session.query(OktaUser).all()


def get_user_by_id(user_list: List[OktaUser], user_id: str) -> OktaUser:
    ret = None
    for x in user_list:
        if x.id == user_id:
            ret = x
            break

    assert ret is not None
    return ret


def okta_users_are_equal(left: OktaUser, right: OktaUser) -> bool:
    # Checks if there are property differences between
    # two OktaUser objects without implementing it on the model
    # itself.
    return (
        left.id == right.id
        and left.email == right.email
        and left.last_name == right.last_name
        and left.first_name == right.first_name
        and left.display_name == right.display_name
        and left.deleted_at == right.deleted_at
        and left.created_at == right.created_at
    )
