import {darken, lighten, PaletteColor, styled} from '@mui/material';
import {DataGrid} from '@mui/x-data-grid';

const getHoverBackgroundColor = (color: PaletteColor, mode: string) => (mode === 'dark' ? color.dark : color.light);

const getSelectedBackgroundColor = (color: PaletteColor, mode: string) =>
  mode === 'dark' ? darken(color.dark, 0.5) : lighten(color.light, 0.5);

const getSelectedHoverBackgroundColor = (color: PaletteColor, mode: string) =>
  mode === 'dark' ? darken(color.dark, 0.4) : lighten(color.light, 0.4);

const BulkRenewalDataGrid = styled(DataGrid)(
  ({
    theme: {
      palette: {highlight, mode},
    },
  }) => ({
    '& .super-app-theme--Expired': {
      backgroundColor: highlight.danger.main,
      '&:hover': {
        backgroundColor: getHoverBackgroundColor(highlight.danger, mode),
      },
      '&.Mui-selected': {
        backgroundColor: getSelectedBackgroundColor(highlight.danger, mode),
        '&:hover': {
          backgroundColor: getSelectedHoverBackgroundColor(highlight.danger, mode),
        },
      },
    },
    '& .super-app-theme--Soon': {
      backgroundColor: highlight.warning.main,
      '&:hover': {
        backgroundColor: getHoverBackgroundColor(highlight.warning, mode),
      },
      '&.Mui-selected': {
        backgroundColor: getSelectedBackgroundColor(highlight.warning, mode),
        '&:hover': {
          backgroundColor: getSelectedHoverBackgroundColor(highlight.warning, mode),
        },
      },
    },
  }),
);
export default BulkRenewalDataGrid;
