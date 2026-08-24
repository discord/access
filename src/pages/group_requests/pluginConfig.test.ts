import {describe, it, expect} from 'vitest';
import {
  pluginIdForApp,
  extractRequestedPluginData,
  visiblePluginConfigEntries,
  pluginConfigRows,
  prefillablePluginData,
} from './pluginConfig';

describe('pluginIdForApp', () => {
  it('returns the plugin id when the app has one', () => {
    expect(pluginIdForApp({id: 'a1', app_group_lifecycle_plugin: 'test_plugin'})).toBe('test_plugin');
  });
  it('returns null when the app has no plugin', () => {
    expect(pluginIdForApp({id: 'a1'})).toBeNull();
    expect(pluginIdForApp({id: 'a1', app_group_lifecycle_plugin: null})).toBeNull();
  });
  it('returns null when no app', () => {
    expect(pluginIdForApp(null)).toBeNull();
    expect(pluginIdForApp(undefined)).toBeNull();
  });
});

describe('extractRequestedPluginData', () => {
  it('returns the plugin_data object from form values', () => {
    const form = {plugin_data: {test_plugin: {configuration: {group_id: 'g1'}}}};
    expect(extractRequestedPluginData(form)).toEqual({test_plugin: {configuration: {group_id: 'g1'}}});
  });
  it('returns an empty object when absent', () => {
    expect(extractRequestedPluginData({})).toEqual({});
    expect(extractRequestedPluginData({plugin_data: undefined})).toEqual({});
  });
});

describe('visiblePluginConfigEntries', () => {
  it('drops null, undefined, and empty-string values', () => {
    expect(visiblePluginConfigEntries({a: 'x', b: null, c: undefined, d: ''})).toEqual([['a', 'x']]);
  });
  it('keeps falsy-but-meaningful values (false, 0)', () => {
    expect(visiblePluginConfigEntries({enabled: false, count: 0})).toEqual([
      ['enabled', false],
      ['count', 0],
    ]);
  });
  it('returns an empty array for empty/nullish config', () => {
    expect(visiblePluginConfigEntries({})).toEqual([]);
    expect(visiblePluginConfigEntries(null)).toEqual([]);
    expect(visiblePluginConfigEntries(undefined)).toEqual([]);
  });
});

describe('pluginConfigRows', () => {
  it('marks nothing changed when no previous config is supplied', () => {
    expect(pluginConfigRows({email: 'a@x.com'})).toEqual([
      {key: 'email', value: 'a@x.com', changed: false, from: undefined},
    ]);
  });

  it('flags a value that differs from the previous config, carrying the old value', () => {
    const rows = pluginConfigRows({email: 'new@x.com'}, {email: 'old@x.com'});
    expect(rows).toEqual([{key: 'email', value: 'new@x.com', changed: true, from: 'old@x.com'}]);
  });

  it('does not flag an unchanged value when a previous config is supplied', () => {
    const rows = pluginConfigRows({email: 'same@x.com'}, {email: 'same@x.com'});
    expect(rows).toEqual([{key: 'email', value: 'same@x.com', changed: false, from: 'same@x.com'}]);
  });

  it('treats a newly-added key (absent in previous) as changed with an undefined from', () => {
    const rows = pluginConfigRows({region: 'us'}, {});
    expect(rows).toEqual([{key: 'region', value: 'us', changed: true, from: undefined}]);
  });
});

describe('prefillablePluginData', () => {
  const stored = {'plugin-a': {configuration: {channel: '#eng'}}};

  it('keeps the stored config when the app still uses that plugin', () => {
    expect(prefillablePluginData({id: 'app1', app_group_lifecycle_plugin: 'plugin-a'}, stored)).toEqual(stored);
  });

  it('drops the config when the app switched plugins', () => {
    expect(prefillablePluginData({id: 'app1', app_group_lifecycle_plugin: 'plugin-b'}, stored)).toEqual({});
  });

  it('drops the config when the app has no lifecycle plugin', () => {
    expect(prefillablePluginData({id: 'app1', app_group_lifecycle_plugin: null}, stored)).toEqual({});
  });

  it('drops the config when there is no app at all', () => {
    expect(prefillablePluginData(null, stored)).toEqual({});
  });

  it('returns an empty object for empty or missing stored data', () => {
    expect(prefillablePluginData({id: 'app1', app_group_lifecycle_plugin: 'plugin-a'}, {})).toEqual({});
    expect(prefillablePluginData({id: 'app1', app_group_lifecycle_plugin: 'plugin-a'}, undefined)).toEqual({});
  });
});
