import '@mui/material/styles';

declare module '@mui/material/styles' {
  interface Palette {
    highlight: {
      [variant: string]: Palette['primary'];
    };
  }

  interface PaletteOptions {
    highlight?: {
      [variant: string]: PaletteOptions['primary'];
    };
  }
}
