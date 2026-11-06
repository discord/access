import * as React from 'react';
import {render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {describe, expect, it, vi, beforeEach} from 'vitest';

import {AppDetail, OktaUserDetail} from '../../api/apiSchemas';

const APP_NAME = 'HammerAndChiselZendeskSandbox';
const APP_TAG = {id: 'sox-tag-00000000000', name: 'SOX', constraints: {owner_time_limit: 7776000}};

// The app carries a tag of its own, which `CreateGroup` copies onto every group
// created under it whether or not the requester picks it.
const APP = {
  id: 'zendesk-sandbox-0000',
  name: APP_NAME,
  active_app_tags: [{active_tag: APP_TAG}],
} as unknown as AppDetail;

// How the Tags field spells an inherited tag.
const INHERITED_CHIP = `${APP_TAG.name} (${APP_NAME})`;

const createMutate = vi.fn();
const EXISTING_GROUPS = [
  {id: 'existing-group-000000', type: 'okta_group', name: 'Platform-Admins'},
  {id: 'existing-role-0000000', type: 'role_group', name: 'Role-Platform-Engineers'},
];

const USER = {
  id: 'user-000000000000000',
  email: 'user@example.com',
  active_group_memberships: [],
  active_group_ownerships: [],
} as unknown as OktaUserDetail;

const OWNER_USER = {id: 'owner-user-00000000', email: 'Owner@example.com', display_name: 'Olive Owner'};
const MEMBER_ROLE = {id: 'member-role-0000000', name: 'Role-Support-Agents'};

const constraintsAskedFor = vi.fn();
// The owner-side limit the app's tag imposes, as the reader would report it.
const OWNER_LIMIT_SECONDS = 7776000;
let reportLimit = true;

vi.mock('react-router-dom', () => ({
  useNavigate: () => vi.fn(),
  Link: React.forwardRef<HTMLAnchorElement, {to: string; children: React.ReactNode}>(({to, ...rest}, ref) => (
    <a ref={ref} href={to} {...rest} />
  )),
}));

vi.mock('../../constraints', () => ({
  timeLimitLabel: (seconds: number) => `${seconds / 86400} days`,
  useConstraintsForTags: (ids: string[]) => {
    constraintsAskedFor(ids);
    return {
      pending: false,
      error: null,
      blocked: false,
      timeLimit: () => (reportLimit ? OWNER_LIMIT_SECONDS : null),
      isReasonRequired: () => false,
      isSelfAddDisallowed: () => false,
      forGroup: () => ({timeLimit: () => null, isReasonRequired: () => false, isSelfAddDisallowed: () => false}),
    };
  },
}));

vi.mock('../../api/apiComponents', () => ({
  useGroupRequestsCreate: () => ({mutate: createMutate}),
  useApps: () => ({data: {items: [APP]}}),
  // Honours `enabled` like the real hook: a request with no app in play must
  // not read as one carrying the app's tags.
  useAppById: (_variables: unknown, options?: {enabled?: boolean}) => ({
    data: options?.enabled === false ? undefined : APP,
    isLoading: false,
  }),
  useTags: () => ({data: {items: []}}),
  useUsers: () => ({data: {items: [OWNER_USER]}}),
  useRoles: () => ({data: {items: [MEMBER_ROLE]}}),
  // Mirrors the API's search: a case-insensitive substring match on the name.
  useGroups: ({queryParams}: {queryParams: {q: string}}, options?: {enabled?: boolean}) => ({
    data:
      options?.enabled === false
        ? undefined
        : {items: EXISTING_GROUPS.filter((g) => g.name.toLowerCase().includes(queryParams.q.toLowerCase()))},
  }),
}));

import CreateRequest from './Create';

beforeEach(() => {
  createMutate.mockClear();
  constraintsAskedFor.mockClear();
  reportLimit = true;
});

const renderDialog = () => render(<CreateRequest currentUser={USER} open setOpen={vi.fn()} />);

const lastConstraintQuery = (): string[] => constraintsAskedFor.mock.calls.at(-1)![0];

async function openDialogAndPickApp() {
  renderDialog();
  await userEvent.click(await screen.findByRole('combobox', {name: 'Group Type'}));
  await userEvent.click(await screen.findByRole('option', {name: 'App Group'}));
  await userEvent.click(await screen.findByRole('combobox', {name: 'App'}));
  await userEvent.click(await screen.findByRole('option', {name: APP.name}));
}

// A group created under an app carries that app's tags, so the durations this
// form offers have to answer for them. Nothing rejects a request that exceeds
// them -- `ApproveGroupRequest` shortens it on approval -- so a duration left on
// offer is one the requester is told they asked for and does not get.
describe('tags a requested app group would inherit', () => {
  it('asks about them alongside the tags the requester picked', async () => {
    await openDialogAndPickApp();
    await waitFor(() => expect(lastConstraintQuery()).toContain(APP_TAG.id));
  });

  it('lists them in the Tags field, naming the app they come from', async () => {
    await openDialogAndPickApp();
    expect(await screen.findByText(INHERITED_CHIP)).toBeInTheDocument();
  });

  // They sit among the requester's own chips, so the only thing marking them as
  // the app's is that they cannot be taken off.
  it('gives them no delete control', async () => {
    await openDialogAndPickApp();
    const chip = (await screen.findByText(INHERITED_CHIP)).closest('.MuiChip-root');
    expect(chip).not.toBeNull();
    expect(within(chip as HTMLElement).queryByTestId('CancelIcon')).toBeNull();
  });

  it('stops offering a duration the inherited limit forbids', async () => {
    await openDialogAndPickApp();
    const select = await screen.findByRole('combobox', {name: 'Requested ownership length'});
    await waitFor(() => expect(select).not.toHaveTextContent('Indefinite'));
    await userEvent.click(select);
    const offered = screen.getAllByRole('option').map((option) => option.textContent);
    expect(offered).not.toContain('Indefinite');
    expect(offered).toContain('90 Days');
  });
});

describe('requesting a group whose name is taken', () => {
  it('flags the name and disables Submit, ignoring case', async () => {
    renderDialog();

    await userEvent.type(screen.getByLabelText(/^Name/), 'platform-admins');

    expect(screen.getByText(/A group named "platform-admins" already exists/)).toBeInTheDocument();
    expect(screen.getByRole('link', {name: 'request access to it'})).toHaveAttribute('href', '/requests');
    expect(screen.getByRole('button', {name: 'Submit'})).toBeDisabled();
  });

  it('checks a "Role-" name typed as a plain group as the role it is filed as', async () => {
    renderDialog();

    await userEvent.type(screen.getByLabelText(/^Name/), 'Role-Platform-Engineers');

    expect(screen.getByText(/A group named "Role-Platform-Engineers" already exists/)).toBeInTheDocument();
    expect(screen.getByRole('button', {name: 'Submit'})).toBeDisabled();
  });

  it('allows a name that only partly matches an existing group', async () => {
    renderDialog();

    await userEvent.type(screen.getByLabelText(/^Name/), 'Platform');

    expect(screen.queryByText(/already exists/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', {name: 'Submit'})).toBeEnabled();
  });
});

async function pick(field: string, option: string) {
  await userEvent.click(screen.getByRole('combobox', {name: field}));
  await userEvent.click(await screen.findByRole('option', {name: option}));
}

describe('naming owners and members', () => {
  it('sends the named owners and members, with a membership end date', async () => {
    renderDialog();

    await userEvent.type(screen.getByLabelText(/^Name/), 'Support-Tools');
    await pick('Owners: users', OWNER_USER.display_name);
    expect(screen.queryByRole('combobox', {name: 'Requested membership length'})).not.toBeInTheDocument();
    await pick('Members: roles', MEMBER_ROLE.name);
    expect(screen.getByRole('combobox', {name: 'Requested membership length'})).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/Why do you need this group/), 'Support needs it');
    await userEvent.click(screen.getByRole('button', {name: 'Submit'}));

    await waitFor(() => expect(createMutate).toHaveBeenCalledTimes(1));
    const body = createMutate.mock.calls[0][0].body;
    expect(body).toMatchObject({
      requested_owner_user_ids: [OWNER_USER.id],
      requested_owner_role_ids: [],
      requested_member_user_ids: [],
      requested_member_role_ids: [MEMBER_ROLE.id],
    });
    expect(body.requested_membership_ending_at).toEqual(expect.any(String));
  });

  it('sends no owners and no membership end date when none are named', async () => {
    renderDialog();

    await userEvent.type(screen.getByLabelText(/^Name/), 'Support-Tools');
    await userEvent.type(screen.getByLabelText(/Why do you need this group/), 'Support needs it');
    await userEvent.click(screen.getByRole('button', {name: 'Submit'}));

    await waitFor(() => expect(createMutate).toHaveBeenCalledTimes(1));
    const body = createMutate.mock.calls[0][0].body;
    expect(body.requested_owner_user_ids).toEqual([]);
    expect(body.requested_owner_role_ids).toEqual([]);
    expect(body.requested_membership_ending_at).toBeUndefined();
  });
});

describe('naming roles on a role request', () => {
  it('disables the role pickers for a Role', async () => {
    renderDialog();

    await userEvent.click(screen.getByRole('combobox', {name: 'Group Type'}));
    await userEvent.click(await screen.findByRole('option', {name: 'Role'}));

    expect(screen.getByRole('combobox', {name: 'Owners: roles'})).toBeDisabled();
    expect(screen.getByRole('combobox', {name: 'Members: roles'})).toBeDisabled();
  });

  it('flags roles already named once the name files the request as a role', async () => {
    renderDialog();

    await pick('Members: roles', MEMBER_ROLE.name);
    await userEvent.type(screen.getByLabelText(/^Name/), 'Role-Support');

    expect(screen.getByRole('combobox', {name: 'Members: roles'})).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByRole('button', {name: 'Submit'})).toBeDisabled();
  });
});
