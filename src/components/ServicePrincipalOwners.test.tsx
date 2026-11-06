import {render, screen} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {describe, expect, it} from 'vitest';

import ServicePrincipalOwners from './ServicePrincipalOwners';

const renderOwners = (owners: any[]) =>
  render(
    <MemoryRouter>
      <ServicePrincipalOwners owners={owners} />
    </MemoryRouter>,
  );

describe('ServicePrincipalOwners', () => {
  it('links active user and role owners', () => {
    renderOwners([
      {
        user_id: 'u1',
        active_user: {
          id: 'u1',
          email: 'Ada@Example.com',
          first_name: 'Ada',
          last_name: 'Lovelace',
          display_name: 'Ada',
        },
      },
      {role_group_id: 'r1', active_role_group: {id: 'r1', name: 'Role-Platform', type: 'role_group'}},
    ]);
    expect(screen.getByRole('link', {name: /Ada/})).toHaveAttribute('href', '/users/ada@example.com');
    expect(screen.getByRole('link', {name: /Role-Platform/})).toHaveAttribute('href', '/roles/Role-Platform');
  });

  it('hides deleted owners', () => {
    renderOwners([{user_id: 'u1', active_user: null}]);
    expect(screen.queryByRole('link')).toBeNull();
    expect(screen.getByText('No active owners')).toBeInTheDocument();
  });
});
