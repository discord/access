from datetime import datetime, timedelta, timezone

from flask_sqlalchemy import SQLAlchemy

from api.models import AccessRequest, AccessRequestStatus, OktaGroup, OktaUser
from api.syncer import expire_access_requests


def test_no_expire_new_access_request(
    db: SQLAlchemy, access_request: AccessRequest, okta_group: OktaGroup, user: OktaUser
) -> None:
    db.session.add(user)
    db.session.add(okta_group)
    db.session.commit()
    access_request.requested_group_id = okta_group.id
    access_request.requester_user_id = user.id
    db.session.add(access_request)
    db.session.commit()

    access_request_id = access_request.id

    expire_access_requests()

    access_request = db.session.get(AccessRequest, access_request_id)
    assert access_request.status == AccessRequestStatus.PENDING
    assert access_request.resolved_at is None


def test_expire_old_access_request(
    db: SQLAlchemy, access_request: AccessRequest, okta_group: OktaGroup, user: OktaUser
) -> None:
    db.session.add(user)
    db.session.add(okta_group)
    db.session.commit()
    access_request.created_at = datetime.now(timezone.utc) - timedelta(days=30)
    access_request.requested_group_id = okta_group.id
    access_request.requester_user_id = user.id
    db.session.add(access_request)
    db.session.commit()

    access_request_id = access_request.id

    expire_access_requests()

    access_request = db.session.get(AccessRequest, access_request_id)
    assert access_request.status == AccessRequestStatus.REJECTED
    assert access_request.resolved_at is not None


def test_expire_old_temporary_access_request(
    db: SQLAlchemy, access_request: AccessRequest, okta_group: OktaGroup, user: OktaUser
) -> None:
    db.session.add(user)
    db.session.add(okta_group)
    db.session.commit()
    access_request.created_at = datetime.now(timezone.utc) - timedelta(days=1)
    access_request.request_ending_at = datetime.now(timezone.utc) - timedelta(hours=12)
    access_request.requested_group_id = okta_group.id
    access_request.requester_user_id = user.id
    db.session.add(access_request)
    db.session.commit()

    access_request_id = access_request.id

    expire_access_requests()

    access_request = db.session.get(AccessRequest, access_request_id)
    assert access_request.status == AccessRequestStatus.REJECTED
    assert access_request.resolved_at is not None
