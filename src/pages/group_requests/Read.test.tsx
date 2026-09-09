import {describe, it, expect, vi, beforeEach} from 'vitest';
import {render, screen, waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {QueryClient, QueryClientProvider} from '@tanstack/react-query';

import {AppDetail, GroupRequestDetail, OktaUserDetail} from '../../api/apiSchemas';
import {ACCESS_APP_RESERVED_NAME} from '../../authorization';

const resolveMutate = vi.fn();

const APP_TAG = {id: 'sox-tag-00000000000', name: 'SOX'};

// The app carries a tag of its own. `CreateGroup` copies it onto every group
// created under the app, so it binds the new group whether or not the approver
// picks it.
const APP = {
  id: 'zendesk-sandbox-0000',
  name: 'HammerAndChiselZendeskSandbox',
  active_app_tags: [{active_tag: APP_TAG}],
} as unknown as AppDetail;

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

vi.mock('../../api/apiComponents', () => ({
  // The page resolves each requested tag by its own id.
  tagByIdQuery: (variables: {pathParams: {tagId: string}}) => ({
    queryKey: ['tag', variables.pathParams.tagId],
    queryFn: async () => ({id: variables.pathParams.tagId, name: variables.pathParams.tagId}),
  }),
  useGroupRequestById: () => ({data: PENDING_APP_GROUP_REQUEST, isError: false, isLoading: false}),
  useGroupRequestByIdPut: () => ({mutate: resolveMutate}),
  useAppById: () => ({data: APP, isLoading: false}),
  useApps: () => ({data: {items: [APP]}}),
  useTags: () => ({data: {items: []}}),
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
  ownerTimeLimit = null;
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

// A group created under an app carries that app's tags on top of the ones the
// approver picks, so the approval form has to answer for both: the durations it
// offers come from the constraints it asked about, and `ApproveGroupRequest`
// shortens the granted ownership against the tags the group actually ends up
// with.
describe('tags an app group inherits from its app', () => {
  it('asks about them alongside the tags the approver picked', async () => {
    renderPage();

    await screen.findByRole('button', {name: /Approve/});
    await waitFor(() => expect(lastConstraintQuery()).toContain(APP_TAG.id));
  });

  it('shows them as belonging to the app rather than as a choice', async () => {
    renderPage();

    expect(await screen.findByText(`Also inherited from ${APP.name}:`)).toBeInTheDocument();
    expect(screen.getByText(APP_TAG.name)).toBeInTheDocument();
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
    expect(screen.queryByText(`Also inherited from ${APP.name}:`)).not.toBeInTheDocument();
  });
});
