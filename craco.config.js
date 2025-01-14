const CracoAlias = require('react-app-alias');
const path = require('path');
const webpack = require('webpack');
const {loadAccessConfig} = require('./src/config/loadAccessConfig');

const accessConfig = loadAccessConfig();

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
      new webpack.DefinePlugin({
        ACCESS_CONFIG: accessConfig,
      }),
    ],
  },
};
