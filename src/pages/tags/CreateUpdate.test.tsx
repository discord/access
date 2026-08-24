import {render, screen} from '@testing-library/react';
import {describe, expect, it} from 'vitest';

import {propagationNote} from './propagationNote';

describe('propagationNote', () => {
  it('states that constraints do apply when propagation is on', () => {
    expect(propagationNote(true)).toBe(
      'These constraints do apply to roles that own or are members of groups with this tag.',
    );
  });

  it('states that constraints do not apply when propagation is off', () => {
    expect(propagationNote(false)).toBe(
      'These constraints do not apply to roles that own or are members of groups with this tag.',
    );
  });
});
