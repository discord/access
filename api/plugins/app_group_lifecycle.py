import logging
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Literal, NoReturn

import pluggy
from fastapi import HTTPException
from sqlalchemy import Select, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, joinedload

from api.extensions import db
from api.models import App, AppGroup, OktaUser, OktaUserGroupMember
from api.plugins._async_dispatch import run_hooks_to_completion, verify_async_impls
from api.services import okta

app_group_lifecycle_plugin_name = "access_app_group_lifecycle"
hookspec = pluggy.HookspecMarker(app_group_lifecycle_plugin_name)
hookimpl = pluggy.HookimplMarker(app_group_lifecycle_plugin_name)

_cached_app_group_lifecycle_hook: pluggy.HookRelay | None = None


class AppGroupLifecycleHook(StrEnum):
    """The lifecycle hooks that receive an ``AppGroupLifecycleContext`` and are awaited by the
    application (StrEnum value == the pluggy hook name). The
    metadata/config/status/validation hooks are pure schema accessors and remain
    synchronous, so they are not members here (and are excluded from the async check)."""

    GROUP_CREATED = "group_created"
    GROUP_UPDATED = "group_updated"
    GROUP_DELETED = "group_deleted"
    GROUP_MEMBERS_ADDED = "group_members_added"
    GROUP_MEMBERS_REMOVED = "group_members_removed"
    SYNC_GROUP = "sync_group"


class PluginNotFoundError(Exception):
    """Raised by plugin endpoints when the requested plugin id is not
    registered. The exception handler in `api/exception_handlers.py`
    serializes it as a 404 with an `{"error": "..."}` body — the wire
    shape the React client consumes from these endpoints."""

    def __init__(self, plugin_id: str) -> None:
        self.plugin_id = plugin_id
        super().__init__(f"Plugin '{plugin_id}' not found")


class AmbiguousOktaTargetError(Exception):
    """More than one Okta target group matches an external group id, so a push mapping cannot be
    created unambiguously. This is a misconfiguration (e.g. a stale + re-imported target sharing
    the same external id) that will not self-heal, so it is surfaced as an error rather than
    conflated with the not-yet-imported case (which simply defers)."""


class MissingOktaTargetError(Exception):
    """Okta is not aware of a target group from an external app and so cannot create a push mapping."""


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppGroupLifecyclePluginMetadata:
    """Metadata for an app group lifecycle plugin."""

    id: str
    display_name: str
    description: str


_cached_plugin_registry: list[AppGroupLifecyclePluginMetadata] | None = None


@dataclass(frozen=True)
class AppGroupLifecyclePluginConfigProperty:
    """Schema for a configuration property required by an app group lifecycle plugin."""

    display_name: str
    help_text: str | None = None
    type: Literal["text", "number", "boolean"] = "text"
    default_value: Any = None
    required: bool = False
    # An open, plugin-defined bag of validation rules -- the shape varies by plugin (this repo's
    # examples use ``{"patterns": [{"regex", "message"}]}``, enforced client-side by the config
    # form, and ``{"allowed_values": [...]}``), so it is intentionally not strictly typed.
    validation: dict[str, Any] | None = None
    # When True, the host rejects edits to this field on update (group config only);
    # the value may be set freely at create time. Enforced in
    # validate_app_group_lifecycle_plugin_group_config.
    immutable: bool = False
    # Optional static text shown inline after a text field's value (an input suffix /
    # end adornment), e.g. an email domain like "@example.com" appended to a local-part
    # field. Purely presentational — it is not part of the stored value.
    suffix: str | None = None

    def __post_init__(self) -> None:
        # The frontend only renders a suffix on text inputs; setting it on a number/boolean is a
        # silently-inert mistake, so fail fast at construction (i.e. when the plugin declares it).
        if self.suffix is not None and self.type != "text":
            raise ValueError(f"suffix is only supported on text config properties, not type={self.type!r}")


@dataclass(frozen=True)
class AppGroupLifecyclePluginStatusProperty:
    """Schema for a status property exposed by an app group lifecycle plugin."""

    display_name: str
    help_text: str | None = None
    type: Literal["text", "number", "date", "boolean"] = "text"


@dataclass
class AppGroupLifecyclePluginData:
    """Data for an app group lifecycle plugin."""

    configuration: dict[str, Any]
    status: dict[str, Any]


@dataclass(frozen=True)
class _StatusWrite:
    """One recorded ``set_status(..., durable_on_failure=True)`` call, in plain-Python form.

    Deliberately holds no ORM references: it is replayed *after* a rollback has expired the entire
    identity map, so anything but the entity type, its id, and a plain value would be unusable by
    then.
    """

    entity_type: Literal["app", "group"]
    entity_id: str
    property_name: str
    value: Any


def _active_membership(keyed_column: InstrumentedAttribute[str]) -> Select[tuple[str]]:
    """A subquery over the currently-held, non-owner rows of ``okta_user_group_member``, selecting
    one side of the membership for the caller to match with ``IN``.

    Membership is what an external system provisions and ownership is who administers the group in
    Access, so owner rows are excluded; ``ended_at`` carries the temporal filter every query on this
    table must apply. The caller adds the predicate on the other side.

    Selecting one column into an ``IN``, rather than joining the membership table and
    de-duplicating with ``DISTINCT``, for two reasons. A user can hold the same group both
    directly and through a role, so the join multiplies rows; and ``SELECT DISTINCT`` over the
    entity would have to compare ``AppGroup.plugin_data`` / ``OktaUser.profile``, which are ``JSON``
    rather than ``JSONB`` on any non-Postgres backend and have no equality operator there. Both
    composite indexes on this table lead with a keyed side and then ``is_owner``, ``ended_at``, so
    this stays an index lookup.

    Args:
        keyed_column: The column to select -- ``OktaUserGroupMember.user_id`` to find a group's
                      members, ``OktaUserGroupMember.group_id`` to find a user's groups.

    Returns:
        A SELECT of that column, usable as the right-hand side of an ``IN``.
    """
    return select(keyed_column).where(
        OktaUserGroupMember.is_owner.is_(False),
        or_(OktaUserGroupMember.ended_at.is_(None), OktaUserGroupMember.ended_at > func.now()),
    )


async def get_active_group_members(session: AsyncSession, group_id: str) -> list[OktaUser]:
    """The users who currently hold membership in a group.

    The single definition of "this group's membership" that the plugin interface exposes, shared by
    ``AppGroupLifecycleContext.list_group_members`` and by the ``members`` payload the operations
    hand to ``group_deleted``. Keep it shared: a hook that reads membership one way during a sync
    and is handed it another way during a delete would silently disagree with itself.

    Takes the session explicitly rather than reaching for `db.session`, because a context holds its
    own captured session -- see ``AppGroupLifecycleContext.__init__``.

    Args:
        session: The session to query on.
        group_id: The group whose membership to read.

    Returns:
        Active, non-owner members, ordered by email. Owners are excluded: they administer the group
        rather than hold the access it grants. A user holding membership both directly and through
        a role appears once. Soft-deleted users are excluded.
    """
    return list(
        (
            await session.scalars(
                select(OktaUser)
                .where(
                    OktaUser.id.in_(
                        _active_membership(OktaUserGroupMember.user_id).where(OktaUserGroupMember.group_id == group_id)
                    )
                )
                .where(OktaUser.deleted_at.is_(None))
                .order_by(OktaUser.email)
            )
        ).all()
    )


