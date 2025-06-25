import {darken, lighten, PaletteColor, styled} from '@mui/material';
import {grey} from '@mui/material/colors';
import {DataGrid} from '@mui/x-data-grid';

const getHoverBackgroundColor = (color: PaletteColor, mode: string) => (mode === 'dark' ? color.dark : color.light);

const getSelectedBackgroundColor = (color: PaletteColor, mode: string) =>
  mode === 'dark' ? darken(color.dark, 0.5) : lighten(color.light, 0.5);

const getSelectedHoverBackgroundColor = (color: PaletteColor, mode: string) =>
  mode === 'dark' ? darken(color.dark, 0.4) : lighten(color.light, 0.4);

const getRowSelectedColor = (color: PaletteColor, mode: string) =>
  mode === 'dark' ? color.contrastText : color.contrastText;

const getRowSelectedBackgroundColor = (color: PaletteColor, mode: string) =>
  mode === 'dark' ? darken(color.contrastText, 0.5) : lighten(color.contrastText, 0.5);

const getRowSelectedHoverBackgroundColor = (color: PaletteColor, mode: string) =>
  mode === 'dark' ? darken(color.contrastText, 0.4) : lighten(color.contrastText, 0.4);

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
    '& .super-app-theme--Selected-Expired': {
      backgroundColor: getRowSelectedColor(highlight.danger, mode),
      '&:hover': {
        backgroundColor: getHoverBackgroundColor(highlight.danger, mode),
      },
      '&.Mui-selected': {
        backgroundColor: getRowSelectedBackgroundColor(highlight.danger, mode),
        '&:hover': {
          backgroundColor: getRowSelectedHoverBackgroundColor(highlight.danger, mode),
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
    '& .super-app-theme--Selected-Soon': {
      backgroundColor: getRowSelectedColor(highlight.warning, mode),
      '&:hover': {
        backgroundColor: getHoverBackgroundColor(highlight.warning, mode),
      },
      '&.Mui-selected': {
        backgroundColor: getRowSelectedBackgroundColor(highlight.warning, mode),
        '&:hover': {
          backgroundColor: getRowSelectedHoverBackgroundColor(highlight.warning, mode),
        },
      },
    },
    '& .super-app-theme--Should-Expire': {
      backgroundColor: highlight.info.main,
      '&:hover': {
        backgroundColor: getHoverBackgroundColor(highlight.info, mode),
      },
      '&.Mui-selected': {
        backgroundColor: getSelectedBackgroundColor(highlight.info, mode),
        '&:hover': {
          backgroundColor: getSelectedHoverBackgroundColor(highlight.info, mode),
        },
      },
    },
    '& .super-app-theme--Selected-Should-Expire': {
      backgroundColor: getRowSelectedColor(highlight.info, mode),
      '&:hover': {
        backgroundColor: getHoverBackgroundColor(highlight.info, mode),
      },
      '&.Mui-selected': {
        backgroundColor: getRowSelectedBackgroundColor(highlight.info, mode),
        '&:hover': {
          backgroundColor: getRowSelectedHoverBackgroundColor(highlight.info, mode),
        },
      },
    },
    '& .super-app-theme--Selected': {
      backgroundColor: mode === 'dark' ? grey[800] : lighten(grey[200], 0.5),
      '&:hover': {
        backgroundColor: mode === 'dark' ? lighten(grey[800], 0.05) : lighten(grey[200], 0.2),
      },
      '&.Mui-selected': {
        backgroundColor: mode === 'dark' ? grey[800] : lighten(grey[200], 0.5),
        '&:hover': {
          backgroundColor: mode === 'dark' ? lighten(grey[800], 0.05) : lighten(grey[200], 0.2),
        },
      },
    },
  }),
);
export default BulkRenewalDataGrid;
