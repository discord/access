const CracoAlias = require('react-app-alias');
const fs = require('fs');
const path = require('path');
const webpack = require('webpack');

function load_access_config() {
  // Load the default config
  const defaultConfigPath = path.resolve(__dirname, 'src/config/config.default.json');
  const accessConfig = JSON.parse(fs.readFileSync(defaultConfigPath, 'utf8'));

  // Check for config.override.json
  const overrideConfigPath = path.resolve(__dirname, 'config.override.json');
  if (fs.existsSync(overrideConfigPath)) {
    const overrideConfig = JSON.parse(fs.readFileSync(overrideConfigPath, 'utf8'));
    Object.assign(accessConfig, overrideConfig);
  } else {
    // Check for ACCESS_FILE_CONFIG_PATH environment variable
    const envConfigPath = process.env.ACCESS_FILE_CONFIG_PATH;
    if (envConfigPath && fs.existsSync(envConfigPath)) {
      const envConfig = JSON.parse(fs.readFileSync(envConfigPath, 'utf8'));
      Object.assign(accessConfig, envConfig);
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
        tsConfigPath: './tsconfig.paths.json',
      },
    },
  ],
  webpack: {
    alias: {
      '@mui/styled-engine': '@mui/styled-engine-sc',
    },
    plugins: [
      new webpack.DefinePlugin({
        ACCESS_CONFIG: JSON.stringify(accessConfig),
      }),
    ],
  },
};
