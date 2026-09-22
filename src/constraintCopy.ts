// The words for the six tag constraints, in one place.
//
// Every surface that names or explains a constraint reads from here: the
// effective-constraints panel, the tag page, the tag form, and the help guide.
// Naming the same constraint differently on two pages is the confusion this module
// exists to prevent, so a new surface looks copy up rather than writing its own.
//
// The values themselves (which tags are enabled, how they coalesce, which reach a
// role) are resolved server-side and read through `constraints.ts`. Nothing here
// knows anything about a particular tag; these are the names of the six settings.
//
// The behavioural model all of this copy conveys, which is easy to get backwards:
// every constraint splits the same way.
//
//   Base, always. The constraint governs access *to the tagged group*, by either
//   route -- a user's own membership or ownership, and the association of a role
//   that carries its members in.
//
//   Propagation adds. The same constraint governs membership *of the role itself*,
//   so the role's roster is forced through the same review its access to the group
//   already gets.
//
// So the narrower scope is not "no constraint": the role's access to the group is
// still bounded, and what is unbounded is who is in the role. The owner side
// crosses axes -- an owner limit reaches roles that *own* the tagged group and
// lands on those roles' *membership*, because being a member of the role is what
// confers the ownership (`OWNER_SIDE_COUNTERPART` in `api/models/tag.py`).

/**
 * One paragraph of longform help.
 *
 * `lead` renders bold immediately before `text` with no separator, so `text`
 * carries its own leading punctuation:
 * `{lead: 'When it reaches roles', text: ', the same limit applies…'}`.
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

/**
 * Display names, keyed by the constraint keys in `Tag.CONSTRAINTS` (`api/models/core_models.py`).
 *
 * Sentence case, no trailing punctuation: a label is a noun phrase, and the control
 * or cell beside it supplies the rest. Membership and ownership are named the same
 * way in each pair so the six read as three settings with two sides.
 */
export const CONSTRAINT_LABELS: Record<string, string> = {
  [MEMBER_TIME_LIMIT]: 'Membership time limit',
  [OWNER_TIME_LIMIT]: 'Ownership time limit',
  [REQUIRE_MEMBER_REASON]: 'Require a membership reason',
  [REQUIRE_OWNER_REASON]: 'Require an ownership reason',
  [DISALLOW_SELF_ADD_MEMBERSHIP]: 'Disallow adding oneself as a member',
  [DISALLOW_SELF_ADD_OWNERSHIP]: 'Disallow adding oneself as an owner',
};

/**
 * The row label for one side of a constraint pair, for a layout that already names
 * the sides in column headers and would otherwise repeat "membership" six times.
 */
export const CONSTRAINT_ROW_LABELS: Record<string, string> = {
  timeLimit: 'Time limit',
  requireReason: 'Require a reason',
  disallowSelfAdd: 'Disallow self-add',
};

/**
 * The order constraints are presented in, wherever more than one is listed.
 *
 * Listings are otherwise at the mercy of their source: the tag page renders
 * `Object.keys(tag.constraints)`, which is the order the form happened to write them
 * in, so two tags carrying identical constraints can list them differently. Sorting
 * by this makes every listing agree.
 *
 * Membership leads each pair because it is the common case; ownership is the
 * privileged one.
 */
export const CONSTRAINT_ORDER: string[] = [
  MEMBER_TIME_LIMIT,
  OWNER_TIME_LIMIT,
  REQUIRE_MEMBER_REASON,
  REQUIRE_OWNER_REASON,
  DISALLOW_SELF_ADD_MEMBERSHIP,
  DISALLOW_SELF_ADD_OWNERSHIP,
];

/** Shown on the tag page when a tag stores no constraint that is in force. */
export const NO_CONSTRAINTS_NOTE = 'This tag is just a label and does not apply any constraints to tagged groups.';

/** How the two scope options are named wherever a tag's scope is stated or chosen. */
export const SCOPE_LABELS = {
  roles: 'Tagged groups and their owner/member roles',
  groupsOnly: 'Tagged groups only',
};

export const SCOPE_SUMMARIES = {
  roles: 'Membership of those roles is held to the same constraints.',
  groupsOnly: "A role's access to the tagged group is governed, but who is in the role is not.",
};