class AppGroupLifecycleContext:
    """The capability surface an app group lifecycle hook may use to talk to Access.

    A hook gets exactly these verbs and nothing else: no session, no query builder, no
    ``api.operations`` import, no ``api.services.okta`` import. Every method is bound to the
    invoking plugin's id, so plugin code never threads ``plugin_id`` through a capability call and
    cannot read or write another plugin's ``plugin_data`` namespace.

    Lifetime is one instance per hook invocation, constructed host-side by
    ``invoke_app_group_lifecycle_hook``. **Transaction policy belongs to the host: a hook must not
    commit or roll back.** The host commits after the hook returns normally, and on a hook exception
    rolls back and then re-applies the status writes recorded here (see ``set_status``).
    """

    def __init__(self, *, session: AsyncSession, plugin_id: str) -> None:
        """Build the context for one hook invocation.

        Args:
            session: The session the host's transaction runs on. Never exposed to the plugin.
            plugin_id: The plugin being invoked. Every capability call is bound to it.
        """
        # Captured eagerly rather than resolved lazily from `db.session` on each use. `db.session` is
        # an async_scoped_session proxy; once the scope is removed the next access builds a brand-new
        # AsyncSession, so a lazy lookup could silently switch sessions mid-hook and land writes in a
        # transaction nobody commits. Holding the session also keeps this class testable without
        # app-level DB setup -- and reaching for the module-global proxy in here would recreate
        # exactly the coupling this context exists to remove.
        self._session = session
        self._plugin_id = plugin_id
        self._status_writes: list[_StatusWrite] = []

    @property
    def plugin_id(self) -> str:
        """The id of the plugin this context is bound to. Compare against your own plugin id in a
        hook's filter guard: ``if plugin_id != ctx.plugin_id: return``."""
        return self._plugin_id

    # ---- Serialization ----

    async def lock(self, key: str) -> None:
        """Serialize this hook against concurrent runs of the same plugin locking the same ``key``,
        for the remainder of the host's transaction.

        Takes a Postgres transaction-level advisory lock, and blocks until it is available. There
        is deliberately **no release**: the lock is held until the host commits or rolls back after
        the hook, and that is precisely what makes a check-then-write pair inside one hook atomic
        against a concurrent hook run. (Hence a plain ``await`` rather than an ``async with`` block,
        which would advertise a scope this cannot honor.) The lock is held across whatever I/O the
        rest of the hook performs, so keep the locked region tight and give external clients a
        timeout.

        A no-op on non-Postgres backends (e.g. the SQLite test DB), where the relevant paths are
        single-writer.

        Args:
            key: What to serialize on, typically an identifier for the external resource being
                 claimed. Namespaced by plugin id, so two plugins choosing the same string do not
                 contend.
        """
        bind = self._session.get_bind()
        if bind is None or bind.dialect.name != "postgresql":
            return
        # hashtextextended maps the key to the bigint the advisory-lock functions take; key
        # collisions only cause extra (harmless) serialization.
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"{self._plugin_id}:{key}"},
        )

    # ---- Queries ----

    async def find_groups_by_status(
        self,
        status_property_name: str,
        status_property_value: str,
        *,
        exclude_group: AppGroup | None = None,
        limit: int | None = None,
    ) -> list[AppGroup]:
        """Find the app groups whose status for this plugin records a given value.

        The uniqueness and ownership lookup a plugin needs to answer "does another Access group
        already claim this external resource?". Scoped to apps configured with this plugin id --
        one external system can back several Access apps and they all name the same plugin. The
        predicate is pushed into SQL as a JSON path lookup on the stored status, so this stays a
        point lookup rather than a scan of every plugin-managed group.

        Args:
            status_property_name: The status property to match on.
            status_property_value: The value it must equal, compared **as text**: a status stored
                                   as a number or boolean will not match a Python ``int``/``bool``
                                   (JSONB renders a boolean as ``"true"`` where ``str(True)`` is
                                   ``"True"``), so pass the string form.
            exclude_group: Drop one group from the result, normally the group being reconciled.
            limit: Return at most this many matches. Omit for all of them.

        Returns:
            Active app groups, each with ``.app`` loaded; every other relationship is
            ``lazy="raise_on_sql"`` and must not be touched. Soft-deleted apps and groups are
            excluded.
        """
        path = (self._plugin_id, "status", status_property_name)
        stmt = (
            select(AppGroup)
            .options(joinedload(AppGroup.app))
            .join(App, AppGroup.app_id == App.id)
            .where(App.app_group_lifecycle_plugin == self._plugin_id)
            .where(App.deleted_at.is_(None))
            .where(AppGroup.deleted_at.is_(None))
            .where(AppGroup.plugin_data[path].as_string() == status_property_value)
        )
        if exclude_group is not None:
            stmt = stmt.where(AppGroup.id != exclude_group.id)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list((await self._session.scalars(stmt)).unique().all())

    async def _require_own_group(self, group: AppGroup) -> None:
        """Guard that ``group`` belongs to an app configured with this plugin.

        The context is bound to one plugin id so that plugin code cannot reach past its own apps.
        A caller-supplied group is the one argument that could otherwise sidestep that binding, so
        any capability taking one has to re-establish it. Resolved from the database rather than
        read off ``group.app``, which is ``lazy="raise_on_sql"`` and would turn an unloaded
        relationship into an obscure failure in place of this one.

        Args:
            group: The group to check.

        Raises:
            ValueError: If the group's app names a different lifecycle plugin, or none at all.
        """
        owning_plugin = await self._session.scalar(select(App.app_group_lifecycle_plugin).where(App.id == group.app_id))
        if owning_plugin != self._plugin_id:
            raise ValueError(
                f"Group {group.id} is not configured with the '{self._plugin_id}' app group lifecycle plugin"
            )

    async def list_group_members(self, group: AppGroup) -> list[OktaUser]:
        """The users who currently hold membership in ``group``.

        The membership read a plugin needs to converge ``sync_group``. It is a capability rather
        than an eager-load on the passed-in group because every relationship on ``group`` except
        ``.app`` is ``lazy="raise_on_sql"``.

        Args:
            group: The app group whose membership to read. Must belong to an app configured with
                   this plugin -- in practice a group the plugin was handed by a hook or got back
                   from ``find_user_groups``.

        Returns:
            Active, non-owner members, ordered by email. Group *owners* are excluded: they
            administer the group rather than hold the access it grants. A user who holds
            membership both directly and through a role has two membership rows and still appears
            once. Soft-deleted users are excluded. Every relationship on a returned ``OktaUser``
            is ``lazy="raise_on_sql"`` and must not be touched; its columns are loaded.

        Raises:
            ValueError: If the group's app is not configured with this plugin. Membership is not
                stored per-plugin the way ``plugin_data`` is, so nothing but this check keeps the
                capability inside the apps the plugin is responsible for.
        """
        await self._require_own_group(group)
        return await get_active_group_members(self._session, group.id)

    async def find_user_groups(self, user: OktaUser, *, app: App | None = None) -> list[AppGroup]:
        """The app groups ``user`` currently holds membership in, among this plugin's apps.

        The cross-group read a plugin needs when the external system grants a user one combined
        permission set rather than one per group: on removal from a group the correct action is to
        recompute the union over the groups the user still holds and write the reduced set, which
        the ``members`` delta handed to ``group_members_removed`` cannot express on its own.

        Args:
            user: The user whose memberships to read.
            app: Narrow the result to a single app. Omit to span every app configured with this
                 plugin id -- one external system can back several Access apps, and a user's
                 grants are the union across all of them.

        Returns:
            Active app groups, ordered by name, each with ``.app`` loaded; every other
            relationship is ``lazy="raise_on_sql"`` and must not be touched. Groups the user only
            *owns* are excluded, matching ``list_group_members``, as are soft-deleted apps and
            groups. A group reached both directly and through a role appears once.
        """
        stmt = (
            select(AppGroup)
            .options(joinedload(AppGroup.app))
            .join(App, AppGroup.app_id == App.id)
            .where(App.app_group_lifecycle_plugin == self._plugin_id)
            .where(App.deleted_at.is_(None))
            .where(AppGroup.deleted_at.is_(None))
            .where(
                AppGroup.id.in_(
                    _active_membership(OktaUserGroupMember.group_id).where(OktaUserGroupMember.user_id == user.id)
                )
            )
            .order_by(AppGroup.name)
        )
        if app is not None:
            stmt = stmt.where(AppGroup.app_id == app.id)
        return list((await self._session.scalars(stmt)).unique().all())

    # ---- Configuration and status ----

    def _data(self, app_or_group: App | AppGroup) -> AppGroupLifecyclePluginData:
        """This plugin's slice of ``plugin_data``, validated. Reading and writing go through here so
        the bound ``plugin_id`` is the only namespace reachable from a context.

        Args:
            app_or_group: The app or group whose ``plugin_data`` to slice.

        Returns:
            This plugin's configuration and status, empty dicts where unset.

        Raises:
            ValueError: If the stored slice is not a dict, or its configuration/status is not.
        """
        return _get_data_for_plugin(app_or_group.plugin_data, self._plugin_id)

    def _store(self, app_or_group: App | AppGroup, data: AppGroupLifecyclePluginData) -> None:
        """Write the slice back and mark the object for persistence. `plugin_data` is a
        change-tracked mutable JSON column, but the explicit ``add`` keeps a freshly-loaded or
        re-attached instance in the unit of work -- and means a plugin never touches the session.

        Args:
            app_or_group: The app or group to write to.
            data: This plugin's configuration and status, replacing its whole slice.
        """
        app_or_group.plugin_data[self._plugin_id] = asdict(data)
        self._session.add(app_or_group)

    def get_config(self, app_or_group: App | AppGroup, config_property_name: str, default: Any | None = None) -> Any:
        """Read this plugin's configuration on an app or group.

        Args:
            app_or_group: The app or group carrying the configuration.
            config_property_name: The configuration property to read.
            default: Returned when the property is unset.

        Returns:
            The stored value, or ``default``.
        """
        return self._data(app_or_group).configuration.get(config_property_name, default)

    def set_config(self, app_or_group: App | AppGroup, config_property_name: str, value: Any) -> None:
        """Write this plugin's configuration on an app or group.

        For configuration a reconcile infers from the external system and backfills. Persisted by
        the host's post-hook commit, and discarded if the hook raises.

        Args:
            app_or_group: The app or group to write the configuration to.
            config_property_name: The configuration property to write.
            value: The value to store. Must be JSON-serializable.
        """
        data = self._data(app_or_group)
        data.configuration[config_property_name] = value
        self._store(app_or_group, data)

    def get_status(self, app_or_group: App | AppGroup, status_property_name: str, default: Any | None = None) -> Any:
        """Read this plugin's status on an app or group.

        Args:
            app_or_group: The app or group carrying the status.
            status_property_name: The status property to read.
            default: Returned when the property is unset.

        Returns:
            The stored value, or ``default``.
        """
        return self._data(app_or_group).status.get(status_property_name, default)

    def set_status(
        self,
        app_or_group: App | AppGroup,
        status_property_name: str,
        value: Any,
        *,
        durable_on_failure: bool = False,
    ) -> None:
        """Write this plugin's status on an app or group. Persisted by the host's post-hook commit.

        Args:
            app_or_group: The app or group to write the status to.
            status_property_name: The status property to write.
            value: The value to store. Must be JSON-serializable.
            durable_on_failure: Whether the write must outlive a failure of this hook.

                Pass ``True`` for **diagnostic** status: a sync status, an error string, a
                last-synced timestamp. The host re-applies those writes in a fresh transaction
                after rolling back a failed hook, so an operator can still see why reconciliation
                failed instead of a group stuck with no explanation.

                Leave it ``False`` (the default) for anything that is an **ownership or identity
                token**: an external group id, a mapping id. Those are only sound when committed in
                the same transaction as the check that justified them -- typically under
                ``lock()`` -- and replaying one in a fresh transaction after the lock has released
                drops that guarantee, letting two Access groups claim the same external group.
        """
        data = self._data(app_or_group)
        data.status[status_property_name] = value
        self._store(app_or_group, data)
        if durable_on_failure:
            entity_id = getattr(app_or_group, "id", None)
            if entity_id is not None:
                self._status_writes.append(
                    _StatusWrite(
                        entity_type="app" if isinstance(app_or_group, App) else "group",
                        entity_id=entity_id,
                        property_name=status_property_name,
                        value=value,
                    )
                )

    async def _reapply_durable_status(self, hook_method: "AppGroupLifecycleHook", *, context: str) -> None:
        """Host-only. Re-apply the status writes a failed hook marked ``durable_on_failure``, in a
        fresh transaction, so an operator can still see why reconciliation failed.

        The rollback that precedes this call has expired every instance the plugin *modified* --
        exactly the set of rows a status write targets. A column read on one of them raises
        MissingGreenlet and a relationship read raises InvalidRequestError, so the recorded writes
        carry only the entity type, its id, and a plain value, and each target row is re-loaded
        here. The nested rollback spares instances the plugin never touched, but that does not help
        these targets, and the fallback branch for a hook that commits rolls the whole session back
        regardless.

        `select(...).execution_options(populate_existing=True)` rather than `session.get`: `get`
        *refreshes* the expired identity-map instance and raises ObjectDeletedError if the row is
        gone, where a select simply yields no rows and we skip that target. It also leaves the
        caller's instance usable again, which a bare rollback does not.

        Best-effort: a failure here is logged and swallowed. The surrounding operation has already
        committed its own work by the time the hook fires, and a plugin's diagnostic status must
        never be the thing that breaks a request.

        Args:
            hook_method: The hook that failed. ``GROUP_DELETED`` skips the re-apply entirely -- see
                         below.
            context: A description of the hook invocation, for log messages.
        """
        # group_deleted fires while the row is on its way out (ModifyGroupType fires it *before*
        # deleting the app_group row, DeleteGroup before the soft delete). Nothing will read that
        # group's status again, and committing here would land a commit in the middle of the
        # caller's half-finished operation.
        if hook_method == AppGroupLifecycleHook.GROUP_DELETED or not self._status_writes:
            return

        # Group by target so each row loads once and the last write to a property wins; a single
        # reconcile can mark the same status more than once.
        by_target: dict[tuple[str, str], dict[str, Any]] = {}
        for write in self._status_writes:
            by_target.setdefault((write.entity_type, write.entity_id), {})[write.property_name] = write.value

        try:
            for (entity_type, entity_id), properties in by_target.items():
                model: type[App] | type[AppGroup] = App if entity_type == "app" else AppGroup
                target = (
                    await self._session.scalars(
                        select(model).where(model.id == entity_id).execution_options(populate_existing=True)
                    )
                ).first()
                if target is None:
                    logger.info(f"{context}: skipping durable status re-apply, {entity_type} {entity_id} is gone")
                    continue
                data = self._data(target)
                data.status.update(properties)
                self._store(target, data)
                self._session.add(target)
            await self._session.commit()
        except Exception:
            logging.getLogger("api").exception(f"{context}: failed to re-apply durable plugin status")
            try:
                await self._session.rollback()
            except Exception:
                logging.getLogger("api").exception(f"{context}: rollback after failed status re-apply also failed")

    # ---- Group mutation ----

    async def set_group_description(self, group: AppGroup, description: str) -> None:
        """Set an Access group's description, syncing it to Okta.

        For a plugin adopting a description from the external system it mirrors. Goes through the
        same operation the API uses, so the ORM update and the Okta push stay consistent, but with
        both of that operation's re-entrancy hazards disabled: it does not re-fire the lifecycle
        hooks (which would recurse), and it does not commit (the host commits after the hook; a
        commit here would release any advisory lock this hook holds).

        The Okta push is not rolled back with the surrounding transaction, so a hook that fails
        after this call leaves Okta briefly ahead of Access. Reconciliation is expected to be
        idempotent and re-enforce on the next pass; the alternative -- committing here -- breaks
        the lock.

        Args:
            group: The app group to describe.
            description: The description to set, replacing whatever is there.
        """
        # Deferred: api.operations imports api.plugins, so a module-level import would cycle.
        from api.operations import ModifyGroupDetails

        await ModifyGroupDetails(
            group=group,
            description=description,
            fire_lifecycle_hook=False,
            commit_db_changes=False,
        ).execute()

    # ---- Okta group push ----
    #
    # Thin delegates so a plugin never imports `api.services.okta`. They do Okta network I/O only and
    # never touch the session.

    async def create_push_mapping_and_new_group(self, group: AppGroup, okta_app_id: str, target_group_name: str) -> str:
        """Create a push mapping with a new target group name, so Okta creates both its target
        group and the downstream external group and links them in one step.

        Args:
            group: The Access group to push from.
            okta_app_id: The Okta app to create the mapping in.
            target_group_name: The name Okta gives the target group it creates.

        Returns:
            The id of the push mapping created.

        Raises:
            Exception: If Okta accepts the request but returns no mapping id.
        """
        result = await okta.create_group_push_mapping(
            appId=okta_app_id, sourceGroupId=group.id, targetGroupName=target_group_name
        )
        return self._mapping_id(result)

    async def create_push_mapping_for_existing_group(
        self, group: AppGroup, okta_app_id: str, external_id_field_name: str, external_id: str
    ) -> str:
        """Link an Access group to an already-imported Okta target group -- the adoption path.

        Args:
            group: The Access group to push from.
            okta_app_id: The Okta app to create the mapping in.
            external_id_field_name: The Okta target group profile attribute carrying the external
                                    system's id.
            external_id: The external id identifying the target group to link to.

        Returns:
            The id of the push mapping created.

        Raises:
            MissingOktaTargetError: If Okta has not imported the target group yet. Defer and retry.
            AmbiguousOktaTargetError: If more than one target group matches; a misconfiguration
                that will not self-heal.
            Exception: If Okta accepts the request but returns no mapping id.
        """
        target_group_id = await self._okta_target_group_id(external_id_field_name, external_id)
        if not target_group_id:
            raise MissingOktaTargetError(
                f"Could not find a target group with {external_id_field_name} of '{external_id}' in Okta. "
                "This may require manual action to import external app groups to Okta."
            )
        result = await okta.create_group_push_mapping(
            appId=okta_app_id, sourceGroupId=group.id, targetGroupId=target_group_id
        )
        return self._mapping_id(result)

    async def discover_existing_push_mapping_and_target_group_external_id(
        self, group: AppGroup, okta_app_id: str, target_group_external_id_field: str
    ) -> tuple[str, str] | None:
        """Find this group's existing push mapping and recover the external id from the Okta target
        group's profile.

        Args:
            group: The Access group whose mapping to look up.
            okta_app_id: The Okta app the mapping lives in.
            target_group_external_id_field: The Okta target group profile attribute carrying the
                                            external system's id.

        Returns:
            ``(push_mapping_id, external_id)``, or None if the group is not linked.

        Raises:
            Exception: If the mapping exists but has no id or no target group id.
            ValueError: If the target group's profile does not carry the external id field.
        """
        mappings = await okta.list_group_push_mappings(okta_app_id, sourceGroupId=group.id)
        # Okta allows at most one mapping per app, source, and target.
        mapping = next(iter(mappings), None)
        if not mapping:
            logger.debug(f"No mapping found for group {group.name}.")
            return None

        mapping_id = mapping.get("id")
        if not mapping_id:
            raise Exception(f"Push mapping for {group.name} has no ID. Mapping:\n{mapping}")
        target_group_id = mapping.get("targetGroupId")
        if not target_group_id:
            raise Exception(f"Push mapping for {group.name} has no target group ID. Mapping:\n{mapping}")

        # Custom Okta attributes live in the profile union's actual_instance.additional_properties;
        # both the profile and its actual_instance are Optional on the SDK model, so guard before
        # dereferencing them.
        profile = (await okta.get_group(target_group_id)).group.profile
        actual_instance = profile.actual_instance if profile is not None else None
        external_id = (
            (actual_instance.additional_properties or {}).get(target_group_external_id_field)
            if actual_instance is not None
            else None
        )
        if not external_id:
            raise ValueError(
                f"ID '{target_group_external_id_field}' could not be resolved for target group mapped to "
                f"{group.name}.\nTarget group {target_group_id} has profile:\n{profile}"
            )
        return mapping_id, external_id

    async def delete_push_mapping(self, okta_app_id: str, mapping_id: str, delete_target_group: bool = False) -> None:
        """Delete (unlink) a push mapping.

        Args:
            okta_app_id: The Okta app the mapping lives in.
            mapping_id: The push mapping to delete.
            delete_target_group: When True, Okta also deletes the downstream target group it
                                 created. When False, only the mapping goes and the target group is
                                 left in place.
        """
        await okta.delete_group_push_mapping(
            appId=okta_app_id, mappingId=mapping_id, deleteTargetGroup=delete_target_group
        )

    @staticmethod
    def _mapping_id(result: dict[str, Any]) -> str:
        """The mapping id out of an Okta push-mapping creation response.

        Args:
            result: The response body Okta returned.

        Returns:
            The push mapping id.

        Raises:
            Exception: If Okta accepted the request but returned no id, which would otherwise be
                recorded as a mapping that cannot be looked up or deleted.
        """
        mapping_id = result.get("id")
        if not mapping_id:
            raise Exception(f"Okta push mapping creation returned no id: {result}")
        return mapping_id

    @staticmethod
    async def _okta_target_group_id(external_id_profile_field_name: str, external_id: str) -> str | None:
        """Resolve an external id to the Okta target group whose profile records it.

        Args:
            external_id_profile_field_name: The Okta group profile attribute carrying the external
                                            system's id.
            external_id: The external id to resolve.

        Returns:
            The Okta group id, or None if Okta has not imported the group yet.

        Raises:
            AmbiguousOktaTargetError: On more than one match. That is a misconfiguration (e.g. a
                stale plus a re-imported target sharing an external id) which will not self-heal,
                so it must not be conflated with the not-yet-imported case that simply defers.
        """
        search = f'type eq "APP_GROUP" and profile.{external_id_profile_field_name} eq "{external_id}"'
        matches = await okta.list_groups(query_params={"search": search})
        if len(matches) > 1:
            raise AmbiguousOktaTargetError(
                f"Found {len(matches)} Okta groups with {external_id_profile_field_name} of '{external_id}'; "
                "expected at most one. Resolve the duplicate imports in Okta."
            )
        if not matches:
            return None
        return matches[0].group.id


