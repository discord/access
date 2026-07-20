"""Request-body factories for endpoint tests.

These are `polyfactory` `ModelFactory` subclasses bound to the Pydantic
request-body schemas in `api.schemas.requests_schemas`. They exist so a test
that POSTs/PUTs a request body only has to spell out the fields it actually
asserts on; the schema supplies the rest.

`json(**overrides)` returns the body as a JSON-mode dict ready for
`client.post`/`client.put(url, json=...)`. It emits exactly two kinds of field:
the ones the caller overrode, and the ones polyfactory has to supply because
the schema gives it no choice — required fields with no default (`group_id`,
`approved`, a discriminator like `type` / `requested_group_type`). Fields that
carry a schema default and weren't overridden are left out entirely (via
`model_dump(exclude_unset=True)`), so the server applies the same default a bare
hand-written dict would have relied on. `__use_defaults__ = True` keeps
polyfactory from inventing random values for those defaulted fields in the first
place.

Two consequences worth knowing:

- Partial-update (PATCH-like) bodies stay partial. `UpdateAppBody` etc. treat an
  absent field as "leave unchanged" and a present `null` as a value; because the
  dict omits fields the caller didn't set, updating only `name` can't wipe
  `description`.
- Drift stays quiet in the right way. When a schema grows a new *required*
  field, polyfactory supplies a value so existing call sites keep sending a
  complete, valid body instead of every hand dict suddenly 400ing on the missing
  key. A new *optional* field is simply omitted until a test cares about it.

The membership-editor bodies (`GroupMember` / `RoleMember`) need one extra
touch: their `*_to_add` / `*_to_remove` lists are required with no default, so
without help polyfactory would generate random ids into the lists a test didn't
set — and the endpoint would act on them. Their factories declare those four
lists as `Use(list)`, so each is an explicit empty list unless overridden; a
call that sets only `members_to_add` sends the other three as `[]`.

When NOT to reach for these: tests that deliberately send a malformed body — an
omitted required field, an unknown tag id, a discriminator with a missing
companion field — to assert a 4xx. A schema-bound factory would fill in exactly
the field those tests leave out, so they keep their literal dicts.
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel
from polyfactory import Use
from polyfactory.factories.pydantic_factory import ModelFactory

from api.schemas.requests_schemas import (
    CreateAccessRequestBody,
    CreateAppBody,
    CreateRoleRequestBody,
    CreateTagBody,
    GroupMember,
    ResolveAccessRequestBody,
    ResolveGroupRequestBody,
    ResolveRoleRequestBody,
    RoleMember,
    UpdateAppBody,
    UpdateTagBody,
    _AppGroupCreateBody,
    _AppGroupRequestBody,
    _AppGroupUpdateBody,
    _OktaGroupCreateBody,
    _OktaGroupRequestBody,
    _OktaGroupUpdateBody,
    _RoleGroupCreateBody,
    _RoleGroupRequestBody,
    _RoleGroupUpdateBody,
)

_T = TypeVar("_T", bound=BaseModel)


class _RequestBodyFactory(ModelFactory[_T]):
    """Base for the request-body factories. `__model__` is inferred from the
    type argument each concrete factory binds (e.g.
    `_RequestBodyFactory[CreateAccessRequestBody]`)."""

    __is_base_factory__ = True
    # Keep polyfactory from inventing random values for fields that have a
    # Pydantic default; combined with exclude_unset below, those fields drop out
    # of the payload and the server applies the default.
    __use_defaults__ = True

    @classmethod
    def json(cls, **overrides: Any) -> dict[str, Any]:
        """A JSON-mode dict for `client.post`/`client.put(url, json=...)`.

        Emits only the fields the caller set plus the ones the schema forces
        polyfactory to supply (required fields with no default). `exclude_unset`
        drops defaulted fields the caller didn't touch, so partial-update bodies
        stay partial and the payload matches a minimal hand-written dict.
        """
        return cls.build(**overrides).model_dump(mode="json", exclude_unset=True)


# --- Access requests --------------------------------------------------------


class CreateAccessRequestBodyFactory(_RequestBodyFactory[CreateAccessRequestBody]):
    """POST /api/requests. Pass `group_id`; override `group_owner`/`reason`/
    `ending_at` when the test asserts on them."""


class ResolveAccessRequestBodyFactory(_RequestBodyFactory[ResolveAccessRequestBody]):
    """PUT /api/requests/{id}. Pass `approved` (no default — it is the
    decision); override `reason`/`ending_at` when relevant."""


# --- Role requests ----------------------------------------------------------


class CreateRoleRequestBodyFactory(_RequestBodyFactory[CreateRoleRequestBody]):
    """POST /api/role-requests. Pass `role_id` and `group_id`."""


class ResolveRoleRequestBodyFactory(_RequestBodyFactory[ResolveRoleRequestBody]):
    """PUT /api/role-requests/{id}. Pass `approved`."""


# --- Group requests ---------------------------------------------------------
#
# `CreateGroupRequestBody` is a discriminated union on `requested_group_type`,
# so there is one factory per concrete variant. Each fixes its own
# `requested_group_type` literal and generates a pattern-valid
# `requested_group_name`; override the name when a test asserts on it.


class OktaGroupRequestBodyFactory(_RequestBodyFactory[_OktaGroupRequestBody]):
    """POST /api/group-requests with `requested_group_type="okta_group"`."""


class RoleGroupRequestBodyFactory(_RequestBodyFactory[_RoleGroupRequestBody]):
    """POST /api/group-requests with `requested_group_type="role_group"`."""


class AppGroupRequestBodyFactory(_RequestBodyFactory[_AppGroupRequestBody]):
    """POST /api/group-requests with `requested_group_type="app_group"`. Pass
    `requested_app_id` — it is required and has no default."""


class ResolveGroupRequestBodyFactory(_RequestBodyFactory[ResolveGroupRequestBody]):
    """PUT /api/group-requests/{id}. Pass `approved`; override the `resolved_*`
    fields when the test exercises an approve-with-edits path."""


# --- Tags -------------------------------------------------------------------


class CreateTagBodyFactory(_RequestBodyFactory[CreateTagBody]):
    """POST /api/tags. `constraints` defaults to `None`; pass a dict of valid
    constraint keys when the test needs them (unknown keys are rejected)."""


class UpdateTagBodyFactory(_RequestBodyFactory[UpdateTagBody]):
    """PUT /api/tags/{id}. Every field is optional; pass only what changes."""


# --- Apps -------------------------------------------------------------------


class CreateAppBodyFactory(_RequestBodyFactory[CreateAppBody]):
    """POST /api/apps. `initial_additional_app_groups` defaults to `None`, so
    the app-group name-prefix validator is skipped unless a test supplies it."""


class UpdateAppBodyFactory(_RequestBodyFactory[UpdateAppBody]):
    """PUT /api/apps/{id}. Every field is optional; pass only what changes."""


# --- Groups (create / update) -----------------------------------------------
#
# `CreateGroupBody` and `UpdateGroupBody` are each discriminated unions on
# `type`, so there is one factory per concrete variant. The create factories
# generate a pattern-valid `name`; the update variants leave `name` at its
# `None` default (a partial update touches only the fields it sets).


class OktaGroupCreateBodyFactory(_RequestBodyFactory[_OktaGroupCreateBody]):
    """POST /api/groups with `type="okta_group"`."""


class RoleGroupCreateBodyFactory(_RequestBodyFactory[_RoleGroupCreateBody]):
    """POST /api/groups with `type="role_group"`."""


class AppGroupCreateBodyFactory(_RequestBodyFactory[_AppGroupCreateBody]):
    """POST /api/groups with `type="app_group"`. Pass `app_id` when the group
    should be attached to an app."""


class OktaGroupUpdateBodyFactory(_RequestBodyFactory[_OktaGroupUpdateBody]):
    """PUT /api/groups/{id} with `type="okta_group"`."""


class RoleGroupUpdateBodyFactory(_RequestBodyFactory[_RoleGroupUpdateBody]):
    """PUT /api/groups/{id} with `type="role_group"`."""


class AppGroupUpdateBodyFactory(_RequestBodyFactory[_AppGroupUpdateBody]):
    """PUT /api/groups/{id} with `type="app_group"`."""


# --- Membership editors -----------------------------------------------------
#
# The `*_to_add` / `*_to_remove` lists are required with no schema default, so
# `Use(list)` gives each an empty default (see the module docstring). A call
# sets only the list(s) it exercises; the others stay empty.


class GroupMemberFactory(_RequestBodyFactory[GroupMember]):
    """PUT /api/groups/{id}/members. Pass `members_to_add` / `owners_to_add`
    (etc.) as user-id lists; unset lists default to empty."""

    members_to_add = Use(list)
    members_to_remove = Use(list)
    owners_to_add = Use(list)
    owners_to_remove = Use(list)


class RoleMemberFactory(_RequestBodyFactory[RoleMember]):
    """PUT /api/roles/{id}/members. Pass `groups_to_add` / `owner_groups_to_add`
    (etc.) as group-id lists; unset lists default to empty."""

    groups_to_add = Use(list)
    groups_to_remove = Use(list)
    owner_groups_to_add = Use(list)
    owner_groups_to_remove = Use(list)
