/**
 * Read-only display of an app-group-lifecycle plugin's group configuration,
 * labeled by the plugin's group-config property display names (falling back to
 * the raw key). Renders nothing when there is no configuration to show.
 *
 * When `previousConfiguration` is supplied, changed values are shown as
 * old → new (strikethrough old value) to match the other resolved fields.
 */

import * as React from 'react';
import Typography from '@mui/material/Typography';

import {useAppGroupLifecyclePluginGroupConfigProps} from '../../api/apiComponents';
import {PluginConfigProp} from '../../api/apiSchemas';
import {pluginConfigRows} from './pluginConfig';

interface PluginConfigDisplayProps {
  pluginId: string;
  configuration: Record<string, any>;
  /** Prior config to diff against (e.g. requested, when showing the applied config). */
  previousConfiguration?: Record<string, any> | null;
  /** Optional heading rendered above the values (e.g. "Plugin Configuration"). */
  label?: string;
}

export default function PluginConfigDisplay({
  pluginId,
  configuration,
  previousConfiguration,
  label,
}: PluginConfigDisplayProps) {
  const {data: configProps} = useAppGroupLifecyclePluginGroupConfigProps(
    {pathParams: {pluginId}},
    {enabled: !!pluginId},
  );

  // Only show fields that carry a value; an empty config renders nothing so
  // callers don't need to guard on it.
  const rows = pluginConfigRows(configuration, previousConfiguration);
  if (rows.length === 0) {
    return null;
  }

  return (
    <>
      {label && (
        <Typography variant="body2" sx={{mt: 0.5}}>
          <b>{label}:</b>
        </Typography>
      )}
      {rows.map(({key, value, changed, from}) => {
        const prop = configProps?.[key] as PluginConfigProp | undefined;
        return (
          <Typography variant="body2" key={key} sx={{pl: label ? 2 : 0}}>
            <b>{prop?.display_name ?? key}:</b>{' '}
            {changed ? (
              <>
                <span style={{textDecoration: 'line-through'}}>
                  {from === undefined || from === '' ? '—' : String(from)}
                </span>
                {' → '}
                {String(value)}
              </>
            ) : (
              String(value)
            )}
          </Typography>
        );
      })}
    </>
  );
}