class AppGroupLifecyclePluginSpec:
    """Plugin specification for managing app group lifecycles."""

    @hookspec
    def get_plugin_metadata(self) -> AppGroupLifecyclePluginMetadata | None:
        """Identify this plugin implementation.

        Returns:
            This plugin's metadata. Its id, display name, and description must each be unique
            across the loaded plugins.
        """

    # Configuration hooks

    @hookspec
    def get_plugin_app_config_properties(
        self, plugin_id: str | None
    ) -> dict[str, AppGroupLifecyclePluginConfigProperty] | None:
        """
        Declare the schema for app-level configuration plugin data.

        Args:
            plugin_id: If provided, only the plugin matching this ID should respond.
                       If None, all plugins may respond.

        Returns:
            A mapping of configuration property IDs to descriptors, or None to decline.
        """

    @hookspec
    def validate_plugin_app_config(self, config: dict[str, Any], plugin_id: str | None) -> dict[str, str] | None:
        """
        Validate app plugin config before saving.

        Args:
            config: The configuration to validate.
            plugin_id: If provided, only the plugin matching this ID should respond.
                       If None, all plugins may respond.

        Returns:
            A dictionary mapping any invalid fields to error messages, or an empty dictionary if the configuration is valid.
        """

    @hookspec
    def get_plugin_group_config_properties(
        self, plugin_id: str | None, app_config: dict[str, Any]
    ) -> dict[str, AppGroupLifecyclePluginConfigProperty] | None:
        """
        Declare the schema for app-group-level configuration plugin data.

        Args:
            plugin_id: If provided, only the plugin matching this ID should respond.
                       If None, all plugins may respond.
            app_config: The app-level configuration for this plugin, so the returned schema
                        can reflect app-specific constraints (e.g. validation patterns).
                        Empty when no app context is supplied. An implementation that
                        doesn't need it may omit this parameter from its signature; pluggy
                        only passes the arguments the implementation declares.

        Returns:
            A mapping of configuration property IDs to descriptors, or None to decline.
        """

    @hookspec
    def validate_plugin_group_config(
        self, config: dict[str, Any], app_config: dict[str, Any], plugin_id: str | None
    ) -> dict[str, str] | None:
        """
        Validate app group plugin config before saving.

        Args:
            config: The group configuration to validate.
            app_config: The app-level configuration for this plugin, for validating the
                        group config against app-level settings (e.g. an allowed pattern).
                        An implementation that doesn't need it may omit this parameter from
                        its signature; pluggy only passes the arguments the implementation
                        declares.
            plugin_id: If provided, only the plugin matching this ID should respond.
                       If None, all plugins may respond.

        Returns:
            A dictionary mapping any invalid fields to error messages.
        """

    # Status hooks

    @hookspec
    def get_plugin_app_status_properties(
        self, plugin_id: str | None
    ) -> dict[str, AppGroupLifecyclePluginStatusProperty] | None:
        """
        Declare the schema for app-level status plugin data.

        Args:
            plugin_id: If provided, only the plugin matching this ID should respond.
                       If None, all plugins may respond.

        Returns:
            A mapping of status property IDs to descriptors, or None to decline.
        """

    @hookspec
    def get_plugin_group_status_properties(
        self, plugin_id: str | None
    ) -> dict[str, AppGroupLifecyclePluginStatusProperty] | None:
        """
        Declare the schema for group-level status plugin data.

        Args:
            plugin_id: If provided, only the plugin matching this ID should respond.
                       If None, all plugins may respond.

        Returns:
            A mapping of status property IDs to descriptors, or None to decline.
        """

    # Group lifecycle hooks

    @hookspec
    async def group_created(self, ctx: AppGroupLifecycleContext, group: AppGroup, plugin_id: str | None) -> None:
        """
        Handle group creation.

        Args:
            ctx: The plugin capability context, bound to this plugin's id. Every Access
                 interaction goes through it -- locks, lookups, configuration and status
                 writes, Okta group push. Mutations are only persisted through `ctx`, and a
                 hook must not commit or roll back: the host owns the transaction.
            group: The app group that was created.
            plugin_id: If provided, only the plugin matching this ID should respond.
                       If None, all plugins may respond.
        """

    @hookspec
    async def group_updated(
        self,
        ctx: AppGroupLifecycleContext,
        group: AppGroup,
        old_name: str,
        old_description: str,
        plugin_id: str | None,
    ) -> None:
        """
        Handle group update (name or description change).

        Args:
            ctx: The plugin capability context, bound to this plugin's id. Every Access
                 interaction goes through it -- locks, lookups, configuration and status
                 writes, Okta group push. Mutations are only persisted through `ctx`, and a
                 hook must not commit or roll back: the host owns the transaction.
            group: The app group after the update.
            old_name: The group's name before the update.
            old_description: The group's description before the update.
            plugin_id: If provided, only the plugin matching this ID should respond.
                       If None, all plugins may respond.
        """

    @hookspec
    async def group_deleted(
        self,
        ctx: AppGroupLifecycleContext,
        group: AppGroup,
        members: list[OktaUser],
        plugin_id: str | None,
    ) -> None:
        """
        Handle group deletion.

        Args:
            ctx: The plugin capability context, bound to this plugin's id. Every Access
                 interaction goes through it -- locks, lookups, configuration and status
                 writes, Okta group push. Mutations are only persisted through `ctx`, and a
                 hook must not commit or roll back: the host owns the transaction.
            group: The app group that was deleted.
            members: The users who held membership when the group stopped being this plugin's to
                     manage, captured before the deletion ended those memberships. Passed rather
                     than looked up because by the time this fires `ctx.list_group_members(group)`
                     is empty on the delete path -- and, worse, is *not* empty on the type-change
                     path, so a hook reading it would behave differently depending on why the
                     group went away. Where the external system attaches one combined permission
                     set to a user rather than one per group, recompute each member's union over
                     `ctx.find_user_groups(user)` and write the reduced set, rather than deleting
                     the user outright.
            plugin_id: If provided, only the plugin matching this ID should respond.
                       If None, all plugins may respond.
        """

    # Membership hooks

    @hookspec
    async def group_members_added(
        self,
        ctx: AppGroupLifecycleContext,
        group: AppGroup,
        members: list[OktaUser],
        plugin_id: str | None,
    ) -> None:
        """
        Handle member addition.

        Args:
            ctx: The plugin capability context, bound to this plugin's id. Every Access
                 interaction goes through it -- locks, lookups, configuration and status
                 writes, Okta group push. Mutations are only persisted through `ctx`, and a
                 hook must not commit or roll back: the host owns the transaction.
            group: The app group to which members were added.
            members: The list of users that were added to the group.
            plugin_id: If provided, only the plugin matching this ID should respond.
                       If None, all plugins may respond.
        """

    @hookspec
    async def group_members_removed(
        self,
        ctx: AppGroupLifecycleContext,
        group: AppGroup,
        members: list[OktaUser],
        plugin_id: str | None,
    ) -> None:
        """
        Handle member removal.

        Args:
            ctx: The plugin capability context, bound to this plugin's id. Every Access
                 interaction goes through it -- locks, lookups, configuration and status
                 writes, Okta group push. Mutations are only persisted through `ctx`, and a
                 hook must not commit or roll back: the host owns the transaction.
            group: The app group from which members were removed.
            members: The list of users that were removed from the group. This is the delta, not
                     the membership that remains. Where the external system attaches one combined
                     permission set per user rather than one per group, recompute the union over
                     `ctx.find_user_groups(user)` and write the reduced set, rather than revoking
                     this group's grants outright -- the user may still hold them via another
                     group.
            plugin_id: If provided, only the plugin matching this ID should respond.
                       If None, all plugins may respond.
        """

    @hookspec
    async def sync_group(self, ctx: AppGroupLifecycleContext, group: AppGroup, plugin_id: str | None) -> None:
        """
        Reconcile one app group (membership and any external group state).

        Invoked by the `access sync-app-groups` CLI command once per active app group of every app
        with this plugin configured, each in its own transaction -- so one group's failure cannot
        strand the groups behind it, and a plugin does not implement its own batch loop or error
        isolation. Let the exception propagate to report a failure; the CLI counts it and exits
        non-zero.

        Args:
            ctx: The plugin capability context, bound to this plugin's id. Every Access
                 interaction goes through it -- locks, lookups, configuration and status
                 writes, Okta group push. Mutations are only persisted through `ctx`, and a
                 hook must not commit or roll back: the host owns the transaction.
                 `ctx.list_group_members(group)` is the group's current membership.
            group: The app group to reconcile. `group.app` is eager-loaded; every other
                   relationship is `lazy="raise_on_sql"` and must be reached through `ctx`
                   rather than read off `group`, which raises.
            plugin_id: If provided, only the plugin matching this ID should respond.
                       If None, all plugins may respond.

        A hook needing an object graph the context cannot reach should have the caller
        (`_sync_all_app_groups` in `api/cli.py`) eager-load it, the way request-path operations do
        for the notification hooks.
        """


