import {describe, it, expect, vi, beforeEach} from 'vitest';
import {render, screen, waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import {ThemeProvider, createTheme} from '@mui/material/styles';
import dayjs from 'dayjs';
import RelativeTime from 'dayjs/plugin/relativeTime';

import {OktaUserGroupMemberDetail} from '../../api/apiSchemas';

// The dialog renders relative timestamps; the app extends dayjs where it first
// needs this, which this module graph does not reach.
dayjs.extend(RelativeTime);

// The row grid reads `palette.highlight.*`, which the app's theme adds. Only
// the key names matter here, not the app's colours, so this supplies the shape
// rather than mirroring `App.tsx` -- a section the grid starts reading that is
// missing here fails loudly instead of silently rendering differently.
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

// What the stand-in reader reports, so a test can walk the states a selection
// change moves through: in flight, then resolved.
let reported: {blocked: boolean; timeLimit: number | null} = {blocked: false, timeLimit: null};

vi.mock('react-router-dom', () => ({useNavigate: () => vi.fn()}));

vi.mock('../../constraints', () => ({
  timeLimitLabel: (seconds: number) => `${seconds / 86400} days`,
  useConstraintsForGroups: () => ({
    pending: reported.blocked,
    error: null,
    blocked: reported.blocked,
    timeLimit: () => reported.timeLimit,
    isReasonRequired: () => false,
    isSelfAddDisallowed: () => false,
    forGroup: () => ({timeLimit: () => null, isReasonRequired: () => false, isSelfAddDisallowed: () => false}),
  }),
}));

vi.mock('../../api/apiComponents', () => ({
  useGroupMembersByIdPut: () => ({mutate: vi.fn()}),
}));

import BulkRenewal from './BulkRenewal';

const ROW = {
  id: 'membership-000000001',
  is_owner: false,
  active_user: {id: 'user-000000000001', email: 'dana@example.com', display_name: 'Dana Reyes'},
  active_group: {id: 'group-00000000001', name: 'Zendesk-Admin', type: 'okta_group'},
  group: {id: 'group-00000000001', name: 'Zendesk-Admin', type: 'okta_group'},
  ended_at: '2026-12-01T00:00:00Z',
} as unknown as OktaUserGroupMemberDetail;

beforeEach(() => {
  reported = {blocked: false, timeLimit: null};
});

async function openDialog() {
  const view = render(
    <ThemeProvider theme={theme}>
      <BulkRenewal rows={[ROW]} />
    </ThemeProvider>,
  );
  await userEvent.click(await screen.findByRole('button', {name: 'Bulk Review'}));
  return view;
}

// The duration control's label is not associated with its input, and the row
// grid contributes a combobox of its own, so it is reached through the form
// control the label sits in.
const durationSelect = (): HTMLElement => {
  const label = Array.from(document.querySelectorAll('label')).find((l) => l.textContent === 'For how long?');
  const control = label?.closest('.MuiFormControl-root')?.querySelector('[role="combobox"]');
  if (!control) throw new Error('duration select not found');
  return control as HTMLElement;
};

const durationOptions = async () => {
  await userEvent.click(durationSelect());
  const options = screen.getAllByRole('option').map((o) => o.textContent);
  await userEvent.keyboard('{Escape}');
  return options;
};

// The narrowed duration list has to widen again when the selection stops being
// constrained. Both states report a null limit -- in flight it is null because
// the reader fails closed, and afterwards because nothing constrains the rows --
// so the limit alone cannot distinguish them.
describe('a selection that stops being constrained', () => {
  it('offers the full duration list again', async () => {
    reported = {blocked: false, timeLimit: 86400};
    const {rerender} = await openDialog();
    expect(await durationOptions()).not.toContain('Indefinite');

    // The new selection's answer is in flight: still narrowed, since widening
    // on an unknown answer would make a forbidden duration briefly clickable.
    reported = {blocked: true, timeLimit: null};
    rerender(
      <ThemeProvider theme={theme}>
        <BulkRenewal rows={[ROW]} />
      </ThemeProvider>,
    );
    expect(await durationOptions()).not.toContain('Indefinite');

    // Answered, and nothing constrains these rows.
    reported = {blocked: false, timeLimit: null};
    rerender(
      <ThemeProvider theme={theme}>
        <BulkRenewal rows={[ROW]} />
      </ThemeProvider>,
    );
    await waitFor(async () => expect(await durationOptions()).toContain('Indefinite'));
  });
});
