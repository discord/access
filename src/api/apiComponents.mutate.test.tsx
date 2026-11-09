import * as React from 'react';
import {describe, it, expect, vi, afterEach} from 'vitest';
import {renderHook, waitFor} from '@testing-library/react';
import {QueryClient, QueryClientProvider} from '@tanstack/react-query';

import {useGroupMembersByIdPut} from './apiComponents';
import type {GroupMember} from './apiSchemas';

// Several mutate() calls from one hook instance share a fetcherOptions object.
// Each request must still carry only its own body (BulkRenewal regression).

function stubFetch() {
  const spy = vi.fn(
    async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response('{}', {status: 200, headers: {'Content-Type': 'application/json'}}),
  );
  vi.stubGlobal('fetch', spy);
  window.fetch = spy as unknown as typeof window.fetch;
  return spy;
}

function wrapper({children}: {children: React.ReactNode}) {
  const client = new QueryClient({defaultOptions: {mutations: {retry: false}}});
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function membersBody(members: string[]): GroupMember {
  return {
    members_to_add: members,
    members_should_expire: [],
    members_to_remove: [],
    owners_to_add: [],
    owners_should_expire: [],
    owners_to_remove: [],
  };
}

describe('generated mutation hooks', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('sends each mutate() call its own body', async () => {
    const spy = stubFetch();
    const {result} = renderHook(() => useGroupMembersByIdPut(), {wrapper});

    const first = membersBody(['a', 'b', 'c']);
    const second = membersBody(['a']);
    result.current.mutate({pathParams: {groupId: 'g1'}, body: first});
    result.current.mutate({pathParams: {groupId: 'g2'}, body: second});

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));

    const sent = Object.fromEntries(
      spy.mock.calls.map(([url, init]) => [
        String(url).split('/').at(-2),
        JSON.parse(String(init?.body)).members_to_add,
      ]),
    );
    expect(sent).toEqual({g1: ['a', 'b', 'c'], g2: ['a']});
    expect(second.members_to_add).toEqual(['a']);
  });
});
