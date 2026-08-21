/**
 * Deciding when an app group is still being reconciled by its lifecycle plugin.
 *
 * The lifecycle hooks run in a post-response background task, so a group that was just created or
 * edited reaches the UI before the plugin has reported anything. The page therefore has to poll
 * for a while rather than trust the payload it was handed.
 *
 * What counts as "still reconciling" is the plugin's call, not ours: status values are the
 * plugin's own vocabulary, and matching a literal like "pending" here would hardcode one plugin's
 * strings into a client every operator shares. A plugin declares its in-progress values via
 * `pending_values` on a status property, and this reads them back.
 */

import {PluginStatusProp} from '../api/apiSchemas';

/** How often to re-read a group while its plugin still has a reconcile outstanding. */
export const RECONCILE_POLL_INTERVAL_MS = 5_000;

/**
 * How long to keep polling before giving up. A reconcile that has not reported by now is either
 * genuinely slow or wedged, and neither is worth an unbounded timer on an open tab — the
 * sync-app-groups cronjob converges it either way, and a reload picks up the result.
 */
export const RECONCILE_POLL_WINDOW_MS = 60_000;

/**
 * True when a plugin-managed entity looks like it is mid-reconcile.
 *
 * Two ways that happens:
 *  - a status property is sitting on one of the values its plugin declared as in-progress; or
 *  - the plugin declares in-progress values but has written no status at all yet, which is what a
 *    just-created group looks like before its first hook lands.
 *
 * A plugin that declares no `pending_values` anywhere has opted out, and never polls.
 */
export function isPluginStatusPending(
  statusProperties: Record<string, PluginStatusProp> | undefined | null,
  currentStatus: Record<string, unknown> | undefined | null,
): boolean {
  if (statusProperties == null) {
    return false;
  }

  const declared = Object.entries(statusProperties).filter(
    ([, property]) => (property?.pending_values ?? []).length > 0,
  );
  if (declared.length === 0) {
    return false;
  }

  const status = currentStatus ?? {};
  if (Object.keys(status).length === 0) {
    return true;
  }

  return declared.some(([name, property]) => (property.pending_values ?? []).includes(status[name]));
}
