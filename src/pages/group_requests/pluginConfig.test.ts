import {describe, it, expect} from 'vitest';
import {pluginIdForApp, extractRequestedPluginData} from './pluginConfig';

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
