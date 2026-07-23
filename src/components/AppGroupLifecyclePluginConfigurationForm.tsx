/**
 * Form component for configuring App Group Lifecycle Plugins.
 * Used in CreateUpdate dialogs (edit mode).
 */

import * as React from 'react';
import Box from '@mui/material/Box';
import CircularProgress from '@mui/material/CircularProgress';
import FormControl from '@mui/material/FormControl';
import FormHelperText from '@mui/material/FormHelperText';
import InputAdornment from '@mui/material/InputAdornment';
import MenuItem from '@mui/material/MenuItem';
import Select, {SelectChangeEvent} from '@mui/material/Select';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import Checkbox from '@mui/material/Checkbox';
import FormControlLabel from '@mui/material/FormControlLabel';
import {useFormContext, Controller} from 'react-hook-form';

import {
  useAppGroupLifecyclePlugins,
  useAppGroupLifecyclePluginAppConfigProps,
  useAppGroupLifecyclePluginGroupConfigProps,
} from '../api/apiComponents';
import {PluginConfigProp, PluginInfo} from '../api/apiSchemas';

// Helper-text note appended to a locked (immutable, edit-mode) config field.
const LOCKED_NOTE = 'Cannot be changed after creation.';

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

  /**
   * Whether the entity being configured already exists (edit mode); immutable fields lock when true.
   */
  isExistingEntity?: boolean;

  /**
   * For group-level config, the owning app's id. Passed to the group-config-props
   * lookup so the schema reflects app-level constraints (e.g. an email pattern surfaced
   * as a client-side validation rule).
   */
  appId?: string;
}

/**
 * Renders a single configuration field based on its schema
 */
// Build react-hook-form `validate` rules from a config property's optional
// `validation.patterns` (a list of {regex, message}). Each non-empty value must
// match every pattern; emptiness is left to the `required` rule, and a malformed
// regex is ignored client-side since the backend validation is authoritative.

// An immutable config field may be set freely at create time and must lock on edit.
// Rendered read-only (not disabled) so its value is still submitted — a disabled input
// is omitted from the form payload, which the backend would read as a change and reject.
export function isFieldLocked(property: PluginConfigProp, isExistingEntity: boolean): boolean {
  return !!property.immutable && isExistingEntity;
}

function patternValidators(property: PluginConfigProp): Record<string, (value: any) => true | string> {
  const patterns = ((property.validation?.patterns ?? []) as Array<{regex: string; message?: string}>) || [];
  const validators: Record<string, (value: any) => true | string> = {};
  patterns.forEach((p, i) => {
    validators[`pattern_${i}`] = (value: any) => {
      if (value === undefined || value === null || value === '') return true;
      try {
        return new RegExp(p.regex).test(String(value)) || (p.message ?? `Must match ${p.regex}`);
      } catch {
        return true;
      }
    };
  });
  return validators;
}

export function ConfigField({
  property,
  value,
  fieldName,
  locked,
}: {
  property: PluginConfigProp;
  value: any;
  fieldName: string;
  locked: boolean;
}) {
  const {register, control, getFieldState, formState} = useFormContext();
  // Subscribe to this field's validation error so client-side failures surface inline
  // (reading getFieldState off formState subscribes to formState.errors).
  const fieldError = getFieldState(fieldName, formState).error;

  switch (property.type) {
    case 'boolean': {
      const boolHelp = locked
        ? `${property.help_text ? property.help_text + ' ' : ''}${LOCKED_NOTE}`
        : property.help_text;
      return (
        <FormControl fullWidth sx={{mb: 2}}>
          <Controller
            name={fieldName}
            control={control}
            defaultValue={value ?? property.default_value ?? false}
            render={({field}) => (
              <FormControlLabel
                control={<Checkbox {...field} checked={field.value} disabled={locked} />}
                label={property.display_name}
              />
            )}
          />
          {boolHelp && <FormHelperText>{boolHelp}</FormHelperText>}
        </FormControl>
      );
    }

    case 'number': {
      const numHelp = locked
        ? `${property.help_text ? property.help_text + ' ' : ''}${LOCKED_NOTE}`
        : property.help_text;
      return (
        <TextField
          fullWidth
          label={property.display_name}
          type="number"
          error={!!fieldError}
          helperText={(fieldError?.message as string) || numHelp}
          required={property.required}
          defaultValue={value ?? property.default_value}
          InputProps={{readOnly: locked}}
          {...register(
            fieldName,
            locked
              ? {}
              : {
                  required: property.required,
                  valueAsNumber: true,
                },
          )}
          sx={{mb: 2}}
        />
      );
    }

    case 'text':
    default: {
      const textHelp = locked
        ? `${property.help_text ? property.help_text + ' ' : ''}${LOCKED_NOTE}`
        : property.help_text;
      return (
        <TextField
          fullWidth
          label={property.display_name}
          error={!!fieldError}
          helperText={(fieldError?.message as string) || textHelp}
          required={property.required}
          defaultValue={value ?? property.default_value ?? ''}
          InputProps={{
            readOnly: locked,
            // A static suffix (e.g. an email domain) shown inline after the value; decorative,
            // not part of the stored value.
            ...(property.suffix
              ? {endAdornment: <InputAdornment position="end">{property.suffix}</InputAdornment>}
              : {}),
          }}
          {...register(
            fieldName,
            locked
              ? {}
              : {
                  required: property.required,
                  validate: patternValidators(property),
                },
          )}
          sx={{mb: 2}}
        />
      );
    }
  }
}

export default function AppGroupLifecyclePluginConfigurationForm({
  entityType,
  selectedPluginId,
  currentConfig = {},
  onPluginChange,
  isExistingEntity = false,
  appId,
}: AppGroupLifecyclePluginConfigurationFormProps) {
  const {data: plugins, isLoading: pluginsLoading} = useAppGroupLifecyclePlugins({});

  // Call both hooks unconditionally (rules of hooks) and select by entity type; only the
  // group lookup takes an app_id, so the app's config (e.g. its email pattern) is reflected
  // in the group config schema and validated client-side.
  const appConfigProps = useAppGroupLifecyclePluginAppConfigProps(
    {pathParams: {pluginId: selectedPluginId || ''}},
    {enabled: !!selectedPluginId && entityType === 'app'},
  );
  const groupConfigProps = useAppGroupLifecyclePluginGroupConfigProps(
    {
      pathParams: {pluginId: selectedPluginId || ''},
      queryParams: appId ? {app_id: appId} : undefined,
    },
    {enabled: !!selectedPluginId && entityType === 'group'},
  );
  const {data: configProperties, isLoading: configLoading} = entityType === 'app' ? appConfigProps : groupConfigProps;

  const selectedPlugin = React.useMemo(() => {
    if (!plugins || !selectedPluginId) return null;
    return plugins.find((p: PluginInfo) => p.id === selectedPluginId) || null;
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
            {plugins.map((plugin: PluginInfo) => (
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
                      property={property as PluginConfigProp}
                      value={currentConfig[propertyId]}
                      fieldName={fieldName}
                      locked={isFieldLocked(property as PluginConfigProp, isExistingEntity)}
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
