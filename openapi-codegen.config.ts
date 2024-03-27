import {generateSchemaTypes, generateReactQueryComponents} from '@openapi-codegen/typescript';
import {defineConfig} from '@openapi-codegen/cli';
export default defineConfig({
  api: {
    from: {
      relativePath: 'api/swagger.json',
      source: 'file',
    },
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
