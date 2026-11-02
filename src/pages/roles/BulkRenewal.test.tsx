import {describe, it, expect, vi, beforeEach} from 'vitest';
import {render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import {ThemeProvider, createTheme} from '@mui/material/styles';
import dayjs from 'dayjs';
import RelativeTime from 'dayjs/plugin/relativeTime';

import {OktaUserDetail, RoleGroupMapDetail} from '../../api/apiSchemas';
import {ACCESS_APP_RESERVED_NAME} from '../../authorization';

dayjs.extend(RelativeTime);

// The row grid reads `palette.highlight.*`, which the app's theme adds.
const base = createTheme();
const theme = createTheme(base, {
  palette: {
    highlight: {
      success: base.palette.augmentColor({color: {main: '#00ff00'}, name: 'success'}),
      warning: base.palette.augmentColor({color: {main: '#ffff00'}, name: 'warning'}),
      danger: base.palette.augmentColor({color: {main: '#ff0000'}, name: 'danger'}),
      info: base.palette.augmentColor({color: {main: '#cccccc'}, name: 'info'}),
    },
  },
} as any);

const ROLE = {id: 'role-000000000001', name: 'Eng-Oncall', type: 'role_group', is_managed: true};
const TAGGED = {id: 'group-tagged-0001', name: 'Prod-Admins', type: 'okta_group', is_managed: true};
const PLAIN = {id: 'group-plain-00001', name: 'Wiki-Editors', type: 'okta_group', is_managed: true};
const DELETED = {id: 'group-deleted-001', name: 'Old-Group', type: 'okta_group', is_managed: true};

const row = (id: number, group: typeof TAGGED) =>
  ({
    id,
    is_owner: false,
    role_group: ROLE,
    group,
    created_at: '2026-01-01T00:00:00Z',
    ended_at: dayjs().add(3, 'day').toISOString(),
  }) as unknown as RoleGroupMapDetail;

const TAGGED_ROW = row(1, TAGGED);
const PLAIN_ROW = row(2, PLAIN);
const DELETED_ROW = row(3, DELETED);

const ROLE_MEMBERSHIP = {active_group: ROLE};
const ROLE_OWNERSHIP = {active_group: ROLE};
const ADMIN_MEMBERSHIP = {
  active_group: {id: 'admins', type: 'app_group', is_owner: true, app: {name: ACCESS_APP_RESERVED_NAME}},
};

let currentUser: OktaUserDetail;
vi.mock('../../authentication', () => ({useCurrentUser: () => currentUser}));

vi.mock('react-router-dom', () => ({useNavigate: () => vi.fn()}));

// Tagged rows disallow self-add; the deleted group is absent from the answer.
vi.mock('../../constraints', () => ({
  timeLimitLabel: (seconds: number) => `${seconds / 86400} days`,
  useConstraintsForGroups: () => ({
    pending: false,
    error: null,
    blocked: false,
    timeLimit: () => null,
    isReasonRequired: () => false,
    isSelfAddDisallowed: () => false,
    forGroup: (groupId: string) => ({
      answered: groupId !== DELETED.id,
      timeLimit: () => null,
      isReasonRequired: () => false,
      isSelfAddDisallowed: () => groupId !== PLAIN.id,
    }),
  }),
}));

const mutate = vi.fn();
vi.mock('../../api/apiComponents', () => ({
  useRoleMembersByIdPut: () => ({mutate}),
  useGroupById: ({pathParams: {groupId}}: {pathParams: {groupId: string}}) => ({
    data: [ROLE, TAGGED, PLAIN].find((g) => g.id === groupId),
    isError: false,
    refetch: vi.fn(),
  }),
}));

vi.mock('../role_requests/Create', () => ({
  default: (props: any) => (
    <div role="dialog" aria-label="role request">
      {props.role.name} → {props.group.name} ({props.owner ? 'owner' : 'member'})
    </div>
  ),
}));

import BulkRenewal from './BulkRenewal';

beforeEach(() => {
  mutate.mockClear();
});

async function openDialog(rows: RoleGroupMapDetail[], select?: number) {
  render(
    <ThemeProvider theme={theme}>
      <BulkRenewal rows={rows} select={select} />
    </ThemeProvider>,
  );
  await userEvent.click(await screen.findByRole('button', {name: /Bulk Review|Review/}));
}

const gridRow = (groupName: string) => screen.getByRole('gridcell', {name: groupName}).closest('[role="row"]')!;

describe('a row blocked by a self-add tag', () => {
  it('offers a role owner a renewal request for that row', async () => {
    currentUser = {active_group_memberships: [ROLE_MEMBERSHIP], active_group_ownerships: [ROLE_OWNERSHIP]} as any;
    await openDialog([TAGGED_ROW, PLAIN_ROW]);

    expect(within(gridRow(PLAIN.name) as HTMLElement).getByRole('button', {name: 'yes'})).toBeEnabled();
    await userEvent.click(within(gridRow(TAGGED.name) as HTMLElement).getByRole('button', {name: /Request renewal/}));

    expect(await screen.findByRole('dialog', {name: 'role request'})).toHaveTextContent(
      'Eng-Oncall → Prod-Admins (member)',
    );
  });

  it('points a role member who does not own the role to another group owner', async () => {
    currentUser = {active_group_memberships: [ROLE_MEMBERSHIP], active_group_ownerships: []} as any;
    await openDialog([TAGGED_ROW]);

    const tagged = within(gridRow(TAGGED.name) as HTMLElement);
    expect(tagged.getByText(/You do not own this role/)).toBeInTheDocument();
    expect(tagged.queryByRole('button')).toBeNull();
  });

  it('does not blame a tag when the group has no answer', async () => {
    currentUser = {active_group_memberships: [ROLE_MEMBERSHIP], active_group_ownerships: [ROLE_OWNERSHIP]} as any;
    await openDialog([DELETED_ROW]);

    const deleted = within(gridRow(DELETED.name) as HTMLElement);
    expect(deleted.getByText(/Unable to load this group's restrictions/)).toBeInTheDocument();
    expect(deleted.queryByText(/group tag/)).toBeNull();
    expect(deleted.queryByRole('button')).toBeNull();
  });
});

describe('submitting', () => {
  it('leaves a pre-selected blocked row out of a non-admin renewal', async () => {
    currentUser = {active_group_memberships: [ROLE_MEMBERSHIP], active_group_ownerships: [ROLE_OWNERSHIP]} as any;
    await openDialog([TAGGED_ROW, PLAIN_ROW], TAGGED_ROW.id);

    await userEvent.click(within(gridRow(PLAIN.name) as HTMLElement).getByRole('button', {name: 'yes'}));
    await userEvent.click(screen.getByRole('button', {name: 'Submit'}));

    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(mutate.mock.calls[0][0].body.groups_to_add).toEqual([PLAIN.id]);
    expect(screen.queryByText(/As an admin/)).toBeNull();
  });

  it('lets an admin renew a blocked row after confirming the override', async () => {
    currentUser = {
      active_group_memberships: [ROLE_MEMBERSHIP, ADMIN_MEMBERSHIP],
      active_group_ownerships: [ROLE_OWNERSHIP],
    } as any;
    await openDialog([TAGGED_ROW]);

    await userEvent.click(within(gridRow(TAGGED.name) as HTMLElement).getByRole('button', {name: 'yes'}));
    await userEvent.click(screen.getByRole('button', {name: 'Submit'}));
    expect(await screen.findByText(/As an admin, you can override this/)).toBeInTheDocument();
    expect(mutate).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole('button', {name: 'Submit'}));
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(mutate.mock.calls[0][0].body.groups_to_add).toEqual([TAGGED.id]);
  });
});
