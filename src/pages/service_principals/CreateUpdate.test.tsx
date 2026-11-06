import {render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {MemoryRouter} from 'react-router-dom';
import {beforeEach, describe, expect, it, vi} from 'vitest';

const createMutate = vi.fn();
const OWNER = {id: 'owner-1', email: 'Owner@example.com', first_name: 'Olive', last_name: 'Owner'};

vi.mock('../../api/apiComponents', () => ({
  useServicePrincipalsCreate: () => ({mutate: createMutate, isPending: false}),
  useServicePrincipalByIdPut: () => ({mutate: vi.fn(), isPending: false}),
  useUsers: () => ({data: {items: [OWNER]}}),
  useRoles: () => ({data: {items: []}}),
}));

import CreateUpdateServicePrincipal from './CreateUpdate';
import {OktaUserDetail} from '../../api/apiSchemas';
import {appName} from '../../config/accessConfig';

const admin = {
  id: 'u1',
  email: 'admin@example.com',
  active_group_memberships: [
    {
      active_group: {
        id: 'g-owners',
        name: `App-${appName}-Owners`,
        type: 'app_group',
        is_owner: true,
        app: {id: 'a1', name: appName},
      },
    },
  ],
} as unknown as OktaUserDetail;

const renderDialog = (currentUser: OktaUserDetail) =>
  render(
    <MemoryRouter>
      <CreateUpdateServicePrincipal currentUser={currentUser} />
    </MemoryRouter>,
  );

beforeEach(() => createMutate.mockClear());

describe('CreateUpdateServicePrincipal', () => {
  it('is hidden from non-admins', () => {
    renderDialog({id: 'u2', email: 'user@example.com'} as OktaUserDetail);
    expect(screen.queryByRole('button', {name: /Create Service Principal/})).toBeNull();
  });

  it('requires an owner, then submits the principal', async () => {
    renderDialog(admin);
    await userEvent.click(screen.getByRole('button', {name: /Create Service Principal/}));
    await userEvent.type(screen.getByLabelText(/Display name/), 'Deploy bot');
    await userEvent.type(screen.getByLabelText(/client ID/), 'abc.access');

    const create = screen.getByRole('button', {name: 'Create'});
    expect(create).toBeDisabled();
    expect(screen.getByText('Name at least one owning user or role.')).toBeInTheDocument();

    await userEvent.click(screen.getByLabelText('Owners: users'));
    await userEvent.click(await screen.findByText('Olive Owner'));
    expect(create).toBeEnabled();
    await userEvent.click(create);

    expect(createMutate).toHaveBeenCalledWith({
      body: {display_name: 'Deploy bot', subject: 'abc.access', owner_user_ids: ['owner-1'], owner_role_ids: []},
    });
  });
});