const RECERTIFICATION_NOTE = 'Typically used to enforce auditable periodic access recertification.';
const AUDIT_TRAIL_NOTE = 'Typically used to enforce an audit trail.';

const REACHES_ROLES = 'When the tag reaches roles';
const GROUPS_ONLY = 'When it applies to tagged groups only';

// The one-line helper shown beneath a control or beside a value. Deliberately
// silent about the constraint's own value, which every surface already displays
// next to this line; what it adds is what *else* the setting governs.
const SUMMARIES: Record<string, {roles: string; groupsOnly: string}> = {
  [MEMBER_TIME_LIMIT]: {
    roles: 'Also caps membership of the roles in the group.',
    groupsOnly: "A role's membership in the group is capped, but users' membership in the role is not.",
  },
  [OWNER_TIME_LIMIT]: {
    roles: 'Also caps membership of the roles that own the group.',
    groupsOnly: "A role's ownership is capped, but users' membership in the role is not.",
  },
  [REQUIRE_MEMBER_REASON]: {
    roles: 'Also required to add someone to a role in the group.',
    groupsOnly: 'Not required to add someone to a role in the group.',
  },
  [REQUIRE_OWNER_REASON]: {
    roles: 'Also required to add someone to a role that owns the group.',
    groupsOnly: 'Not required to add someone to a role that owns the group.',
  },
  [DISALLOW_SELF_ADD_MEMBERSHIP]: {
    roles: 'Covers joining a role that is already in the group.',
    groupsOnly: 'Unavailable in this scope, since joining a role would sidestep it.',
  },
  [DISALLOW_SELF_ADD_OWNERSHIP]: {
    roles: 'Covers joining a role that already owns the group.',
    groupsOnly: 'Unavailable in this scope, since joining a role would sidestep it.',
  },
};

const DETAILS: Record<string, {base: HelpParagraph[]; roles: string; groupsOnly: string}> = {
  [MEMBER_TIME_LIMIT]: {
    base: [
      {
        text:
          'Caps how long anyone holds membership in a group carrying this tag, whether as an individual or ' +
          'through a role. Memberships already longer than the limit are shortened when the tag is applied; ' +
          'nothing is ever extended.',
      },
      {text: RECERTIFICATION_NOTE},
    ],
    roles:
      ', the same limit applies to membership of those roles, so nobody sits in a role indefinitely while ' +
      "that role's access to the group requires regular renewal.",
    groupsOnly:
      ", the role's membership in the group is time-bound but its roster is not, so only half of the " +
      "rationale for a user's access is revisited.",
  },
  [OWNER_TIME_LIMIT]: {
    base: [
      {
        text:
          'Caps how long anyone holds ownership of a group carrying this tag, whether as an individual or ' +
          'through a role. Ownerships already longer than the limit are shortened when the tag is applied.',
      },
      {text: RECERTIFICATION_NOTE},
    ],
    roles:
      ', the limit also applies to membership of the roles that own the group: being a member of the role ' +
      'is what confers the ownership.',
    groupsOnly:
      ", the role's ownership of the group is time-bound but its roster is not, so only half of the " +
      "rationale for a user's access is revisited.",
  },
  [REQUIRE_MEMBER_REASON]: {
    base: [
      {
        text:
          'Requires a reason when granting membership in a group carrying this tag, to an individual or to ' +
          'the members of a role, by a direct add or by approving a request.',
      },
      {text: AUDIT_TRAIL_NOTE},
    ],
    roles:
      ', adding someone to a role in the group requires a reason too, so the record shows why ' +
      'each individual gained membership.',
    groupsOnly:
      ', the record may show why the role was given membership but not why the user was in the role—only ' +
      'half the story.',
  },
  [REQUIRE_OWNER_REASON]: {
    base: [
      {
        text:
          'Requires a reason when granting ownership of a group carrying this tag, to an individual or to ' +
          'the members of a role, by a direct add or by approving a request.',
      },
      {text: AUDIT_TRAIL_NOTE},
    ],
    roles: ', adding someone to a role that owns the group requires a reason too.',
    groupsOnly:
      ', the record may show why the role was given ownership but not why the user was in the role—only ' +
      'half the story.',
  },
  // The self-add pair reads the same either way, because the narrower scope is not
  // a second behaviour to describe: the backend rejects it on every write
  // (`PROPAGATION_REQUIRED_CONSTRAINT_KEYS` in `api/models/tag.py`). Saying why is
  // the point -- an admin meets the rule while deciding, rather than bouncing off
  // a disabled control with no explanation.
  [DISALLOW_SELF_ADD_MEMBERSHIP]: {
    base: [
      {
        text:
          'Prevents someone granting themselves membership in a group carrying this tag, whether by adding ' +
          'themselves directly or by attaching a role they belong to.',
      },
    ],
    roles:
      ', joining a role already in the group is blocked too. Otherwise, the restriction is sidestepped ' +
      'entirely, which is why it is only available on a tag that reaches roles.',
    groupsOnly:
      ', joining a role already in the group would sidestep the restriction entirely, so this constraint ' +
      'is only available on a tag that reaches roles.',
  },
  [DISALLOW_SELF_ADD_OWNERSHIP]: {
    base: [
      {
        text:
          'Prevents someone granting themselves ownership of a group carrying this tag, whether by adding ' +
          'themselves directly or by attaching a role they belong to.',
      },
    ],
    roles:
      ', joining a role that already owns the group is blocked too. Otherwise, the restriction is ' +
      'sidestepped entirely, which is why it is only available on a tag that reaches roles.',
    groupsOnly:
      ', joining a role that already owns the group would sidestep the restriction entirely, so this ' +
      'constraint is only available on a tag that reaches roles.',
  },
};

