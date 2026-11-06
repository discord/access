import {describe, it, expect, vi, beforeEach} from 'vitest';
import {render, screen, waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {QueryClient, QueryClientProvider} from '@tanstack/react-query';

import {AppRequestDetail, OktaUserDetail} from '../../api/apiSchemas';
import {ACCESS_APP_RESERVED_NAME} from '../../authorization';

const resolveMutate = vi.fn();

const REQUESTER = {id: 'requester-0000000000', email: 'requester@example.com'};
const OWNER_USER = {id: 'owner-user-00000000', email: 'Owner@example.com', display_name: 'Olive Owner'};
const MEMBER_ROLE = {id: 'member-role-0000000', name: 'Role-Support-Agents'};

const PENDING_REQUEST = {
  id: 'request-000000000000',
  status: 'PENDING',
  created_at: '2026-09-01T00:00:00Z',
  requester: REQUESTER,
  requested_app_name: 'Support-Desk',
  requested_app_description: 'Ticketing for support',
  requested_app_tags: [],
  requested_owner_user_ids: [OWNER_USER.id],
  requested_owner_role_ids: [],
  requested_app_groups: [
    {
      name: 'App-Support-Desk-Agents',
      description: 'Agents answer tickets',
      owner_user_ids: [],
      owner_role_ids: [],
      member_user_ids: [],
      member_role_ids: [MEMBER_ROLE.id],
    },
  ],
  requested_ownership_ending_at: '2026-09-15T00:00:00Z',
  requested_membership_ending_at: '2026-09-15T00:00:00Z',
  named_users: [OWNER_USER],
  named_roles: [MEMBER_ROLE],
} as unknown as AppRequestDetail;

let appRequest: AppRequestDetail = PENDING_REQUEST;

vi.mock('react-router-dom', async () => {
  const React = await import('react');
  return {
    useNavigate: () => vi.fn(),
    useParams: () => ({id: PENDING_REQUEST.id}),
    Link: ({children}: {children?: React.ReactNode}) => children,
  };
});

const ADMIN = {
  id: 'admin-00000000000000',
  email: 'admin@example.com',
  active_group_ownerships: [],
  active_group_memberships: [
    {active_group: {type: 'app_group', is_owner: true, app: {name: ACCESS_APP_RESERVED_NAME}}},
  ],
} as unknown as OktaUserDetail;

const OTHER_USER = {
  id: 'other-00000000000000',
  email: 'other@example.com',
  active_group_ownerships: [],
  active_group_memberships: [],
} as unknown as OktaUserDetail;

let currentUser: OktaUserDetail = ADMIN;
vi.mock('../../authentication', () => ({useCurrentUser: () => currentUser}));

vi.mock('../../constraints', () => {
  const reader = () => ({
    timeLimit: () => null,
    isReasonRequired: () => false,
    isSelfAddDisallowed: () => false,
  });
  return {
    timeLimitLabel: (seconds: number) => `${seconds / 86400} days`,
    useConstraintsForTags: () => ({pending: false, error: null, blocked: false, ...reader(), forGroup: reader}),
  };
});

vi.mock('../../api/apiComponents', () => ({
  tagByIdQuery: (variables: {pathParams: {tagId: string}}) => ({
    queryKey: ['tag', variables.pathParams.tagId],
    queryFn: async () => ({id: variables.pathParams.tagId, name: variables.pathParams.tagId}),
  }),
  useAppRequestById: () => ({data: appRequest, isError: false, isLoading: false}),
  useAppRequestByIdPut: () => ({mutate: resolveMutate}),
}));

import ReadAppRequest from './Read';

const renderPage = () =>
  render(
    <QueryClientProvider client={new QueryClient({defaultOptions: {queries: {retry: false}}})}>
      <ReadAppRequest />
    </QueryClientProvider>,
  );

beforeEach(() => {
  resolveMutate.mockClear();
  currentUser = ADMIN;
  appRequest = PENDING_REQUEST;
});

describe('the requested details', () => {
  it('lists the app owners and each initial group with its members', async () => {
    renderPage();

    expect(screen.getByText('App-Support-Desk-Agents')).toBeInTheDocument();
    expect(screen.getByText('Agents answer tickets')).toBeInTheDocument();
    expect(screen.getByText('App owners:')).toBeInTheDocument();
    expect(screen.getByText(OWNER_USER.display_name)).toBeInTheDocument();
    expect(screen.getByText(MEMBER_ROLE.name)).toBeInTheDocument();
    expect(screen.getByText(/Membership Ending:/)).toBeInTheDocument();
  });

  it('counts named owners who are no longer active as departed', async () => {
    appRequest = {
      ...PENDING_REQUEST,
      requested_owner_user_ids: [OWNER_USER.id, 'gone-user-000000000'],
    } as unknown as AppRequestDetail;
    renderPage();

    expect(screen.getByText(OWNER_USER.display_name)).toBeInTheDocument();
    expect(screen.getByText('1 departed user')).toBeInTheDocument();
  });
});

describe('a pending request naming principals no longer active', () => {
  const DEPARTED_REQUEST = {
    ...PENDING_REQUEST,
    requested_owner_user_ids: [OWNER_USER.id, 'gone-user-000000000'],
    requested_app_groups: [
      {...PENDING_REQUEST.requested_app_groups![0], member_role_ids: [MEMBER_ROLE.id, 'gone-role-000000000']},
    ],
  } as unknown as AppRequestDetail;

  it('warns an Access admin where they are named and that approval skips them', async () => {
    appRequest = DEPARTED_REQUEST;
    renderPage();

    const warning = screen.getByText(/will be skipped on approval/);
    expect(warning).toHaveTextContent('2 named users or roles are no longer active');
    expect(warning).toHaveTextContent('1 in app owners');
    expect(warning).toHaveTextContent('1 in App-Support-Desk-Agents members');
    expect(screen.getByRole('button', {name: /Approve/})).toBeEnabled();
    expect(screen.getByRole('button', {name: /Reject/})).toBeEnabled();
  });

  it('cannot be approved when no app owner is still active', async () => {
    appRequest = {
      ...DEPARTED_REQUEST,
      requested_owner_user_ids: ['gone-user-000000000'],
    } as unknown as AppRequestDetail;
    renderPage();

    expect(screen.getByText(/At least one app owner must still be active/)).toBeInTheDocument();
    expect(screen.getByRole('button', {name: /Approve/})).toBeDisabled();
    expect(screen.getByRole('button', {name: /Reject/})).toBeEnabled();
  });

  it('shows no warning to a user who cannot approve it', async () => {
    appRequest = DEPARTED_REQUEST;
    currentUser = OTHER_USER;
    renderPage();

    expect(screen.queryByText(/no longer active/)).not.toBeInTheDocument();
  });
});

describe('an Access admin reviewing a pending request', () => {
  it('can approve or reject it', async () => {
    renderPage();

    expect(screen.getByRole('button', {name: /Approve/})).toBeInTheDocument();
    expect(screen.getByRole('button', {name: /Reject/})).toBeInTheDocument();
  });

  it('sends end dates with the approval', async () => {
    renderPage();

    await userEvent.click(screen.getByRole('button', {name: /Approve/}));

    await waitFor(() => expect(resolveMutate).toHaveBeenCalledTimes(1));
    const call = resolveMutate.mock.calls[0][0];
    expect(call.pathParams).toEqual({appRequestId: PENDING_REQUEST.id});
    expect(call.body.resolved_ownership_ending_at).toEqual(expect.any(String));
    expect(call.body.resolved_membership_ending_at).toEqual(expect.any(String));
  });

  it.each([
    ['Ownership Ending At', 'resolved_ownership_ending_at'],
    ['Membership Ending At', 'resolved_membership_ending_at'],
  ])('offers %s no longer than requested, nor Indefinite', async (label, field) => {
    // Filed two days ago for five days, so a five-day choice must still end when requested.
    const requestedEndingAt = new Date(Date.now() + 3 * 86400 * 1000).toISOString();
    appRequest = {
      ...PENDING_REQUEST,
      created_at: new Date(Date.now() - 2 * 86400 * 1000).toISOString(),
      requested_ownership_ending_at: requestedEndingAt,
      requested_membership_ending_at: requestedEndingAt,
    } as unknown as AppRequestDetail;
    renderPage();

    await userEvent.click(screen.getByRole('combobox', {name: label}));
    const options = (await screen.findAllByRole('option')).map((option) => option.textContent);
    expect(options).toEqual(['12 Hours', '5 Days', 'Custom']);
    await userEvent.click(screen.getByRole('option', {name: '5 Days'}));
    await userEvent.click(screen.getByRole('button', {name: /Approve/}));

    await waitFor(() => expect(resolveMutate).toHaveBeenCalledTimes(1));
    const endingAt = resolveMutate.mock.calls[0][0].body[field];
    expect(new Date(endingAt).getTime()).toBeLessThanOrEqual(new Date(requestedEndingAt).getTime());
  });

  it('offers Indefinite and longer durations when the request has no end date', async () => {
    appRequest = {
      ...PENDING_REQUEST,
      requested_ownership_ending_at: null,
      requested_membership_ending_at: null,
    } as unknown as AppRequestDetail;
    renderPage();

    await userEvent.click(screen.getByRole('combobox', {name: /Ownership Ending At/}));
    expect(await screen.findByRole('option', {name: 'Indefinite'})).toBeInTheDocument();
    await userEvent.click(screen.getByRole('option', {name: '90 Days'}));
    await userEvent.click(screen.getByRole('button', {name: /Approve/}));

    await waitFor(() => expect(resolveMutate).toHaveBeenCalledTimes(1));
    const endingAt = resolveMutate.mock.calls[0][0].body.resolved_ownership_ending_at;
    expect(new Date(endingAt).getTime()).toBeGreaterThan(Date.now() + 89 * 86400 * 1000);
  });

  it('sends nothing when the request asked for indefinite ownership and it is kept', async () => {
    appRequest = {...PENDING_REQUEST, requested_ownership_ending_at: null} as unknown as AppRequestDetail;
    renderPage();

    await userEvent.click(screen.getByRole('button', {name: /Approve/}));

    await waitFor(() => expect(resolveMutate).toHaveBeenCalledTimes(1));
    expect(resolveMutate.mock.calls[0][0].body.resolved_ownership_ending_at).toBeUndefined();
  });
});

describe('the requester viewing their own pending request', () => {
  it('can withdraw it but not approve it, even as an Access admin', async () => {
    currentUser = {...ADMIN, id: REQUESTER.id} as unknown as OktaUserDetail;
    renderPage();

    expect(screen.queryByRole('button', {name: /Approve/})).not.toBeInTheDocument();
    expect(screen.queryByRole('combobox', {name: /Ownership Ending At/})).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', {name: /Withdraw/}));
    await waitFor(() => expect(resolveMutate).toHaveBeenCalledTimes(1));
    expect(resolveMutate.mock.calls[0][0].body.resolved_ownership_ending_at).toBeUndefined();
  });
});

describe('another user viewing a pending request', () => {
  it('cannot act on it', async () => {
    currentUser = OTHER_USER;
    renderPage();

    expect(screen.queryByRole('button', {name: /Approve/})).not.toBeInTheDocument();
    expect(screen.queryByRole('button', {name: /Reject|Withdraw/})).not.toBeInTheDocument();
    expect(screen.getByText(/can be reviewed by Access admins/)).toBeInTheDocument();
  });
});

describe('an approved request', () => {
  it('links to the created app and shows no form', async () => {
    appRequest = {
      ...PENDING_REQUEST,
      status: 'APPROVED',
      resolved_at: '2026-09-02T00:00:00Z',
      resolver: {id: ADMIN.id, email: ADMIN.email},
      resolved_ownership_ending_at: '2026-09-10T00:00:00Z',
    } as unknown as AppRequestDetail;
    renderPage();

    expect(screen.getByText(/approved the request and created app/)).toBeInTheDocument();
    expect(screen.queryByRole('button', {name: /Approve/})).not.toBeInTheDocument();
  });
});

describe('a request its service principal withdrew', () => {
  it('names the service principal as the resolver', async () => {
    appRequest = {
      ...PENDING_REQUEST,
      status: 'REJECTED',
      resolved_at: '2026-09-02T00:00:00Z',
      resolver: null,
      resolver_service_principal: {id: 'sp-0000000000000000', display_name: 'Provisioning Bot', owners: []},
    } as unknown as AppRequestDetail;
    renderPage();

    expect(screen.getByText('Provisioning Bot')).toBeInTheDocument();
    expect(screen.getByText(/rejected the request/)).toBeInTheDocument();
  });
});