def get_app_group_lifecycle_hook() -> pluggy.HookRelay:
    """Get the hook relay for app group lifecycle plugins.

    Loads the registered plugins on first call and caches the relay.

    Returns:
        The pluggy hook relay to call hooks through.

    Raises:
        RuntimeError: If a plugin registers a synchronous implementation of a hook the
            application awaits (see ``verify_async_impls``).
    """
    global _cached_app_group_lifecycle_hook

    if _cached_app_group_lifecycle_hook is not None:
        return _cached_app_group_lifecycle_hook

    pm = pluggy.PluginManager(app_group_lifecycle_plugin_name)
    pm.add_hookspecs(AppGroupLifecyclePluginSpec)

    count = pm.load_setuptools_entrypoints(app_group_lifecycle_plugin_name)
    logger.info(f"Loaded {count} app group lifecycle plugin(s)")
    verify_async_impls(pm, tuple(AppGroupLifecycleHook))

    _cached_app_group_lifecycle_hook = pm.hook

    return _cached_app_group_lifecycle_hook


def get_app_group_lifecycle_plugins() -> list[AppGroupLifecyclePluginMetadata]:
    """
    Get a registry of all loaded app group lifecycle plugins with their metadata.

    Returns:
        A list of plugin metadata objects.
    """
    global _cached_plugin_registry

    if _cached_plugin_registry is not None:
        return _cached_plugin_registry

    hook = get_app_group_lifecycle_hook()

    # Collect metadata from all registered plugins
    plugins: list[AppGroupLifecyclePluginMetadata] = [
        plugin for plugin in hook.get_plugin_metadata() if plugin is not None
    ]

    # Validate uniqueness
    seen_ids: set[str] = set()
    seen_display_names: set[str] = set()
    seen_descriptions: set[str] = set()

    for plugin in plugins:
        if not plugin.id:
            raise ValueError("Plugin ID is required")
        if not plugin.display_name:
            raise ValueError(f"Display name is required but missing for plugin {plugin.id}")
        if not plugin.description:
            raise ValueError(f"Description is required but missing for plugin {plugin.id}")

        if plugin.id in seen_ids:
            raise ValueError(f"Duplicate plugin ID detected: {plugin.id}")
        if plugin.display_name in seen_display_names:
            raise ValueError(f"Duplicate plugin display name detected: {plugin.display_name}")
        if plugin.description in seen_descriptions:
            raise ValueError(f"Duplicate plugin description detected: {plugin.description}")

        seen_ids.add(plugin.id)
        seen_display_names.add(plugin.display_name)
        seen_descriptions.add(plugin.description)

    _cached_plugin_registry = plugins
    logger.info(f"Registered {len(plugins)} app group lifecycle plugin(s): {[plugin.id for plugin in plugins]}")

    return _cached_plugin_registry


