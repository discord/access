import React from 'react';

import Alert from '@mui/material/Alert';

/** The ids in `ids` that `named` has no entry for, such as users or roles deleted since a request was filed. */
export function departedIds(ids: string[] | undefined, named: {id: string}[] | undefined): string[] {
  const namedIds = new Set((named ?? []).map((entry) => entry.id));
  return (ids ?? []).filter((id) => !namedIds.has(id));
}

/** Describes departed principals inline, such as "1 departed user, 2 departed roles". */
export function departedLabel(users: number, roles: number): string {
  const parts = [];
  if (users > 0) parts.push(`${users} departed ${users === 1 ? 'user' : 'users'}`);
  if (roles > 0) parts.push(`${roles} departed ${roles === 1 ? 'role' : 'roles'}`);
  return parts.join(', ');
}

export interface DepartedPlace {
  where: string;
  count: number;
}

interface DepartedPrincipalsWarningProps {
  places: DepartedPlace[];
  hint: string;
}

/**
 * Warns that named users or roles are no longer active.
 *
 * Args:
 *   places: Where departed principals are named, and how many in each. Places with a count of 0
 *     are left out.
 *   hint: What the reader can do about it, shown after the count.
 *
 * Renders nothing when no place names a departed principal.
 */
export default function DepartedPrincipalsWarning({places, hint}: DepartedPrincipalsWarningProps) {
  const named = places.filter((place) => place.count > 0);
  const total = named.reduce((sum, place) => sum + place.count, 0);
  if (total === 0) {
    return null;
  }
  const subject = total === 1 ? 'named user or role is' : 'named users or roles are';
  const where = named.map((place) => `${place.count} in ${place.where}`).join(', ');
  return (
    <Alert severity="warning" sx={{my: 1}}>
      {`${total} ${subject} no longer active (${where}). ${hint}`}
    </Alert>
  );
}
