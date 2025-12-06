"""Tests for audit events plugin core functionality."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from api.plugins.audit_events import AuditEventEnvelope, get_audit_events_hook


@pytest.fixture
def sample_envelope() -> AuditEventEnvelope:
    """Create a sample audit event envelope for testing."""
    return AuditEventEnvelope(
        id=uuid4(),
        event_type="access_request.created",
        timestamp=datetime.now(timezone.utc),
        actor_id="user-123",
        actor_email="user@example.com",
        target_type="access_request",
        target_id="req-123",
        target_name="Test Access Request",
        action="created",
        reason="Testing purposes",
        payload={"test": "data"},
        metadata={"ip_address": "10.0.1.100"},
    )


class TestAuditEventEnvelope:
    """Tests for AuditEventEnvelope dataclass."""

    def test_envelope_creation(self, sample_envelope: AuditEventEnvelope) -> None:
        """Test AuditEventEnvelope can be instantiated with all required fields."""
        assert sample_envelope.event_type == "access_request.created"
        assert sample_envelope.actor_id == "user-123"
        assert sample_envelope.actor_email == "user@example.com"
        assert sample_envelope.target_type == "access_request"
        assert sample_envelope.target_id == "req-123"
        assert sample_envelope.target_name == "Test Access Request"
        assert sample_envelope.action == "created"
        assert sample_envelope.reason == "Testing purposes"
        assert sample_envelope.payload == {"test": "data"}
        assert sample_envelope.metadata == {"ip_address": "10.0.1.100"}

    def test_envelope_with_optional_fields_none(self) -> None:
        """Test envelope can be created with optional fields as None."""
        envelope = AuditEventEnvelope(
            id=uuid4(),
            event_type="app_delete",
            timestamp=datetime.now(timezone.utc),
            actor_id="system",
            actor_email=None,  # Optional field
            target_type="app",
            target_id="app-456",
            target_name=None,  # Optional field
            action="deleted",
            reason=None,  # Optional field
            payload={},
            metadata={},
        )
        assert envelope.actor_email is None
        assert envelope.target_name is None
        assert envelope.reason is None

    def test_envelope_id_is_uuid(self, sample_envelope: AuditEventEnvelope) -> None:
        """Test envelope ID is a valid UUID."""
        assert isinstance(sample_envelope.id, type(uuid4()))
        assert str(sample_envelope.id)  # Can be converted to string

    def test_envelope_timestamp_is_datetime(self, sample_envelope: AuditEventEnvelope) -> None:
        """Test envelope timestamp is a datetime object."""
        assert isinstance(sample_envelope.timestamp, datetime)
        assert sample_envelope.timestamp.tzinfo is not None  # Should be timezone-aware


class TestGetAuditEventsHook:
    """Tests for get_audit_events_hook function."""

    def test_get_audit_events_hook_returns_hook_relay(self) -> None:
        """Test hook initialization returns a HookRelay object."""
        hook = get_audit_events_hook()
        assert hook is not None
        assert hasattr(hook, "audit_event_logged")

    def test_get_audit_events_hook_is_singleton(self) -> None:
        """Test hook is cached and returns the same instance on subsequent calls."""
        hook1 = get_audit_events_hook()
        hook2 = get_audit_events_hook()
        assert hook1 is hook2

    def test_hook_invocation_with_no_plugins_succeeds(self, sample_envelope: AuditEventEnvelope) -> None:
        """Test hook can be called successfully even when no plugins are registered."""
        hook = get_audit_events_hook()
        # Should not raise an exception even if no plugins are listening
        hook.audit_event_logged(envelope=sample_envelope)

    def test_hook_invocation_with_multiple_envelopes(self) -> None:
        """Test hook can be called multiple times with different envelopes."""
        hook = get_audit_events_hook()

        envelope1 = AuditEventEnvelope(
            id=uuid4(),
            event_type="access_approve",
            timestamp=datetime.now(timezone.utc),
            actor_id="approver-1",
            actor_email="approver@example.com",
            target_type="access_request",
            target_id="req-1",
            target_name="Request 1",
            action="approved",
            reason="Approved for deployment",
            payload={},
            metadata={},
        )

        envelope2 = AuditEventEnvelope(
            id=uuid4(),
            event_type="group_member_added",
            timestamp=datetime.now(timezone.utc),
            actor_id="admin-1",
            actor_email="admin@example.com",
            target_type="group",
            target_id="group-1",
            target_name="Test Group",
            action="member_added",
            reason="Adding team member",
            payload={},
            metadata={},
        )

        # Both should succeed without error
        hook.audit_event_logged(envelope=envelope1)
        hook.audit_event_logged(envelope=envelope2)


class TestAuditEventTypes:
    """Tests for various audit event types to ensure envelope structure supports all types."""

    def test_access_request_event(self) -> None:
        """Test envelope for access request events."""
        envelope = AuditEventEnvelope(
            id=uuid4(),
            event_type="access_create",
            timestamp=datetime.now(timezone.utc),
            actor_id="requester-123",
            actor_email="requester@example.com",
            target_type="access_request",
            target_id="req-789",
            target_name="Production API Access",
            action="created",
            reason="Need access for deployment",
            payload={"app_group_id": "app-123"},
            metadata={},
        )
        assert envelope.event_type == "access_create"
        assert envelope.target_type == "access_request"

    def test_group_management_event(self) -> None:
        """Test envelope for group management events."""
        envelope = AuditEventEnvelope(
            id=uuid4(),
            event_type="group_member_added",
            timestamp=datetime.now(timezone.utc),
            actor_id="admin-456",
            actor_email="admin@example.com",
            target_type="group_membership",
            target_id="group-456",
            target_name="DevOps Team",
            action="member_added",
            reason="New team member onboarding",
            payload={"member_id": "user-789"},
            metadata={},
        )
        assert envelope.event_type == "group_member_added"
        assert envelope.target_type == "group_membership"

    def test_app_management_event(self) -> None:
        """Test envelope for app management events."""
        envelope = AuditEventEnvelope(
            id=uuid4(),
            event_type="app_create",
            timestamp=datetime.now(timezone.utc),
            actor_id="admin-123",
            actor_email="admin@example.com",
            target_type="app",
            target_id="app-new-123",
            target_name="New Production App",
            action="created",
            reason="New application setup",
            payload={"description": "Production application"},
            metadata={},
        )
        assert envelope.event_type == "app_create"
        assert envelope.target_type == "app"

    def test_role_request_event(self) -> None:
        """Test envelope for role request events."""
        envelope = AuditEventEnvelope(
            id=uuid4(),
            event_type="role_request_approve",
            timestamp=datetime.now(timezone.utc),
            actor_id="approver-456",
            actor_email="approver@example.com",
            target_type="role_request",
            target_id="role-req-123",
            target_name="Admin Role Request",
            action="approved",
            reason="Verified team lead authority",
            payload={"role_group_id": "role-123"},
            metadata={},
        )
        assert envelope.event_type == "role_request_approve"
        assert envelope.target_type == "role_request"

    def test_tag_management_event(self) -> None:
        """Test envelope for tag management events."""
        envelope = AuditEventEnvelope(
            id=uuid4(),
            event_type="tag_create",
            timestamp=datetime.now(timezone.utc),
            actor_id="admin-789",
            actor_email="admin@example.com",
            target_type="tag",
            target_id="tag-123",
            target_name="Production",
            action="created",
            reason="New tag for production resources",
            payload={"tag_name": "Production"},
            metadata={},
        )
        assert envelope.event_type == "tag_create"
        assert envelope.target_type == "tag"
