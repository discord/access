import {render, screen} from '@testing-library/react';
import {describe, expect, it, vi} from 'vitest';

// This repo's MUI style engine is @mui/styled-engine-sc (styled-components),
// aliased in vite.config.ts for the app build. That alias doesn't reach
// vitest's dependency resolution: @mui/material's CJS build still requires
// the default `@mui/styled-engine`, which in turn requires `@emotion/styled`
// — a package that isn't installed here — so importing any real MUI
// component blows up at module-load time (see the identical workaround in
// EffectiveConstraints.test.tsx). Stand in plain DOM elements that preserve
// the structure PropagationNoteView relies on: Typography wraps everything and
// Box is the bold span, so mapping Box to <strong> lets the assertions below
// check that only the "do" / "do not" clause is bolded.
vi.mock('@mui/material/Typography', () => ({default: ({children}: any) => <p>{children}</p>}));
vi.mock('@mui/material/Box', () => ({default: ({children}: any) => <strong>{children}</strong>}));

import PropagationNoteView from './PropagationNoteView';
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

describe('PropagationNoteView', () => {
  it('renders text identical to propagationNote(true), with "do" bolded', () => {
    const {container} = render(<PropagationNoteView propagateToRoles={true} />);
    expect(container.textContent).toBe(propagationNote(true));
    expect(screen.getByText('do', {selector: 'strong'})).toBeInTheDocument();
  });

  it('renders text identical to propagationNote(false), with "do not" bolded', () => {
    const {container} = render(<PropagationNoteView propagateToRoles={false} />);
    expect(container.textContent).toBe(propagationNote(false));
    expect(screen.getByText('do not', {selector: 'strong'})).toBeInTheDocument();
  });
});
