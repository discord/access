/**
 * Read-only component for displaying App Group Lifecycle Plugin configuration and status.
 * Used in Read pages (view mode).
 */

import * as React from 'react';
import Accordion from '@mui/material/Accordion';
import AccordionDetails from '@mui/material/AccordionDetails';
import AccordionSummary from '@mui/material/AccordionSummary';
import Box from '@mui/material/Box';
import CircularProgress from '@mui/material/CircularProgress';
import Paper from '@mui/material/Paper';
import Stack from '@mui/material/Stack';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableRow from '@mui/material/TableRow';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';

import {
  useGetAppGroupLifecyclePlugins,
  useGetAppGroupLifecyclePluginAppConfigProperties,
  useGetAppGroupLifecyclePluginGroupConfigProperties,
  useGetAppGroupLifecyclePluginAppStatusProperties,
  useGetAppGroupLifecyclePluginGroupStatusProperties,
} from '../api/apiComponents';
import {AppGroupLifecyclePluginConfigProperties, AppGroupLifecyclePluginStatusProperties} from '../api/apiSchemas';

type PluginData = {
  [propertyId: string]: any;
};

interface AppGroupLifecyclePluginDataProps {
  /**
   * Entity type at which the plugin is configured ('app' or 'group')
   */
  entityType: 'app' | 'group';

  /**
   * Plugin ID to display
   */
  pluginId: string;

  /**
   * Current configuration values
   */
  currentConfig?: PluginData;

  /**
   * Current status values (read-only)
   */
  currentStatus?: PluginData;
}

function PluginDataPropertiesTable({
  type,
  properties,
  data,
}: {
  type: 'Configuration' | 'Status';
  properties: AppGroupLifecyclePluginConfigProperties | AppGroupLifecyclePluginStatusProperties;
  data: PluginData;
}) {
  return (
    <Stack direction="column" spacing={1} sx={{minWidth: '300px', flexGrow: 1}}>
      <Typography variant="body1" component={'div'}>
        {type}
      </Typography>
      <TableContainer component={Paper}>
        <Table sx={{minWidth: 325}} size="small" aria-label={`plugin ${type.toLowerCase()}`}>
          <TableBody>
            {Object.entries(properties).map(([propertyId, property]) => {
              const value = data[propertyId];
              let displayValue = '—';
              if (value != null) {
                switch (property.type) {
                  case 'date':
                    try {
                      displayValue = new Date(value).toLocaleString();
                    } catch {
                      displayValue = String(value);
                    }
                    break;
                  case 'boolean':
                    displayValue = value ? 'Yes' : 'No';
                    break;
                  default:
                    displayValue = String(value);
                }
              }

              const row = (
                <TableRow key={propertyId}>
                  <TableCell>
                    <Typography variant="body2">{property.display_name}</Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2">{displayValue}</Typography>
                  </TableCell>
                </TableRow>
              );

              return property.help_text ? (
                <Tooltip key={propertyId} title={property.help_text} placement="top" arrow>
                  {row}
                </Tooltip>
              ) : (
                row
              );
            })}
          </TableBody>
        </Table>
      </TableContainer>
    </Stack>
  );
}

export default function AppGroupLifecyclePluginData({
  entityType,
  pluginId,
  currentConfig = {},
  currentStatus = {},
}: AppGroupLifecyclePluginDataProps) {
  const [expanded, setExpanded] = React.useState(false);
  const {data: plugins, isLoading: pluginsLoading} = useGetAppGroupLifecyclePlugins();

  const useConfigPropertiesHook =
    entityType === 'app'
      ? useGetAppGroupLifecyclePluginAppConfigProperties
      : useGetAppGroupLifecyclePluginGroupConfigProperties;
  const useStatusPropertiesHook =
    entityType === 'app'
      ? useGetAppGroupLifecyclePluginAppStatusProperties
      : useGetAppGroupLifecyclePluginGroupStatusProperties;

  const {data: configProperties, isLoading: configLoading} = useConfigPropertiesHook(
    {pathParams: {pluginId: pluginId}},
    {enabled: !!pluginId},
  );

  const {data: statusProperties, isLoading: statusLoading} = useStatusPropertiesHook(
    {pathParams: {pluginId: pluginId}},
    {enabled: !!pluginId},
  );

  const selectedPlugin = React.useMemo(() => {
    if (!plugins || !pluginId) return null;
    return plugins.find((p) => p.id === pluginId) || null;
  }, [plugins, pluginId]);

  if (pluginsLoading) {
    return (
      <Box sx={{display: 'flex', justifyContent: 'center', p: 2}}>
        <CircularProgress size={24} />
      </Box>
    );
  }

  if (!selectedPlugin) {
    return null;
  }

  const hasConfig = configProperties && Object.keys(configProperties).length > 0;
  const hasStatus = statusProperties && Object.keys(statusProperties).length > 0;

  return (
    <TableContainer component={Paper}>
      <Accordion expanded={expanded} onChange={(_e, newExpanded) => setExpanded(newExpanded)}>
        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
          <Box
            sx={{
              display: 'inline-flex',
              flexGrow: 1,
            }}>
            <Stack
              direction="column"
              spacing={1}
              sx={{
                flexGrow: 0.95,
              }}>
              <Typography variant="h6" color="text.accent">
                Associated App Group Lifecycle Plugin: {selectedPlugin.display_name}
              </Typography>
              <Typography variant="body1" color="grey">
                {selectedPlugin.description}
              </Typography>
            </Stack>
          </Box>
        </AccordionSummary>
        <AccordionDetails>
          <Table aria-label="plugin details">
            <TableBody className="accordion-body">
              <TableRow>
                <TableCell colSpan={2}>
                  {configLoading || statusLoading ? (
                    <Box sx={{display: 'flex', justifyContent: 'center', p: 2}}>
                      <CircularProgress size={24} />
                    </Box>
                  ) : hasConfig || hasStatus ? (
                    <Stack direction="row" useFlexGap flexWrap={'wrap'} justifyContent={'space-between'} gap={'2rem'}>
                      {hasConfig && (
                        <PluginDataPropertiesTable
                          type="Configuration"
                          properties={configProperties}
                          data={currentConfig}
                        />
                      )}
                      {hasStatus && (
                        <PluginDataPropertiesTable type="Status" properties={statusProperties} data={currentStatus} />
                      )}
                    </Stack>
                  ) : (
                    <Typography variant="body2" color="text.secondary">
                      No configuration or status information available for this plugin.
                    </Typography>
                  )}
                </TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </AccordionDetails>
      </Accordion>
    </TableContainer>
  );
}
