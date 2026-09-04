import {describe, it, expect, vi, beforeEach} from 'vitest';
import {render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import {AppDetail, GroupDetail, OktaUserDetail} from '../../api/apiSchemas';

const createMutate = vi.fn();
const updateMutate = vi.fn();

vi.mock('react-router-dom', () => ({useNavigate: () => vi.fn()}));
vi.mock('../../api/apiComponents', () => ({
  useApps: () => ({data: {items: []}}),
  useTags: () => ({data: {items: []}}),
  useGroupsCreate: () => ({mutate: createMutate}),
  useGroupByIdPut: () => ({mutate: updateMutate}),
}));

import CreateUpdateGroup from './CreateUpdate';

const ACCESS_OWNER_GROUP = {
  id: 'access-owners-000000',
  type: 'app_group',
  name: 'App-Access-Owners',
  is_owner: true,
  app: {id: 'access-app-0000000000', name: 'Access'},
};

// The Access admin tier is membership in the Access app's owner group.
const ACCESS_ADMIN = {
  id: 'admin-00000000000000',
  email: 'admin@example.com',
  active_group_memberships: [{active_group: ACCESS_OWNER_GROUP}],
  active_group_ownerships: [],
} as unknown as OktaUserDetail;

const APP = {id: 'zendesk-sandbox-0000', name: 'HammerAndChiselZendeskSandbox'} as unknown as AppDetail;

const OWNER_APP_GROUP = {
  id: 'owner-group-00000000',
  type: 'app_group',
  name: 'App-HammerAndChiselZendeskSandbox-Owners',
  description: 'Owners of the sandbox',
  is_owner: true,
  is_managed: true,
  app: APP,
  active_group_tags: [],
} as unknown as GroupDetail;

beforeEach(() => {
  createMutate.mockClear();
  updateMutate.mockClear();
});

const openDialog = async (label: string) => {
  await userEvent.click(screen.getByRole('button', {name: label}));
};

const submitDialog = async (label: string) => {
  await userEvent.click(screen.getByRole('button', {name: label}));
};

describe('creating an app group from an app page', () => {
  // The Type select and App autocomplete are locked to the app being viewed. A lock
  // must not drop the value: react-hook-form omits `disabled` fields from submitted
  // data, and the API discriminates CreateGroupBody on `type`, so a dropped `type`
  // fails validation with "Unable to extract tag using discriminator 'type'".
  it('submits the locked type and app alongside the typed name', async () => {
    render(<CreateUpdateGroup currentUser={ACCESS_ADMIN} defaultGroupType="app_group" app={APP} />);

    await openDialog('Create App Group');
    await userEvent.type(screen.getByLabelText(/^Name/), 'Admin');
    await userEvent.type(screen.getByLabelText(/^Description/), 'Grants the Admin role');
    await submitDialog('Create');

    expect(createMutate).toHaveBeenCalledTimes(1);
    expect(createMutate.mock.calls[0][0].body).toMatchObject({
      type: 'app_group',
      app_id: APP.id,
      name: 'App-HammerAndChiselZendeskSandbox-Admin',
      description: 'Grants the Admin role',
    });
  });

  it('still refuses to let the type be changed', async () => {
    render(<CreateUpdateGroup currentUser={ACCESS_ADMIN} defaultGroupType="app_group" app={APP} />);

    await openDialog('Create App Group');
    const typeSelect = screen.getByRole('combobox', {name: 'Type'});
    await userEvent.click(typeSelect);

    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(typeSelect).toHaveTextContent('App Group');
  });
});

describe('editing an app owner group', () => {
  // Type, name and description are all locked for an owner group. Only `type` has to be
  // submitted -- it discriminates the update body. Name and description are immutable,
  // so they are left out of the partial update entirely.
  it('submits the locked type and omits the immutable name and description', async () => {
    render(<CreateUpdateGroup currentUser={ACCESS_ADMIN} defaultGroupType="app_group" group={OWNER_APP_GROUP} />);

    await openDialog('edit');
    await submitDialog('Update');

    expect(updateMutate).toHaveBeenCalledTimes(1);
    const body = updateMutate.mock.calls[0][0].body;
    expect(body).toMatchObject({type: 'app_group'});
    expect(body.name).toBeUndefined();
    expect(body.description).toBeUndefined();
  });

  // An owner group whose description is empty must still be editable -- validating a
  // `required` description the user cannot reach would leave the form with no way out.
  it('submits when the immutable description is empty', async () => {
    const emptyDescription = {...OWNER_APP_GROUP, description: ''} as GroupDetail;
    render(<CreateUpdateGroup currentUser={ACCESS_ADMIN} defaultGroupType="app_group" group={emptyDescription} />);

    await openDialog('edit');
    await submitDialog('Update');

    expect(updateMutate).toHaveBeenCalledTimes(1);
  });
});