/**
 * The display name for one constraint.
 *
 * @param key A key from `Tag.CONSTRAINTS`.
 * @returns The label, or `key` itself when this build has no copy for it; a
 *   backend serving a seventh constraint should show its key rather than a blank
 *   cell, which is what a bare lookup would render.
 */
export function constraintLabel(key: string): string {
  return CONSTRAINT_LABELS[key] ?? key;
}

/**
 * The one-line helper for a constraint, given the scope the tag applies at.
 *
 * @param key A key from `Tag.CONSTRAINTS`.
 * @param propagates The tag's `propagate_to_roles`, already defaulted.
 * @returns A single sentence, or `''` for an unrecognised key; a surface renders
 *   nothing rather than a gap where a sentence should be.
 */
export function constraintSummary(key: string, propagates: boolean): string {
  const summary = SUMMARIES[key];
  if (summary == null) {
    return '';
  }
  return propagates ? summary.roles : summary.groupsOnly;
}

/**
 * The longform explanation for a constraint, given the scope the tag applies at.
 *
 * @param key A key from `Tag.CONSTRAINTS`.
 * @param propagates The tag's `propagate_to_roles`, already defaulted. Omit where
 *   the scope is not a single known value (the effective-constraints panel
 *   coalesces across several tags that may disagree), and the closing paragraph
 *   about scope is left off rather than guessed.
 * @returns The paragraphs to render, or `[]` for an unrecognised key.
 */
export function constraintDetail(key: string, propagates?: boolean): HelpParagraph[] {
  const detail = DETAILS[key];
  if (detail == null) {
    return [];
  }
  if (propagates == null) {
    return detail.base;
  }
  return [
    ...detail.base,
    {lead: propagates ? REACHES_ROLES : GROUPS_ONLY, text: propagates ? detail.roles : detail.groupsOnly},
  ];
}

/**
 * Comparator ordering constraint keys by `CONSTRAINT_ORDER`.
 *
 * Keys absent from that list sort last, in the order they arrived, so an
 * unrecognised constraint is listed rather than dropped.
 */
export function byConstraintOrder(a: string, b: string): number {
  const rank = (key: string) => {
    const index = CONSTRAINT_ORDER.indexOf(key);
    return index === -1 ? CONSTRAINT_ORDER.length : index;
  };
  return rank(a) - rank(b);
}

/**
 * Whether a stored constraint value is actually in force.
 *
 * The tag form writes all four boolean keys on every save, so a tag typically
 * stores several that are switched off. Those are not constraints and are left out
 * of any listing.
 *
 * Discriminated on `=== false`, not on falsiness: only a flag can be switched off,
 * and a falsy *number* is the tightest possible limit rather than the absence of
 * one. Same test as `_constraint_entry` in `api/models/tag.py`.
 */
export function isConstraintInForce(value: number | boolean): boolean {
  return value !== false;
}
