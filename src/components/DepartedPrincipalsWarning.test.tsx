import {describe, it, expect} from 'vitest';
import {render, screen} from '@testing-library/react';

import DepartedPrincipalsWarning, {departedIds, departedLabel} from './DepartedPrincipalsWarning';

describe('departedIds', () => {
  it('returns the ids with no named entry, in order', () => {
    expect(departedIds(['a', 'b', 'c'], [{id: 'b'}])).toEqual(['a', 'c']);
  });

  it('treats missing lists as empty', () => {
    expect(departedIds(undefined, [{id: 'a'}])).toEqual([]);
    expect(departedIds(['a'], undefined)).toEqual(['a']);
  });
});

describe('departedLabel', () => {
  it('counts users and roles separately', () => {
    expect(departedLabel(1, 0)).toBe('1 departed user');
    expect(departedLabel(2, 1)).toBe('2 departed users, 1 departed role');
  });
});

describe('DepartedPrincipalsWarning', () => {
  it('renders nothing when no place names a departed principal', () => {
    const {container} = render(<DepartedPrincipalsWarning places={[{where: 'Owners', count: 0}]} hint="Hint." />);
    expect(container).toBeEmptyDOMElement();
  });

  it('states the total and where they are, then the hint', () => {
    render(
      <DepartedPrincipalsWarning
        places={[
          {where: 'Owners', count: 1},
          {where: 'Members', count: 0},
          {where: 'Viewers', count: 2},
        ]}
        hint="Edit them."
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent(
      '3 named users or roles are no longer active (1 in Owners, 2 in Viewers). Edit them.',
    );
  });
});
