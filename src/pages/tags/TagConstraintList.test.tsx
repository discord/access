import {render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {describe, expect, it} from 'vitest';

import TagConstraintList from './TagConstraintList';
import {constraintLabel} from '../../constraintCopy';

const MEMBER_TIME_LIMIT = constraintLabel('member_time_limit');

function renderList(constraints: Record<string, number | boolean>, propagateToRoles = true) {
  return render(
    <TagConstraintList
      constraintKeys={Object.keys(constraints)}
      constraints={constraints}
      propagateToRoles={propagateToRoles}
    />,
  );
}

describe('TagConstraintList', () => {
  it('shows a time limit in days rather than seconds', () => {
    renderList({member_time_limit: 7776000});
    expect(screen.getByText(MEMBER_TIME_LIMIT)).toBeInTheDocument();
    expect(screen.getByText('90 days')).toBeInTheDocument();
  });

  it('shows a flag that is switched on as "Yes"', () => {
    renderList({require_member_reason: true});
    expect(screen.getByText('Yes')).toBeInTheDocument();
  });

  it('reveals the constraint help on demand', async () => {
    renderList({member_time_limit: 7776000});
    const button = screen.getByRole('button', {name: /What .*means/});
    await userEvent.click(button);
    expect(screen.getByText(/Caps how long anyone holds membership/)).toBeInTheDocument();
  });

  // A backend ahead of the deployed frontend. The effective-constraints panel
  // prints an unrecognised value rather than describing it, and these two views
  // exist to say the same thing about the same constraint.
  describe('a constraint this build has no copy for', () => {
    it('prints a numeric value instead of claiming the setting is a flag', () => {
      renderList({max_members: 5});
      expect(screen.getByText('5')).toBeInTheDocument();
      expect(screen.queryByText('Yes')).not.toBeInTheDocument();
    });

    it('still names the constraint, falling back to its key', () => {
      renderList({max_members: 5});
      expect(screen.getByText('max_members')).toBeInTheDocument();
    });

    it('offers no help button, since there is no help to reveal', () => {
      // An unguarded button toggles nothing and points `aria-controls` at an
      // element that never renders.
      renderList({max_members: 5});
      expect(screen.queryByRole('button')).not.toBeInTheDocument();
    });
  });
});
