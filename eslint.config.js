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
    plugins: {'react-hooks': reactHooks},
    rules: {
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'error',
      '@tanstack/query/exhaustive-deps': 'error',
    },
  },
  {
    files: ['*.{js,ts}', 'scripts/**/*.js', 'src/config/loadAccessConfig.js'],
    languageOptions: {globals: globals.node},
  },
  eslintConfigPrettier,
);
