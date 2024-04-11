import * as React from 'react';

import {
  Unstable_NumberInput as BaseNumberInput,
  NumberInputProps as NumInputProps,
  numberInputClasses,
} from '@mui/base/Unstable_NumberInput';
import {styled} from '@mui/system';
import {grey} from '@mui/material/colors';

const NumInput = React.forwardRef(function CustomNumberInput(
  props: NumInputProps,
  ref: React.ForwardedRef<HTMLDivElement>,
) {
  return (
    <BaseNumberInput
      slots={{
        root: InputRoot,
        input: InputElement,
        incrementButton: Button,
        decrementButton: Button,
      }}
      slotProps={{
        incrementButton: {
          children: <span className="arrow">▴</span>,
        },
        decrementButton: {
          children: <span className="arrow">▾</span>,
        },
      }}
      {...props}
      ref={ref}
    />
  );
});

interface NumberInputProps {
  label: string;
  min?: number;
  max?: number;
  default?: number;
  endAdornment?: string;
  setValue: (val: number | undefined) => void;
}

export default function NumberInput(props: NumberInputProps) {
  return (
    <NumInput
      aria-label={props.label}
      min={props.min}
      max={props.max}
      defaultValue={props.default}
      onChange={(event, val) => {
        event.preventDefault();
        props.setValue(val ?? undefined);
      }}
      endAdornment={<InputAdornment>{props.endAdornment}</InputAdornment>}
    />
  );
}

const InputAdornment = styled('div')(
  ({theme}) => `
    margin: 8px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    grid-row: 1/3;
    color: ${theme.palette.mode === 'dark' ? grey[500] : grey[700]};
  `,
);

const InputRoot = styled('div')(
  ({theme}) => `
    font-family: 'IBM Plex Sans', sans-serif;
    font-weight: 400;
    border-radius: 8px;
    color: ${theme.palette.mode === 'dark' ? grey[300] : grey[900]};
    background: ${theme.palette.mode === 'dark' ? grey[900] : '#fff'};
    border: 1px solid ${theme.palette.mode === 'dark' ? grey[700] : grey[200]};
    box-shadow: 0px 2px 4px ${theme.palette.mode === 'dark' ? 'rgba(0,0,0, 0.5)' : 'rgba(0,0,0, 0.05)'};
    display: grid;
    grid-template-columns: auto 1fr auto 19px;
    grid-template-rows: 1fr 1fr;
    overflow: hidden;
    padding: 4px;
  
    &.${numberInputClasses.focused} {
      border-color: ${theme.palette.primary.main};
      box-shadow: 0 0 0 3px ${theme.palette.primary_extra_light.main};
    }
  
    &:hover {
      border-color: ${theme.palette.primary.main};
    }
  
    // firefox
    &:focus-visible {
      outline: 0;
    }
  `,
);

const InputElement = styled('input')(
  ({theme}) => `
    font-size: 0.875rem;
    font-family: inherit;
    font-weight: 400;
    line-height: 1.5;
    grid-row: 1/3;
    color: ${theme.palette.mode === 'dark' ? grey[300] : grey[900]};
    background: inherit;
    border: none;
    border-radius: inherit;
    padding: 8px 12px;
    outline: 0;
  `,
);

const Button = styled('button')(
  ({theme}) => `
    display: flex;
    flex-flow: row nowrap;
    justify-content: center;
    align-items: center;
    appearance: none;
    padding: 0;
    width: 19px;
    height: 20px;
    font-family: system-ui, sans-serif;
    font-size: 0.875rem;
    line-height: 1;
    box-sizing: border-box;
    background: ${theme.palette.mode === 'dark' ? grey[900] : 'white'};
    border: 0;
    color: ${theme.palette.mode === 'dark' ? grey[300] : grey[900]};
    transition-property: all;
    transition-timing-function: cubic-bezier(0.4, 0, 0.2, 1);
    transition-duration: 120ms;
  
    &:hover {
      background: ${theme.palette.mode === 'dark' ? grey[800] : grey[50]};
      border-color: ${theme.palette.mode === 'dark' ? grey[600] : grey[300]};
      cursor: pointer;
    }
  
    &.${numberInputClasses.incrementButton} {
      grid-column: 4/5;
      grid-row: 1/2;
      border-top-left-radius: 4px;
      border-top-right-radius: 4px;
      border: 1px solid;
      border-bottom: 0;
      border-color: ${grey[200]};
      background: ${theme.palette.mode === 'dark' ? grey[900] : undefined};
      color: ${theme.palette.mode === 'dark' ? grey[200] : grey[600]};
  
      &:hover {
        cursor: pointer;
        color: #FFF;
        background: ${theme.palette.primary.main};
        border-color: ${theme.palette.primary.main};
      }
    }
  
    &.${numberInputClasses.decrementButton} {
      grid-column: 4/5;
      grid-row: 2/3;
      border-bottom-left-radius: 4px;
      border-bottom-right-radius: 4px;
      border: 1px solid;
      border-color: ${theme.palette.mode === 'dark' ? grey[700] : grey[200]};
      background: ${theme.palette.mode === 'dark' ? grey[900] : undefined};
      color: ${theme.palette.mode === 'dark' ? grey[200] : grey[600]};
  
      &:hover {
        cursor: pointer;
        color: #FFF;
        background: ${theme.palette.primary.main};
        border-color: ${theme.palette.primary.main};
      }
    }
  
    & .arrow {
      transform: translateY(-1px);
    }
  
    & .arrow {
      transform: translateY(-1px);
    }
  `,
);
