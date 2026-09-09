import {render, screen, waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {describe, expect, it, vi, beforeEach} from 'vitest';

import {AppDetail, OktaUserDetail} from '../../api/apiSchemas';

const APP_TAG = {id: 'sox-tag-00000000000', name: 'SOX', constraints: {owner_time_limit: 7776000}};

// The app carries a tag of its own, which `CreateGroup` copies onto every group
// created under it whether or not the requester picks it.
const APP = {
  id: 'zendesk-sandbox-0000',
  name: 'HammerAndChiselZendeskSandbox',
  active_app_tags: [{active_tag: APP_TAG}],
} as unknown as AppDetail;

const USER = {id: 'user-000000000000', email: 'dana@example.com'} as unknown as OktaUserDetail;

const constraintsAskedFor = vi.fn();
// The owner-side limit the app's tag imposes, as the reader would report it.
const OWNER_LIMIT_SECONDS = 7776000;
let reportLimit = true;

vi.mock('react-router-dom', () => ({useNavigate: () => vi.fn()}));

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
  useGroupRequestsCreate: () => ({mutate: vi.fn()}),
  useApps: () => ({data: {items: [APP]}}),
  useAppById: () => ({data: APP, isLoading: false}),
  useTags: () => ({data: {items: []}}),
}));

import CreateRequest from './Create';

beforeEach(() => {
  constraintsAskedFor.mockClear();
  reportLimit = true;
});

const lastConstraintQuery = (): string[] => constraintsAskedFor.mock.calls.at(-1)![0];

async function openDialogAndPickApp() {
  render(<CreateRequest currentUser={USER} open setOpen={() => {}} />);
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

  it('shows them as belonging to the app rather than as a choice', async () => {
    await openDialogAndPickApp();
    expect(await screen.findByText(`Also inherited from ${APP.name}:`)).toBeInTheDocument();
    expect(screen.getByText(APP_TAG.name)).toBeInTheDocument();
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
