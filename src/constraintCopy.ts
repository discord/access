// The words for the six tag constraints, in one place.
//
// Every surface that names a constraint reads from here: the effective-constraints
// panel, the tag page, and the tag form. Naming the same constraint differently on
// two pages is the confusion this module exists to prevent, so a new surface looks
// a label up rather than writing its own.
//
// The values themselves — which tags are enabled, how they coalesce, which reach a
// role — are resolved server-side and read through `constraints.ts`. Nothing here
// knows anything about a particular tag; these are the names of the six settings.

/**
 * Display names, keyed by the constraint keys in `Tag.CONSTRAINTS` (`api/models/core_models.py`).
 *
 * Sentence case, no trailing punctuation: a label is a noun phrase, and the control
 * or cell beside it supplies the rest. Membership and ownership are named the same
 * way in each pair so the six read as three settings with two sides.
 */
export const CONSTRAINT_LABELS: Record<string, string> = {
  member_time_limit: 'Membership time limit',
  owner_time_limit: 'Ownership time limit',
  require_member_reason: 'Require a membership reason',
  require_owner_reason: 'Require an ownership reason',
  disallow_self_add_membership: 'Disallow adding oneself as a member',
  disallow_self_add_ownership: 'Disallow adding oneself as an owner',
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
  'member_time_limit',
  'owner_time_limit',
  'require_member_reason',
  'require_owner_reason',
  'disallow_self_add_membership',
  'disallow_self_add_ownership',
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
