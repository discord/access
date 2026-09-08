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
// REQUIRE_DESCRIPTIONS is a build-time global sourced from an untracked local `.env`; CI runs
// without it set, so `requireDescriptions` is false there regardless of this developer's
// environment. Mocking the config module makes tests that depend on it true independent of
// both, rather than passing only on a machine with the right `.env`.
vi.mock('../../config/accessConfig', () => ({
  default: {
    NAME_VALIDATION_PATTERN: '^[A-Z][A-Za-z0-9-]*$',
    NAME_VALIDATION_ERROR: 'Name must start capitalized and contain only alphanumeric characters or hyphens.',
  },
  appName: 'Access',
  requireDescriptions: true,
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

  // A non-owner group's Description has no owner-group remainder budget to protect, so it must
  // not carry the DOM-level cap: the field's own `maxLength` rule (1024, same figure) is what
  // enforces the limit, and it fails visibly instead of silently truncating input.
  it('does not cap the Description field at the DOM level', async () => {
    render(<CreateUpdateGroup currentUser={ACCESS_ADMIN} defaultGroupType="app_group" app={APP} />);

    await openDialog('Create App Group');

    expect(screen.getByLabelText(/^Description/)).not.toHaveAttribute('maxlength');
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

  it('seeds an empty field and submits the bare base line for a description that is the base line plus trailing whitespace', async () => {
    render(
      <CreateUpdateGroup
        currentUser={ACCESS_ADMIN}
        defaultGroupType="app_group"
        group={ownerGroupWith(`${BASE}\n\n   `)}
      />,
    );
    await openDialog('edit');

    expect(screen.getByLabelText(/^Additional description/)).toHaveValue('');

    await submitDialog('Update');

    expect(updateMutate.mock.calls[0][0].body).toMatchObject({description: BASE});
  });

  it('round-trips a remainder containing a blank line unchanged through seed and submit', async () => {
    const remainder = 'Also grants billing\n\nSecond paragraph';
    render(
      <CreateUpdateGroup
        currentUser={ACCESS_ADMIN}
        defaultGroupType="app_group"
        group={ownerGroupWith(`${BASE}\n\n${remainder}`)}
      />,
    );
    await openDialog('edit');

    expect(screen.getByLabelText(/^Additional description/)).toHaveValue(remainder);

    await submitDialog('Update');

    expect(updateMutate.mock.calls[0][0].body).toMatchObject({description: `${BASE}\n\n${remainder}`});
  });

  it('round-trips a conforming description byte-identical when submitted without touching the field', async () => {
    const original = `${BASE}\n\nAlso grants billing access`;
    render(
      <CreateUpdateGroup currentUser={ACCESS_ADMIN} defaultGroupType="app_group" group={ownerGroupWith(original)} />,
    );
    await openDialog('edit');
    await submitDialog('Update');

    expect(updateMutate.mock.calls[0][0].body).toMatchObject({description: original});
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
