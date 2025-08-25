"""
Test script for pagination Pydantic schemas.
"""

import pytest

from api.models import AccessRequestStatus
from api_v2.schemas.pagination import (
    AuditOrderBy,
    PaginationResponse,
    SearchAccessRequestPagination,
    SearchAuditPagination,
    SearchGroupPagination,
    SearchPagination,
    SearchRolePagination,
    SearchRoleRequestPagination,
    SearchUserGroupAuditPagination,
)


def test_pagination_schemas_import():
    """Test pagination schemas can be imported."""
    # Pagination schemas imported successfully
    pass


def test_base_search_pagination():
    """Test base search pagination."""
    base_search = SearchPagination(page=1, per_page=25, q="test query")
    # SearchPagination: page={base_search.page}, per_page={base_search.per_page}
    assert base_search.page == 1
    assert base_search.per_page == 25
    assert base_search.q == "test query"


def test_group_search_pagination():
    """Test group search pagination."""
    group_search = SearchGroupPagination(page=2, per_page=10, managed=True)
    # SearchGroupPagination: managed={group_search.managed}
    assert group_search.managed is True
    assert group_search.page == 2
    assert group_search.per_page == 10


def test_role_search_pagination():
    """Test role search pagination."""
    role_search = SearchRolePagination(owner_id="user123", q="admin")
    # SearchRolePagination: owner_id={role_search.owner_id}
    assert role_search.owner_id == "user123"
    assert role_search.q == "admin"


def test_audit_order_by_enum():
    """Test audit order by enum."""
    # AuditOrderBy enum values: {[order.value for order in AuditOrderBy]}
    enum_values = [order.value for order in AuditOrderBy]
    assert len(enum_values) > 0
    assert "ended_at" in enum_values


def test_audit_search_pagination():
    """Test audit search pagination."""
    audit_search = SearchAuditPagination(owner=True, active=False, order_by=AuditOrderBy.ended_at, order_desc=False)
    # SearchAuditPagination: order_by={audit_search.order_by}, desc={audit_search.order_desc}
    assert audit_search.owner is True
    assert audit_search.active is False
    assert audit_search.order_by == AuditOrderBy.ended_at
    assert audit_search.order_desc is False


def test_user_group_audit_pagination():
    """Test user-group audit pagination."""
    user_group_audit = SearchUserGroupAuditPagination(
        user_id="user123",
        group_id="group456",
        direct=True,
        deleted=False,
        start_date=1640995200,  # 2022-01-01 timestamp
        end_date=1672531199,  # 2022-12-31 timestamp
    )
    # SearchUserGroupAuditPagination: user={user_group_audit.user_id}, direct={user_group_audit.direct}
    assert user_group_audit.user_id == "user123"
    assert user_group_audit.group_id == "group456"
    assert user_group_audit.direct is True
    assert user_group_audit.deleted is False


def test_access_request_search_pagination():
    """Test access request search pagination."""
    access_search = SearchAccessRequestPagination(
        status=AccessRequestStatus.PENDING, requester_user_id="user123", requested_group_id="group456"
    )
    # SearchAccessRequestPagination: status={access_search.status}
    assert access_search.status == AccessRequestStatus.PENDING
    assert access_search.requester_user_id == "user123"
    assert access_search.requested_group_id == "group456"


def test_role_request_search_pagination():
    """Test role request search pagination."""
    role_request_search = SearchRoleRequestPagination(
        status=AccessRequestStatus.APPROVED, requester_role_id="role123", requested_group_id="group456"
    )
    # SearchRoleRequestPagination: status={role_request_search.status}
    assert role_request_search.status == AccessRequestStatus.APPROVED
    assert role_request_search.requester_role_id == "role123"
    assert role_request_search.requested_group_id == "group456"


def test_pagination_response():
    """Test pagination response."""
    pagination_response = PaginationResponse(
        total=150, pages=6, next="https://api.example.com/items?page=3", prev="https://api.example.com/items?page=1"
    )
    # PaginationResponse: total={pagination_response.total}, pages={pagination_response.pages}
    assert pagination_response.total == 150
    assert pagination_response.pages == 6
    assert pagination_response.next == "https://api.example.com/items?page=3"
    assert pagination_response.prev == "https://api.example.com/items?page=1"


def test_validation_negative_page():
    """Test validation - negative page should fail."""
    with pytest.raises(ValueError):
        invalid_search = SearchPagination(page=-1)
        # Validation correctly rejected negative page


def test_validation_per_page_too_large():
    """Test validation - per_page too large should fail."""
    with pytest.raises(ValueError):
        invalid_search = SearchPagination(per_page=200)
        # Validation correctly rejected per_page > 100


def test_json_serialization():
    """Test JSON serialization."""
    audit_search = SearchAuditPagination(owner=True, active=False, order_by=AuditOrderBy.ended_at, order_desc=False)
    search_json = audit_search.model_dump()
    # JSON serialization works: {len(search_json)} fields
    assert isinstance(search_json, dict)
    assert len(search_json) > 0


def test_minimal_search():
    """Test with None values (should be allowed)."""
    minimal_search = SearchPagination()
    # Minimal search (all None): {minimal_search.model_dump()}
    search_dict = minimal_search.model_dump()
    assert isinstance(search_dict, dict)

    # All pagination schemas working
    # AuditOrderBy enum working
    # Field validation working (page >= 1, per_page <= 100)
    # Optional field handling working
    # JSON serialization working