def get_app_group_lifecycle_plugin_to_invoke(group: Any) -> str | None:
    """
    Determine the ID of the app group lifecycle plugin to invoke for a given group, if any.

    Args:
        group: The app group for which to determine the app group lifecycle plugin to invoke.

    Returns:
        The ID of the app group lifecycle plugin to invoke, or None if no plugin is configured.
    """
    if type(group) is not AppGroup or group.app.app_group_lifecycle_plugin is None:
        return None

    return group.app.app_group_lifecycle_plugin


async def invoke_app_group_lifecycle_hook(
    hook_method: AppGroupLifecycleHook, *, group: Any, **kwargs: Any
) -> list[BaseException]:
    """Invoke an app-group lifecycle hook for ``group``, if a plugin is configured.

    No-op when no lifecycle plugin applies to ``group``. Owns the whole transaction policy for
    lifecycle hooks: it builds the ``AppGroupLifecycleContext`` the hook works through, commits on
    success, and on failure rolls back and then re-applies the status writes the plugin marked
    ``durable_on_failure`` (see ``AppGroupLifecycleContext.set_status``).

    The hook runs inside a **SAVEPOINT**, so a plugin failure discards the plugin's writes without
    expiring the caller's ORM state. Callers may therefore keep reading their own instances across a
    fire. The one exception is a hook that commits -- which this interface forbids -- since that
    ends the savepoint and forces the fallback to a full session rollback; see the rollback branch
    below.

    Args:
        hook_method: The lifecycle hook to fire.
        group: The group the hook is about. Its lifecycle plugin, read from ``group.app``, decides
               which plugin is invoked and what the context is bound to.
        **kwargs: Forwarded to the hook alongside ``ctx`` and ``group`` -- ``members=`` for the
                  membership hooks, ``old_name=``/``old_description=`` for ``group_updated``.

    Returns:
        The exceptions the hook implementations raised, empty on success, so a batch caller can
        count failures. Never propagates, so a misbehaving plugin can't abort the surrounding
        operation.
    """
    plugin_id = get_app_group_lifecycle_plugin_to_invoke(group)
    if plugin_id is None:
        return []
    hook = get_app_group_lifecycle_hook()
    ctx = AppGroupLifecycleContext(session=db.session, plugin_id=plugin_id)
    context = f"{hook_method} hook for group {getattr(group, 'id', None)} (plugin '{plugin_id}')"
    # Run the hook inside a SAVEPOINT so a plugin failure rolls back the plugin's writes and
    # nothing else. A *top-level* rollback passes dirty_only=False to
    # SessionTransaction._restore_snapshot, expiring every instance in the identity map;
    # `expire_on_commit=False` does not apply. On an AsyncSession the caller's next attribute
    # read -- including a primary key, which refreshes rather than returning the identity key --
    # then needs sync IO and raises MissingGreenlet, which takes down the operation that fired the
    # hook. A *nested* rollback passes dirty_only=True and expires only what the plugin itself
    # modified, so the caller's objects stay usable.
    #
    # begin_nested() flushes before emitting the SAVEPOINT: _take_snapshot() calls
    # session.flush() for any origin that is not BEGIN/AUTOBEGIN. That flush is what makes this
    # safe rather than merely narrower; the caller's pending state lands in the *outer*
    # transaction, ahead of the savepoint, and having been flushed is no longer `modified`, so
    # the dirty-only expiry skips it. It also means the flush can raise on the caller's own
    # pending state, which must not escape a function documented never to propagate.
    try:
        savepoint = await db.session.begin_nested()
    except Exception as e:
        logging.getLogger("api").exception(f"Failed to open savepoint before {context}")
        return [e]
    # run_hooks_to_completion uses asyncio.wait (not gather): a cancelled request
    # won't tear down an in-flight hook, and one plugin failing won't cancel the
    # others. Failures are logged there; we roll back rather than commit partial
    # writes, but never propagate, so a misbehaving plugin can't abort the
    # surrounding operation.
    _, exceptions = await run_hooks_to_completion(
        getattr(hook, hook_method)(ctx=ctx, group=group, plugin_id=plugin_id, **kwargs),
        context=context,
    )
    if exceptions:
        if savepoint.is_active:
            await savepoint.rollback()
        else:
            # A hook that committed ended the savepoint itself, so there is nothing to roll back
            # to; fall back to a session rollback rather than silently skipping it. That path
            # cascades the expiry described above to the caller's instances, and is reachable only
            # from a plugin that commits, which this interface forbids.
            await db.session.rollback()
        await ctx._reapply_durable_status(hook_method, context=context)
        # Returned, not raised: _sync_all_app_groups in api/cli.py counts these to set the
        # CLI's exit status. Most callers discard the value; do not "simplify" it back to
        # None on that basis.
        return exceptions
    try:
        if savepoint.is_active:
            await savepoint.commit()
        await db.session.commit()
    except Exception as e:
        logging.getLogger("api").exception(f"Failed to commit after {context}")
        await db.session.rollback()
        await ctx._reapply_durable_status(hook_method, context=context)
        return [e]
    return []


