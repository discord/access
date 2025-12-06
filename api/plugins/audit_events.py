"""Audit event notification system for plugins.

This module defines the pluggy hookspec for audit event streaming. Access operations
call this hook after committing audit data to the database, allowing plugins to
capture and process audit events (e.g., stream to SIEM systems).

Constitution Principle III: All audit events must include WHO/WHAT/TARGET/WHEN/WHY context.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID

import pluggy

audit_events_plugin_name = "access_audit_events"
hookspec = pluggy.HookspecMarker(audit_events_plugin_name)
hookimpl = pluggy.HookimplMarker(audit_events_plugin_name)

_cached_audit_events_hook: Optional[pluggy.HookRelay] = None

logger = logging.getLogger(__name__)


@dataclass
class AuditEventEnvelope:
    """Canonical structure for audit events passed to plugins.

    This envelope contains complete WHO/WHAT/TARGET/WHEN/WHY context required
    by Constitution Principle III, plus metadata for plugin processing.

    Attributes:
        id: Unique event identifier (UUID v4) for deduplication across retries
        event_type: Access event type (e.g., 'access_request.created', 'group.member_added')
        timestamp: When the audited action occurred (timezone-aware UTC datetime)
        actor_id: WHO - User ID of the person performing the action
        actor_email: WHO - Email of the actor for human traceability
        target_type: WHAT - Type of resource affected (group, app, role, tag, access_request)
        target_id: WHAT - ID of the affected resource
        target_name: WHAT - Human-readable name of the affected resource
        action: WHAT - Specific action performed (created, approved, deleted, etc.)
        reason: WHY - User-provided justification for the action (if applicable)
        payload: Complete audit data including all context from database models
        metadata: Additional context (request_id, session_id, ip_address, etc.)
    """

    id: UUID
    event_type: str
    timestamp: datetime
    actor_id: str
    actor_email: Optional[str]
    target_type: str
    target_id: str
    target_name: Optional[str]
    action: str
    reason: Optional[str]
    payload: Dict[str, Any]
    metadata: Dict[str, Any]


class AuditEventsPluginSpec:
    """Pluggy hookspec for audit event notifications."""

    @hookspec
    def audit_event_logged(self, envelope: AuditEventEnvelope) -> None:
        """Called immediately after Access commits audit data to the database.

        This hook is invoked synchronously after every state-changing operation
        that generates an audit event. Plugins receive the complete audit context
        and can process it (e.g., stream to SIEM, trigger alerts, update metrics).

        Args:
            envelope: Complete audit event with WHO/WHAT/TARGET/WHEN/WHY context

        Important:
            - Hook is called AFTER database commit (zero data loss guarantee)
            - Hook execution is synchronous (blocks request until complete)
            - Plugins MUST handle errors gracefully to avoid breaking Access operations
            - Plugins SHOULD complete quickly (<100ms) to avoid request latency impact
            - Plugins MUST NOT modify the envelope (read-only)
        """


def get_audit_events_hook() -> pluggy.HookRelay:
    """Get the audit events hook relay for calling plugins.

    Returns:
        HookRelay configured with audit events plugin spec

    Usage:
        hook = get_audit_events_hook()
        hook.audit_event_logged(envelope=event)
    """
    global _cached_audit_events_hook

    if _cached_audit_events_hook is None:
        pm = pluggy.PluginManager(audit_events_plugin_name)
        pm.add_hookspecs(AuditEventsPluginSpec)
        count = pm.load_setuptools_entrypoints(audit_events_plugin_name)
        logger.info(f"Initialized {audit_events_plugin_name} plugin manager")
        logger.info(f"Count of loaded audit events plugins: {count}")
        _cached_audit_events_hook = pm.hook

    return _cached_audit_events_hook
