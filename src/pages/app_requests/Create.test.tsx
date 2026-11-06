import * as React from 'react';
import {render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {describe, expect, it, vi, beforeEach} from 'vitest';

import {OktaUserDetail} from '../../api/apiSchemas';

const createMutate = vi.fn();

const USER = {
  id: 'user-000000000000000',
  email: 'user@example.com',
  active_group_memberships: [],
  active_group_ownerships: [],
} as unknown as OktaUserDetail;

const OWNER_USER = {id: 'owner-user-00000000', email: 'Owner@example.com', display_name: 'Olive Owner'};
const MEMBER_ROLE = {id: 'member-role-0000000', name: 'Role-Support-Agents'};
const EXISTING_APPS = [{id: 'existing-app-0000000', name: 'Wiki'}];

vi.mock('react-router-dom', () => ({
  useNavigate: () => vi.fn(),
  Link: React.forwardRef<HTMLAnchorElement, {to: string; children: React.ReactNode}>(({to, ...rest}, ref) => (
    <a ref={ref} href={to} {...rest} />
  )),
}));

vi.mock('../../constraints', () => ({
  timeLimitLabel: (seconds: number) => `${seconds / 86400} days`,
  useConstraintsForTags: () => ({
    pending: false,
    error: null,
    blocked: false,
    timeLimit: () => null,
    isReasonRequired: () => false,
    isSelfAddDisallowed: () => false,
    forGroup: () => ({timeLimit: () => null, isReasonRequired: () => false, isSelfAddDisallowed: () => false}),
  }),
}));

vi.mock('../../api/apiComponents', () => ({
  useAppRequestsCreate: () => ({mutate: createMutate}),
  // Mirrors the API's search: a case-insensitive substring match on the name.
  useApps: ({queryParams}: {queryParams: {q: string}}, options?: {enabled?: boolean}) => ({
    data:
      options?.enabled === false
        ? undefined
        : {items: EXISTING_APPS.filter((app) => app.name.toLowerCase().includes(queryParams.q.toLowerCase()))},
  }),
  useTags: () => ({data: {items: []}}),
  useUsers: () => ({data: {items: [OWNER_USER]}}),
  useRoles: () => ({data: {items: [MEMBER_ROLE]}}),
}));

import CreateAppRequest, {appGroupNameErrors} from './Create';

beforeEach(() => {
  createMutate.mockClear();
});

const renderDialog = () => render(<CreateAppRequest currentUser={USER} open setOpen={vi.fn()} />);

async function pickIn(container: HTMLElement, field: string, option: string) {
  await userEvent.click(within(container).getByRole('combobox', {name: field}));
  await userEvent.click(await screen.findByRole('option', {name: option}));
}

async function fillApp(name = 'Support-Desk') {
  await userEvent.type(screen.getByLabelText(/^App name/), name);
  await userEvent.type(screen.getByLabelText(/Why do you need this app/), 'Support needs it');
}

async function addGroup(suffix: string): Promise<HTMLElement> {
  await userEvent.click(screen.getByRole('button', {name: 'Add app group'}));
  const row = screen.getAllByTestId('app-group-row').at(-1)!;
  if (suffix !== '') {
    await userEvent.type(within(row).getByLabelText(/^Group name/), suffix);
  }
  return row;
}

describe('appGroupNameErrors', () => {
  it('accepts distinct names', () => {
    expect(appGroupNameErrors('Wiki', ['Editors', 'Readers'])).toEqual([null, null]);
  });

  it('requires a name', () => {
    expect(appGroupNameErrors('Wiki', ['', 'Readers'])[0]).toMatch(/required/);
  });

  it('reserves the owners group, ignoring case', () => {
    expect(appGroupNameErrors('Wiki', ['owners'])[0]).toMatch(/App-Wiki-Owners group is created with the app/);
  });

  it('flags both copies of a repeated name, ignoring case', () => {
    const errors = appGroupNameErrors('Wiki', ['Editors', 'editors', 'Readers']);
    expect(errors[0]).toMatch(/Another group/);
    expect(errors[1]).toMatch(/Another group/);
    expect(errors[2]).toBeNull();
  });
});

describe('requesting an app', () => {
  it('sends the app, its owners, and each initial group with the prefix', async () => {
    renderDialog();

    await fillApp();
    await pickIn(document.body, 'App owners: users', OWNER_USER.display_name);
    const row = await addGroup('Agents');
    await pickIn(row, 'Members: roles', MEMBER_ROLE.name);
    await userEvent.click(screen.getByRole('button', {name: 'Submit'}));

    await waitFor(() => expect(createMutate).toHaveBeenCalledTimes(1));
    const body = createMutate.mock.calls[0][0].body;
    expect(body).toMatchObject({
      requested_app_name: 'Support-Desk',
      requested_owner_user_ids: [OWNER_USER.id],
      requested_owner_role_ids: [],
      requested_app_tags: [],
      request_reason: 'Support needs it',
      requested_app_groups: [
        {
          name: 'App-Support-Desk-Agents',
          owner_user_ids: [],
          owner_role_ids: [],
          member_user_ids: [],
          member_role_ids: [MEMBER_ROLE.id],
        },
      ],
    });
    expect(body.requested_ownership_ending_at).toEqual(expect.any(String));
    expect(body.requested_membership_ending_at).toEqual(expect.any(String));
  });

  it('asks for a membership length only once a group names members', async () => {
    renderDialog();

    await fillApp();
    const row = await addGroup('Agents');
    expect(screen.queryByRole('combobox', {name: 'Requested membership length'})).not.toBeInTheDocument();
    await pickIn(row, 'Members: roles', MEMBER_ROLE.name);
    expect(screen.getByRole('combobox', {name: 'Requested membership length'})).toBeInTheDocument();
  });

  it('sends no owners, groups, or membership end date when none are named', async () => {
    renderDialog();

    await fillApp();
    await userEvent.click(screen.getByRole('button', {name: 'Submit'}));

    await waitFor(() => expect(createMutate).toHaveBeenCalledTimes(1));
    const body = createMutate.mock.calls[0][0].body;
    expect(body.requested_owner_user_ids).toEqual([]);
    expect(body.requested_app_groups).toEqual([]);
    expect(body.requested_membership_ending_at).toBeUndefined();
  });

  it('shows the app prefix on each group', async () => {
    renderDialog();

    await fillApp();
    const row = await addGroup('');
    expect(within(row).getByText('App-Support-Desk-')).toBeInTheDocument();
  });

  it('drops a removed group', async () => {
    renderDialog();

    await fillApp();
    const row = await addGroup('Agents');
    await userEvent.click(within(row).getByRole('button', {name: 'Remove app group'}));
    await userEvent.click(screen.getByRole('button', {name: 'Submit'}));

    await waitFor(() => expect(createMutate).toHaveBeenCalledTimes(1));
    expect(createMutate.mock.calls[0][0].body.requested_app_groups).toEqual([]);
  });
});

describe('validating initial group names', () => {
  it('flags the reserved owners group as soon as it is typed', async () => {
    renderDialog();

    await fillApp();
    await addGroup('Owners');

    expect(screen.getByText(/App-Support-Desk-Owners group is created with the app/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', {name: 'Submit'}));
    expect(createMutate).not.toHaveBeenCalled();
  });

  it('flags a repeated name', async () => {
    renderDialog();

    await fillApp();
    await addGroup('Agents');
    await addGroup('agents');

    expect(screen.getAllByText('Another group has this name')).toHaveLength(2);
  });

  it('flags an empty name on submit rather than on add', async () => {
    renderDialog();

    await fillApp();
    await addGroup('');
    expect(screen.queryByText('Name is required')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', {name: 'Submit'}));
    expect(screen.getByText('Name is required')).toBeInTheDocument();
    expect(createMutate).not.toHaveBeenCalled();
  });
});

describe('requesting an app whose name is taken', () => {
  it('flags the name and disables Submit, ignoring case', async () => {
    renderDialog();

    await userEvent.type(screen.getByLabelText(/^App name/), 'WIKI');

    expect(screen.getByText(/An app named "Wiki" already exists/)).toBeInTheDocument();
    expect(screen.getByRole('button', {name: 'Submit'})).toBeDisabled();
  });

  it('allows a name that only partly matches an existing app', async () => {
    renderDialog();

    await userEvent.type(screen.getByLabelText(/^App name/), 'Wikis');

    expect(screen.queryByText(/already exists/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', {name: 'Submit'})).toBeEnabled();
  });
});
