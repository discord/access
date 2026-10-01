// The words for the six tag constraints, in one place.
//
// A surface that names a constraint looks it up here rather than writing its own.
// The effective-constraints panel on a group links to the tag page that carries
// each constraint, and the two calling the same setting different things leaves a
// reader following that link with no way to tell it is the same setting.
//
// The values themselves — which tags are enabled, how they coalesce, which reach a
// role — are resolved server-side and read through `constraints.ts`. Nothing here
// knows anything about a particular tag; these are the names of the six settings.

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

/**
 * The display name for one constraint.
 *
 * @param key A key from `Tag.CONSTRAINTS`.
 * @returns The label, or `key` itself when this build has no copy for it — a
 *   backend serving a seventh constraint should show its key rather than a blank
 *   cell, which is what a bare lookup would render.
 */
export function constraintLabel(key: string): string {
  return CONSTRAINT_LABELS[key] ?? key;
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
