// ESLint flat config for the React frontend and the Node build scripts.
//
// Formatting is prettier's job; eslint-config-prettier comes last so no rule here
// reports on what prettier rewrites.
import js from '@eslint/js';
import pluginQuery from '@tanstack/eslint-plugin-query';
import eslintConfigPrettier from 'eslint-config-prettier/flat';
import reactHooks from 'eslint-plugin-react-hooks';
import {defineConfig, globalIgnores} from 'eslint/config';
import globals from 'globals';
import tseslint from 'typescript-eslint';

export default defineConfig(
  globalIgnores([
    '.venv/',
    'build/',
    'dist/',
    'node_modules/',
    'venv/',
    // Owned by `npm run codegen`, which writes them from the backend OpenAPI spec;
    // they are not edited by hand.
    'src/api/apiComponents.ts',
    'src/api/apiContext.ts',
    'src/api/apiFetcher.ts',
    'src/api/apiSchemas.ts',
    'src/api/apiUtils.ts',
  ]),
  js.configs.recommended,
  tseslint.configs.recommended,
  pluginQuery.configs['flat/recommended'],
  {
    rules: {
      // A leading underscore marks a binding that must exist but is not read,
      // and a rest sibling is the idiom for omitting keys from an object.
      '@typescript-eslint/no-unused-vars': [
        'error',
        {
          argsIgnorePattern: '^_',
          caughtErrorsIgnorePattern: '^_',
          destructuredArrayIgnorePattern: '^_',
          ignoreRestSiblings: true,
          varsIgnorePattern: '^_',
        },
      ],
    },
  },
  {
    files: ['src/**/*.{ts,tsx}'],
    languageOptions: {
      globals: globals.browser,
      // Type information for the rules that need it, such as
      // @tanstack/query/no-void-query-fn.
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    extends: [reactHooks.configs.flat['recommended-latest']],
    rules: {
      // Warnings, not errors: the existing effects and memos this flags are keyed
      // deliberately (on search params, on a joined id string, on a completion
      // counter), and adding the missing dependencies would change when they run.
      // Promote both to 'error' once those call sites are reworked.
      'react-hooks/exhaustive-deps': 'warn',
      '@tanstack/query/exhaustive-deps': 'warn',
      // Warnings for the same reason: these flag the effects that copy search
      // params into state and the memos keyed on a joined id string, which are
      // the call sites exhaustive-deps flags. Promote both to 'error' with it.
      'react-hooks/set-state-in-effect': 'warn',
      'react-hooks/use-memo': 'warn',
    },
  },
  {
    files: ['*.{js,ts}', 'scripts/**/*.js', 'src/config/loadAccessConfig.js'],
    languageOptions: {globals: globals.node},
  },
  eslintConfigPrettier,
);
