import {render, screen} from '@testing-library/react';
import {describe, expect, it} from 'vitest';

import PropagationNoteView from './PropagationNoteView';

// Emphasising one clause is the whole job of this component, so it is asserted
// through the rendered weight rather than through the element carrying it.
// jsdom reports the `bold` keyword numerically.
const BOLD = '700';

function weightOf(element: Element): string {
  return window.getComputedStyle(element).fontWeight;
}

function expectOnlyEmphasised(text: string) {
  const clause = screen.getByText(text);
  expect(weightOf(clause)).toBe(BOLD);
  // The sentence around it stays unemphasised, which is what makes the clause
  // stand out at all.
  expect(weightOf(clause.parentElement!)).not.toBe(BOLD);
}

describe('PropagationNoteView', () => {
  it('states that constraints do apply when propagation is on, with "do" bolded', () => {
    const {container} = render(<PropagationNoteView propagateToRoles={true} />);
    expect(container.textContent).toBe(
      'These constraints do apply to roles that own or are members of groups with this tag.',
    );
    expectOnlyEmphasised('do');
  });

  it('states that constraints do not apply when propagation is off, with "do not" bolded', () => {
    const {container} = render(<PropagationNoteView propagateToRoles={false} />);
    expect(container.textContent).toBe(
      'These constraints do not apply to roles that own or are members of groups with this tag.',
    );
    expectOnlyEmphasised('do not');
  });
});
