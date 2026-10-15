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