def _get_data_for_plugin(plugin_data: dict[str, Any], plugin_id: str) -> AppGroupLifecyclePluginData:
    """
    Get the data for a particular app group lifecycle plugin.

    Args:
        plugin_data: The app or group's raw plugin_data property.
        plugin_id: The ID of the plugin which should respond.

    Returns:
        The data for the plugin.
    """
    this_plugin_data = plugin_data.get(plugin_id, {})
    if not isinstance(this_plugin_data, dict):
        raise ValueError(f"The data for app group lifecycle plugin '{plugin_id}' must be a dictionary")

    configuration = this_plugin_data.get("configuration", {})
    if not isinstance(configuration, dict):
        raise ValueError(
            f"The configuration property in the data for app group lifecycle plugin '{plugin_id}' must be a dictionary"
        )

    status = this_plugin_data.get("status", {})
    if not isinstance(status, dict):
        raise ValueError(
            f"The status property in the data for app group lifecycle plugin '{plugin_id}' must be a dictionary"
        )

    return AppGroupLifecyclePluginData(configuration, status)


def is_plugin_config_changed(old_plugin_data: dict[str, Any], new_plugin_data: dict[str, Any], plugin_id: str) -> bool:
    """
    Determine whether a particular app group lifecycle plugin's configuration differs
    between two plugin_data payloads. Only the plugin's configuration is compared; status
    differences are ignored (plugins write their own status, and treating those writes as
    a change would re-trigger configuration-driven reconciliation in a loop).

    Args:
        old_plugin_data: The existing plugin_data.
        new_plugin_data: The candidate plugin_data.
        plugin_id: The ID of the plugin whose configuration to compare.

    Returns:
        True if the plugin's configuration differs between the two payloads.
    """
    old_config = _get_data_for_plugin(old_plugin_data, plugin_id).configuration
    new_config = _get_data_for_plugin(new_plugin_data, plugin_id).configuration
    return old_config != new_config


