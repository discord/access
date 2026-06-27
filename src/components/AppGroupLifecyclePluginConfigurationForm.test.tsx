import {describe, it, expect, vi} from 'vitest';

// Mock MUI and React Hook Form modules so the test environment (jsdom / Node)
// does not need @emotion/styled or styled-components. We only exercise the
// pure helper isFieldLocked, which has no UI dependencies.
vi.mock('@mui/material/Box', () => ({default: () => null}));
vi.mock('@mui/material/CircularProgress', () => ({default: () => null}));
vi.mock('@mui/material/FormControl', () => ({default: () => null}));
vi.mock('@mui/material/FormHelperText', () => ({default: () => null}));
vi.mock('@mui/material/MenuItem', () => ({default: () => null}));
vi.mock('@mui/material/Select', () => ({default: () => null}));
vi.mock('@mui/material/TextField', () => ({default: () => null}));
vi.mock('@mui/material/Typography', () => ({default: () => null}));
vi.mock('@mui/material/Checkbox', () => ({default: () => null}));
vi.mock('@mui/material/FormControlLabel', () => ({default: () => null}));
vi.mock('react-hook-form', () => ({useFormContext: () => ({}), Controller: () => null}));
vi.mock('../api/apiComponents', () => ({
  useAppGroupLifecyclePlugins: () => ({data: [], isLoading: false}),
  useAppGroupLifecyclePluginAppConfigProps: () => ({data: {}, isLoading: false}),
  useAppGroupLifecyclePluginGroupConfigProps: () => ({data: {}, isLoading: false}),
}));

import {isFieldLocked} from './AppGroupLifecyclePluginConfigurationForm';
import {PluginConfigProp} from '../api/apiSchemas';

const prop = (over: Partial<PluginConfigProp>): PluginConfigProp =>
  ({display_name: 'X', type: 'text', required: false, ...over}) as PluginConfigProp;

describe('isFieldLocked', () => {
  it('locks an immutable field when editing an existing entity', () => {
    expect(isFieldLocked(prop({immutable: true}), true)).toBe(true);
  });

  it('does not lock an immutable field at create time', () => {
    expect(isFieldLocked(prop({immutable: true}), false)).toBe(false);
  });

  it('never locks a mutable field', () => {
    expect(isFieldLocked(prop({immutable: false}), true)).toBe(false);
    expect(isFieldLocked(prop({}), true)).toBe(false);
  });
});
