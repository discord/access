import {describe, expect, it} from 'vitest';

import {propagationConflictMessage} from './propagationRules';

describe('propagationConflictMessage', () => {
  it('returns null when propagation is on, whatever the self-add settings', () => {
    expect(propagationConflictMessage({propagateToRoles: 'yes', ownerAdd: 'yes', memberAdd: 'yes'})).toBeNull();
  });

  it('returns null when propagation is off and no self-add restriction is set', () => {
    expect(propagationConflictMessage({propagateToRoles: 'no', ownerAdd: 'no', memberAdd: 'no'})).toBeNull();
  });

  it('names the membership restriction when only it conflicts', () => {
    const message = propagationConflictMessage({propagateToRoles: 'no', ownerAdd: 'no', memberAdd: 'yes'});
    expect(message).toContain('adding selves as members');
    expect(message).not.toContain('adding selves as owners');
  });

  it('names the ownership restriction when only it conflicts', () => {
    const message = propagationConflictMessage({propagateToRoles: 'no', ownerAdd: 'yes', memberAdd: 'no'});
    expect(message).toContain('adding selves as owners');
    expect(message).not.toContain('adding selves as members');
  });

  it('names both restrictions when both conflict, since dropping one would still be invalid', () => {
    const message = propagationConflictMessage({propagateToRoles: 'no', ownerAdd: 'yes', memberAdd: 'yes'});
    expect(message).toContain('adding selves as owners');
    expect(message).toContain('adding selves as members');
  });
});
