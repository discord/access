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
  /** Whether the tag enforces its constraints at all. */
  enabled: boolean;
}

/**
 * The caveat to show alongside the effects when the saved tag is not enabled.
 *
 * The effects are still worth showing -- they describe the tag the admin is about
 * to save -- but a disabled tag enforces nothing, so nothing happens on this save.
 */
export const DORMANT_UNTIL_ENABLED = 'These take effect when the tag is enabled. A disabled tag enforces nothing.';

const days = (n: number) => `${n} day${n === 1 ? '' : 's'}`;

/** A tag stripped of every constraint, which is what a disabled tag enforces. */
function constrainsNothing(settings: TagSettings): TagSettings {
  return {
    ...settings,
    memberTimeLimitDays: undefined,
    ownerTimeLimitDays: undefined,
    requireMemberReason: false,
    requireOwnerReason: false,
    disallowSelfAddMembership: false,
    disallowSelfAddOwnership: false,
  };
}

/**
 * What the tag enforced before the edit, which is not always what it stored.
 *
 * Enabling a disabled tag is measured against a tag that constrains nothing, so
 * every limit and flag it already carried counts as newly applied -- the largest
 * change this dialog makes, and one that comparing the constraints alone misses
 * entirely, since the edit need not touch any of them.
 *
 * A tag that stays disabled is measured against what it stored, so the warning
 * describes the edit the admin is making rather than re-reporting settings they
 * left alone. `propagatesToRoles` is never cleared: the scope decides where the
 * limits reach, and each limit's own sentence already says so.
 */
function enforcedBefore(before: TagSettings, after: TagSettings): TagSettings {
  const beingEnabled = !before.enabled && after.enabled;
  return beingEnabled ? constrainsNothing(before) : before;
}

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
 * @param saved The tag as saved. Read through `enforcedBefore`, so a tag
 *   being enabled is compared against one that constrains nothing.
 * @param after The tag as the form currently stands. When it is not enabled the
 *   effects describe what the tag will do once it is; pair them with
 *   `DORMANT_UNTIL_ENABLED`.
 * @returns Sentences to list, or `[]` when nothing tightens, in which case there is
 *   nothing to warn about.
 */
export function tighteningEffects(saved: TagSettings, after: TagSettings): string[] {
  const before = enforcedBefore(saved, after);
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
