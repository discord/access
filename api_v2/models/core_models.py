"""
Pure SQLAlchemy models for FastAPI application.
Migrated from Flask-SQLAlchemy to remove Flask dependencies.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates
from sqlalchemy.sql import expression
from sqlalchemy_json import mutable_json_type

from .base import Base


class OktaUserGroupMember(Base):
    __tablename__ = "okta_user_group_member"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        autoincrement=True,
        primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(String(50), ForeignKey("okta_user.id"))
    group_id: Mapped[str] = mapped_column(String(50), ForeignKey("okta_group.id"))
    role_group_map_id: Mapped[Optional[int]] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        ForeignKey("role_group_map.id"),
    )
    is_owner: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=expression.false(), default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=func.now(), onupdate=func.now())
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime())

    created_actor_id: Mapped[Optional[str]] = mapped_column(String(50), ForeignKey("okta_user.id"))
    ended_actor_id: Mapped[Optional[str]] = mapped_column(String(50), ForeignKey("okta_user.id"))
    created_reason: Mapped[str] = mapped_column(String(1024), nullable=False, default="", server_default="")
    should_expire: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=expression.false(), default=False
    )

    group: Mapped[OktaGroup] = relationship(
        "OktaGroup",
        back_populates="all_user_memberships_and_ownerships",
        lazy="raise",
    )
    active_group: Mapped[OktaGroup] = relationship(
        "OktaGroup",
        primaryjoin="and_(OktaGroup.id == OktaUserGroupMember.group_id, " "OktaGroup.deleted_at.is_(None))",
        back_populates="active_user_memberships_and_ownerships",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )

    user: Mapped[OktaUser] = relationship(
        "OktaUser",
        back_populates="all_group_memberships_and_ownerships",
        foreign_keys=[user_id],
        lazy="raise",
    )
    active_user: Mapped[OktaUser] = relationship(
        "OktaUser",
        primaryjoin="and_(OktaUser.id == OktaUserGroupMember.user_id, " "OktaUser.deleted_at.is_(None))",
        back_populates="active_group_memberships_and_ownerships",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )

    role_group_mapping: Mapped[RoleGroupMap] = relationship(
        "RoleGroupMap",
        back_populates="all_group_memberships_and_ownerships",
        foreign_keys=[role_group_map_id],
        lazy="raise",
    )
    active_role_group_mapping: Mapped[RoleGroupMap] = relationship(
        "RoleGroupMap",
        primaryjoin="and_(RoleGroupMap.id == OktaUserGroupMember.role_group_map_id, "
        "or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > func.now()))",
        back_populates="active_group_memberships_and_ownerships",
        foreign_keys=[role_group_map_id],
        viewonly=True,
        lazy="raise",
    )

    access_request: Mapped[AccessRequest] = relationship(
        "AccessRequest",
        back_populates="approved_membership",
        lazy="raise",
        uselist=False,
    )

    created_actor: Mapped[OktaUser] = relationship(
        "OktaUser", foreign_keys=[created_actor_id], lazy="raise", viewonly=True
    )

    ended_actor: Mapped[OktaUser] = relationship("OktaUser", foreign_keys=[ended_actor_id], lazy="raise", viewonly=True)


class OktaUser(Base):
    __tablename__ = "okta_user"

    id: Mapped[str] = mapped_column(String(50), primary_key=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=func.now(), onupdate=func.now())
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime())
    email: Mapped[str] = mapped_column(String(100), nullable=False)
    first_name: Mapped[str] = mapped_column(String(50), nullable=False)
    last_name: Mapped[str] = mapped_column(String(50), nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(100))
    employee_number: Mapped[Optional[str]] = mapped_column(String(50))
    manager_id: Mapped[Optional[str]] = mapped_column(String(50), ForeignKey("okta_user.id"))

    # These 2 indexes are equivalent to a unique index on (email,deleted_at) with UNIQUE NULLS NOT DISTINCT
    # SQL alchemy 1.4 does not support UNIQUE NULLS NOT DISTINCT, but 2.0 does with postgresql_nulls_not_distinct=True.
    __table_args__ = (
        Index(
            "idx_email_deleted_at",
            "email",
            "deleted_at",
            unique=True,
            postgresql_where=text("deleted_at IS NOT NULL"),
            sqlite_where=text("deleted_at IS NOT NULL"),
        ),
        Index(
            "idx_email",
            "email",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )

    profile: Mapped[Dict[str, Any]] = mapped_column(
        mutable_json_type(
            dbtype=JSON().with_variant(JSONB(), "postgresql"),
            nested=True,
        ),
        nullable=False,
        server_default="{}",
    )

    manager: Mapped[OktaUser] = relationship(
        "OktaUser",
        back_populates="reports",
        remote_side=[id],
        lazy="raise",
    )

    reports: Mapped[List[OktaUser]] = relationship(
        "OktaUser",
        back_populates="manager",
        lazy="raise",
    )

    all_group_memberships_and_ownerships: Mapped[List[OktaUserGroupMember]] = relationship(
        "OktaUserGroupMember",
        primaryjoin="OktaUser.id == OktaUserGroupMember.user_id",
        back_populates="user",
        lazy="raise",
    )
    active_group_memberships_and_ownerships: Mapped[List[OktaUserGroupMember]] = relationship(
        "OktaUserGroupMember",
        primaryjoin="and_(OktaUser.id == OktaUserGroupMember.user_id, "
        "or_(OktaUserGroupMember.ended_at.is_(None), OktaUserGroupMember.ended_at > func.now()))",
        back_populates="active_user",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )
    active_group_memberships: Mapped[List[OktaUserGroupMember]] = relationship(
        "OktaUserGroupMember",
        primaryjoin="and_(OktaUser.id == OktaUserGroupMember.user_id, "
        "OktaUserGroupMember.is_owner.is_(False), "
        "or_(OktaUserGroupMember.ended_at.is_(None), OktaUserGroupMember.ended_at > func.now()))",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )
    active_group_ownerships: Mapped[List[OktaUserGroupMember]] = relationship(
        "OktaUserGroupMember",
        primaryjoin="and_(OktaUser.id == OktaUserGroupMember.user_id, "
        "OktaUserGroupMember.is_owner.is_(True), "
        "or_(OktaUserGroupMember.ended_at.is_(None), OktaUserGroupMember.ended_at > func.now()))",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )

    all_access_requests: Mapped[List[AccessRequest]] = relationship(
        "AccessRequest",
        primaryjoin="OktaUser.id == AccessRequest.requester_user_id",
        back_populates="requester",
        lazy="raise",
        innerjoin=True,
    )

    all_resolved_access_requests: Mapped[List[AccessRequest]] = relationship(
        "AccessRequest",
        primaryjoin="OktaUser.id == AccessRequest.resolver_user_id",
        back_populates="resolver",
        lazy="raise",
        innerjoin=True,
    )

    all_resolved_role_requests: Mapped[List[RoleRequest]] = relationship(
        "RoleRequest",
        primaryjoin="OktaUser.id == RoleRequest.resolver_user_id",
        back_populates="resolver",
        lazy="raise",
        innerjoin=True,
    )

    pending_access_requests: Mapped[List[AccessRequest]] = relationship(
        "AccessRequest",
        primaryjoin="and_(OktaUser.id == AccessRequest.requester_user_id, "
        "AccessRequest.status == 'PENDING', "
        "AccessRequest.resolved_at.is_(None))",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )

    resolved_access_requests: Mapped[List[AccessRequest]] = relationship(
        "AccessRequest",
        primaryjoin="and_(OktaUser.id == AccessRequest.resolver_user_id, "
        "AccessRequest.status != 'PENDING', "
        "AccessRequest.resolved_at.is_not(None))",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )


class OktaGroup(Base):
    __tablename__ = "okta_group"

    id: Mapped[str] = mapped_column(String(50), primary_key=True, nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=func.now(), onupdate=func.now())
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime())
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    is_managed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=expression.true(), default=True)

    externally_managed_data: Mapped[Dict[str, Any]] = mapped_column(
        mutable_json_type(
            dbtype=JSON().with_variant(JSONB(), "postgresql"),
            nested=True,
        ),
        nullable=False,
        server_default="{}",
    )

    plugin_data: Mapped[Dict[str, Any]] = mapped_column(
        mutable_json_type(dbtype=JSON().with_variant(JSONB(), "postgresql"), nested=True),
        nullable=False,
        server_default="{}",
    )

    all_user_memberships_and_ownerships: Mapped[List[OktaUserGroupMember]] = relationship(
        "OktaUserGroupMember",
        back_populates="group",
        lazy="raise",
    )
    active_user_memberships_and_ownerships: Mapped[List[OktaUserGroupMember]] = relationship(
        "OktaUserGroupMember",
        primaryjoin="and_(OktaGroup.id == OktaUserGroupMember.group_id, "
        "or_(OktaUserGroupMember.ended_at.is_(None), OktaUserGroupMember.ended_at > func.now()))",
        back_populates="active_group",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )
    active_user_memberships: Mapped[List[OktaUserGroupMember]] = relationship(
        "OktaUserGroupMember",
        primaryjoin="and_(OktaGroup.id == OktaUserGroupMember.group_id, "
        "OktaUserGroupMember.is_owner.is_(False), "
        "or_(OktaUserGroupMember.ended_at.is_(None), OktaUserGroupMember.ended_at > func.now()))",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )
    active_user_ownerships: Mapped[List[OktaUserGroupMember]] = relationship(
        "OktaUserGroupMember",
        primaryjoin="and_(OktaGroup.id == OktaUserGroupMember.group_id, "
        "OktaUserGroupMember.is_owner.is_(True), "
        "or_(OktaUserGroupMember.ended_at.is_(None), OktaUserGroupMember.ended_at > func.now()))",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )
    active_non_role_user_memberships: Mapped[List[OktaUserGroupMember]] = relationship(
        "OktaUserGroupMember",
        primaryjoin="and_(OktaGroup.id == OktaUserGroupMember.group_id, "
        "OktaUserGroupMember.is_owner.is_(False), "
        "OktaUserGroupMember.role_group_map_id.is_(None), "
        "or_(OktaUserGroupMember.ended_at.is_(None), OktaUserGroupMember.ended_at > func.now()))",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )
    active_non_role_user_ownerships: Mapped[List[OktaUserGroupMember]] = relationship(
        "OktaUserGroupMember",
        primaryjoin="and_(OktaGroup.id == OktaUserGroupMember.group_id, "
        "OktaUserGroupMember.is_owner.is_(True), "
        "OktaUserGroupMember.role_group_map_id.is_(None), "
        "or_(OktaUserGroupMember.ended_at.is_(None), OktaUserGroupMember.ended_at > func.now()))",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )

    all_role_mappings: Mapped[List[RoleGroupMap]] = relationship(
        "RoleGroupMap",
        back_populates="group",
        foreign_keys="RoleGroupMap.group_id",
        lazy="raise",
    )
    active_role_mappings: Mapped[List[RoleGroupMap]] = relationship(
        "RoleGroupMap",
        primaryjoin="and_(OktaGroup.id == RoleGroupMap.group_id, "
        "or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > func.now()))",
        back_populates="active_group",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )
    active_role_member_mappings: Mapped[List[RoleGroupMap]] = relationship(
        "RoleGroupMap",
        primaryjoin="and_(OktaGroup.id == RoleGroupMap.group_id, "
        "RoleGroupMap.is_owner.is_(False), "
        "or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > func.now()))",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )
    active_role_owner_mappings: Mapped[List[RoleGroupMap]] = relationship(
        "RoleGroupMap",
        primaryjoin="and_(OktaGroup.id == RoleGroupMap.group_id, "
        "RoleGroupMap.is_owner.is_(True), "
        "or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > func.now()))",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )

    all_access_requests: Mapped[List[AccessRequest]] = relationship(
        "AccessRequest", back_populates="requested_group", lazy="raise"
    )

    pending_access_requests: Mapped[List[AccessRequest]] = relationship(
        "AccessRequest",
        primaryjoin="and_(OktaGroup.id == AccessRequest.requested_group_id, "
        "AccessRequest.status == 'PENDING', "
        "AccessRequest.resolved_at.is_(None))",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )

    all_role_requests_to: Mapped[List[RoleRequest]] = relationship(
        "RoleRequest",
        back_populates="requested_group",
        primaryjoin="OktaGroup.id == RoleRequest.requested_group_id",
        lazy="raise",
    )

    all_role_requests_from: Mapped[List[RoleRequest]] = relationship(
        "RoleRequest",
        back_populates="requester_role",
        primaryjoin="OktaGroup.id == RoleRequest.requester_role_id",
        lazy="raise",
    )

    pending_role_requests: Mapped[List[RoleRequest]] = relationship(
        "RoleRequest",
        primaryjoin="and_(OktaGroup.id == RoleRequest.requested_group_id, "
        "RoleRequest.status == 'PENDING', "
        "RoleRequest.resolved_at.is_(None))",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )

    all_group_tags: Mapped[List[OktaGroupTagMap]] = relationship(
        "OktaGroupTagMap",
        back_populates="group",
        lazy="raise",
    )
    active_group_tags: Mapped[List[OktaGroupTagMap]] = relationship(
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


class RoleGroupMap(Base):
    __tablename__ = "role_group_map"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        autoincrement=True,
        primary_key=True,
    )
    role_group_id: Mapped[str] = mapped_column("role_id", String(50), ForeignKey("okta_group.id"))
    group_id: Mapped[str] = mapped_column("group_id", String(50), ForeignKey("okta_group.id"))
    is_owner: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=expression.false(), default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=func.now(), onupdate=func.now())
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime())

    created_actor_id: Mapped[Optional[str]] = mapped_column(String(50), ForeignKey("okta_user.id"))
    ended_actor_id: Mapped[Optional[str]] = mapped_column(String(50), ForeignKey("okta_user.id"))
    created_reason: Mapped[str] = mapped_column(String(1024), nullable=False, default="", server_default="")
    should_expire: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=expression.false(), default=False
    )

    role_group: Mapped[RoleGroup] = relationship(
        "RoleGroup",
        back_populates="all_role_associated_group_mappings",
        foreign_keys=[role_group_id],
        lazy="raise",
    )
    active_role_group: Mapped[RoleGroup] = relationship(
        "RoleGroup",
        primaryjoin="and_(remote(OktaGroup.id) == RoleGroupMap.role_group_id, " "OktaGroup.deleted_at.is_(None))",
        back_populates="active_role_associated_group_mappings",
        foreign_keys=[role_group_id],
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )

    group: Mapped[OktaGroup] = relationship(
        "OktaGroup",
        back_populates="all_role_mappings",
        foreign_keys=[group_id],
        lazy="raise",
    )
    active_group: Mapped[OktaGroup] = relationship(
        "OktaGroup",
        primaryjoin="and_(OktaGroup.id == RoleGroupMap.group_id, " "OktaGroup.deleted_at.is_(None))",
        back_populates="active_role_mappings",
        foreign_keys=[group_id],
        viewonly=True,
        lazy="select",
        innerjoin=True,
    )

    all_group_memberships_and_ownerships: Mapped[List[OktaUserGroupMember]] = relationship(
        "OktaUserGroupMember", back_populates="role_group_mapping", lazy="raise"
    )
    active_group_memberships_and_ownerships: Mapped[List[OktaUserGroupMember]] = relationship(
        "OktaUserGroupMember",
        primaryjoin="and_(RoleGroupMap.id == OktaUserGroupMember.role_group_map_id, "
        "or_(OktaUserGroupMember.ended_at.is_(None), OktaUserGroupMember.ended_at > func.now()))",
        back_populates="active_role_group_mapping",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )

    created_actor: Mapped[OktaUser] = relationship(
        "OktaUser", foreign_keys=[created_actor_id], lazy="raise", viewonly=True
    )

    ended_actor: Mapped[OktaUser] = relationship("OktaUser", foreign_keys=[ended_actor_id], lazy="raise", viewonly=True)

    role_request: Mapped[RoleRequest] = relationship(
        "RoleRequest",
        back_populates="approved_membership",
        lazy="raise",
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
    id: Mapped[str] = mapped_column(String(50), ForeignKey("okta_group.id"), primary_key=True)

    all_role_associated_group_mappings: Mapped[List[RoleGroupMap]] = relationship(
        "RoleGroupMap",
        back_populates="role_group",
        foreign_keys="RoleGroupMap.role_group_id",
        lazy="raise",
    )
    active_role_associated_group_mappings: Mapped[List[RoleGroupMap]] = relationship(
        "RoleGroupMap",
        primaryjoin="and_(OktaGroup.id == foreign(RoleGroupMap.role_group_id), "
        "or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > func.now()))",
        back_populates="active_role_group",
        foreign_keys="RoleGroupMap.role_group_id",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )
    active_role_associated_group_member_mappings: Mapped[List[RoleGroupMap]] = relationship(
        "RoleGroupMap",
        primaryjoin="and_(RoleGroup.id == foreign(RoleGroupMap.role_group_id), "
        "RoleGroupMap.is_owner.is_(False), "
        "or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > func.now()))",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )
    active_role_associated_group_owner_mappings: Mapped[List[RoleGroupMap]] = relationship(
        "RoleGroupMap",
        primaryjoin="and_(RoleGroup.id == foreign(RoleGroupMap.role_group_id), "
        "RoleGroupMap.is_owner.is_(True), "
        "or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > func.now()))",
        viewonly=True,
        lazy="raise",
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
    id: Mapped[str] = mapped_column(String(50), ForeignKey("okta_group.id"), primary_key=True)
    app_id: Mapped[str] = mapped_column(String(50), ForeignKey("app.id"), nullable=False)
    is_owner: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=expression.false(), default=False)

    app: Mapped["App"] = relationship("App", back_populates="app_groups", lazy="select")

    __mapper_args__ = {
        "polymorphic_identity": "app_group",
    }

    @validates("name")
    def validate_group(self, key: str, name: str) -> str:
        from sqlalchemy.orm import object_session

        session = object_session(self)
        if session:
            app = session.query(App).filter(App.id == self.app_id).filter(App.deleted_at.is_(None)).first()
            if app is None:
                raise ValueError(f"Specified App with app_id: {self.app_id} does not exist")
            app_group_name_prefix = (
                f"{AppGroup.APP_GROUP_NAME_PREFIX}{app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
            )
            if not name.startswith(app_group_name_prefix):
                raise ValueError(
                    'App Group name "{}" should be prefixed with App name. For example: "{}"'.format(
                        name, app_group_name_prefix
                    )
                )
        return name


class App(Base):
    __tablename__ = "app"

    ACCESS_APP_RESERVED_NAME = "Access"

    id: Mapped[str] = mapped_column(String(20), primary_key=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=func.now(), onupdate=func.now())
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime())
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(1024), nullable=False, default="")

    app_groups: Mapped[List[AppGroup]] = relationship("AppGroup", back_populates="app", lazy="raise")

    active_app_groups: Mapped[List[AppGroup]] = relationship(
        "AppGroup",
        primaryjoin="and_(App.id == AppGroup.app_id, AppGroup.deleted_at.is_(None))",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
        order_by="AppGroup.name",
    )

    active_owner_app_groups: Mapped[List[AppGroup]] = relationship(
        "AppGroup",
        primaryjoin="and_(App.id == AppGroup.app_id, " "AppGroup.deleted_at.is_(None), " "AppGroup.is_owner.is_(True))",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
        order_by="AppGroup.name",
    )

    active_non_owner_app_groups: Mapped[List[AppGroup]] = relationship(
        "AppGroup",
        primaryjoin="and_(App.id == AppGroup.app_id, "
        "AppGroup.deleted_at.is_(None), "
        "AppGroup.is_owner.is_(False))",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
        order_by="AppGroup.name",
    )

    all_app_tags: Mapped[List[AppTagMap]] = relationship(
        "AppTagMap",
        back_populates="app",
        lazy="raise",
    )
    active_app_tags: Mapped[List[AppTagMap]] = relationship(
        "AppTagMap",
        primaryjoin="and_(App.id == AppTagMap.app_id, "
        "or_(AppTagMap.ended_at.is_(None), AppTagMap.ended_at > func.now()))",
        back_populates="active_app",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )


class AccessRequestStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class AccessRequest(Base):
    __tablename__ = "access_request"

    id: Mapped[str] = mapped_column(String(20), primary_key=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=func.now(), onupdate=func.now())
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime())

    status: Mapped[AccessRequestStatus] = mapped_column(
        Enum(AccessRequestStatus),
        nullable=False,
        default=AccessRequestStatus.PENDING,
    )

    requester_user_id: Mapped[str] = mapped_column(String(50), ForeignKey("okta_user.id"))
    requested_group_id: Mapped[str] = mapped_column(String(50), ForeignKey("okta_group.id"))
    request_ownership: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    request_reason: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    request_ending_at: Mapped[Optional[datetime]] = mapped_column(DateTime())

    resolver_user_id: Mapped[Optional[str]] = mapped_column(String(50), ForeignKey("okta_user.id"))
    resolution_reason: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    approval_ending_at: Mapped[Optional[datetime]] = mapped_column(DateTime())

    approved_membership_id: Mapped[Optional[int]] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        ForeignKey("okta_user_group_member.id"),
    )

    requester: Mapped[OktaUser] = relationship(
        "OktaUser",
        back_populates="all_access_requests",
        foreign_keys=[requester_user_id],
        lazy="raise",
    )

    active_requester: Mapped[OktaUser] = relationship(
        "OktaUser",
        primaryjoin="and_(OktaUser.id == AccessRequest.requester_user_id, " "OktaUser.deleted_at.is_(None))",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )

    requested_group: Mapped[OktaGroup] = relationship(
        "OktaGroup",
        back_populates="all_access_requests",
        foreign_keys=[requested_group_id],
        lazy="raise",
    )

    active_requested_group: Mapped[OktaGroup] = relationship(
        "OktaGroup",
        primaryjoin="and_(OktaGroup.id == AccessRequest.requested_group_id, " "OktaGroup.deleted_at.is_(None))",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )

    resolver: Mapped[OktaUser] = relationship(
        "OktaUser",
        back_populates="all_resolved_access_requests",
        foreign_keys=[resolver_user_id],
        lazy="raise",
    )

    active_resolver: Mapped[OktaUser] = relationship(
        "OktaUser",
        primaryjoin="and_(OktaUser.id == AccessRequest.resolver_user_id, " "OktaUser.deleted_at.is_(None))",
        viewonly=True,
        lazy="raise",
    )

    approved_membership: Mapped[OktaUserGroupMember] = relationship(
        "OktaUserGroupMember",
        back_populates="access_request",
        foreign_keys=[approved_membership_id],
        lazy="raise",
    )


class RoleRequest(Base):
    __tablename__ = "role_request"

    id: Mapped[str] = mapped_column(String(20), primary_key=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=func.now(), onupdate=func.now())
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime())

    status: Mapped[AccessRequestStatus] = mapped_column(
        Enum(AccessRequestStatus),
        nullable=False,
        default=AccessRequestStatus.PENDING,
    )

    requester_user_id: Mapped[str] = mapped_column(String(50), ForeignKey("okta_user.id"))
    requester_role_id: Mapped[str] = mapped_column(String(50), ForeignKey("okta_group.id"))
    requested_group_id: Mapped[str] = mapped_column(String(50), ForeignKey("okta_group.id"))
    request_ownership: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    request_reason: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    request_ending_at: Mapped[Optional[datetime]] = mapped_column(DateTime())

    resolver_user_id: Mapped[Optional[str]] = mapped_column(String(50), ForeignKey("okta_user.id"))
    resolution_reason: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    approval_ending_at: Mapped[Optional[datetime]] = mapped_column(DateTime())

    approved_membership_id: Mapped[Optional[int]] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        ForeignKey("role_group_map.id"),
    )

    requester: Mapped[OktaUser] = relationship(
        "OktaUser",
        primaryjoin="OktaUser.id == RoleRequest.requester_user_id",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )

    active_requester: Mapped[OktaUser] = relationship(
        "OktaUser",
        primaryjoin="and_(OktaUser.id == RoleRequest.requester_user_id, " "OktaUser.deleted_at.is_(None))",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )

    requester_role: Mapped[OktaGroup] = relationship(
        "OktaGroup",
        back_populates="all_role_requests_from",
        foreign_keys=[requester_role_id],
        lazy="raise",
    )

    active_requester_role: Mapped[OktaGroup] = relationship(
        "OktaGroup",
        primaryjoin="and_(OktaGroup.id == RoleRequest.requested_group_id, " "OktaGroup.deleted_at.is_(None))",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )

    requested_group: Mapped[OktaGroup] = relationship(
        "OktaGroup",
        back_populates="all_role_requests_to",
        foreign_keys=[requested_group_id],
        lazy="raise",
    )

    active_requested_group: Mapped[OktaGroup] = relationship(
        "OktaGroup",
        primaryjoin="and_(OktaGroup.id == RoleRequest.requested_group_id, " "OktaGroup.deleted_at.is_(None))",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )

    resolver: Mapped[OktaUser] = relationship(
        "OktaUser",
        back_populates="all_resolved_role_requests",
        foreign_keys=[resolver_user_id],
        lazy="raise",
    )

    active_resolver: Mapped[OktaUser] = relationship(
        "OktaUser",
        primaryjoin="and_(OktaUser.id == RoleRequest.resolver_user_id, " "OktaUser.deleted_at.is_(None))",
        viewonly=True,
        lazy="raise",
    )

    approved_membership: Mapped[RoleGroupMap] = relationship(
        "RoleGroupMap",
        back_populates="role_request",
        foreign_keys=[approved_membership_id],
        lazy="raise",
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


class Tag(Base):
    __tablename__ = "tag"

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

    id: Mapped[str] = mapped_column(String(50), primary_key=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=func.now(), onupdate=func.now())
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime())
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=True)

    constraints: Mapped[Dict[str, Any]] = mapped_column(
        mutable_json_type(
            dbtype=JSON().with_variant(JSONB(), "postgresql"),
            nested=True,
        ),
        nullable=False,
        server_default="{}",
    )

    all_group_tags: Mapped[List[OktaGroupTagMap]] = relationship(
        "OktaGroupTagMap",
        back_populates="tag",
        lazy="raise",
    )
    active_group_tags: Mapped[List[OktaGroupTagMap]] = relationship(
        "OktaGroupTagMap",
        primaryjoin="and_(Tag.id == OktaGroupTagMap.tag_id, "
        "or_(OktaGroupTagMap.ended_at.is_(None), OktaGroupTagMap.ended_at > func.now()))",
        back_populates="active_tag",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )

    all_app_tags: Mapped[List[AppTagMap]] = relationship(
        "AppTagMap",
        back_populates="tag",
        lazy="raise",
    )
    active_app_tags: Mapped[List[AppTagMap]] = relationship(
        "AppTagMap",
        primaryjoin="and_(Tag.id == AppTagMap.tag_id, "
        "or_(AppTagMap.ended_at.is_(None), AppTagMap.ended_at > func.now()))",
        back_populates="active_tag",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )


class OktaGroupTagMap(Base):
    __tablename__ = "okta_group_tag_map"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        autoincrement=True,
        primary_key=True,
    )
    tag_id: Mapped[str] = mapped_column(String(50), ForeignKey("tag.id"))
    group_id: Mapped[str] = mapped_column(String(50), ForeignKey("okta_group.id"))
    app_tag_map_id: Mapped[Optional[int]] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        ForeignKey("app_tag_map.id"),
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=func.now(), onupdate=func.now())
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime())

    tag: Mapped[Tag] = relationship(
        "Tag",
        back_populates="all_group_tags",
        foreign_keys=[tag_id],
        lazy="raise",
    )
    active_tag: Mapped[Tag] = relationship(
        "Tag",
        primaryjoin="and_(Tag.id == OktaGroupTagMap.tag_id, " "Tag.deleted_at.is_(None))",
        back_populates="active_group_tags",
        viewonly=True,
        lazy="select",
        innerjoin=True,
    )
    enabled_active_tag: Mapped[Tag] = relationship(
        "Tag",
        primaryjoin="and_(Tag.id == OktaGroupTagMap.tag_id, " "Tag.deleted_at.is_(None), Tag.enabled.is_(True))",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )

    group: Mapped[OktaGroup] = relationship(
        "OktaGroup",
        back_populates="all_group_tags",
        lazy="raise",
    )
    active_group: Mapped[OktaGroup] = relationship(
        "OktaGroup",
        primaryjoin="and_(OktaGroup.id == OktaGroupTagMap.group_id, " "OktaGroup.deleted_at.is_(None))",
        back_populates="active_group_tags",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )

    app_tag_mapping: Mapped[AppTagMap] = relationship(
        "AppTagMap",
        back_populates="group_tag_mappings",
        foreign_keys=[app_tag_map_id],
        lazy="raise",
    )
    active_app_tag_mapping: Mapped[AppTagMap] = relationship(
        "AppTagMap",
        primaryjoin="and_(AppTagMap.id == OktaGroupTagMap.app_tag_map_id, "
        "or_(AppTagMap.ended_at.is_(None), AppTagMap.ended_at > func.now()))",
        back_populates="active_group_tag_mappings",
        foreign_keys=[app_tag_map_id],
        viewonly=True,
        lazy="select",
    )


class AppTagMap(Base):
    __tablename__ = "app_tag_map"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        autoincrement=True,
        primary_key=True,
    )
    tag_id: Mapped[str] = mapped_column(String(50), ForeignKey("tag.id"))
    app_id: Mapped[str] = mapped_column(String(50), ForeignKey("app.id"))

    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=func.now(), onupdate=func.now())
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime())

    tag: Mapped[Tag] = relationship(
        "Tag",
        back_populates="all_app_tags",
        foreign_keys=[tag_id],
        lazy="raise",
    )
    active_tag: Mapped[Tag] = relationship(
        "Tag",
        primaryjoin="and_(Tag.id == AppTagMap.tag_id, " "Tag.deleted_at.is_(None))",
        back_populates="active_app_tags",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )
    enabled_active_tag: Mapped[Tag] = relationship(
        "Tag",
        primaryjoin="and_(Tag.id == AppTagMap.tag_id, " "Tag.deleted_at.is_(None), Tag.enabled.is_(True))",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )

    app: Mapped[App] = relationship(
        "App",
        back_populates="all_app_tags",
        lazy="raise",
    )
    active_app: Mapped[App] = relationship(
        "App",
        primaryjoin="and_(App.id == AppTagMap.app_id, " "App.deleted_at.is_(None))",
        back_populates="active_app_tags",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )

    group_tag_mappings: Mapped[List[OktaGroupTagMap]] = relationship(
        "OktaGroupTagMap", back_populates="app_tag_mapping", lazy="raise"
    )
    active_group_tag_mappings: Mapped[List[OktaGroupTagMap]] = relationship(
        "OktaGroupTagMap",
        primaryjoin="and_(AppTagMap.id == OktaGroupTagMap.app_tag_map_id, "
        "or_(OktaGroupTagMap.ended_at.is_(None), OktaGroupTagMap.ended_at > func.now()))",
        back_populates="active_app_tag_mapping",
        viewonly=True,
        lazy="raise",
        innerjoin=True,
    )
