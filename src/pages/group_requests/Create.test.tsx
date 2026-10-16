import * as React from 'react';
import {describe, it, expect, vi, beforeEach} from 'vitest';
import {render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import {OktaUserDetail} from '../../api/apiSchemas';

const createMutate = vi.fn();
const EXISTING_GROUPS = [{id: 'existing-group-000000', type: 'okta_group', name: 'Platform-Admins'}];

vi.mock('react-router-dom', () => ({
  useNavigate: () => vi.fn(),
  Link: React.forwardRef<HTMLAnchorElement, {to: string; children: React.ReactNode}>(({to, ...rest}, ref) => (
    <a ref={ref} href={to} {...rest} />
  )),
}));
vi.mock('../../api/apiComponents', () => ({
  useApps: () => ({data: {items: []}}),
  useAppById: () => ({data: undefined}),
  useTags: () => ({data: {items: []}}),
  useGroupRequestsCreate: () => ({mutate: createMutate}),
  // Mirrors the API's search: a case-insensitive substring match on the name.
  useGroups: ({queryParams}: {queryParams: {q: string}}, options?: {enabled?: boolean}) => ({
    data:
      options?.enabled === false
        ? undefined
        : {items: EXISTING_GROUPS.filter((g) => g.name.toLowerCase().includes(queryParams.q.toLowerCase()))},
  }),
}));

import CreateRequest from './Create';

const USER = {
  id: 'user-000000000000000',
  email: 'user@example.com',
  active_group_memberships: [],
  active_group_ownerships: [],
} as unknown as OktaUserDetail;

beforeEach(() => {
  createMutate.mockClear();
});

const renderDialog = () => render(<CreateRequest currentUser={USER} open setOpen={vi.fn()} />);

describe('requesting a group whose name is taken', () => {
  it('flags the name and disables Submit, ignoring case', async () => {
    renderDialog();

    await userEvent.type(screen.getByLabelText(/^Name/), 'platform-admins');

    expect(screen.getByText(/A group named "platform-admins" already exists/)).toBeInTheDocument();
    expect(screen.getByRole('link', {name: 'request access to it'})).toHaveAttribute('href', '/requests');
    expect(screen.getByRole('button', {name: 'Submit'})).toBeDisabled();
  });

  it('allows a name that only partly matches an existing group', async () => {
    renderDialog();

    await userEvent.type(screen.getByLabelText(/^Name/), 'Platform');

    expect(screen.queryByText(/already exists/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', {name: 'Submit'})).toBeEnabled();
  });
});
