import {describe, expect, it} from 'vitest';

import {isPluginStatusPending} from './pluginStatus';

describe('isPluginStatusPending', () => {
  const properties = {
    sync_status: {display_name: 'Sync Status', pending_values: ['pending']},
    sync_error: {display_name: 'Sync Error'},
    last_synced_at: {display_name: 'Last Synced', pending_values: null},
  };

  it('is pending while a status sits on a value its plugin declared in-progress', () => {
    expect(isPluginStatusPending(properties, {sync_status: 'pending'})).toBe(true);
  });

  it('is not pending once the status moves off those values', () => {
    expect(isPluginStatusPending(properties, {sync_status: 'synced'})).toBe(false);
    // Terminal states are not pending: the Google plugin's "error" stays put until something
    // changes it, so polling on it would never stop.
    expect(isPluginStatusPending(properties, {sync_status: 'error'})).toBe(false);
  });

  it('is pending when the plugin has written no status yet', () => {
    // What a freshly created group looks like: the hook runs after the response, so the first
    // read gets an empty status object.
    expect(isPluginStatusPending(properties, {})).toBe(true);
    expect(isPluginStatusPending(properties, undefined)).toBe(true);
  });

  it('never polls for a plugin that declares no in-progress values', () => {
    expect(
      isPluginStatusPending({sync_error: {display_name: 'Sync Error'}, member_count: {display_name: 'Members'}}, {}),
    ).toBe(false);
    expect(isPluginStatusPending({}, {})).toBe(false);
  });

  it('is not pending when the status properties have not loaded', () => {
    expect(isPluginStatusPending(undefined, {sync_status: 'pending'})).toBe(false);
  });

  it('matches non-string values too', () => {
    expect(
      isPluginStatusPending({converged: {display_name: 'Converged', pending_values: [false]}}, {converged: false}),
    ).toBe(true);
    expect(
      isPluginStatusPending({converged: {display_name: 'Converged', pending_values: [false]}}, {converged: true}),
    ).toBe(false);
  });
});
