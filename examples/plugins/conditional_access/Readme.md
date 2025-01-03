# Conditional Access Plugin

This plugin will allow you to automatically approve or deny access requests based on the group or tag membership of the group.

## Installation

Add the below to your Dockerfile to install the plugin. You can put it before the ENV section at the bottom of the file.
```
# Add the specific plugins and install conditional access plugin
WORKDIR /app/plugins
ADD ./examples/plugins/conditional_access ./conditional_access
RUN pip install -r ./conditional_access/requirements.txt && pip install ./conditional_access

# Reset working directory
WORKDIR /app
```

Build and run your docker container as normal.


## Configuration

You can set the following environment variables to configure the plugin but note that neither are required by default. If you only want to use the specific tag `Auto-Approve` then no environment variables are required. You must however create the tag within the Access Application.

- `AUTO_APPROVED_GROUP_NAMES`: A comma-separated list of group names that will be auto-approved.
- `AUTO_APPROVED_TAG_NAMES`: A comma-separated list of tag names that will be auto-approved.


## Usage

The plugin will automatically approve access requests to the groups or tags specified in the environment variables by running a check on each access request that is processed. If neither the group name nor the tag name match, then a log line stating manual approval is required will be output.
