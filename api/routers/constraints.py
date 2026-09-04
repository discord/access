"""Effective-constraints router.

One place to ask "what constraints apply here?", so the dialogs where
someone chooses an access duration or types a justification read the same
answer the enforcement paths do. Deciding it client-side means transcribing
`api.models.tag` -- which tags are enabled, which reach a role through its
associations, and the minimum or logical OR across them -- into TypeScript once
per dialog, and every copy is free to drift from the rule it mirrors.

Deliberately not a field on the group/audit list endpoints. `effective_constraints`
needs the association and tag eager loads for every group it touches, so
hanging it off a paginated list would pay that cost for every row of every page
render. Here it is paid once per dialog interaction, for the ids actually
selected, and bounded by `EffectiveConstraintsQuery`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from sqlalchemy import select
from sqlalchemy.orm import selectin_polymorphic, selectinload

from api.auth.dependencies import CurrentUserId
from api.database import DbSession
from api.models import AppGroup, OktaGroup, RoleGroup, Tag
from api.models.tag import (
    effective_constraints,
    effective_constraints_across_groups,
    effective_constraints_for_tags,
)
from api.routers._eager import effective_constraint_options, group_tag_map_options
from api.schemas import (
    EffectiveConstraintDetail,
    EffectiveConstraintsQuery,
    EffectiveConstraintsResponse,
)

router = APIRouter(prefix="/api/constraints", tags=["constraints"])


def _details(entries: list[dict]) -> list[EffectiveConstraintDetail]:
    """Validate raw `api.models.tag` entries into their response schema."""
    return [EffectiveConstraintDetail.model_validate(entry) for entry in entries]


@router.get("/effective", name="effective_constraints")
async def get_effective_constraints(
    db: DbSession,
    current_user_id: CurrentUserId,
    q_args: Annotated[EffectiveConstraintsQuery, Query()],
) -> EffectiveConstraintsResponse:
    """Report the constraints in force over a set of groups, or over a set of tags.

    Group mode answers "what binds the things I have selected?", returning both
    the roll-up across the whole selection and a per-group breakdown. Tag mode
    answers "what would these tags impose?" for a group that does not exist yet
    -- a group request being approved -- and so has no per-group breakdown.

    Name exactly one of `group_ids` or `tag_ids`; naming both or neither is a
    400. Ids naming a deleted or nonexistent record are dropped, so an entirely
    stale selection yields an empty answer rather than an error.
    """
    # FastAPI publishes this docstring as the endpoint's OpenAPI description,
    # which reaches the generated TypeScript client and any docs page, so it is
    # written for a caller of the HTTP endpoint. Notes for a reader of this
    # function belong in comments like this one, and the interface
    # documentation for the work itself lives on the `api.models.tag` helpers.
    if bool(q_args.group_ids) == bool(q_args.tag_ids):
        raise HTTPException(400, "Provide exactly one of group_ids or tag_ids")

    if q_args.tag_ids:
        tags = list(
            (await db.scalars(select(Tag).where(Tag.id.in_(q_args.tag_ids)).where(Tag.deleted_at.is_(None)))).all()
        )
        return EffectiveConstraintsResponse(coalesced=_details(effective_constraints_for_tags(tags)))

    groups = list(
        (
            await db.scalars(
                select(OktaGroup)
                .options(
                    # Mirrors `get_group`'s loaders: the subtype (so a
                    # RoleGroup's associations are reachable), the group's own
                    # tags with app provenance, and the association chain.
                    # Every one of these is `lazy="raise_on_sql"`.
                    selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
                    selectinload(OktaGroup.active_group_tags).options(*group_tag_map_options()),
                    *effective_constraint_options(),
                )
                # Ids naming a group that no longer exists are dropped rather
                # than failing the request: a dialog's selection is built from
                # a list render that may predate a deletion, and one stale id
                # should not blank the answer for the rest.
                .where(OktaGroup.id.in_(q_args.group_ids))
                .where(OktaGroup.deleted_at.is_(None))
            )
        ).all()
    )

    return EffectiveConstraintsResponse(
        coalesced=_details(effective_constraints_across_groups(groups)),
        by_group={group.id: _details(effective_constraints(group)) for group in groups},
    )
