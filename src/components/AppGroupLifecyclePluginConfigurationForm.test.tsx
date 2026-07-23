import {describe, it, expect, vi} from 'vitest';

// Mock MUI and React Hook Form modules so the test environment (jsdom / Node)
// can import the component module without pulling in @emotion/styled. We only
// exercise the pure helper groupConfigHasFields, which has no UI dependencies.
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

import {groupConfigHasFields} from './AppGroupLifecyclePluginConfigurationForm';

describe('groupConfigHasFields', () => {
  it('is true when the plugin declares config properties', () => {
    expect(groupConfigHasFields({email: {display_name: 'Email'}})).toBe(true);
  });

  it('is false for an empty, null, or undefined property map', () => {
    expect(groupConfigHasFields({})).toBe(false);
    expect(groupConfigHasFields(null)).toBe(false);
    expect(groupConfigHasFields(undefined)).toBe(false);
  });
});
