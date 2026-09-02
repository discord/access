import {timeLimitLabel} from '../../constraints';

// Labels and help text for the six tag constraints, shared by the tag form and
// the tag page so the two never name the same constraint differently.
//
// The behavioural model all of this copy conveys, which is easy to get
// backwards: every constraint splits the same way.
//
//   Base, always. The constraint governs access *to the tagged group*, by
//   either route -- a user's own membership or ownership, and the association
//   of a role that carries its members in. Time limits cap both the
//   `OktaUserGroupMember` and the `RoleGroupMap` rows pointing at the group;
//   reason and self-add gate adding a user and attaching a role alike.
//
//   Propagation adds. The same constraint governs membership *of the role
//   itself*, so the role's roster is forced through the same review its access
//   to the group already gets.
//
// So "without propagation" is not "no constraint": the role's access to the
// group is still bounded, and what is unbounded is who is in the role. And the
// owner side crosses axes -- a tag's owner limit reaches roles that *own* the
// tagged group and lands on those roles' *membership*, because being a member
// of the role is what confers the ownership (`OWNER_SIDE_COUNTERPART` in
// `api/models/tag.py`).

/**
 * One paragraph of help text.
 *
 * `lead` renders bold immediately before `text` with no separator, so `text`
 * carries its own leading punctuation:
 * `{lead: 'With propagation to roles', text: ', the same limit applies…'}`.
 */
export interface HelpParagraph {
  lead?: string;
  text: string;
}

export const MEMBER_TIME_LIMIT = 'member_time_limit';
export const OWNER_TIME_LIMIT = 'owner_time_limit';
export const REQUIRE_MEMBER_REASON = 'require_member_reason';
export const REQUIRE_OWNER_REASON = 'require_owner_reason';
export const DISALLOW_SELF_ADD_MEMBERSHIP = 'disallow_self_add_membership';
export const DISALLOW_SELF_ADD_OWNERSHIP = 'disallow_self_add_ownership';

// Punctuation lives in the label rather than being appended by the form. All
// four boolean constraints read as questions, since each is a yes/no setting
// answered by a Yes/No toggle; the two time limits take a number and so are
// not phrased as questions.
export const CONSTRAINT_LABELS: Record<string, string> = {
  [MEMBER_TIME_LIMIT]: 'Member time limit',
  [OWNER_TIME_LIMIT]: 'Owner time limit',
  [REQUIRE_MEMBER_REASON]: 'Require membership justification?',
  [REQUIRE_OWNER_REASON]: 'Require ownership justification?',
  [DISALLOW_SELF_ADD_MEMBERSHIP]: 'Disallow adding oneself as a member?',
  [DISALLOW_SELF_ADD_OWNERSHIP]: 'Disallow adding oneself as an owner?',
};

// Shown on the tag page when a tag stores no constraint that is in force.
// More useful than an empty-list dash: a tag with no constraints is a
// perfectly valid thing to have, and saying so beats leaving the reader to
// wonder whether the page failed to load them.
export const NO_CONSTRAINTS_NOTE = 'This tag is just a label and does not apply any constraints to tagged groups.';

const RECERTIFICATION_NOTE = 'This constraint is typically used to enforce auditable periodic access recertification.';
const AUDIT_TRAIL_NOTE = 'This constraint is typically used to enforce an audit trail.';

const WITH_PROPAGATION = 'With propagation to roles';
const WITHOUT_PROPAGATION = 'Without propagation';
const REQUIRES_PROPAGATION = 'This constraint requires propagation to roles';