def merge_app_lifecycle_plugin_data(app_or_group: App | AppGroup, old_plugin_data: dict[str, Any]) -> None:
    """
    Update the app lifecycle plugin data on the new app or group object by merging with the plugin data from the existing object.

    Args:
        app_or_group: The existing app or group for which to update the plugin data.
        old_plugin_data: The plugin data of the existing app or group object, which may be a partial patch.
    """
    app_group_lifecycle_plugin_ids = [plugin.id for plugin in get_app_group_lifecycle_plugins()]
    for plugin_id in old_plugin_data:
        if plugin_id in app_group_lifecycle_plugin_ids:
            data = _get_data_for_plugin(old_plugin_data, plugin_id)
            patch_data = _get_data_for_plugin(app_or_group.plugin_data, plugin_id)
            data.configuration.update(patch_data.configuration)
            data.status.update(patch_data.status)
            app_or_group.plugin_data[plugin_id] = asdict(obj=data)


class AppGroupLifecyclePluginFilteringError(Exception):
    """Exception raised when no or multiple app group lifecycle plugins respond to a hook call."""

    def __init__(self, plugin_id: str, response_count: int):
        self.plugin_id = plugin_id
        self.response_count = response_count
        super().__init__(f"Expected one response for plugin '{plugin_id}' but got {response_count}")


def _get_hook_call_response(hook_caller: pluggy.HookCaller, plugin_id: str, **args: dict[str, Any]) -> Any:
    """
    Get a response from a particular app group lifecycle plugin.

    Args:
        hook_caller: The hook caller to use.
        plugin_id: The ID of the plugin which should respond.
        **args: Additional arguments to pass to the hook caller.

    Returns:
        The singular plugin response.
    """
    responses = [response for response in hook_caller(plugin_id=plugin_id, **args) if response is not None]
    if len(responses) != 1:
        raise AppGroupLifecyclePluginFilteringError(plugin_id, len(responses))
    return responses[0]


def get_app_group_lifecycle_plugin_app_config_properties(
    plugin_id: str,
) -> dict[str, AppGroupLifecyclePluginConfigProperty]:
    """
    Get the app-level configuration properties for a particular app group lifecycle plugin.

    Args:
        plugin_id: The ID of the plugin which should respond.

    Returns:
        A dictionary mapping configuration property names to schemas.
    """
    hook = get_app_group_lifecycle_hook()
    return _get_hook_call_response(hook.get_plugin_app_config_properties, plugin_id)


def get_app_group_lifecycle_plugin_group_config_properties(
    plugin_id: str, app_plugin_data: dict[str, Any] | None = None
) -> dict[str, AppGroupLifecyclePluginConfigProperty]:
    """
    Get the group-level configuration properties for a particular app group lifecycle plugin.

    Args:
        plugin_id: The ID of the plugin which should respond.
        app_plugin_data: The owning app's plugin data, so the schema can reflect app-level
                         settings (e.g. validation patterns). Defaults to empty (no app context).

    Returns:
        A dictionary mapping configuration property names to schemas.
    """
    app_configuration = _get_data_for_plugin(app_plugin_data or {}, plugin_id).configuration
    hook = get_app_group_lifecycle_hook()
    return _get_hook_call_response(hook.get_plugin_group_config_properties, plugin_id, app_config=app_configuration)


def validate_app_group_lifecycle_plugin_app_config(plugin_data: dict[str, Any], plugin_id: str) -> dict[str, str]:
    """
    Validate the app-level configuration data for a particular app group lifecycle plugin.

    Args:
        plugin_data: The plugin data to validate.
        plugin_id: The ID of the plugin which should respond.

    Returns:
        A dictionary mapping any invalid fields to error messages.
    """
    configuration = _get_data_for_plugin(plugin_data, plugin_id).configuration
    hook = get_app_group_lifecycle_hook()
    return _get_hook_call_response(hook.validate_plugin_app_config, plugin_id, config=configuration)


