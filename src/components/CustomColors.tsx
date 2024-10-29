import {alpha} from '@mui/material';
import {lightGreen, red, yellow} from '@mui/material/colors';

interface CustomColors {
  [usage: string]: {
    [variant: string]: {
      light: string;
      dark: string;
    };
  };
}

export const CUSTOM_COLORS: CustomColors = {
  highlight: {
    success: {
      light: lightGreen[100],
      dark: alpha(lightGreen[500], 0.3),
    },
    warning: {
      light: yellow[100],
      dark: alpha(yellow[500], 0.3),
    },
    danger: {
      light: red[100],
      dark: alpha(red[500], 0.3),
    },
  },
};
