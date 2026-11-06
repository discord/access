import {render, screen} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {describe, expect, it} from 'vitest';

import RequesterLink, {requesterName} from './RequesterLink';

const USER = {id: 'u1', email: 'Ada@Example.com', first_name: 'Ada', last_name: 'Lovelace', display_name: 'Ada L'};
const SERVICE = {
  id: 'sp_00000000000000001',
  display_name: 'Deploy Bot',
  provider: 'cloudflare',
  created_at: null,
  updated_at: null,
} as any;

const renderLink = (props: any) =>
  render(
    <MemoryRouter>
      <RequesterLink {...props} />
    </MemoryRouter>,
  );

describe('RequesterLink', () => {
  it('links a user by email, labeled with their name', () => {
    renderLink({user: USER});
    expect(screen.getByRole('link', {name: 'Ada L'})).toHaveAttribute('href', '/users/ada@example.com');
  });

  it('shows a user by email when asked', () => {
    renderLink({user: USER, showEmail: true});
    expect(screen.getByRole('link', {name: 'ada@example.com'})).toBeInTheDocument();
  });

  it('strikes through a deleted user and links by id', () => {
    renderLink({user: {...USER, deleted_at: '2026-01-01T00:00:00Z'}});
    const link = screen.getByRole('link', {name: 'Ada L'});
    expect(link).toHaveAttribute('href', '/users/u1');
    expect(link).toHaveStyle({textDecoration: 'line-through'});
  });

  it('marks a service principal and links to its page', () => {
    renderLink({servicePrincipal: SERVICE});
    const link = screen.getByText('Deploy Bot').closest('a');
    expect(link).toHaveAttribute('href', '/service-principals/sp_00000000000000001');
    expect(screen.getByLabelText('service principal')).toBeInTheDocument();
  });

  it('names whichever requester is set', () => {
    expect(requesterName(USER as any, null)).toBe('Ada L');
    expect(requesterName(null, SERVICE)).toBe('Deploy Bot');
  });
});