def validate_app_group_lifecycle_plugin_group_config(
    plugin_data: dict[str, Any],
    plugin_id: str,
    app_plugin_data: dict[str, Any] | None = None,
    old_plugin_data: dict[str, Any] | None = None,
) -> dict[str, str]:
    """
    Validate the group-level configuration data for a particular app group lifecycle plugin.

    Args:
        plugin_data: The group's plugin data property.
        plugin_id: The ID of the plugin which should respond.
        app_plugin_data: The owning app's plugin data, so the plugin can validate the group
                         config against app-level settings. Defaults to empty (no app context).
        old_plugin_data: The group's existing plugin data, supplied on update so the host can
                         reject edits to immutable config properties. Omitted on create.

    Returns:
        A dictionary mapping any invalid fields to error messages.
    """
    configuration = _get_data_for_plugin(plugin_data, plugin_id).configuration
    app_configuration = _get_data_for_plugin(app_plugin_data or {}, plugin_id).configuration
    hook = get_app_group_lifecycle_hook()
    errors = dict(
        _get_hook_call_response(
            hook.validate_plugin_group_config, plugin_id, config=configuration, app_config=app_configuration
        )
    )

    # On update, apply immutable-field semantics generically (plugins only declare
    # `immutable=True`): a changed immutable field is rejected, while a plugin error for an
    # *unchanged* immutable field is suppressed -- the user can't action it on this update
    # (the field is locked), and it was acceptable at creation or was adopted/grandfathered
    # from external state, so it must not block the rest of the update.
    if old_plugin_data is not None:
        old_configuration = _get_data_for_plugin(old_plugin_data, plugin_id).configuration
        properties = _get_hook_call_response(
            hook.get_plugin_group_config_properties, plugin_id, app_config=app_configuration
        )
        for name, prop in properties.items():
            if not prop.immutable:
                continue
            # Only treat an immutable field as edited when it's actually present in the
            # (possibly partial) patch: an omission isn't a change, while an explicit value
            # -- even null -- still is.
            if name in configuration and old_configuration.get(name) != configuration.get(name):
                errors.setdefault(name, f"The '{name}' field cannot be changed after creation")
            else:
                errors.pop(name, None)

    return errors


def raise_http_for_plugin_filtering_error(error: AppGroupLifecyclePluginFilteringError) -> NoReturn:
    """Map a hook-filtering failure onto the right HTTP status. Never returns.

    `AppGroupLifecyclePluginFilteringError` covers two very different situations, and the
    caller cannot be told the same thing about both:

    - the id names no registered plugin, which is bad client input or stale app config
      (e.g. an operator dropped a plugin while apps still referenced its id) -> **400**;
    - the id *is* registered but its hook did not answer with exactly one response, which
      is a server-side misconfiguration -> **500**, an explicit one naming the plugin
      rather than letting the plain Exception become an unhandled stack trace.

    Use this from any router that can see this error, whether raised by a validation
    helper below or propagated out of an operation.

    Args:
        error: The filtering failure to map.

    Raises:
        HTTPException: Always -- 400 for an unknown plugin id, 500 for a registered plugin whose
            hook did not answer with exactly one response.
    """
    if error.plugin_id not in [plugin.id for plugin in get_app_group_lifecycle_plugins()]:
        raise HTTPException(400, f"The plugin {error.plugin_id} is not known")
    raise HTTPException(500, f"Misconfigured app group lifecycle plugin '{error.plugin_id}': {error}") from error


def validate_group_plugin_config_or_raise(
    plugin_data: dict[str, Any],
    app_plugin_data: dict[str, Any],
    plugin_id: str | None,
    old_plugin_data: dict[str, Any] | None = None,
) -> None:
    """Validate group-level plugin_data against the configured plugin's schema, raising
    an HTTP error on invalid config. No-op when the app has no app group lifecycle plugin.

    Shared by every router that accepts group plugin config -- group create/update and
    app-group requests -- so they answer identically for the same bad input. Callers
    resolve the plugin id and the owning app's plugin_data themselves, since each obtains
    them differently (a freshly-built group can't lazy-load its app, and a group request
    has no group yet). Callers editing an existing group also pass its current
    plugin_data as `old_plugin_data` so the host can reject changes to immutable config
    fields; omit it when the group does not exist yet.

    Args:
        plugin_data: The group-level plugin_data to validate.
        app_plugin_data: The owning app's plugin_data, for validating against app-level settings.
        plugin_id: The app's configured lifecycle plugin, or None for no plugin.
        old_plugin_data: The group's current plugin_data, on update, so edits to immutable config
                         properties are rejected. Omit on create.

    Raises:
        HTTPException: 400 if the config is invalid or malformed, or if the plugin id is unknown;
            500 if a registered plugin's validation hook misbehaves.
    """
    if plugin_id is None:
        return

    try:
        errors = validate_app_group_lifecycle_plugin_group_config(
            plugin_data, plugin_id, app_plugin_data, old_plugin_data=old_plugin_data
        )
    except ValueError as e:
        raise HTTPException(400, f"plugin_data: {e}") from e
    except AppGroupLifecyclePluginFilteringError as e:
        raise_http_for_plugin_filtering_error(e)
    if errors:
        raise HTTPException(400, f"plugin_data: {errors}")


def validate_app_plugin_config_or_raise(plugin_data: dict[str, Any], plugin_id: str | None) -> None:
    """Validate app-level plugin_data against the configured plugin's schema, raising an
    HTTP error on invalid config. No-op when no app group lifecycle plugin is configured.

    The app-level counterpart of `validate_group_plugin_config_or_raise`. It takes no
    app-context or `old_plugin_data` arguments: app config has no parent to validate
    against, and immutable app config properties are not a concept the hook exposes.

    Args:
        plugin_data: The app-level plugin_data to validate.
        plugin_id: The app's configured lifecycle plugin, or None for no plugin.

    Raises:
        HTTPException: 400 if the config is invalid or malformed, or if the plugin id is unknown;
            500 if a registered plugin's validation hook misbehaves.
    """
    if plugin_id is None:
        return

    try:
        errors = validate_app_group_lifecycle_plugin_app_config(plugin_data, plugin_id)
    except ValueError as e:
        raise HTTPException(400, f"plugin_data: {e}") from e
    except AppGroupLifecyclePluginFilteringError as e:
        raise_http_for_plugin_filtering_error(e)
    if errors:
        raise HTTPException(400, f"plugin_data: {errors}")


def get_app_group_lifecycle_plugin_app_status_properties(
    plugin_id: str,
) -> dict[str, AppGroupLifecyclePluginStatusProperty]:
    """
    Get the app-level status properties for a particular app group lifecycle plugin.

    Args:
        plugin_id: The ID of the plugin which should respond.

    Returns:
        A dictionary mapping status property names to schemas.
    """
    hook = get_app_group_lifecycle_hook()
    return _get_hook_call_response(hook.get_plugin_app_status_properties, plugin_id)


def get_app_group_lifecycle_plugin_group_status_properties(
    plugin_id: str,
) -> dict[str, AppGroupLifecyclePluginStatusProperty]:
    """
    Get the group-level status properties for a particular app group lifecycle plugin.

    Args:
        plugin_id: The ID of the plugin which should respond.

    Returns:
        A dictionary mapping status property names to schemas.
    """
    hook = get_app_group_lifecycle_hook()
    return _get_hook_call_response(hook.get_plugin_group_status_properties, plugin_id)


# ---- Okta group-push helpers (exposed to app group lifecycle plugins) ----
#
# App group lifecycle plugins that back Access groups with an externally-managed group provider
# (e.g. Google Workspace) link the two through Okta group push. These helpers wrap the Okta group
# push mapping surface so plugins can create, discover, and resolve those links through the plugin
# interface rather than importing the internal `api.services.okta` client directly.
