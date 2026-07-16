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
export function extractRequestedPluginData(formValues: {plugin_data?: unknown}): Record<string, any> {
  const data = formValues?.plugin_data;
  return data && typeof data === 'object' ? (data as Record<string, any>) : {};
}
