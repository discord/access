import * as React from 'react';
import {describe, it, expect, vi, beforeEach} from 'vitest';
import {render, screen, waitFor} from '@testing-library/react';
import {QueryClient, QueryClientProvider} from '@tanstack/react-query';

import type {EffectiveConstraintDetail, EffectiveConstraintsResponse} from './api/apiSchemas';

// Stand in for the generated client so the hooks can be driven without a
// server. `effectiveConstraintsQuery` returns react-query's `{queryKey,
// queryFn}` pair, so a fake only has to key on the ids it was handed.
const server = vi.hoisted(() => ({
  respond: null as unknown as (ids: string[], mode: string) => Promise<EffectiveConstraintsResponse>,
  calls: [] as {mode: string; ids: string[]}[],
}));

vi.mock('./api/apiComponents', () => ({
  effectiveConstraintsQuery: (variables: {queryParams: Record<string, string[]>}) => {
    const mode = Object.keys(variables.queryParams)[0];
    const ids = variables.queryParams[mode];
    return {
      queryKey: [mode, ...ids],
      queryFn: () => {
        server.calls.push({mode, ids});
        return server.respond(ids, mode);
      },
    };
  },
}));

import {useConstraintsForGroups, useConstraintsForTags} from './constraints';

function entry(constraint: string, value: number | boolean): EffectiveConstraintDetail {
  return {constraint, name: constraint, value, sources: []};
}

function Probe({ids, tags = false}: {ids: string[]; tags?: boolean}) {
  /* eslint-disable react-hooks/rules-of-hooks */
  const reader = tags ? useConstraintsForTags(ids) : useConstraintsForGroups(ids);
  /* eslint-enable react-hooks/rules-of-hooks */
  return (
    <>
      <div data-testid="blocked">{String(reader.blocked)}</div>
      <div data-testid="pending">{String(reader.pending)}</div>
      <div data-testid="errored">{String(reader.error != null)}</div>
      <div data-testid="timeLimit">{String(reader.timeLimit(false))}</div>
      <div data-testid="isReasonRequired">{String(reader.isReasonRequired(false))}</div>
      <div data-testid="isSelfAddDisallowed">{String(reader.isSelfAddDisallowed(false))}</div>
      <div data-testid="rowSelfAddDisallowed">{String(reader.forGroup(ids[0]).isSelfAddDisallowed(false))}</div>
    </>
  );
}

function renderProbe(ids: string[], tags = false) {
  const client = new QueryClient({defaultOptions: {queries: {retry: false, gcTime: 0}}});
  return render(
    <QueryClientProvider client={client}>
      <Probe ids={ids} tags={tags} />
    </QueryClientProvider>,
  );
}

const shown = (id: string) => screen.getByTestId(id).textContent;

beforeEach(() => {
  server.calls = [];
});

