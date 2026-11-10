import {describe, expect, it} from 'vitest';

import {formatPluginValue, requestErrorMessage} from './helpers';

describe('requestErrorMessage', () => {
  it('returns a string payload as is', () => {
    expect(requestErrorMessage({payload: 'Group not found'})).toBe('Group not found');
  });

  it('reads detail, then title, from a problem-detail payload', () => {
    expect(requestErrorMessage({payload: {title: 'Conflict', detail: 'Name taken'}})).toBe('Name taken');
    expect(requestErrorMessage({payload: {title: 'Conflict', detail: null}})).toBe('Conflict');
    expect(requestErrorMessage({payload: {}})).toBe('Unexpected error');
  });
});

describe('formatPluginValue', () => {
  it('renders primitives as String does', () => {
    expect(formatPluginValue('abc')).toBe('abc');
    expect(formatPluginValue(42)).toBe('42');
    expect(formatPluginValue(false)).toBe('false');
  });

  it('renders objects and arrays as JSON', () => {
    expect(formatPluginValue({a: 1})).toBe('{"a":1}');
    expect(formatPluginValue(['x', 'y'])).toBe('["x","y"]');
    expect(formatPluginValue(null)).toBe('null');
  });
});
