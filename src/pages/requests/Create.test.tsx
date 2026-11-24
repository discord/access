import * as React from 'react';
import {render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, describe, expect, it, vi} from 'vitest';

import {OktaUserDetail} from '../../api/apiSchemas';
import accessConfig from '../../config/accessConfig';

const USER = {
  id: 'user-000000000000000',
  email: 'user@example.com',
  active_group_memberships: [],
  active_group_ownerships: [],
} as unknown as OktaUserDetail;

vi.mock('react-router-dom', () => ({
  useNavigate: () => vi.fn(),
}));

vi.mock('../../authorization', () => ({
  canManageGroup: () => false,
  isAccessAdmin: () => false,
}));

vi.mock('../../constraints', () => ({
  timeLimitLabel: (seconds: number) => `${seconds / 86400} days`,
  carriedConstraints: () => ({isSelfAddDisallowed: () => false}),
  useConstraintsForGroups: () => ({
    pending: false,
    error: null,
    blocked: false,
    timeLimit: () => null,
    isReasonRequired: () => false,
    isSelfAddDisallowed: () => false,
  }),
}));

vi.mock('../../api/apiComponents', () => ({
  useAccessRequestsCreate: () => ({mutate: vi.fn()}),
  useGroups: () => ({data: {items: []}}),
}));

import CreateRequest from './Create';

const BUILT_OWNERSHIP_DEFAULT = accessConfig.DEFAULT_OWNERSHIP_ACCESS_TIME;

afterEach(() => {
  accessConfig.DEFAULT_OWNERSHIP_ACCESS_TIME = BUILT_OWNERSHIP_DEFAULT;
});

const lengthSelect = () => screen.findByRole('combobox', {name: 'For how long?'});

describe('the requested access length', () => {
  it('moves to the default for ownership or membership as the requester switches between them', async () => {
    accessConfig.DEFAULT_OWNERSHIP_ACCESS_TIME = 'indefinite';
    render(<CreateRequest currentUser={USER} open setOpen={vi.fn()} />);

    expect(await lengthSelect()).toHaveTextContent(accessConfig.ACCESS_TIME_LABELS[accessConfig.DEFAULT_ACCESS_TIME]);

    await userEvent.click(screen.getByRole('button', {name: 'Owner'}));
    expect(await lengthSelect()).toHaveTextContent('Indefinite');

    await userEvent.click(screen.getByRole('button', {name: 'Member'}));
    expect(await lengthSelect()).toHaveTextContent(accessConfig.ACCESS_TIME_LABELS[accessConfig.DEFAULT_ACCESS_TIME]);
  });

  it('starts an ownership request on the ownership default', async () => {
    accessConfig.DEFAULT_OWNERSHIP_ACCESS_TIME = 'indefinite';
    render(<CreateRequest currentUser={USER} owner open setOpen={vi.fn()} />);

    expect(await lengthSelect()).toHaveTextContent('Indefinite');
  });
});
