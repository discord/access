import {describe, it, expect} from 'vitest';

import {deepMerge} from './apiUtils';

describe('deepMerge', () => {
  it('does not mutate target or source', () => {
    const target = {headers: {a: '1'}};
    const source = {headers: {b: '2'}, body: {members_to_add: ['x']}};

    const merged = deepMerge<object, object>(target, source);

    expect(merged).toEqual({headers: {a: '1', b: '2'}, body: {members_to_add: ['x']}});
    expect(target).toEqual({headers: {a: '1'}});
    expect(source).toEqual({headers: {b: '2'}, body: {members_to_add: ['x']}});
  });

  it('replaces arrays instead of merging them by index', () => {
    const merged = deepMerge<object, object>({body: {ids: ['a', 'b', 'c']}}, {body: {ids: ['z']}});

    expect(merged).toEqual({body: {ids: ['z']}});
  });

  it('keeps consecutive merges into the same target independent', () => {
    // Mirrors BulkRenewal: one shared fetcherOptions, one mutate() per group.
    const fetcherOptions = {};
    const first = {pathParams: {groupId: 'g1'}, body: {members_to_add: ['a', 'b', 'c']}};
    const second = {pathParams: {groupId: 'g2'}, body: {members_to_add: ['a']}};

    const firstMerged = deepMerge<object, object>(fetcherOptions, first);
    const secondMerged = deepMerge<object, object>(fetcherOptions, second);

    expect(fetcherOptions).toEqual({});
    expect(firstMerged).toEqual({pathParams: {groupId: 'g1'}, body: {members_to_add: ['a', 'b', 'c']}});
    expect(secondMerged).toEqual({pathParams: {groupId: 'g2'}, body: {members_to_add: ['a']}});
    expect(second.body.members_to_add).toEqual(['a']);
  });

  it('passes non-plain values through untouched', () => {
    const body = new FormData();
    const merged = deepMerge<object, {body: FormData}>({body: {stale: true}}, {body});

    expect(merged.body).toBe(body);
  });

  it('returns source when target is undefined', () => {
    const source = {pathParams: {groupId: 'g1'}};

    expect(deepMerge<object | undefined, typeof source>(undefined, source)).toBe(source);
  });
});
