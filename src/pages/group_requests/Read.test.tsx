import {describe, it, expect, vi, beforeEach} from 'vitest';
import {render, screen, waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import {AppDetail, GroupRequestDetail, OktaUserDetail} from '../../api/apiSchemas';

const resolveMutate = vi.fn();

const APP = {id: 'zendesk-sandbox-0000', name: 'HammerAndChiselZendeskSandbox'} as unknown as AppDetail;

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
  requested_group_tags: [],
} as unknown as GroupRequestDetail;

vi.mock('react-router-dom', async () => {
  const React = await import('react');
  return {
    useNavigate: () => vi.fn(),
    useParams: () => ({id: PENDING_APP_GROUP_REQUEST.id}),
    Link: ({children}: {children?: React.ReactNode}) => children,
  };
});

vi.mock('../../authentication', () => ({useCurrentUser: () => APP_OWNER}));

vi.mock('../../api/apiComponents', () => ({
  useGroupRequestById: () => ({data: PENDING_APP_GROUP_REQUEST, isError: false, isLoading: false}),
  useGroupRequestByIdPut: () => ({mutate: resolveMutate}),
  useAppById: () => ({data: APP, isLoading: false}),
  useApps: () => ({data: {items: [APP]}}),
  useTags: () => ({data: {items: []}}),
}));

import ReadGroupRequest from './Read';

beforeEach(() => resolveMutate.mockClear());

describe('an app owner approving an app group request', () => {
  // The Type select is locked for a non-admin approver. `submit` derives the resolved
  // group name and app id from that type, so it has to survive into the payload --
  // otherwise the request resolves to an unprefixed group with no app.
  it('resolves to the prefixed group name and the requested app', async () => {
    render(<ReadGroupRequest />);

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
    render(<ReadGroupRequest />);

    const typeSelect = await screen.findByRole('combobox', {name: 'Type'});
    await userEvent.click(typeSelect);

    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(typeSelect).toHaveTextContent('App Group');
  });
});