const EDIT_HELP: Record<string, HelpParagraph[]> = {
  [MEMBER_TIME_LIMIT]: [
    {
      text:
        'Limits how long membership in a group carrying this tag can last. This covers both ways membership is ' +
        'held: as an individual user or as a member of a role. Memberships longer than the limit are shortened ' +
        'when the tag is applied.',
    },
    {text: RECERTIFICATION_NOTE},
    {
      lead: WITH_PROPAGATION,
      text:
        ", the same limit applies to users' membership in those roles, so nobody sits in a role indefinitely " +
        "while that role's membership in the group requires regular renewal.",
    },
    {
      lead: WITHOUT_PROPAGATION,
      text: ", the role's membership in the tagged group is time-bound, but its own roster is not.",
    },
  ],
  [OWNER_TIME_LIMIT]: [
    {
      text:
        'Limits how long ownership of a group carrying this tag can last. This covers both ways ownership is ' +
        'held: as an individual user or as a member of a role. Ownerships longer than the limit are shortened ' +
        'when the tag is applied.',
    },
    {text: RECERTIFICATION_NOTE},
    {
      lead: WITH_PROPAGATION,
      text:
        ", the same limit applies to users' membership in those roles, so nobody sits in a role indefinitely " +
        "while that role's ownership of the group requires regular renewal.",
    },
    {
      lead: WITHOUT_PROPAGATION,
      text:
        ", the role's ownership of the tagged group is time-bound, but its own roster is not. So only half of " +
        "the rationale for a user's access must be periodically reevaluated.",
    },
  ],
  [REQUIRE_OWNER_REASON]: [
    {
      text:
        'Requires a reason when granting ownership of a group carrying this tag to either an individual user or ' +
        'members of a role, via either a direct add or access request approval.',
    },
    {text: AUDIT_TRAIL_NOTE},
    {
      lead: WITH_PROPAGATION,
      text:
        ', a reason is also required to add someone to a role that owns such a group, ensuring a complete audit ' +
        'record of why each individual gained ownership of the group.',
    },
    {
      lead: WITHOUT_PROPAGATION,
      text:
        ", the audit trail may only show why a user's role was given ownership and not why they had that " +
        'role—half the story.',
    },
  ],
  [REQUIRE_MEMBER_REASON]: [
    {
      text:
        'Requires a reason when granting membership in a group carrying this tag to either an individual user ' +
        'or members of a role, via either a direct add or access request approval.',
    },
    {text: AUDIT_TRAIL_NOTE},
    {
      lead: WITH_PROPAGATION,
      text:
        ", a reason is also required to add someone to a role that's in such a group, ensuring a complete audit " +
        'record of why each individual gained membership in the group.',
    },
    {
      lead: WITHOUT_PROPAGATION,
      text:
        ", the audit trail may only show why a user's role was given membership and not why they had that " +
        'role—half the story.',
    },
  ],
  // The self-add pair has no "without propagation" paragraph: `#617` rejects
  // that combination on every write, so there is no such behaviour to describe.
  // Saying why here is the point -- an admin meets the rule while deciding,
  // rather than bouncing off the inline conflict message after toggling.
  [DISALLOW_SELF_ADD_OWNERSHIP]: [
    {
      text:
        'Prevents someone from granting themselves ownership of a group carrying this tag, whether by adding ' +
        'themselves directly or by attaching a role they belong to as an owner.',
    },
    {
      lead: REQUIRES_PROPAGATION,
      text: ', which stops them joining a role that already owns the group and sidestepping the restriction entirely.',
    },
  ],
  [DISALLOW_SELF_ADD_MEMBERSHIP]: [
    {
      text:
        'Prevents someone from granting themselves membership in a group carrying this tag, whether by adding ' +
        'themselves directly or by attaching a role they belong to as a member.',
    },
    {
      lead: REQUIRES_PROPAGATION,
      text: ", which stops them joining a role that's already in the group and sidestepping the restriction entirely.",
    },
  ],
};

const ROSTER_UNBOUNDED = 'Membership in those roles is not limited by this tag and so may not require periodic review.';

function timeLimitRead(side: 'member' | 'owner', seconds: number, propagateToRoles: boolean): HelpParagraph[] {
  const held =
    side === 'member'
      ? 'Membership in a group carrying this tag is limited to'
      : 'Ownership of a group carrying this tag is limited to';
  const label = timeLimitLabel(seconds);
  const bounded = `${held} ${label}, whether held as an individual user or as a member of a role.`;
  return [
    {
      text: propagateToRoles
        ? `${bounded} Membership in those roles is limited to ${label} too.`
        : `${bounded} ${ROSTER_UNBOUNDED}`,
    },
  ];
}

