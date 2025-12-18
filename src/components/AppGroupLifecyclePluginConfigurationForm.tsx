/**
 * Form component for configuring App Group Lifecycle Plugins.
 * Used in CreateUpdate dialogs (edit mode).
 */

import * as React from 'react';
import Box from '@mui/material/Box';
import CircularProgress from '@mui/material/CircularProgress';
import FormControl from '@mui/material/FormControl';
import FormHelperText from '@mui/material/FormHelperText';
import MenuItem from '@mui/material/MenuItem';
import Select, {SelectChangeEvent} from '@mui/material/Select';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import Checkbox from '@mui/material/Checkbox';
import FormControlLabel from '@mui/material/FormControlLabel';
import {useFormContext, Controller} from 'react-hook-form';

import {
  useGetAppGroupLifecyclePlugins,
  useGetAppGroupLifecyclePluginAppConfigProperties,
  useGetAppGroupLifecyclePluginGroupConfigProperties,
} from '../api/apiComponents';
import {AppGroupLifecyclePluginConfigProperty} from '../api/apiSchemas';

type PluginConfiguration = {
  [propertyId: string]: any;
};

interface AppGroupLifecyclePluginConfigurationFormProps {
  /**
   * Entity type at which the plugin is configured ('app' or 'group')
   */
  entityType: 'app' | 'group';

  /**
   * Currently selected plugin ID (if any)
   */
  selectedPluginId?: string | null;

  /**
   * Current configuration values
   */
  currentConfig?: PluginConfiguration;

  /**
   * Callback when plugin selection changes (app level only)
   */
  onPluginChange?: (pluginId: string | null) => void;
}

/**
 * Renders a single configuration field based on its schema
 */
function ConfigField({
  property,
  value,
  fieldName,
}: {
  property: AppGroupLifecyclePluginConfigProperty;
  value: any;
  fieldName: string;
}) {
  const {register, control} = useFormContext();

  switch (property.type) {
    case 'boolean':
      return (
        <FormControl fullWidth sx={{mb: 2}}>
          <Controller
            name={fieldName}
            control={control}
            defaultValue={value ?? property.default_value ?? false}
            render={({field}) => (
              <FormControlLabel control={<Checkbox {...field} checked={field.value} />} label={property.display_name} />
            )}
          />
          {property.help_text && <FormHelperText>{property.help_text}</FormHelperText>}
        </FormControl>
      );

    case 'number':
      return (
        <TextField
          fullWidth
          label={property.display_name}
          type="number"
          helperText={property.help_text}
          required={property.required}
          defaultValue={value ?? property.default_value}
          {...register(fieldName, {
            required: property.required,
            valueAsNumber: true,
          })}
          sx={{mb: 2}}
        />
      );

    case 'text':
    default:
      return (
        <TextField
          fullWidth
          label={property.display_name}
          helperText={property.help_text}
          required={property.required}
          defaultValue={value ?? property.default_value ?? ''}
          {...register(fieldName, {
            required: property.required,
          })}
          sx={{mb: 2}}
        />
      );
  }
}

export default function AppGroupLifecyclePluginConfigurationForm({
  entityType,
  selectedPluginId,
  currentConfig = {},
  onPluginChange,
}: AppGroupLifecyclePluginConfigurationFormProps) {
  const {data: plugins, isLoading: pluginsLoading} = useGetAppGroupLifecyclePlugins();

  const useConfigPropertiesHook =
    entityType === 'app'
      ? useGetAppGroupLifecyclePluginAppConfigProperties
      : useGetAppGroupLifecyclePluginGroupConfigProperties;

  const {data: configProperties, isLoading: configLoading} = useConfigPropertiesHook(
    {pathParams: {pluginId: selectedPluginId || ''}},
    {enabled: !!selectedPluginId},
  );

  const selectedPlugin = React.useMemo(() => {
    if (!plugins || !selectedPluginId) return null;
    return plugins.find((p) => p.id === selectedPluginId) || null;
  }, [plugins, selectedPluginId]);

  const handlePluginSelectionChange = (event: SelectChangeEvent<string>) => {
    const newPluginId = event.target.value;
    onPluginChange?.(newPluginId || null);
  };

  if (pluginsLoading) {
    return (
      <Box sx={{display: 'flex', justifyContent: 'center', p: 2}}>
        <CircularProgress size={24} />
      </Box>
    );
  }

  if (!plugins || plugins.length === 0) {
    return null;
  }

  return (
    <Box sx={{mt: 3}}>
      <Typography variant="h6" gutterBottom>
        Configure {entityType === 'app' ? 'an' : 'the'} App Group Lifecycle Plugin
      </Typography>

      {/* Plugin Selection (app-level only) */}
      {entityType === 'app' && (
        <FormControl fullWidth sx={{mb: 3}}>
          <Select value={selectedPluginId || ''} onChange={handlePluginSelectionChange} displayEmpty>
            <MenuItem value="">
              <em>None</em>
            </MenuItem>
            {plugins.map((plugin) => (
              <MenuItem key={plugin.id} value={plugin.id}>
                {plugin.display_name}
              </MenuItem>
            ))}
          </Select>
          {selectedPlugin && <FormHelperText>{selectedPlugin.description}</FormHelperText>}
        </FormControl>
      )}

      {/* Display Selected Plugin (group-level only) */}
      {entityType === 'group' && selectedPlugin && (
        <>
          <Typography variant="subtitle1" gutterBottom>
            Selected Plugin
          </Typography>
          <Box sx={{pl: 2}}>
            <Typography variant="body2" gutterBottom>
              <strong>{selectedPlugin.display_name}</strong>: {selectedPlugin.description}
            </Typography>
          </Box>
        </>
      )}

      {/* Plugin Configuration */}
      {selectedPluginId && (
        <>
          {configLoading ? (
            <Box sx={{display: 'flex', justifyContent: 'center', p: 2}}>
              <CircularProgress size={24} />
            </Box>
          ) : configProperties && Object.keys(configProperties).length > 0 ? (
            <>
              <Typography variant="subtitle1" gutterBottom>
                Configuration
              </Typography>
              <Box sx={{pl: 2}}>
                {Object.entries(configProperties).map(([propertyId, property]) => {
                  const fieldName = `plugin_data.${selectedPluginId}.configuration.${propertyId}`;
                  return (
                    <ConfigField
                      key={propertyId}
                      property={property}
                      value={currentConfig[propertyId]}
                      fieldName={fieldName}
                    />
                  );
                })}
              </Box>
            </>
          ) : null}
        </>
      )}
    </Box>
  );
}