describe('useConstraintsForGroups', () => {
  it('fails the gates closed while the answer is still in flight', async () => {
    // The bug this guards: a gate reading `undefined` reports "unrestricted",
    // so for as long as the fetch takes, the dialog invites an action the
    // backend will refuse.
    let release: (value: EffectiveConstraintsResponse) => void = () => {};
    server.respond = () => new Promise((resolve) => (release = resolve));

    renderProbe(['g1']);

    expect(shown('pending')).toBe('true');
    expect(shown('blocked')).toBe('true');
    expect(shown('isReasonRequired')).toBe('true');
    expect(shown('isSelfAddDisallowed')).toBe('true');
    expect(shown('rowSelfAddDisallowed')).toBe('true');
    // A time limit has no closed value to fail to; `blocked` is what a dialog
    // offering a duration must check.
    expect(shown('timeLimit')).toBe('null');

    release({coalesced: [], by_group: {}});
    await waitFor(() => expect(shown('pending')).toBe('false'));
    expect(shown('isSelfAddDisallowed')).toBe('false');
  });

  it('keeps the gates closed when the request fails, rather than reporting nothing applies', async () => {
    server.respond = () => Promise.reject(new Error('boom'));

    renderProbe(['g1']);

    await waitFor(() => expect(shown('errored')).toBe('true'));
    expect(shown('pending')).toBe('false');
    expect(shown('blocked')).toBe('true');
    expect(shown('isReasonRequired')).toBe('true');
    expect(shown('isSelfAddDisallowed')).toBe('true');
  });

  it('reports the resolved answer once every request lands', async () => {
    server.respond = async () => ({
      coalesced: [entry('member_time_limit', 86400), entry('disallow_self_add_membership', true)],
      by_group: {g1: [entry('disallow_self_add_membership', true)], g2: []},
    });

    renderProbe(['g1', 'g2']);

    await waitFor(() => expect(shown('blocked')).toBe('false'));
    expect(shown('timeLimit')).toBe('86400');
    expect(shown('isSelfAddDisallowed')).toBe('true');
    expect(shown('rowSelfAddDisallowed')).toBe('true');
    expect(server.calls).toHaveLength(1);
  });

  it('makes no request at all when nothing is selected', async () => {
    server.respond = async () => ({coalesced: [], by_group: {}});

    renderProbe([]);

    await waitFor(() => expect(shown('blocked')).toBe('false'));
    expect(server.calls).toHaveLength(0);
    // An empty selection is a settled answer, not an unknown one, so the gates
    // must not be closed — otherwise a dialog would start out blocked forever.
    expect(shown('isSelfAddDisallowed')).toBe('false');
  });
});

describe('splitting a selection larger than the endpoint cap', () => {
  it('asks in batches of 200 and answers as though it had asked once', async () => {
    // A role can be associated with more groups than one request may name. The
    // endpoint rejects over 200 ids with a 400, which before this landed as
    // "no constraints" — every gate silently open on exactly the largest
    // roles.
    server.respond = async (ids) => ({
      // Both batches report both keys, with the second carrying the shorter
      // limit and the only restriction. Every entry therefore has to be
      // folded rather than merely inserted, so a merge that kept the first
      // batch's answer for either key would be visibly wrong.
      coalesced: ids.includes('g000250')
        ? [entry('member_time_limit', 3600), entry('disallow_self_add_membership', true)]
        : [entry('member_time_limit', 86400), entry('disallow_self_add_membership', false)],
      by_group: Object.fromEntries(ids.map((id) => [id, []])),
    });

    const ids = Array.from({length: 260}, (_, i) => `g${String(i).padStart(6, '0')}`);
    renderProbe(ids);

    await waitFor(() => expect(shown('blocked')).toBe('false'));
    expect(server.calls).toHaveLength(2);
    expect(server.calls[0].ids).toHaveLength(200);
    expect(server.calls[1].ids).toHaveLength(60);
    expect(shown('timeLimit')).toBe('3600');
    expect(shown('isSelfAddDisallowed')).toBe('true');
  });

  it('stays blocked while any one batch is still outstanding', async () => {
    let resolved = 0;
    const gate: ((value: EffectiveConstraintsResponse) => void)[] = [];
    server.respond = () =>
      new Promise((resolve) => {
        gate.push(resolve);
        resolved += 1;
      });

    renderProbe(Array.from({length: 260}, (_, i) => `g${String(i).padStart(6, '0')}`));

    await waitFor(() => expect(resolved).toBe(2));
    gate[0]({coalesced: [], by_group: {}});
    // One batch home, one still out: a partial answer is not an answer.
    await waitFor(() => expect(shown('blocked')).toBe('true'));
    expect(shown('isSelfAddDisallowed')).toBe('true');

    gate[1]({coalesced: [], by_group: {}});
    await waitFor(() => expect(shown('blocked')).toBe('false'));
  });
});

describe('useConstraintsForTags', () => {
  it('asks in tag mode and has no per-group answer to give', async () => {
    server.respond = async () => ({coalesced: [entry('owner_time_limit', 3600)], by_group: {}});

    renderProbe(['t1'], true);

    await waitFor(() => expect(shown('blocked')).toBe('false'));
    expect(server.calls[0].mode).toBe('tag_ids');
  });
});
