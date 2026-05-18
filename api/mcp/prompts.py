"""Server-level instructions string and the v1 ``request_access`` prompt.

Both are part of the MCP protocol and work with any compliant client
(Claude Code, Claude.ai, Cursor, Zed, OpenAI-compatible clients,
self-hosted models). They exist to bias the model toward Access's
design goals — prefer roles over direct membership, prefer
time-bounded access for non-day-to-day needs, surface tag constraints
proactively — without re-deriving the entire codebase context every
session.

The instructions string is short on purpose: the LLM pays for it in
tokens every session. The README is the long-form doc; this is the
orientation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

# Kept deliberately short — every MCP session re-issues this text to the
# client. Goal: orient the LLM, point at the most useful tools, signal
# the operator-configurable bits (the prefixes for App- and Role-
# groups, etc.) so it doesn't invent them. Anything else lives in the
# README.
ACCESS_MCP_INSTRUCTIONS = """You are connected to Access, an Okta access-control portal
that implements RBAC over Okta groups and roles.

## Core concepts
- **OktaGroup** is a plain Okta group. **AppGroup** is an Okta group
  associated with an Access ``App`` (prefix configurable, defaults to
  ``App-``). **RoleGroup** is an Okta group representing a job function
  (prefix configurable, defaults to ``Role-``); adding a role to a
  group grants every role member access to that group.
- **AccessRequest** = a user requests membership or ownership in a
  group/role for themselves. **RoleRequest** = a role owner requests
  that a role be granted access to a group. **GroupRequest** = a user
  requests creation of a new group/role.
- **Tags** carry constraints (member time limit, owner time limit,
  reason requirements, self-add restrictions). Disabled tags do not
  apply.

## Design goals to surface to the user
- Prefer **roles over direct group membership**: a role represents a
  job function, and adding the role to groups is the maintainable path.
- Prefer **time-bounded access** for non-day-to-day needs. Indefinite
  access is for ongoing responsibilities; everything else should
  expire. When the user hasn't named a duration and no tag on the
  target enforces a limit, offer the standard set (matches the UI
  dropdown): **12 hours, 5 days, two weeks, 30 days, 90 days, or
  indefinite**.
- **Least privilege**: never recommend a permission tier broader than
  the task requires.

## Available tools (v1)
- Read/list: ``list_groups`` / ``get_group``, ``list_roles`` /
  ``get_role``, ``list_apps`` / ``get_app``, ``list_users`` /
  ``get_user``, ``list_tags`` / ``get_tag``, ``list_access_requests`` /
  ``get_access_request``, ``list_role_requests`` /
  ``get_role_request``, ``list_group_requests`` /
  ``get_group_request``, ``list_audit_entries``,
  ``list_group_memberships``. All require the ``read_all`` scope.
- Write (all require the ``create_requests`` scope):
  - ``create_access_request`` — request membership/ownership in a
    specific group/role for yourself. Open to any authenticated user.
  - ``create_role_request`` — request that a ROLE be granted access to
    a specific (non-role) group. Restricted to OWNERS of the role
    being submitted (and Access admins). If you are not an owner of
    the role, the tool returns an authorization error.
  - ``create_group_request`` — request the creation of a new group,
    role, or app group. Open to any authenticated user; app-group
    requests require ``app_id``.
  Approvals, rejections, and direct membership edits are
  intentionally not exposed in v1.

## Scopes
The token attenuates which tools are available. The default token
session under Cloudflare Managed OAuth (or any provider that does
not issue scope claims) is **read-only**: tools that require
``create_requests`` will return an authorization error unless the
operator has explicitly opted into write capability for default
sessions. A token that does carry ``create_requests`` can submit
access / role / group requests but still cannot approve them —
approvals are not exposed via MCP at all.
"""


REQUEST_ACCESS_PROMPT_DESC = (
    "Walk a user through submitting an Access request, preferring roles over direct "
    "group membership, short-duration access over indefinite access, and surfacing "
    "tag constraints (reason requirements, time limits) up front so the request isn't "
    "rejected by validation."
)


def _request_access_prompt(group_or_role: str = "") -> str:
    """Returns the prompt body the LLM will see. The ``group_or_role``
    argument is what the user typed (group name, role name, or
    description of what they need) — kept loose because users speak in
    different shapes and we don't want to reject before the LLM can
    help disambiguate.
    """
    target_hint = (
        f"The user is asking about: {group_or_role}\n\n"
        if group_or_role
        else "The user has not yet named a specific group or role.\n\n"
    )
    return (
        "You are helping the user submit an Access request. Before drafting "
        "anything, walk through these checks — Access's design goals encode "
        "least-privilege and time-bound access, and the agent should reflect "
        "them.\n\n"
        f"{target_hint}"
        "1. **Is there a role that already grants this access?** Use "
        "``list_roles`` and ``get_role`` to look. If a role exists that "
        "covers the user's job function, requesting that role is preferable "
        "to requesting the specific group — roles are the maintainable RBAC "
        "primitive.\n"
        "2. **Is this access needed indefinitely, or for a specific task?** "
        "If it's task-bound (incident response, one-time investigation, "
        "vendor onboarding window), request time-bounded access with an "
        "``ending_at`` rather than indefinite membership. If the user "
        "hasn't named a duration and no tag enforces a limit, offer the "
        "standard set (these match the UI dropdown): 12 hours, 5 days, "
        "two weeks, 30 days, 90 days, or indefinite.\n"
        "3. **Inspect tag constraints on the target.** ``get_group`` and "
        "``get_role`` return ``active_group_tags``. Enabled tags can force "
        "a reason, force a time limit, or disallow self-add. Surface these "
        "to the user so the request body satisfies them on the first try.\n"
        "4. **Submit via ``create_access_request``.** Include a clear "
        "``reason`` even if no tag requires one — owners reviewing the "
        "request rely on it. Set ``ending_at`` to match what you established "
        "in step 2.\n\n"
        "Do not attempt to approve or modify a request through MCP — the "
        "approve / reject / direct-add paths are intentionally not exposed."
    )


def register_prompts(mcp: "FastMCP") -> None:
    """Register the v1 prompts on the given FastMCP instance."""

    @mcp.prompt(name="request_access", description=REQUEST_ACCESS_PROMPT_DESC)
    def request_access(group_or_role: str = "") -> str:
        return _request_access_prompt(group_or_role)
