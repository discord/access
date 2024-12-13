const CracoAlias = require('react-app-alias');
const fs = require('fs');
const path = require('path');
const webpack = require('webpack');

function load_access_config() {
  // Load the default config
  const defaultConfigPath = path.resolve(__dirname, 'src/config/config.default.json');
  const accessConfig = JSON.parse(fs.readFileSync(defaultConfigPath, 'utf8'));

  // Dockerfile copies the file at build arg ACCESS_FILE_CONFIG_PATH to config.overrides.json
  const dockerConfigOverrideFile = 'config.overrides.json';
  const overrideConfigPath = fs.existsSync(path.resolve(__dirname, dockerConfigOverrideFile))
    ? path.resolve(__dirname, dockerConfigOverrideFile)
    : null;

  // Load override config, if present
  if (overrideConfigPath) {
    try {
      const overrideConfig = JSON.parse(fs.readFileSync(overrideConfigPath, 'utf8'));

      // Merge override config with default config
      Object.assign(accessConfig, overrideConfig);
    } catch (err) {
      console.error(`Failed to load override config from ${overrideConfigPath}: ${err}`);
      process.exit(1); // Exit with an error status
    }
  }

  // Sanity check for ACCESS_TIME_LABELS
  if (accessConfig.ACCESS_TIME_LABELS && typeof accessConfig.ACCESS_TIME_LABELS !== 'object') {
    throw new Error('ACCESS_TIME_LABELS must be a dictionary');
  }

  // Sanity check for DEFAULT_ACCESS_TIME
  if (accessConfig.DEFAULT_ACCESS_TIME) {
    const defaultAccessTime = parseInt(accessConfig.DEFAULT_ACCESS_TIME, 10);
    if (isNaN(defaultAccessTime) || !accessConfig.ACCESS_TIME_LABELS.hasOwnProperty(defaultAccessTime)) {
      throw new Error('DEFAULT_ACCESS_TIME must be a valid key in ACCESS_TIME_LABELS');
    }
  }
  return accessConfig;
}

const accessConfig = load_access_config();

module.exports = {
  plugins: [
    {
      plugin: CracoAlias,
      options: {
        source: 'tsconfig',
        /* tsConfigPath should point to the file where "paths" are specified */
        tsConfigPath: './tsconfig.paths.json',
      },
    },
  ],
  webpack: {
    alias: {
      '@mui/styled-engine': '@mui/styled-engine-sc',
    },
    plugins: [
      /* defined in src/globals.d.ts, which is included in tsconfig.json */
      new webpack.DefinePlugin({
        ACCESS_CONFIG: JSON.stringify(accessConfig),
      }),
    ],
  },
};
