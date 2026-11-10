import {describe, it, expect, vi, beforeEach} from 'vitest';
import {act, render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import {OktaUserDetail, TagDetail} from '../../api/apiSchemas';

const navigate = vi.fn();
vi.mock('react-router-dom', () => ({useNavigate: () => navigate}));

vi.mock('../../authorization', () => ({isAccessAdmin: () => true}));

// Each `mutate` call is held until the test settles it, so a test can walk the
// dialog through a batch where the updates finish one at a time.
type Settle = (data: unknown, error: {payload: string} | null) => void;
const server = vi.hoisted(() => ({
  pending: [] as {appId: string; settle: () => void; fail: () => void}[],
}));

vi.mock('../../api/apiComponents', () => ({
  useApps: () => ({
    data: {
      items: [
        {id: 'app-1', name: 'Zendesk', description: ''},
        {id: 'app-2', name: 'Zoom', description: ''},
      ],
    },
  }),
  useAppByIdPut: ({onSettled}: {onSettled: Settle}) => ({
    mutate: (variables: {pathParams: {appId: string}}) => {
      server.pending.push({
        appId: variables.pathParams.appId,
        settle: () => onSettled({}, null),
        fail: () => onSettled(undefined, {payload: 'Update failed'}),
      });
    },
  }),
}));

import AddApps from './AddApps';

const TAG = {id: 'tag-1', name: 'SOX'} as TagDetail;
const USER = {id: 'user-1'} as OktaUserDetail;

beforeEach(() => {
  navigate.mockReset();
  server.pending = [];
});

async function openDialogWithApps(names: string[]) {
  render(<AddApps currentUser={USER} tag={TAG} />);
  await userEvent.click(screen.getByRole('button', {name: 'Add Apps'}));
  for (const name of names) {
    await userEvent.type(screen.getByRole('combobox'), name.slice(0, 3));
    await userEvent.click(await screen.findByRole('option', {name: new RegExp(name)}));
  }
}

describe('AddApps dialog', () => {
  it('cannot be submitted with no apps selected', async () => {
    await openDialogWithApps([]);
    expect(screen.getByRole('button', {name: 'Add'})).toBeDisabled();
  });

  it('closes and reloads only once every update has settled', async () => {
    await openDialogWithApps(['Zendesk', 'Zoom']);
    await userEvent.click(screen.getByRole('button', {name: 'Add'}));
    expect(server.pending.map((p) => p.appId).sort()).toEqual(['app-1', 'app-2']);

    act(() => server.pending[0].settle());
    expect(navigate).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    act(() => server.pending[1].settle());
    expect(navigate).toHaveBeenCalledWith(0);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('stays open with the error once a batch with a failure settles', async () => {
    await openDialogWithApps(['Zendesk', 'Zoom']);
    await userEvent.click(screen.getByRole('button', {name: 'Add'}));

    act(() => server.pending[0].fail());
    act(() => server.pending[1].settle());

    expect(navigate).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent('Update failed');
    expect(screen.getByRole('button', {name: 'Add'})).toBeEnabled();
  });
});
