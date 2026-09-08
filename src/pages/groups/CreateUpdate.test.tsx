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
  // Type and name are locked for an owner group. Only `type` has to be submitted -- it
  // discriminates the update body -- and the immutable name is left out of the partial
  // update entirely. The description is different: only its base line is fixed, and the
  // free text below it is user-editable (see "editing an app owner group description" below).
  // OWNER_APP_GROUP's stored description does not match the base line for its app, so the
  // reseat rule treats it as divergent and repairs it under the current base line on save.
  it('submits the locked type and omits the immutable name, but recomposes the description', async () => {
    render(<CreateUpdateGroup currentUser={ACCESS_ADMIN} defaultGroupType="app_group" group={OWNER_APP_GROUP} />);

    await openDialog('edit');
    await submitDialog('Update');

    expect(updateMutate).toHaveBeenCalledTimes(1);
    const body = updateMutate.mock.calls[0][0].body;
    expect(body).toMatchObject({type: 'app_group'});
    expect(body.name).toBeUndefined();
    expect(body.description).toBe('Owners of the HammerAndChiselZendeskSandbox application\n\nOwners of the sandbox');
  });

  // An owner group's additional description is always optional -- the fixed base line
  // alone always satisfies a `require description` tag constraint, so leaving the free
  // text empty must still submit.
  it('submits when the additional description is empty', async () => {
    const emptyDescription = {...OWNER_APP_GROUP, description: ''} as GroupDetail;
    render(<CreateUpdateGroup currentUser={ACCESS_ADMIN} defaultGroupType="app_group" group={emptyDescription} />);

    await openDialog('edit');
    await submitDialog('Update');

    expect(updateMutate).toHaveBeenCalledTimes(1);
  });
});

describe('editing an app owner group description', () => {
  const ownerGroupWith = (description: string) => ({...OWNER_APP_GROUP, description}) as unknown as GroupDetail;

  const BASE = 'Owners of the HammerAndChiselZendeskSandbox application';

  it('seeds the field with the remainder, not the whole description', async () => {
    render(
      <CreateUpdateGroup
        currentUser={ACCESS_ADMIN}
        defaultGroupType="app_group"
        group={ownerGroupWith(`${BASE}\n\nAlso grants billing access`)}
      />,
    );
    await openDialog('edit');

    expect(screen.getByLabelText(/^Additional description/)).toHaveValue('Also grants billing access');
    expect(screen.getByText(BASE)).toBeInTheDocument();
  });

  it('seeds the whole description when it does not match the base line', async () => {
    render(
      <CreateUpdateGroup
        currentUser={ACCESS_ADMIN}
        defaultGroupType="app_group"
        group={ownerGroupWith('Owners of the sandbox')}
      />,
    );
    await openDialog('edit');

    // Divergent text stays visible and editable, so saving repairs the description.
    expect(screen.getByLabelText(/^Additional description/)).toHaveValue('Owners of the sandbox');
  });

  it('submits the base line rejoined with the edited remainder', async () => {
    render(<CreateUpdateGroup currentUser={ACCESS_ADMIN} defaultGroupType="app_group" group={ownerGroupWith(BASE)} />);
    await openDialog('edit');
    await userEvent.type(screen.getByLabelText(/^Additional description/), 'Also grants billing access');
    await submitDialog('Update');

    expect(updateMutate).toHaveBeenCalledTimes(1);
    expect(updateMutate.mock.calls[0][0].body).toMatchObject({
      description: `${BASE}\n\nAlso grants billing access`,
    });
  });

  it('submits the bare base line when the remainder is cleared', async () => {
    render(
      <CreateUpdateGroup
        currentUser={ACCESS_ADMIN}
        defaultGroupType="app_group"
        group={ownerGroupWith(`${BASE}\n\nAlso grants billing access`)}
      />,
    );
    await openDialog('edit');
    await userEvent.clear(screen.getByLabelText(/^Additional description/));
    await submitDialog('Update');

    expect(updateMutate.mock.calls[0][0].body).toMatchObject({description: BASE});
  });

  it('caps the remainder so the composed description fits 1024 characters', async () => {
    render(<CreateUpdateGroup currentUser={ACCESS_ADMIN} defaultGroupType="app_group" group={ownerGroupWith(BASE)} />);
    await openDialog('edit');

    expect(screen.getByLabelText(/^Additional description/)).toHaveAttribute(
      'maxlength',
      String(1024 - BASE.length - 2),
    );
  });

  it('leaves the field disabled when the app name is unavailable', async () => {
    const groupWithoutApp = {...OWNER_APP_GROUP, description: BASE, app: undefined};
    render(
      <CreateUpdateGroup
        currentUser={ACCESS_ADMIN}
        defaultGroupType="app_group"
        group={groupWithoutApp as unknown as GroupDetail}
      />,
    );
    await openDialog('edit');

    // Better no edit than a wrong prefix built from a missing name.
    expect(screen.getByLabelText(/^Description/)).toBeDisabled();
  });
});
