export function propagationNote(propagateToRoles: boolean): string {
  return propagateToRoles
    ? 'These constraints do apply to roles that own or are members of groups with this tag.'
    : 'These constraints do not apply to roles that own or are members of groups with this tag.';
}
