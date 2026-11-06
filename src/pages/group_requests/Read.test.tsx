import {describe, it, expect, vi, beforeEach} from 'vitest';
import {render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {QueryClient, QueryClientProvider} from '@tanstack/react-query';

import {AppDetail, GroupRequestDetail, OktaUserDetail} from '../../api/apiSchemas';
import {ACCESS_APP_RESERVED_NAME} from '../../authorization';

const resolveMutate = vi.fn();

const APP_NAME = 'HammerAndChiselZendeskSandbox';
const APP_TAG = {id: 'sox-tag-00000000000', name: 'SOX'};

// The app carries a tag of its own. `CreateGroup` copies it onto every group
// created under the app, so it binds the new group whether or not the approver
// picks it.
const APP = {
  id: 'zendesk-sandbox-0000',
  name: APP_NAME,
  active_app_tags: [{active_tag: APP_TAG}],
} as unknown as AppDetail;

// How the Tags field spells an inherited tag.
const INHERITED_CHIP = `${APP_TAG.name} (${APP_NAME})`;

// A tag the request names. The tag *list* mock below returns nothing, so this
// can only be resolved by asking for it by id.
const REQUESTED_TAG_ID = 'quarterly-review-01';

// An app owner, not an Access admin: ownership of the app's owner group is what lets
// them approve, and it is what locks the Type select.
const APP_OWNER = {
  id: 'appowner-0000000000',
  email: 'owner@example.com',
  active_group_memberships: [],
  active_group_ownerships: [
    {
      active_group: {
        id: 'owner-group-00000000',
        type: 'app_group',
        name: 'App-HammerAndChiselZendeskSandbox-Owners',
        is_owner: true,
        app: APP,
      },
    },
  ],
} as unknown as OktaUserDetail;

const PENDING_APP_GROUP_REQUEST = {
  id: 'request-000000000000',
  status: 'PENDING',
  created_at: '2026-09-01T00:00:00Z',
  requester: {id: 'requester-0000000000', email: 'requester@example.com'},
  requested_group_type: 'app_group',
  requested_group_name: 'App-HammerAndChiselZendeskSandbox-Admin',
  requested_group_description: 'Grants the Admin role',
  requested_app_id: APP.id,
  requested_group_tags: [REQUESTED_TAG_ID],
} as unknown as GroupRequestDetail;

const OWNER_USER = {id: 'owner-user-00000000', email: 'Owner@example.com', display_name: 'Olive Owner'};
const MEMBER_ROLE = {id: 'member-role-0000000', name: 'Role-Support-Agents'};

// The same request, naming an owner and a member role.
const NAMING_REQUEST = {
  ...PENDING_APP_GROUP_REQUEST,
  requested_owner_user_ids: [OWNER_USER.id],
  requested_owner_role_ids: [],
  requested_member_user_ids: [],
  requested_member_role_ids: [MEMBER_ROLE.id],
  requested_membership_ending_at: '2026-09-15T00:00:00Z',
  named_users: [OWNER_USER],
  named_roles: [MEMBER_ROLE],
} as unknown as GroupRequestDetail;

let groupRequest: GroupRequestDetail = PENDING_APP_GROUP_REQUEST;

vi.mock('react-router-dom', async () => {
  const React = await import('react');
  return {
    useNavigate: () => vi.fn(),
    useParams: () => ({id: PENDING_APP_GROUP_REQUEST.id}),
    Link: ({children}: {children?: React.ReactNode}) => children,
  };
});

let currentUser: OktaUserDetail = APP_OWNER;
vi.mock('../../authentication', () => ({useCurrentUser: () => currentUser}));

// The page asks the constraints endpoint what the tags impose, which needs a
// QueryClient this render does not provide. Stand in a resolved reader that
// restricts nothing unless a test sets an owner-side limit, and record the ids
// it is asked about so the tests below can assert which tags it covers.
let ownerTimeLimit: number | null = null;
const constraintsAskedFor = vi.fn();
vi.mock('../../constraints', () => {
  const reader = () => ({
    timeLimit: (isOwner: boolean) => (isOwner ? ownerTimeLimit : null),
    isReasonRequired: () => false,
    isSelfAddDisallowed: () => false,
  });
  return {
    timeLimitLabel: (seconds: number) => `${seconds / 86400} days`,
    useConstraintsForTags: (ids: string[]) => {
      constraintsAskedFor(ids);
      return {
        pending: false,
        error: null,
        blocked: false,
        ...reader(),
        forGroup: reader,
      };
    },
  };
});

// What GET /api/apps returns for the App field's search.
let appSearchResults: AppDetail[] = [APP];

vi.mock('../../api/apiComponents', () => ({
  // The page resolves each requested tag by its own id.
  tagByIdQuery: (variables: {pathParams: {tagId: string}}) => ({
    queryKey: ['tag', variables.pathParams.tagId],
    queryFn: async () => ({id: variables.pathParams.tagId, name: variables.pathParams.tagId}),
  }),
  useGroupRequestById: () => ({data: groupRequest, isError: false, isLoading: false}),
  useGroupRequestByIdPut: () => ({mutate: resolveMutate}),
  useAppById: () => ({data: APP, isLoading: false}),
  useApps: () => ({data: {items: appSearchResults}}),
  useTags: () => ({data: {items: []}}),
  useUsers: () => ({data: {items: [OWNER_USER]}}),
  useRoles: () => ({data: {items: [MEMBER_ROLE]}}),
}));

import ReadGroupRequest from './Read';

// `useTagsByIds` reaches react-query directly rather than through a generated
// hook, so these renders need a client of their own.
const renderPage = () =>
  render(
    <QueryClientProvider client={new QueryClient({defaultOptions: {queries: {retry: false}}})}>
      <ReadGroupRequest />
    </QueryClientProvider>,
  );

beforeEach(() => {
  resolveMutate.mockClear();
  constraintsAskedFor.mockClear();
  currentUser = APP_OWNER;
  groupRequest = PENDING_APP_GROUP_REQUEST;
  ownerTimeLimit = null;
  appSearchResults = [APP];
});

// The last id set the page asked about, which is what it will act on.
const lastConstraintQuery = (): string[] => constraintsAskedFor.mock.calls.at(-1)![0];

describe('an app owner approving an app group request', () => {
  // The Type select is locked for a non-admin approver. `submit` derives the resolved
  // group name and app id from that type, so it has to survive into the payload --
  // otherwise the request resolves to an unprefixed group with no app.
  it('resolves to the prefixed group name and the requested app', async () => {
    renderPage();

    const approve = await screen.findByRole('button', {name: /Approve/});
    await userEvent.click(approve);

    await waitFor(() => expect(resolveMutate).toHaveBeenCalledTimes(1));
    // `approved` is deliberately not asserted: the approve/reject choice is React state
    // set from the button's onClick, and jsdom dispatches submit before React flushes that
    // discrete update, so it reads as false here for either button. Browsers flush first.
    expect(resolveMutate.mock.calls[0][0].body).toMatchObject({
      resolved_group_type: 'app_group',
      resolved_group_name: 'App-HammerAndChiselZendeskSandbox-Admin',
      resolved_app_id: APP.id,
    });
  });

  it('still refuses to let the type be changed', async () => {
    renderPage();

    const typeSelect = await screen.findByRole('combobox', {name: 'Type'});
    await userEvent.click(typeSelect);

    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(typeSelect).toHaveTextContent('App Group');
  });
});

// A request's tags are resolved by id, not by searching the tag list. The list
// is paginated and ordered by name, so a request naming a tag that no single
// page contains would otherwise seed a partial selection -- and approval
// submits that selection, silently dropping the rest.
describe('a requested app the app search does not return', () => {
  // With many apps, the requested one can fall past the first page of results. The
  // field must still show it rather than render an empty required dropdown.
  it('is still shown in the App field', async () => {
    appSearchResults = [];
    renderPage();

    expect(await screen.findByRole('combobox', {name: /App/})).toHaveValue(APP.name);
  });
});

describe('a requested tag the tag list does not return', () => {
  it('is still resolved and submitted with the approval', async () => {
    renderPage();

    const approve = await screen.findByRole('button', {name: /Approve/});
    await waitFor(() => expect(screen.getByText(REQUESTED_TAG_ID)).toBeInTheDocument());
    await userEvent.click(approve);

    await waitFor(() => expect(resolveMutate).toHaveBeenCalledTimes(1));
    expect(resolveMutate.mock.calls[0][0].body.resolved_group_tags).toEqual([REQUESTED_TAG_ID]);
  });
});

// A request that asked for no ownership end date defaults the field to
// Indefinite, which a limit removes from the list along with every duration
// over it. Left there the Select has no matching option, renders blank, and
// still submits indefinite for the backend to shorten -- so the approver never
// sees the duration they are actually granting.
describe('an ownership duration the tag forbids', () => {
  it('moves the field to the longest duration still offered', async () => {
    ownerTimeLimit = 7776000;
    renderPage();

    await screen.findByRole('button', {name: /Approve/});
    const select = screen.getByRole('combobox', {name: /Ownership Ending At/});
    await waitFor(() => expect(select).toHaveTextContent('90 Days'));
    expect(select).not.toHaveTextContent('Indefinite');
  });
});

describe('an approver choosing how long ownership lasts', () => {
  const FIVE_DAY_REQUEST = {
    ...PENDING_APP_GROUP_REQUEST,
    requested_ownership_ending_at: '2026-09-06T00:00:00Z',
  } as unknown as GroupRequestDetail;

  it('may grant longer than requested', async () => {
    groupRequest = FIVE_DAY_REQUEST;
    renderPage();

    const approve = await screen.findByRole('button', {name: /Approve/});
    await userEvent.click(screen.getByRole('combobox', {name: /Ownership Ending At/}));
    await userEvent.click(await screen.findByRole('option', {name: '90 Days'}));
    await userEvent.click(approve);

    await waitFor(() => expect(resolveMutate).toHaveBeenCalledTimes(1));
    const endingAt = resolveMutate.mock.calls[0][0].body.resolved_ownership_ending_at;
    expect(new Date(endingAt).getTime()).toBeGreaterThan(Date.now() + 89 * 86400 * 1000);
  });

  it('sends nothing when the request asked for indefinite ownership and it is kept', async () => {
    renderPage();

    await userEvent.click(await screen.findByRole('button', {name: /Approve/}));

    await waitFor(() => expect(resolveMutate).toHaveBeenCalledTimes(1));
    expect(resolveMutate.mock.calls[0][0].body.resolved_ownership_ending_at).toBeUndefined();
  });
});

// A group created under an app carries that app's tags on top of the ones the
// approver picks, so the approval form has to answer for both: the durations it
// offers come from the constraints it asked about, and `ApproveGroupRequest`
// shortens the granted ownership against the tags the group actually ends up
// with.
describe('a request naming owners and members', () => {
  it('lists them among the requested details', async () => {
    groupRequest = NAMING_REQUEST;
    renderPage();

    await screen.findByRole('button', {name: /Approve/});
    expect(screen.getByText('Owners:')).toBeInTheDocument();
    expect(screen.getByText('Members:')).toBeInTheDocument();
    expect(screen.getByText(/Membership Ending:/)).toBeInTheDocument();
    // Once in the requested details and once in the approval pickers.
    expect(screen.getAllByText(OWNER_USER.display_name)).toHaveLength(2);
    expect(screen.getAllByText(MEMBER_ROLE.name)).toHaveLength(2);
  });

  it('submits them with the approval, along with a membership end date', async () => {
    groupRequest = NAMING_REQUEST;
    renderPage();

    await userEvent.click(await screen.findByRole('button', {name: /Approve/}));

    await waitFor(() => expect(resolveMutate).toHaveBeenCalledTimes(1));
    const body = resolveMutate.mock.calls[0][0].body;
    // Unchanged lists are left for the backend to read from the request.
    expect(body.resolved_owner_user_ids).toBeUndefined();
    expect(body.resolved_owner_role_ids).toBeUndefined();
    expect(body.resolved_member_user_ids).toBeUndefined();
    expect(body.resolved_member_role_ids).toBeUndefined();
    expect(body.resolved_membership_ending_at).toEqual(expect.any(String));
  });

  // `named_users` omits a user deleted since filing, so the pickers start without them.
  it('leaves untouched lists to the backend even when a named user is gone', async () => {
    groupRequest = {
      ...NAMING_REQUEST,
      requested_owner_user_ids: [OWNER_USER.id, 'deleted-user-000000'],
    } as unknown as GroupRequestDetail;
    renderPage();

    await userEvent.click(await screen.findByRole('button', {name: /Approve/}));

    await waitFor(() => expect(resolveMutate).toHaveBeenCalledTimes(1));
    const body = resolveMutate.mock.calls[0][0].body;
    expect(body.resolved_owner_user_ids).toBeUndefined();
    expect(body.resolved_owner_role_ids).toBeUndefined();
    expect(body.resolved_member_user_ids).toBeUndefined();
    expect(body.resolved_member_role_ids).toBeUndefined();
  });

  it('sends only the list whose picker the approver changed', async () => {
    groupRequest = NAMING_REQUEST;
    renderPage();

    const approve = await screen.findByRole('button', {name: /Approve/});
    const ownerUsers = screen.getByRole('combobox', {name: 'Owners: users'}).closest('.MuiAutocomplete-root');
    await userEvent.click(within(ownerUsers as HTMLElement).getByTestId('CancelIcon'));
    await userEvent.click(approve);

    await waitFor(() => expect(resolveMutate).toHaveBeenCalledTimes(1));
    const body = resolveMutate.mock.calls[0][0].body;
    expect(body.resolved_owner_user_ids).toEqual([]);
    expect(body.resolved_owner_role_ids).toBeUndefined();
    expect(body.resolved_member_user_ids).toBeUndefined();
    expect(body.resolved_member_role_ids).toBeUndefined();
  });

  it('refuses approval while a Role names a role', async () => {
    groupRequest = {
      ...NAMING_REQUEST,
      requested_group_type: 'role_group',
      requested_group_name: 'Role-Support',
      requested_app_id: null,
    } as unknown as GroupRequestDetail;
    currentUser = {
      ...APP_OWNER,
      active_group_memberships: [
        {active_group: {type: 'app_group', is_owner: true, app: {name: ACCESS_APP_RESERVED_NAME}}},
      ],
    } as unknown as OktaUserDetail;
    renderPage();

    const approve = await screen.findByRole('button', {name: /Approve/});
    await waitFor(() =>
      expect(screen.getByRole('combobox', {name: 'Members: roles'})).toHaveAttribute('aria-invalid', 'true'),
    );
    expect(approve).toBeDisabled();
  });
});

describe('tags an app group inherits from its app', () => {
  it('asks about them alongside the tags the approver picked', async () => {
    renderPage();

    await screen.findByRole('button', {name: /Approve/});
    await waitFor(() => expect(lastConstraintQuery()).toContain(APP_TAG.id));
  });

  it('lists them in the Tags field, naming the app they come from', async () => {
    renderPage();

    expect(await screen.findByText(INHERITED_CHIP)).toBeInTheDocument();
  });

  // They sit among the approver's own chips, so the only thing marking them as
  // the app's is that they cannot be taken off.
  it('gives them no delete control', async () => {
    renderPage();

    const chip = (await screen.findByText(INHERITED_CHIP)).closest('.MuiChip-root');
    expect(chip).not.toBeNull();
    expect(within(chip as HTMLElement).queryByTestId('CancelIcon')).toBeNull();
  });

  it('leaves them out of the resolved tags, which the app supplies on its own', async () => {
    renderPage();

    const approve = await screen.findByRole('button', {name: /Approve/});
    await userEvent.click(approve);

    await waitFor(() => expect(resolveMutate).toHaveBeenCalledTimes(1));
    // Submitting them would write a second tag map with no link to the app, so
    // the tag would stay on the group after being removed from the app.
    expect(resolveMutate.mock.calls[0][0].body.resolved_group_tags).not.toContain(APP_TAG.id);
  });

  it('stops asking about them once the type is no longer an app group', async () => {
    // Only an Access admin can change the type; an app owner's select is read-only.
    currentUser = {
      ...APP_OWNER,
      active_group_memberships: [
        {active_group: {type: 'app_group', is_owner: true, app: {name: ACCESS_APP_RESERVED_NAME}}},
      ],
    } as unknown as OktaUserDetail;
    renderPage();

    await waitFor(() => expect(lastConstraintQuery()).toContain(APP_TAG.id));

    await userEvent.click(await screen.findByRole('combobox', {name: 'Type'}));
    await userEvent.click(await screen.findByRole('option', {name: 'Group'}));

    await waitFor(() => expect(lastConstraintQuery()).not.toContain(APP_TAG.id));
    expect(screen.queryByText(INHERITED_CHIP)).not.toBeInTheDocument();
  });
});

describe('a request from a service principal', () => {
  const SERVICE_REQUEST = {
    ...NAMING_REQUEST,
    requester: null,
    requester_service_principal: {
      id: 'sp_00000000000000001',
      display_name: 'Deploy Bot',
      provider: 'cloudflare',
      subject: 'client.access',
      created_at: null,
      updated_at: null,
      owners: [{role_group_id: MEMBER_ROLE.id, active_role_group: {...MEMBER_ROLE, type: 'role_group'}}],
    },
  } as unknown as GroupRequestDetail;

  it('names the service principal and its owners as the requester', async () => {
    groupRequest = SERVICE_REQUEST;
    renderPage();

    expect((await screen.findAllByText('Deploy Bot')).length).toBeGreaterThan(0);
    expect(screen.getAllByLabelText('service principal').length).toBeGreaterThan(0);
    expect(screen.getByText('Owned by')).toBeInTheDocument();
  });

  it('lets an approver act on it', async () => {
    groupRequest = SERVICE_REQUEST;
    renderPage();

    await userEvent.click(await screen.findByRole('button', {name: /Approve/}));
    await waitFor(() => expect(resolveMutate).toHaveBeenCalledTimes(1));
  });
});
