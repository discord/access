// What saving a tag edit will do to access that already exists.
//
// Editing a tag is a bulk mutation: `ModifyGroupsTimeLimit` shortens every grant
// longer than a new limit, across every group carrying the tag and every app group
// inheriting it. Loosening a tag does none of that, so the warning is worth showing
// only when something actually tightens.

import {constraintLabel, DISALLOW_SELF_ADD_MEMBERSHIP, DISALLOW_SELF_ADD_OWNERSHIP} from '../../constraintCopy';

/** A tag's constraint settings, in the units the form works in. */
export interface TagSettings {
  /** Days, or undefined for no limit. */
  memberTimeLimitDays?: number;
  ownerTimeLimitDays?: number;
  requireMemberReason: boolean;
  requireOwnerReason: boolean;
  disallowSelfAddMembership: boolean;
  disallowSelfAddOwnership: boolean;
  propagatesToRoles: boolean;
}

const days = (n: number) => `${n} day${n === 1 ? '' : 's'}`;

/**
 * Whether a time limit is tighter than it was.
 *
 * Removing a limit loosens; adding one where there was none tightens, since every
 * existing grant is then longer than the limit.
 */
function limitTightened(before: number | undefined, after: number | undefined): boolean {
  if (after == null) {
    return false;
  }
  return before == null || after < before;
}

/**
 * The consequences of saving, one sentence each.
 *
 * Time limits come first and are phrased as what happens to existing access,
 * because that is the part that changes access already granted. The rest gate
 * future changes, so they are phrased that way.
 *
 * @param before The tag as saved.
 * @param after The tag as the form currently stands.
 * @returns Sentences to list, or `[]` when nothing tightens, in which case there is
 *   nothing to warn about.
 */
export function tighteningEffects(before: TagSettings, after: TagSettings): string[] {
  const effects: string[] = [];
  // A limit reaches a role's roster only while the tag reaches roles, and saying
  // so is the difference between shortening one grant and shortening a whole
  // role's membership.
  const alsoRoles = (whichRoles: string) =>
    after.propagatesToRoles ? `, and so is membership of the roles that ${whichRoles} tagged groups` : '';

  if (limitTightened(before.memberTimeLimitDays, after.memberTimeLimitDays)) {
    effects.push(`Membership longer than ${days(after.memberTimeLimitDays!)} is shortened${alsoRoles('reach')}.`);
  }
  if (limitTightened(before.ownerTimeLimitDays, after.ownerTimeLimitDays)) {
    effects.push(`Ownership longer than ${days(after.ownerTimeLimitDays!)} is shortened${alsoRoles('own')}.`);
  }

  // A tag that starts reaching roles applies every limit it already carries to
  // those roles' rosters, which the limit comparisons above do not cover: the
  // limits themselves are unchanged.
  if (after.propagatesToRoles && !before.propagatesToRoles) {
    const unchangedLimits =
      (after.memberTimeLimitDays != null && !limitTightened(before.memberTimeLimitDays, after.memberTimeLimitDays)) ||
      (after.ownerTimeLimitDays != null && !limitTightened(before.ownerTimeLimitDays, after.ownerTimeLimitDays));
    effects.push(
      unchangedLimits
        ? 'These constraints start applying to the roles that reach tagged groups, shortening membership of those roles.'
        : 'These constraints start applying to the roles that reach tagged groups.',
    );
  }

  if (after.requireMemberReason && !before.requireMemberReason) {
    effects.push('A reason becomes required to grant membership.');
  }
  if (after.requireOwnerReason && !before.requireOwnerReason) {
    effects.push('A reason becomes required to grant ownership.');
  }
  if (after.disallowSelfAddMembership && !before.disallowSelfAddMembership) {
    effects.push(`“${constraintLabel(DISALLOW_SELF_ADD_MEMBERSHIP)}” starts applying.`);
  }
  if (after.disallowSelfAddOwnership && !before.disallowSelfAddOwnership) {
    effects.push(`“${constraintLabel(DISALLOW_SELF_ADD_OWNERSHIP)}” starts applying.`);
  }

  return effects;
}
