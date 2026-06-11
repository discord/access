import {generateSchemaTypes, generateReactQueryComponents} from '@openapi-codegen/typescript';
import {defineConfig} from '@openapi-codegen/cli';

// Locally we fetch the spec from the running dev backend. The CI drift check
// (see .github/workflows/openapi-client-drift.yml) dumps the spec to a file
// offline and points us at it via OPENAPI_SPEC_FILE.
const specFile = process.env.OPENAPI_SPEC_FILE;

export default defineConfig({
  api: {
    from: specFile
      ? {source: 'file', relativePath: specFile}
      : {source: 'url', url: 'http://localhost:6060/api/openapi.json'},
    outputDir: 'src/api',
    to: async (context) => {
      const filenamePrefix = 'api';
      const {schemasFiles} = await generateSchemaTypes(context, {
        filenamePrefix,
      });
      await generateReactQueryComponents(context, {
        filenamePrefix,
        schemasFiles,
      });
    },
  },
});
