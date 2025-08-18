from datetime import datetime
from enum import StrEnum
from typing import Any, Callable, Dict, List, Optional

from api import config
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, validates
from sqlalchemy.sql import expression
from sqlalchemy_json import mutable_json_type

from api.extensions import db


class OktaUserGroupMember(db.Model):
    # See https://stackoverflow.com/a/60840921
    id: Mapped[int] = mapped_column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        autoincrement=True,
        primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(db.Unicode(50), db.ForeignKey("okta_user.id"))
    group_id: Mapped[str] = mapped_column(db.Unicode(50), db.ForeignKey("okta_group.id"))
    # If this membership is via a role group map
    # See https://stackoverflow.com/a/60840921
    role_group_map_id: Mapped[Optional[int]] = mapped_column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("role_group_map.id"),
    )
    # Is this user an owner of the group and can administer the group and manage membership?
    # Or is this user only a member of this group?
    is_owner: Mapped[bool] = mapped_column(db.Boolean, nullable=False, server_default=expression.false(), default=False)
    created_at: Mapped[datetime] = mapped_column(db.DateTime(), nullable=False, default=db.func.now())
    updated_at: Mapped[datetime] = mapped_column(
        db.DateTime(), nullable=False, default=db.func.now(), onupdate=db.func.now()
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(db.DateTime())

    # Save the user IDs of the person who added/removed someone from a group
    created_actor_id: Mapped[Optional[str]] = mapped_column(db.Unicode(50), db.ForeignKey("okta_user.id"))
    ended_actor_id: Mapped[Optional[str]] = mapped_column(db.Unicode(50), db.ForeignKey("okta_user.id"))

    created_reason: Mapped[str] = mapped_column(db.Unicode(1024), nullable=False, default="", server_default="")

    # This field is set to True when an owner chooses to not renew access in the Expiring Access workflow
    should_expire: Mapped[bool] = mapped_column(
        db.Boolean, nullable=False, server_default=expression.false(), default=False
    )

    # See more details on specifying alternative join conditions for relationships at
    # https://docs.sqlalchemy.org/en/14/orm/join_conditions.html#specifying-alternate-join-conditions
    group: Mapped["OktaGroup"] = db.relationship(
        "OktaGroup",
        back_populates="all_user_memberships_and_ownerships",
        lazy="raise_on_sql",
    )
    active_group: Mapped["OktaGroup"] = db.relationship(
        "OktaGroup",
        primaryjoin="and_(OktaGroup.id == OktaUserGroupMember.group_id, " "OktaGroup.deleted_at.is_(None))",
        back_populates="active_user_memberships_and_ownerships",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )

    user: Mapped["OktaUser"] = db.relationship(
        "OktaUser",
        back_populates="all_group_memberships_and_ownerships",
        foreign_keys=[user_id],
        lazy="raise_on_sql",
    )
    active_user: Mapped["OktaUser"] = db.relationship(
        "OktaUser",
        primaryjoin="and_(OktaUser.id == OktaUserGroupMember.user_id, " "OktaUser.deleted_at.is_(None))",
        back_populates="active_group_memberships_and_ownerships",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )

    role_group_mapping: Mapped["RoleGroupMap"] = db.relationship(
        "RoleGroupMap",
        back_populates="all_group_memberships_and_ownerships",
        foreign_keys=[role_group_map_id],
        lazy="raise_on_sql",
    )
    active_role_group_mapping: Mapped["RoleGroupMap"] = db.relationship(
        "RoleGroupMap",
        primaryjoin="and_(RoleGroupMap.id == OktaUserGroupMember.role_group_map_id, "
        "or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > func.now()))",
        back_populates="active_group_memberships_and_ownerships",
        foreign_keys=[role_group_map_id],
        viewonly=True,
        lazy="raise_on_sql",
    )

    access_request: Mapped["AccessRequest"] = db.relationship(
        "AccessRequest",
        back_populates="approved_membership",
        lazy="raise_on_sql",
        uselist=False,
    )

    created_actor: Mapped["OktaUser"] = db.relationship(
        "OktaUser", foreign_keys=[created_actor_id], lazy="raise_on_sql", viewonly=True
    )

    ended_actor: Mapped["OktaUser"] = db.relationship(
        "OktaUser", foreign_keys=[ended_actor_id], lazy="raise_on_sql", viewonly=True
    )


class OktaUser(db.Model):
    id: Mapped[str] = mapped_column(db.Unicode(50), primary_key=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(db.DateTime(), nullable=False, default=db.func.now())
    updated_at: Mapped[datetime] = mapped_column(
        db.DateTime(), nullable=False, default=db.func.now(), onupdate=db.func.now()
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(db.DateTime())
    # https://developer.okta.com/docs/reference/api/users/#default-profile-properties
    email: Mapped[str] = mapped_column(db.Unicode(100), nullable=False)
    first_name: Mapped[str] = mapped_column(db.Unicode(50), nullable=False)
    last_name: Mapped[str] = mapped_column(db.Unicode(50), nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(db.Unicode(100))
    employee_number: Mapped[Optional[str]] = mapped_column(db.Unicode(50))
    manager_id: Mapped[Optional[str]] = mapped_column(db.Unicode(50), db.ForeignKey("okta_user.id"))

    # These 2 indexes are equivalent to a unique index on (email,deleted_at) with UNIQUE NULLS NOT DISTINCT
    # SQL alchemy 1.4 does not support UNIQUE NULLS NOT DISTINCT, but 2.0 does.
    __table_args__ = (
        db.Index(
            "idx_email_deleted_at",
            "email",
            "deleted_at",
            unique=True,
            postgresql_where=db.text("deleted_at IS NOT NULL"),
            sqlite_where=db.text("deleted_at IS NOT NULL"),
        ),
        db.Index(
            "idx_email",
            "email",
            unique=True,
            postgresql_where=db.text("deleted_at IS NULL"),
            sqlite_where=db.text("deleted_at IS NULL"),
        ),
    )

    # A JSON field for storing the user profile, including extra user attribute data from Okta
    # https://github.com/edelooff/sqlalchemy-json
    # https://amercader.net/blog/beware-of-json-fields-in-sqlalchemy/
    profile: Mapped[Dict[str, Any]] = mapped_column(
        mutable_json_type(
            dbtype=db.JSON().with_variant(JSONB, "postgresql"),
            nested=True,
        ),
        nullable=False,
        server_default="{}",
    )

    manager: Mapped["OktaUser"] = db.relationship(
        "OktaUser",
        back_populates="reports",
        remote_side=[id],
        lazy="raise_on_sql",
    )

    reports: Mapped[List["OktaUser"]] = db.relationship(
        "OktaUser",
        back_populates="manager",
        lazy="raise_on_sql",
    )

    # See more details on specifying alternative join conditions for relationships at
    # https://docs.sqlalchemy.org/en/14/orm/join_conditions.html#specifying-alternate-join-conditions
    all_group_memberships_and_ownerships: Mapped[List[OktaUserGroupMember]] = db.relationship(
        "OktaUserGroupMember",
        primaryjoin="OktaUser.id == OktaUserGroupMember.user_id",
        back_populates="user",
        lazy="raise_on_sql",
    )
    active_group_memberships_and_ownerships: Mapped[List[OktaUserGroupMember]] = db.relationship(
        "OktaUserGroupMember",
        primaryjoin="and_(OktaUser.id == OktaUserGroupMember.user_id, "
        "or_(OktaUserGroupMember.ended_at.is_(None), OktaUserGroupMember.ended_at > func.now()))",
        back_populates="active_user",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )
    active_group_memberships: Mapped[List[OktaUserGroupMember]] = db.relationship(
        "OktaUserGroupMember",
        primaryjoin="and_(OktaUser.id == OktaUserGroupMember.user_id, "
        "OktaUserGroupMember.is_owner.is_(False), "
        "or_(OktaUserGroupMember.ended_at.is_(None), OktaUserGroupMember.ended_at > func.now()))",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )
    active_group_ownerships: Mapped[List[OktaUserGroupMember]] = db.relationship(
        "OktaUserGroupMember",
        primaryjoin="and_(OktaUser.id == OktaUserGroupMember.user_id, "
        "OktaUserGroupMember.is_owner.is_(True), "
        "or_(OktaUserGroupMember.ended_at.is_(None), OktaUserGroupMember.ended_at > func.now()))",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )

    all_access_requests: Mapped[List["AccessRequest"]] = db.relationship(
        "AccessRequest",
        primaryjoin="OktaUser.id == AccessRequest.requester_user_id",
        back_populates="requester",
        lazy="raise_on_sql",
        innerjoin=True,
    )

    all_resolved_access_requests: Mapped[List["AccessRequest"]] = db.relationship(
        "AccessRequest",
        primaryjoin="OktaUser.id == AccessRequest.resolver_user_id",
        back_populates="resolver",
        lazy="raise_on_sql",
        innerjoin=True,
    )

    all_resolved_role_requests: Mapped[List["AccessRequest"]] = db.relationship(
        "RoleRequest",
        primaryjoin="OktaUser.id == RoleRequest.resolver_user_id",
        back_populates="resolver",
        lazy="raise_on_sql",
        innerjoin=True,
    )

    pending_access_requests: Mapped[List["AccessRequest"]] = db.relationship(
        "AccessRequest",
        primaryjoin="and_(OktaUser.id == AccessRequest.requester_user_id, "
        "AccessRequest.status == 'PENDING', "
        "AccessRequest.resolved_at.is_(None))",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )

    resolved_access_requests: Mapped[List["AccessRequest"]] = db.relationship(
        "AccessRequest",
        primaryjoin="and_(OktaUser.id == AccessRequest.resolver_user_id, "
        "AccessRequest.status != 'PENDING', "
        "AccessRequest.resolved_at.is_not(None))",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )


class OktaGroup(db.Model):
    __tablename__ = "okta_group"
    id: Mapped[str] = mapped_column(db.Unicode(50), primary_key=True, nullable=False)
    type: Mapped[str] = mapped_column(db.Unicode(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(db.DateTime(), nullable=False, default=db.func.now())
    updated_at: Mapped[datetime] = mapped_column(
        db.DateTime(), nullable=False, default=db.func.now(), onupdate=db.func.now()
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(db.DateTime())
    # https://developer.okta.com/docs/reference/api/groups/#default-profile-properties
    name: Mapped[str] = mapped_column(db.Unicode(255), nullable=False)
    description: Mapped[str] = mapped_column(db.Unicode(1024), nullable=False, default="")

    # Is this group managed by Access or is it managed externally (Built-in Okta group? via Okta Group rule?)
    is_managed: Mapped[bool] = mapped_column(db.Boolean, nullable=False, server_default=expression.true(), default=True)

    # Field containing additional data about externally managed groups
    externally_managed_data: Mapped[Dict[str, Any]] = mapped_column(
        mutable_json_type(
            dbtype=db.JSON().with_variant(JSONB, "postgresql"),
            nested=True,
        ),
        nullable=False,
        server_default="{}",
    )

    # A JSON field for Group plugin integrations in the form of {"unique_plugin_name":{plugin_data},}
    # https://github.com/edelooff/sqlalchemy-json
    # https://amercader.net/blog/beware-of-json-fields-in-sqlalchemy/
    plugin_data: Mapped[Dict[str, Any]] = mapped_column(
        mutable_json_type(dbtype=db.JSON().with_variant(JSONB, "postgresql"), nested=True),
        nullable=False,
        server_default="{}",
    )

    # See more details on specifying alternative join conditions for relationships at
    # https://docs.sqlalchemy.org/en/14/orm/join_conditions.html#specifying-alternate-join-conditions
    all_user_memberships_and_ownerships: Mapped[List[OktaUserGroupMember]] = db.relationship(
        "OktaUserGroupMember",
        back_populates="group",
        lazy="raise_on_sql",
    )
    active_user_memberships_and_ownerships: Mapped[List[OktaUserGroupMember]] = db.relationship(
        "OktaUserGroupMember",
        primaryjoin="and_(OktaGroup.id == OktaUserGroupMember.group_id, "
        "or_(OktaUserGroupMember.ended_at.is_(None), OktaUserGroupMember.ended_at > func.now()))",
        back_populates="active_group",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )
    active_user_memberships: Mapped[List[OktaUserGroupMember]] = db.relationship(
        "OktaUserGroupMember",
        primaryjoin="and_(OktaGroup.id == OktaUserGroupMember.group_id, "
        "OktaUserGroupMember.is_owner.is_(False), "
        "or_(OktaUserGroupMember.ended_at.is_(None), OktaUserGroupMember.ended_at > func.now()))",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )
    active_user_ownerships: Mapped[List[OktaUserGroupMember]] = db.relationship(
        "OktaUserGroupMember",
        primaryjoin="and_(OktaGroup.id == OktaUserGroupMember.group_id, "
        "OktaUserGroupMember.is_owner.is_(True), "
        "or_(OktaUserGroupMember.ended_at.is_(None), OktaUserGroupMember.ended_at > func.now()))",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )
    active_non_role_user_memberships: Mapped[List[OktaUserGroupMember]] = db.relationship(
        "OktaUserGroupMember",
        primaryjoin="and_(OktaGroup.id == OktaUserGroupMember.group_id, "
        "OktaUserGroupMember.is_owner.is_(False), "
        "OktaUserGroupMember.role_group_map_id.is_(None), "
        "or_(OktaUserGroupMember.ended_at.is_(None), OktaUserGroupMember.ended_at > func.now()))",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )
    active_non_role_user_ownerships: Mapped[List[OktaUserGroupMember]] = db.relationship(
        "OktaUserGroupMember",
        primaryjoin="and_(OktaGroup.id == OktaUserGroupMember.group_id, "
        "OktaUserGroupMember.is_owner.is_(True), "
        "OktaUserGroupMember.role_group_map_id.is_(None), "
        "or_(OktaUserGroupMember.ended_at.is_(None), OktaUserGroupMember.ended_at > func.now()))",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )

    all_role_mappings: Mapped[List["RoleGroupMap"]] = db.relationship(
        "RoleGroupMap",
        back_populates="group",
        foreign_keys="RoleGroupMap.group_id",
        lazy="raise_on_sql",
    )
    active_role_mappings: Mapped[List["RoleGroupMap"]] = db.relationship(
        "RoleGroupMap",
        primaryjoin="and_(OktaGroup.id == RoleGroupMap.group_id, "
        "or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > func.now()))",
        back_populates="active_group",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )
    active_role_member_mappings: Mapped[List["RoleGroupMap"]] = db.relationship(
        "RoleGroupMap",
        primaryjoin="and_(OktaGroup.id == RoleGroupMap.group_id, "
        "RoleGroupMap.is_owner.is_(False), "
        "or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > func.now()))",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )
    active_role_owner_mappings: Mapped[List["RoleGroupMap"]] = db.relationship(
        "RoleGroupMap",
        primaryjoin="and_(OktaGroup.id == RoleGroupMap.group_id, "
        "RoleGroupMap.is_owner.is_(True), "
        "or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > func.now()))",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )

    all_access_requests: Mapped[List["AccessRequest"]] = db.relationship(
        "AccessRequest", back_populates="requested_group", lazy="raise_on_sql"
    )

    pending_access_requests: Mapped[List["AccessRequest"]] = db.relationship(
        "AccessRequest",
        primaryjoin="and_(OktaGroup.id == AccessRequest.requested_group_id, "
        "AccessRequest.status == 'PENDING', "
        "AccessRequest.resolved_at.is_(None))",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )

    # requests to join group
    all_role_requests_to: Mapped[List["RoleRequest"]] = db.relationship(
        "RoleRequest",
        back_populates="requested_group",
        primaryjoin="OktaGroup.id == RoleRequest.requested_group_id",
        lazy="raise_on_sql",
    )

    # request by role group to join group
    all_role_requests_from: Mapped[List["RoleRequest"]] = db.relationship(
        "RoleRequest",
        back_populates="requester_role",
        primaryjoin="OktaGroup.id == RoleRequest.requester_role_id",
        lazy="raise_on_sql",
    )

    pending_role_requests: Mapped[List["RoleRequest"]] = db.relationship(
        "RoleRequest",
        primaryjoin="and_(OktaGroup.id == RoleRequest.requested_group_id, "
        "RoleRequest.status == 'PENDING', "
        "RoleRequest.resolved_at.is_(None))",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )

    all_group_tags: Mapped[List["OktaGroupTagMap"]] = db.relationship(
        "OktaGroupTagMap",
        back_populates="group",
        lazy="raise_on_sql",
    )
    # SQLAlchemy doesn't seem to support loading
    # group.active_role_associated_group_[member|owner]_mappings.active_group when a group_id or user_id is specified
    # in GET /api/audit/users so we have to enable "select" lazy loading.
    active_group_tags: Mapped[List["OktaGroupTagMap"]] = db.relationship(
        "OktaGroupTagMap",
        primaryjoin="and_(OktaGroup.id == OktaGroupTagMap.group_id, "
        "or_(OktaGroupTagMap.ended_at.is_(None), OktaGroupTagMap.ended_at > func.now()))",
        back_populates="active_group",
        viewonly=True,
        lazy="select",
    )

    __mapper_args__ = {
        "polymorphic_identity": "okta_group",
        "polymorphic_on": "type",
    }


class RoleGroupMap(db.Model):
    # See https://stackoverflow.com/a/60840921
    id: Mapped[int] = mapped_column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        autoincrement=True,
        primary_key=True,
    )
    role_group_id: Mapped[str] = mapped_column("role_id", db.Unicode(50), db.ForeignKey("okta_group.id"))
    group_id: Mapped[str] = mapped_column("group_id", db.Unicode(50), db.ForeignKey("okta_group.id"))
    # Does this role grant ownership of the group and allow role members to administer the group and manage membership?
    # Or does this role grant only membership to the group?
    is_owner: Mapped[bool] = mapped_column(db.Boolean, nullable=False, server_default=expression.false(), default=False)
    created_at: Mapped[datetime] = mapped_column(db.DateTime(), nullable=False, default=db.func.now())
    updated_at: Mapped[datetime] = mapped_column(
        db.DateTime(), nullable=False, default=db.func.now(), onupdate=db.func.now()
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(db.DateTime())

    # Save the user IDs of the person who added/removed someone from a group
    created_actor_id: Mapped[Optional[str]] = mapped_column(db.Unicode(50), db.ForeignKey("okta_user.id"))
    ended_actor_id: Mapped[Optional[str]] = mapped_column(db.Unicode(50), db.ForeignKey("okta_user.id"))

    created_reason: Mapped[str] = mapped_column(db.Unicode(1024), nullable=False, default="", server_default="")

    # This field is set to True when an owner chooses to not renew access in the Expiring Access workflow
    should_expire: Mapped[bool] = mapped_column(
        db.Boolean, nullable=False, server_default=expression.false(), default=False
    )

    # See more details on specifying alternative join conditions for relationships at
    # https://docs.sqlalchemy.org/en/14/orm/join_conditions.html#specifying-alternate-join-conditions
    role_group: Mapped["RoleGroup"] = db.relationship(
        "RoleGroup",
        back_populates="all_role_associated_group_mappings",
        foreign_keys=[role_group_id],
        lazy="raise_on_sql",
    )
    active_role_group: Mapped["RoleGroup"] = db.relationship(
        "RoleGroup",
        primaryjoin="and_(remote(OktaGroup.id) == RoleGroupMap.role_group_id, " "OktaGroup.deleted_at.is_(None))",
        back_populates="active_role_associated_group_mappings",
        foreign_keys=[role_group_id],
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )

    group: Mapped[OktaGroup] = db.relationship(
        "OktaGroup",
        back_populates="all_role_mappings",
        foreign_keys=[group_id],
        lazy="raise_on_sql",
    )
    active_group: Mapped[OktaGroup] = db.relationship(
        "OktaGroup",
        primaryjoin="and_(OktaGroup.id == RoleGroupMap.group_id, " "OktaGroup.deleted_at.is_(None))",
        back_populates="active_role_mappings",
        foreign_keys=[group_id],
        viewonly=True,
        # SQLAlchemy doesn't seem to support loading
        # group.active_role_associated_group_[member|owner]_mappings.active_group when a group_id or user_id is specified
        # in GET /api/audit/users so we have to enable "select" lazy loading.
        lazy="select",
        innerjoin=True,
    )

    all_group_memberships_and_ownerships: Mapped[List[OktaUserGroupMember]] = db.relationship(
        "OktaUserGroupMember", back_populates="role_group_mapping", lazy="raise_on_sql"
    )
    active_group_memberships_and_ownerships: Mapped[List[OktaUserGroupMember]] = db.relationship(
        "OktaUserGroupMember",
        primaryjoin="and_(RoleGroupMap.id == OktaUserGroupMember.role_group_map_id, "
        "or_(OktaUserGroupMember.ended_at.is_(None), OktaUserGroupMember.ended_at > func.now()))",
        back_populates="active_role_group_mapping",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )

    created_actor: Mapped[OktaUser] = db.relationship(
        "OktaUser", foreign_keys=[created_actor_id], lazy="raise_on_sql", viewonly=True
    )

    ended_actor: Mapped[OktaUser] = db.relationship(
        "OktaUser", foreign_keys=[ended_actor_id], lazy="raise_on_sql", viewonly=True
    )

    role_request: Mapped["RoleRequest"] = db.relationship(
        "RoleRequest",
        back_populates="approved_membership",
        lazy="raise_on_sql",
        uselist=False,
    )

    @validates("group")
    def validate_group(self, key: str, group: OktaGroup) -> OktaGroup:
        if group.type == RoleGroup.__mapper_args__["polymorphic_identity"]:
            raise ValueError("Roles cannot contain Role Groups as a member of their list of Groups")
        return group


class RoleGroup(OktaGroup):
    ROLE_GROUP_NAME_PREFIX = "Role-"

    __tablename__ = "role_group"
    id: Mapped[str] = mapped_column(db.Unicode(50), db.ForeignKey("okta_group.id"), primary_key=True)

    # See more details on specifying alternative join conditions for relationships at
    # https://docs.sqlalchemy.org/en/14/orm/join_conditions.html#specifying-alternate-join-conditions
    all_role_associated_group_mappings: Mapped[List[RoleGroupMap]] = db.relationship(
        "RoleGroupMap",
        back_populates="role_group",
        foreign_keys="RoleGroupMap.role_group_id",
        lazy="raise_on_sql",
    )
    active_role_associated_group_mappings: Mapped[List[RoleGroupMap]] = db.relationship(
        "RoleGroupMap",
        primaryjoin="and_(OktaGroup.id == foreign(RoleGroupMap.role_group_id), "
        "or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > func.now()))",
        back_populates="active_role_group",
        foreign_keys="RoleGroupMap.role_group_id",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )
    active_role_associated_group_member_mappings: Mapped[List[RoleGroupMap]] = db.relationship(
        "RoleGroupMap",
        primaryjoin="and_(RoleGroup.id == foreign(RoleGroupMap.role_group_id), "
        "RoleGroupMap.is_owner.is_(False), "
        "or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > func.now()))",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )
    active_role_associated_group_owner_mappings: Mapped[List[RoleGroupMap]] = db.relationship(
        "RoleGroupMap",
        primaryjoin="and_(RoleGroup.id == foreign(RoleGroupMap.role_group_id), "
        "RoleGroupMap.is_owner.is_(True), "
        "or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > func.now()))",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )

    __mapper_args__ = {
        "polymorphic_identity": "role_group",
    }


class AppGroup(OktaGroup):
    APP_GROUP_NAME_PREFIX = "App-"
    APP_NAME_GROUP_NAME_SEPARATOR = "-"
    APP_OWNERS_GROUP_NAME_SUFFIX = "Owners"

    __tablename__ = "app_group"
    id: Mapped[str] = mapped_column(db.Unicode(50), db.ForeignKey("okta_group.id"), primary_key=True)

    app_id: Mapped[str] = mapped_column(db.Unicode(50), db.ForeignKey("app.id"), nullable=False)
    # Is this group the app owner group and can administer the app and other app groups?
    # Membership to an app onwer group implicitly grants group owner permissions on the
    # group to administer and manage membership of the app owner group
    is_owner: Mapped[bool] = mapped_column(db.Boolean, nullable=False, server_default=expression.false(), default=False)

    # SQLAlchemy doesn't seem to support loading
    # group.active_role_associated_group_[member|owner]_mappings.active_group when a group_id or user_id is specified
    # in GET /api/audit/users so we have to enable "select" lazy loading.
    app: Mapped["App"] = db.relationship("App", back_populates="app_groups", lazy="select")

    __mapper_args__ = {
        "polymorphic_identity": "app_group",
    }

    @validates("name")
    def validate_group(self, key: str, name: str) -> str:
        app = App.query.filter(App.id == self.app_id).filter(App.deleted_at.is_(None)).first()
        if app is None:
            raise ValueError(f"Specified App with app_id: {self.app_id} does not exist")
        # app_groups should have app name prepended always
        app_group_name_prefix = f"{AppGroup.APP_GROUP_NAME_PREFIX}{app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
        if not name.startswith(app_group_name_prefix):
            raise ValueError(
                'App Group name "{}" should be prefixed with App name. For example: "{}"'.format(
                    name, app_group_name_prefix
                )
            )
        return name


class App(db.Model):
    ACCESS_APP_RESERVED_NAME = config.APP_NAME

    # A 20 character random string like Okta IDs
    id: Mapped[str] = mapped_column(db.Unicode(20), primary_key=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(db.DateTime(), nullable=False, default=db.func.now())
    updated_at: Mapped[datetime] = mapped_column(
        db.DateTime(), nullable=False, default=db.func.now(), onupdate=db.func.now()
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(db.DateTime())

    name: Mapped[str] = mapped_column(db.Unicode(255), nullable=False)
    description: Mapped[str] = mapped_column(db.Unicode(1024), nullable=False, default="")

    app_groups: Mapped[List[AppGroup]] = db.relationship("AppGroup", back_populates="app", lazy="raise_on_sql")

    active_app_groups: Mapped[List[AppGroup]] = db.relationship(
        "AppGroup",
        primaryjoin="and_(App.id == AppGroup.app_id, AppGroup.deleted_at.is_(None))",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
        order_by="AppGroup.name",
    )

    active_owner_app_groups: Mapped[List[AppGroup]] = db.relationship(
        "AppGroup",
        primaryjoin="and_(App.id == AppGroup.app_id, " "AppGroup.deleted_at.is_(None), " "AppGroup.is_owner.is_(True))",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
        order_by="AppGroup.name",
    )

    active_non_owner_app_groups: Mapped[List[AppGroup]] = db.relationship(
        "AppGroup",
        primaryjoin="and_(App.id == AppGroup.app_id, "
        "AppGroup.deleted_at.is_(None), "
        "AppGroup.is_owner.is_(False))",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
        order_by="AppGroup.name",
    )

    all_app_tags: Mapped[List["AppTagMap"]] = db.relationship(
        "AppTagMap",
        back_populates="app",
        lazy="raise_on_sql",
    )
    active_app_tags: Mapped[List["AppTagMap"]] = db.relationship(
        "AppTagMap",
        primaryjoin="and_(App.id == AppTagMap.app_id, "
        "or_(AppTagMap.ended_at.is_(None), AppTagMap.ended_at > func.now()))",
        back_populates="active_app",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )


# Use StrEnum to make it JSON serializable
class AccessRequestStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class AccessRequest(db.Model):
    # A 20 character random string like Okta IDs
    id: Mapped[str] = mapped_column(db.Unicode(20), primary_key=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(db.DateTime(), nullable=False, default=db.func.now())
    updated_at: Mapped[datetime] = mapped_column(
        db.DateTime(), nullable=False, default=db.func.now(), onupdate=db.func.now()
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(db.DateTime())

    status: Mapped[AccessRequestStatus] = mapped_column(
        db.Enum(AccessRequestStatus),
        nullable=False,
        default=AccessRequestStatus.PENDING,
    )

    requester_user_id: Mapped[str] = mapped_column(db.Unicode(50), db.ForeignKey("okta_user.id"))
    requested_group_id: Mapped[str] = mapped_column(db.Unicode(50), db.ForeignKey("okta_group.id"))
    request_ownership: Mapped[bool] = mapped_column(db.Boolean, nullable=False, default=False)
    request_reason: Mapped[str] = mapped_column(db.Unicode(1024), nullable=False, default="")
    request_ending_at: Mapped[Optional[datetime]] = mapped_column(db.DateTime())

    resolver_user_id: Mapped[Optional[str]] = mapped_column(db.Unicode(50), db.ForeignKey("okta_user.id"))
    resolution_reason: Mapped[str] = mapped_column(db.Unicode(1024), nullable=False, default="")

    approval_ending_at: Mapped[Optional[datetime]] = mapped_column(db.DateTime())

    # See https://stackoverflow.com/a/60840921
    approved_membership_id: Mapped[Optional[int]] = mapped_column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("okta_user_group_member.id"),
    )

    requester: Mapped[OktaUser] = db.relationship(
        "OktaUser",
        back_populates="all_access_requests",
        foreign_keys=[requester_user_id],
        lazy="raise_on_sql",
    )

    active_requester: Mapped[OktaUser] = db.relationship(
        "OktaUser",
        primaryjoin="and_(OktaUser.id == AccessRequest.requester_user_id, " "OktaUser.deleted_at.is_(None))",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )

    requested_group: Mapped[OktaGroup] = db.relationship(
        "OktaGroup",
        back_populates="all_access_requests",
        foreign_keys=[requested_group_id],
        lazy="raise_on_sql",
    )

    active_requested_group: Mapped[OktaGroup] = db.relationship(
        "OktaGroup",
        primaryjoin="and_(OktaGroup.id == AccessRequest.requested_group_id, " "OktaGroup.deleted_at.is_(None))",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )

    resolver: Mapped[OktaUser] = db.relationship(
        "OktaUser",
        back_populates="all_resolved_access_requests",
        foreign_keys=[resolver_user_id],
        lazy="raise_on_sql",
    )

    active_resolver: Mapped[OktaUser] = db.relationship(
        "OktaUser",
        primaryjoin="and_(OktaUser.id == AccessRequest.resolver_user_id, " "OktaUser.deleted_at.is_(None))",
        viewonly=True,
        lazy="raise_on_sql",
    )

    approved_membership: Mapped[OktaUserGroupMember] = db.relationship(
        "OktaUserGroupMember",
        back_populates="access_request",
        foreign_keys=[approved_membership_id],
        lazy="raise_on_sql",
    )


class RoleRequest(db.Model):
    # A 20 character random string like Okta IDs
    id: Mapped[str] = mapped_column(db.Unicode(20), primary_key=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(db.DateTime(), nullable=False, default=db.func.now())
    updated_at: Mapped[datetime] = mapped_column(
        db.DateTime(), nullable=False, default=db.func.now(), onupdate=db.func.now()
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(db.DateTime())

    status: Mapped[AccessRequestStatus] = mapped_column(
        db.Enum(AccessRequestStatus),
        nullable=False,
        default=AccessRequestStatus.PENDING,
    )

    # must be an owner of the role
    requester_user_id: Mapped[str] = mapped_column(db.Unicode(50), db.ForeignKey("okta_user.id"))
    # role to be added to the requested group
    requester_role_id: Mapped[str] = mapped_column(db.Unicode(50), db.ForeignKey("okta_group.id"))
    requested_group_id: Mapped[str] = mapped_column(db.Unicode(50), db.ForeignKey("okta_group.id"))
    request_ownership: Mapped[bool] = mapped_column(db.Boolean, nullable=False, default=False)
    request_reason: Mapped[str] = mapped_column(db.Unicode(1024), nullable=False, default="")
    request_ending_at: Mapped[Optional[datetime]] = mapped_column(db.DateTime())

    resolver_user_id: Mapped[Optional[str]] = mapped_column(db.Unicode(50), db.ForeignKey("okta_user.id"))
    resolution_reason: Mapped[str] = mapped_column(db.Unicode(1024), nullable=False, default="")

    approval_ending_at: Mapped[Optional[datetime]] = mapped_column(db.DateTime())

    # See https://stackoverflow.com/a/60840921
    approved_membership_id: Mapped[Optional[int]] = mapped_column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("role_group_map.id"),
    )

    requester: Mapped[OktaUser] = db.relationship(
        "OktaUser",
        primaryjoin="OktaUser.id == RoleRequest.requester_user_id",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )

    active_requester: Mapped[OktaUser] = db.relationship(
        "OktaUser",
        primaryjoin="and_(OktaUser.id == RoleRequest.requester_user_id, " "OktaUser.deleted_at.is_(None))",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )

    requester_role: Mapped[OktaGroup] = db.relationship(
        "OktaGroup",
        back_populates="all_role_requests_from",
        foreign_keys=[requester_role_id],
        lazy="raise_on_sql",
    )

    active_requester_role: Mapped[OktaGroup] = db.relationship(
        "OktaGroup",
        primaryjoin="and_(OktaGroup.id == RoleRequest.requested_group_id, " "OktaGroup.deleted_at.is_(None))",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )

    requested_group: Mapped[OktaGroup] = db.relationship(
        "OktaGroup",
        back_populates="all_role_requests_to",
        foreign_keys=[requested_group_id],
        lazy="raise_on_sql",
    )

    active_requested_group: Mapped[OktaGroup] = db.relationship(
        "OktaGroup",
        primaryjoin="and_(OktaGroup.id == RoleRequest.requested_group_id, " "OktaGroup.deleted_at.is_(None))",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )

    resolver: Mapped[OktaUser] = db.relationship(
        "OktaUser",
        back_populates="all_resolved_role_requests",
        foreign_keys=[resolver_user_id],
        lazy="raise_on_sql",
    )

    active_resolver: Mapped[OktaUser] = db.relationship(
        "OktaUser",
        primaryjoin="and_(OktaUser.id == RoleRequest.resolver_user_id, " "OktaUser.deleted_at.is_(None))",
        viewonly=True,
        lazy="raise_on_sql",
    )

    approved_membership: Mapped[RoleGroupMap] = db.relationship(
        "RoleGroupMap",
        back_populates="role_request",
        foreign_keys=[approved_membership_id],
        lazy="raise_on_sql",
    )


class TagConstraint:
    def __init__(
        self,
        name: str,
        validator: Callable[[Any], bool],
        coalesce: Callable[[Any, Any], Any],
        description: Optional[str] = "",
    ):
        self.name = name
        self.description = description
        self.validator = validator
        self.coalesce = coalesce


class Tag(db.Model):
    MEMBER_TIME_LIMIT_CONSTRAINT_KEY = "member_time_limit"
    OWNER_TIME_LIMIT_CONSTRAINT_KEY = "owner_time_limit"
    REQUIRE_MEMBER_REASON_CONSTRAINT_KEY = "require_member_reason"
    REQUIRE_OWNER_REASON_CONSTRAINT_KEY = "require_owner_reason"
    DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY = "disallow_self_add_membership"
    DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY = "disallow_self_add_ownership"
    CONSTRAINTS: Dict[str, TagConstraint] = {
        MEMBER_TIME_LIMIT_CONSTRAINT_KEY: TagConstraint(
            name="Limit time of membership",
            description="Specify a maximum length of time in seconds that a user or role can be a member of "
            + "this group or groups associated with this app.",
            validator=lambda value: isinstance(value, int) and value > 0,
            coalesce=lambda a, b: min(a, b),
        ),
        OWNER_TIME_LIMIT_CONSTRAINT_KEY: TagConstraint(
            name="Limit time of ownership",
            description="Specify a maximum length of time in seconds that a user or role can be a owner of "
            + "this group or groups associated with this app.",
            validator=lambda value: isinstance(value, int) and value > 0,
            coalesce=lambda a, b: min(a, b),
        ),
        REQUIRE_MEMBER_REASON_CONSTRAINT_KEY: TagConstraint(
            name="Require reason for member access",
            description="Require a reason for adding a user or role as a member to this group or groups "
            + "associated with this app.",
            validator=lambda value: isinstance(value, bool),
            coalesce=lambda a, b: a or b,
        ),
        REQUIRE_OWNER_REASON_CONSTRAINT_KEY: TagConstraint(
            name="Require reason for owner access",
            description="Require a reason for adding a user or role as a owner to this group or groups "
            + "associated with this app.",
            validator=lambda value: isinstance(value, bool),
            coalesce=lambda a, b: a or b,
        ),
        DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY: TagConstraint(
            name="Disallow owners from adding themselves as members",
            description="Do not allow owners from adding themselves as members to this group or groups "
            + "associated with this app",
            validator=lambda value: isinstance(value, bool),
            coalesce=lambda a, b: a or b,
        ),
        DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY: TagConstraint(
            name="Disallow owners from adding themselves as owners",
            description="Do not allow owners from adding themselves as owners to this group or groups "
            + "associated with this app",
            validator=lambda value: isinstance(value, bool),
            coalesce=lambda a, b: a or b,
        ),
    }

    id: Mapped[str] = mapped_column(db.Unicode(50), primary_key=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(db.DateTime(), nullable=False, default=db.func.now())
    updated_at: Mapped[datetime] = mapped_column(
        db.DateTime(), nullable=False, default=db.func.now(), onupdate=db.func.now()
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(db.DateTime())

    name: Mapped[str] = mapped_column(db.Unicode(255), nullable=False)
    description: Mapped[str] = mapped_column(db.Unicode(1024), nullable=False, default="")

    enabled: Mapped[bool] = mapped_column(db.Boolean(), nullable=False, default=True)

    # Field containing additional data about externally managed groups
    constraints: Mapped[Dict[str, Any]] = mapped_column(
        mutable_json_type(
            dbtype=db.JSON().with_variant(JSONB, "postgresql"),
            nested=True,
        ),
        nullable=False,
        server_default="{}",
    )

    all_group_tags: Mapped[List["OktaGroupTagMap"]] = db.relationship(
        "OktaGroupTagMap",
        back_populates="tag",
        lazy="raise_on_sql",
    )
    active_group_tags: Mapped[List["OktaGroupTagMap"]] = db.relationship(
        "OktaGroupTagMap",
        primaryjoin="and_(Tag.id == OktaGroupTagMap.tag_id, "
        "or_(OktaGroupTagMap.ended_at.is_(None), OktaGroupTagMap.ended_at > func.now()))",
        back_populates="active_tag",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )

    all_app_tags: Mapped[List["AppTagMap"]] = db.relationship(
        "AppTagMap",
        back_populates="tag",
        lazy="raise_on_sql",
    )
    active_app_tags: Mapped[List["AppTagMap"]] = db.relationship(
        "AppTagMap",
        primaryjoin="and_(Tag.id == AppTagMap.tag_id, "
        "or_(AppTagMap.ended_at.is_(None), AppTagMap.ended_at > func.now()))",
        back_populates="active_tag",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )


class OktaGroupTagMap(db.Model):
    # See https://stackoverflow.com/a/60840921
    id: Mapped[int] = mapped_column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        autoincrement=True,
        primary_key=True,
    )
    tag_id: Mapped[str] = mapped_column(db.Unicode(50), db.ForeignKey("tag.id"))
    group_id: Mapped[str] = mapped_column(db.Unicode(50), db.ForeignKey("okta_group.id"))
    # If this tag is via a AppTagMap
    # See https://stackoverflow.com/a/60840921
    app_tag_map_id: Mapped[Optional[int]] = mapped_column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("app_tag_map.id"),
    )

    created_at: Mapped[datetime] = mapped_column(db.DateTime(), nullable=False, default=db.func.now())
    updated_at: Mapped[datetime] = mapped_column(
        db.DateTime(), nullable=False, default=db.func.now(), onupdate=db.func.now()
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(db.DateTime())

    tag: Mapped[Tag] = db.relationship(
        "Tag",
        back_populates="all_group_tags",
        foreign_keys=[tag_id],
        lazy="raise_on_sql",
    )
    # SQLAlchemy doesn't seem to support loading
    # group.active_role_associated_group_[member|owner]_mappings.active_group when a group_id or user_id is specified
    # in GET /api/audit/users so we have to enable "select" lazy loading.
    active_tag: Mapped[Tag] = db.relationship(
        "Tag",
        primaryjoin="and_(Tag.id == OktaGroupTagMap.tag_id, " "Tag.deleted_at.is_(None))",
        back_populates="active_group_tags",
        viewonly=True,
        lazy="select",
        innerjoin=True,
    )
    enabled_active_tag: Mapped[Tag] = db.relationship(
        "Tag",
        primaryjoin="and_(Tag.id == OktaGroupTagMap.tag_id, " "Tag.deleted_at.is_(None), Tag.enabled.is_(True))",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )

    group: Mapped[OktaGroup] = db.relationship(
        "OktaGroup",
        back_populates="all_group_tags",
        lazy="raise_on_sql",
    )
    active_group: Mapped[OktaGroup] = db.relationship(
        "OktaGroup",
        primaryjoin="and_(OktaGroup.id == OktaGroupTagMap.group_id, " "OktaGroup.deleted_at.is_(None))",
        back_populates="active_group_tags",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )

    app_tag_mapping: Mapped["AppTagMap"] = db.relationship(
        "AppTagMap",
        back_populates="group_tag_mappings",
        foreign_keys=[app_tag_map_id],
        lazy="raise_on_sql",
    )
    # SQLAlchemy doesn't seem to support loading
    # group.active_role_associated_group_[member|owner]_mappings.active_group when a group_id or user_id is specified
    # in GET /api/audit/users so we have to enable "select" lazy loading.
    active_app_tag_mapping: Mapped["AppTagMap"] = db.relationship(
        "AppTagMap",
        primaryjoin="and_(AppTagMap.id == OktaGroupTagMap.app_tag_map_id, "
        "or_(AppTagMap.ended_at.is_(None), AppTagMap.ended_at > func.now()))",
        back_populates="active_group_tag_mappings",
        foreign_keys=[app_tag_map_id],
        viewonly=True,
        lazy="select",
    )


class AppTagMap(db.Model):
    # See https://stackoverflow.com/a/60840921
    id: Mapped[int] = mapped_column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        autoincrement=True,
        primary_key=True,
    )
    tag_id: Mapped[str] = mapped_column(db.Unicode(50), db.ForeignKey("tag.id"))
    app_id: Mapped[str] = mapped_column(db.Unicode(50), db.ForeignKey("app.id"))

    created_at: Mapped[datetime] = mapped_column(db.DateTime(), nullable=False, default=db.func.now())
    updated_at: Mapped[datetime] = mapped_column(
        db.DateTime(), nullable=False, default=db.func.now(), onupdate=db.func.now()
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(db.DateTime())

    tag: Mapped[Tag] = db.relationship(
        "Tag",
        back_populates="all_app_tags",
        foreign_keys=[tag_id],
        lazy="raise_on_sql",
    )
    active_tag: Mapped[Tag] = db.relationship(
        "Tag",
        primaryjoin="and_(Tag.id == AppTagMap.tag_id, " "Tag.deleted_at.is_(None))",
        back_populates="active_app_tags",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )
    enabled_active_tag: Mapped[Tag] = db.relationship(
        "Tag",
        primaryjoin="and_(Tag.id == AppTagMap.tag_id, " "Tag.deleted_at.is_(None), Tag.enabled.is_(True))",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )

    app: Mapped[App] = db.relationship(
        "App",
        back_populates="all_app_tags",
        lazy="raise_on_sql",
    )
    active_app: Mapped[App] = db.relationship(
        "App",
        primaryjoin="and_(App.id == AppTagMap.app_id, " "App.deleted_at.is_(None))",
        back_populates="active_app_tags",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )

    group_tag_mappings: Mapped[List[OktaGroupTagMap]] = db.relationship(
        "OktaGroupTagMap", back_populates="app_tag_mapping", lazy="raise_on_sql"
    )
    active_group_tag_mappings: Mapped[List[OktaGroupTagMap]] = db.relationship(
        "OktaGroupTagMap",
        primaryjoin="and_(AppTagMap.id == OktaGroupTagMap.app_tag_map_id, "
        "or_(OktaGroupTagMap.ended_at.is_(None), OktaGroupTagMap.ended_at > func.now()))",
        back_populates="active_app_tag_mapping",
        viewonly=True,
        lazy="raise_on_sql",
        innerjoin=True,
    )
