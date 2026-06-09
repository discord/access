"""Pydantic-level tests for the search/audit query models that the routers
consume via `Annotated[Model, Query()]`. Covers the audit default
(`order_by=created_at`, `order_desc=True`), the boolean coercion on
`managed`, and a guard so future edits don't accidentally re-export the
unprefixed `_NAME_PATTERN_STR` etc. constants.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schemas import (
    AuditOrderBy,
    SearchAccessRequestQuery,
    SearchAppQuery,
    SearchGroupQuery,
    SearchGroupRequestQuery,
    SearchGroupRoleAuditQuery,
    SearchRoleQuery,
    SearchRoleRequestQuery,
    SearchTagQuery,
    SearchUserGroupAuditQuery,
)


# --- Audit defaults --------------------------------------------------------


def test_audit_user_default_order_by_created_at_desc() -> None:
    m = SearchUserGroupAuditQuery()
    assert m.order_by == AuditOrderBy.created_at
    assert m.order_desc is True


def test_audit_groups_default_order_by_created_at_desc() -> None:
    m = SearchGroupRoleAuditQuery()
    assert m.order_by == AuditOrderBy.created_at
    assert m.order_desc is True


def test_audit_order_by_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        SearchUserGroupAuditQuery(order_by="not_a_real_column")  # type: ignore[arg-type]


# --- Boolean coercion on `managed` ----------------------------------------


@pytest.mark.parametrize(
    "value,expected", [("true", True), ("True", True), ("1", True), ("false", False), ("0", False)]
)
def test_search_groups_managed_accepts_truthy_strings(value: str, expected: bool) -> None:
    m = SearchGroupQuery(managed=value)  # type: ignore[arg-type]
    assert m.managed is expected


def test_search_groups_managed_rejects_garbage() -> None:
    with pytest.raises(ValidationError):
        SearchGroupQuery(managed="not-a-bool")  # type: ignore[arg-type]


# --- Field surface guards: regression against silently-dropped fields ------


def test_audit_user_carries_owner_id_role_owner_app_owner_etc() -> None:
    m = SearchUserGroupAuditQuery(
        owner_id="alice@example.com",
        app_owner=True,
        deleted=False,
        managed=True,
        start_date=1700000000,
        end_date=1700001000,
        direct=True,
    )
    assert m.owner_id == "alice@example.com"
    assert m.app_owner is True
    assert m.deleted is False
    assert m.managed is True
    assert m.start_date == 1700000000
    assert m.end_date == 1700001000
    assert m.direct is True


def test_audit_groups_carries_role_owner_id() -> None:
    m = SearchGroupRoleAuditQuery(role_owner_id="@me")
    assert m.role_owner_id == "@me"


def test_role_request_carries_role_id() -> None:
    m = SearchRoleRequestQuery(requester_role_id="role-1")
    assert m.requester_role_id == "role-1"


def test_group_request_carries_app_and_type() -> None:
    m = SearchGroupRequestQuery(requested_group_type="app_group", requested_app_id="app-1")
    assert m.requested_group_type == "app_group"
    assert m.requested_app_id == "app-1"


def test_role_search_carries_owner_id() -> None:
    m = SearchRoleQuery(owner_id="@me")
    assert m.owner_id == "@me"


def test_access_request_search_full_field_set() -> None:
    m = SearchAccessRequestQuery(
        status="PENDING",
        requester_user_id="@me",
        requested_group_id="g-1",
        assignee_user_id="@me",
        resolver_user_id="@me",
        q="search",
    )
    assert m.status == "PENDING"
    assert m.requester_user_id == "@me"
    assert m.assignee_user_id == "@me"
    assert m.q == "search"


def test_app_and_tag_search_only_carry_q() -> None:
    a = SearchAppQuery(q="abc")
    t = SearchTagQuery(q="def")
    assert a.q == "abc" and t.q == "def"


# --- Rename guard: importing the renamed group-only constants -------------


def test_group_constants_renamed() -> None:
    # The constants in `requests_schemas.py` are intentionally prefixed
    # `_GROUP_*` to make it obvious they're only used by the group bodies.
    # This test fails fast if a future edit re-introduces an unprefixed
    # constant under the old name.
    from api.schemas.requests_schemas import (  # noqa: F401
        _GROUP_DESC_MAX_LENGTH,
        _GROUP_NAME_MAX_LENGTH,
        _GROUP_NAME_PATTERN_STR,
    )

    import api.schemas.requests_schemas as m

    assert not hasattr(m, "_NAME_PATTERN_STR")
    assert not hasattr(m, "_NAME_MAX_LENGTH")
    assert not hasattr(m, "_DESC_MAX_LENGTH")