function reasonRead(side: 'member' | 'owner', propagateToRoles: boolean): HelpParagraph[] {
  const granting =
    side === 'member'
      ? 'A reason is required to grant membership in a group carrying this tag, to an individual user or to the ' +
        'members of a role.'
      : 'A reason is required to grant ownership of a group carrying this tag, to an individual user or to the ' +
        'members of a role.';
  const role = side === 'member' ? "a role that's in such a group" : 'a role that owns such a group';
  const gained = side === 'member' ? 'membership' : 'ownership';
  return [
    {
      text: propagateToRoles
        ? `${granting} Adding someone to ${role} also requires a reason, so the audit record shows why each ` +
          `individual gained ${gained}.`
        : `${granting} Adding someone to ${role} does not, so the record may show why the role was given ` +
          `${gained} but not why the user was in the role.`,
    },
  ];
}

function selfAddRead(side: 'member' | 'owner', propagateToRoles: boolean): HelpParagraph[] {
  const granting =
    side === 'member'
      ? 'Someone cannot grant themselves membership in a group carrying this tag'
      : 'Someone cannot grant themselves ownership of a group carrying this tag';
  const joining = side === 'member' ? "a role that's already in the group" : 'a role that already owns the group';
  if (propagateToRoles) {
    return [
      {
        text: `${granting}: not directly, not by attaching a role they belong to, and not by joining ${joining}.`,
      },
    ];
  }
  // `#617` rejects this combination on every write and the column shipped
  // defaulting to on, so only a direct database edit reaches it. Kept as a
  // guardrail: if such a tag exists, its reader is exactly the person who needs
  // to know the restriction is being sidestepped. The closing clause is a nudge
  // rather than a description, deliberately unlike the rest of this copy.
  return [
    {
      text:
        `${granting} directly, or by attaching a role they belong to. Joining ${joining} is not blocked, which ` +
        'sidesteps the restriction entirely; this tag should propagate to roles.',
    },
  ];
}

/**
 * Help text for one constraint on the tag form, covering both propagation cases.
 *
 * Static, because the form describes what each setting would do rather than
 * what the tag currently does; nothing here watches the propagation toggle.
 *
 * @param key A key from `Tag.CONSTRAINTS`.
 * @returns The paragraphs to render, or `[]` for an unrecognised key.
 */
export function constraintEditHelp(key: string): HelpParagraph[] {
  return EDIT_HELP[key] ?? [];
}

/**
 * Help text for one constraint on the tag page, covering only what is in force.
 *
 * @param key A key from `Tag.CONSTRAINTS`.
 * @param opts.propagateToRoles The tag's `propagate_to_roles`, already
 *   defaulted -- pass `tag.propagate_to_roles ?? true`, since the field is
 *   optional in the generated type and the server default is `true`.
 * @param opts.value The tag's stored value for this constraint: seconds for a
 *   time limit, a flag otherwise.
 * @returns The paragraphs to render. `[]` for an unrecognised key, for a flag
 *   that is switched off (nothing is in force, so there is nothing to say), and
 *   for a time limit whose value is not a number.
 */
export function constraintReadHelp(
  key: string,
  {propagateToRoles, value}: {propagateToRoles: boolean; value: number | boolean},
): HelpParagraph[] {
  // Discriminated on `=== false`, not on falsiness: only a flag can be switched
  // off, and a falsy *number* is the tightest possible limit rather than the
  // absence of one. Same test as `_constraint_entry` in `api/models/tag.py`.
  if (value === false) {
    return [];
  }
  switch (key) {
    case MEMBER_TIME_LIMIT:
      return typeof value === 'number' ? timeLimitRead('member', value, propagateToRoles) : [];
    case OWNER_TIME_LIMIT:
      return typeof value === 'number' ? timeLimitRead('owner', value, propagateToRoles) : [];
    case REQUIRE_MEMBER_REASON:
      return reasonRead('member', propagateToRoles);
    case REQUIRE_OWNER_REASON:
      return reasonRead('owner', propagateToRoles);
    case DISALLOW_SELF_ADD_MEMBERSHIP:
      return selfAddRead('member', propagateToRoles);
    case DISALLOW_SELF_ADD_OWNERSHIP:
      return selfAddRead('owner', propagateToRoles);
    default:
      return [];
  }
}

/**
 * Whether a stored constraint value is actually in force.
 *
 * The tag form writes all four boolean keys on every save, so a tag typically
 * stores several that are switched off. Those are not constraints and are left
 * out of the tag page's table.
 */
export function isConstraintInForce(value: number | boolean): boolean {
  return value !== false;
}
