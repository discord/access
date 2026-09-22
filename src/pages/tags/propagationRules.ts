// The self-add constraints cannot be combined with a tag that stops at the tagged
// groups, and the backend rejects the combination on every tag write
// (`PROPAGATION_REQUIRED_CONSTRAINT_KEYS` in `api/models/tag.py`). Unlike the reason
// and time-limit constraints, a self-add restriction does not merely weaken when it
// stops reaching roles -- it inverts to permitted, because the owner it blocks can
// add themselves to a role associated with the tagged group and arrive at the same
// access.
//
// Mirrored here so the tag form reports the conflict inline rather than bouncing the
// admin off a 400 after submit. Both directions are blocked on whichever control is
// being changed, which is what keeps the pair unreachable: with the narrow scope
// chosen the restrictions cannot be switched on, and with a restriction on the narrow
// scope cannot be chosen.

import {CONSTRAINT_LABELS, DISALLOW_SELF_ADD_MEMBERSHIP, DISALLOW_SELF_ADD_OWNERSHIP} from '../../constraintCopy';

/** The short pointer shown on a self-add control whose restriction is unavailable. */
export const SELF_ADD_NEEDS_PROPAGATION = 'Requires a tag that reaches roles.';

/**
 * Whether the self-add controls may offer their restriction.
 *
 * An unset value reads as available: a tag reaches roles by default, so nothing is
 * blocked until the control actually holds the narrow scope.
 *
 * @param propagateToRoles The scope field's raw value, as the form holds it.
 */
export function selfAddRestrictionAvailable(propagateToRoles: string | undefined): boolean {
  return propagateToRoles !== 'no';
}

export interface PropagationConflictInput {
  propagateToRoles: string;
  ownerAdd: boolean | undefined;
  memberAdd: boolean | undefined;
}

/**
 * The message shown under the scope control, or `null` when the combination is fine.
 *
 * Names every conflicting restriction, since turning off only one of two would leave
 * the tag still invalid.
 */
export function propagationConflictMessage({
  propagateToRoles,
  ownerAdd,
  memberAdd,
}: PropagationConflictInput): string | null {
  if (propagateToRoles !== 'no') {
    return null;
  }

  const conflicting: string[] = [];
  if (memberAdd === true) {
    conflicting.push(`“${CONSTRAINT_LABELS[DISALLOW_SELF_ADD_MEMBERSHIP]}”`);
  }
  if (ownerAdd === true) {
    conflicting.push(`“${CONSTRAINT_LABELS[DISALLOW_SELF_ADD_OWNERSHIP]}”`);
  }
  if (conflicting.length === 0) {
    return null;
  }

  const subject = conflicting.join(' and ');
  const verb = conflicting.length === 1 ? 'requires' : 'require';
  return (
    `${subject} ${verb} a tag that reaches roles: an owner blocked from adding themselves ` +
    'directly could otherwise add themselves to a role associated with the tagged group ' +
    'and receive the same access.'
  );
}
