// The self-add constraints cannot be combined with propagation turned off, and
// the backend rejects the combination on every tag write
// (`PROPAGATION_REQUIRED_CONSTRAINT_KEYS` in `api/models/tag.py`). Unlike the
// reason and time-limit constraints, a self-add restriction does not merely
// weaken when it stops reaching roles -- it inverts to permitted, because the
// owner it blocks can add themselves to a role associated with the tagged
// group and arrive at the same access.
//
// Mirrored here so the tag form reports the conflict inline rather than
// bouncing the admin off a 400 after submit.

// Captions for the two controls, shared with the form so the message names a
// restriction by the same words the admin just toggled.
export const OWNER_SELF_ADD_LABEL = 'Disallow owners adding selves as owners';
export const MEMBER_SELF_ADD_LABEL = 'Disallow owners adding selves as members';

export interface PropagationConflictInput {
  propagateToRoles: 'yes' | 'no';
  ownerAdd: 'yes' | 'no';
  memberAdd: 'yes' | 'no';
}

// Returns the message to show under the propagation control, or null when the
// combination is coherent. Names every conflicting restriction, since turning
// off only one of two would leave the tag still invalid.
export function propagationConflictMessage({
  propagateToRoles,
  ownerAdd,
  memberAdd,
}: PropagationConflictInput): string | null {
  if (propagateToRoles !== 'no') {
    return null;
  }

  const conflicting: string[] = [];
  if (ownerAdd === 'yes') {
    conflicting.push(`“${OWNER_SELF_ADD_LABEL}”`);
  }
  if (memberAdd === 'yes') {
    conflicting.push(`“${MEMBER_SELF_ADD_LABEL}”`);
  }
  if (conflicting.length === 0) {
    return null;
  }

  const subject = conflicting.join(' and ');
  const verb = conflicting.length === 1 ? 'requires' : 'require';
  return (
    `${subject} ${verb} propagation to roles: an owner blocked from adding themselves ` +
    'directly could otherwise add themselves to a role associated with the tagged group ' +
    'and receive the same access.'
  );
}
