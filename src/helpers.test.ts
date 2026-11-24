import {describe, expect, it} from 'vitest';

import {formatPluginValue, pluginDataEntry, requestErrorMessage} from './helpers';

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

describe('pluginDataEntry', () => {
  const pluginData = {okta: {configuration: {prefix: 'x'}, status: {state: 'ok'}}};

  it("returns the plugin's entry", () => {
    expect(pluginDataEntry(pluginData, 'okta')?.configuration).toEqual({prefix: 'x'});
  });

  it('returns undefined without data, a plugin id, or an entry', () => {
    expect(pluginDataEntry(null, 'okta')).toBeUndefined();
    expect(pluginDataEntry(pluginData, null)).toBeUndefined();
    expect(pluginDataEntry(pluginData, '')).toBeUndefined();
    expect(pluginDataEntry(pluginData, 'other')).toBeUndefined();
  });
});
