//if regex not specified by user, default value set by discord access taken
export const regexConfig = {
  namePattern: new RegExp(process.env.REACT_APP_OKTA_APP_NAME_PATTERN || '^[A-Z][A-Za-z0-9-]*$'),
};

console.log(`Loaded REACT_APP_OKTA_APP_NAME_PATTERN: ${process.env.REACT_APP_OKTA_APP_NAME_PATTERN}`);
