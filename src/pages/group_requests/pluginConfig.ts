/**
 * Pure helpers for collecting app-group-lifecycle plugin config in the group
 * request flow. Kept DOM-free so they can be unit-tested in isolation.
 */

type AppLike = {id?: string | null; app_group_lifecycle_plugin?: string | null} | null | undefined;

/** The lifecycle plugin id configured on an app, or null if none. */
export function pluginIdForApp(app: AppLike): string | null {
  return app?.app_group_lifecycle_plugin ?? null;
}

/**
 * Pull the nested `plugin_data` object that AppGroupLifecyclePluginConfigurationForm
 * registers (field names like `plugin_data.<pluginId>.configuration.<prop>`) out of
 * react-hook-form values, defaulting to {} when no plugin section was rendered.
 */
export function extractRequestedPluginData(formValues: object): Record<string, unknown> {
  const data = 'plugin_data' in formValues ? formValues.plugin_data : undefined;
  return data && typeof data === 'object' ? (data as Record<string, unknown>) : {};
}

/**
 * Config entries worth displaying: drops null/undefined/empty-string values but
 * keeps falsy-but-meaningful values like `false` and `0`.
 */
export function visiblePluginConfigEntries(
  configuration: Record<string, unknown> | null | undefined,
): [string, unknown][] {
  return Object.entries(configuration ?? {}).filter(
    ([, value]) => value !== null && value !== undefined && value !== '',
  );
}

export interface PluginConfigRow {
  key: string;
  value: unknown;
  /** True when a comparison config was supplied and its value for this key differs. */
  changed: boolean;
  /** The comparison ("from") value, when a comparison config was supplied. */
  from: unknown;
}

/**
 * Rows for displaying a plugin's config, optionally diffed against a prior
 * config (e.g. resolved-vs-requested) so a resolver's edits can be shown as
 * old → new. `changed` is only ever true when `previousConfiguration` is given.
 */
export function pluginConfigRows(
  configuration: Record<string, unknown> | null | undefined,
  previousConfiguration?: Record<string, unknown> | null,
): PluginConfigRow[] {
  const hasPrevious = previousConfiguration != null;
  return visiblePluginConfigEntries(configuration).map(([key, value]) => {
    const from = previousConfiguration?.[key];
    return {key, value, changed: hasPrevious && from !== value, from};
  });
}
