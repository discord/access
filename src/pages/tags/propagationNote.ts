export function propagationParts(propagateToRoles: boolean): [string, string, string] {
  return [
    'These constraints ',
    propagateToRoles ? 'do' : 'do not',
    ' apply to roles that own or are members of groups with this tag.',
  ];
}

export function propagationNote(propagateToRoles: boolean): string {
  return propagationParts(propagateToRoles).join('');
}
