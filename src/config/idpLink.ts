import accessConfig from './accessConfig';

export const idpName = accessConfig.IDP_NAME;

function buildUrl(template: string, id: string): string | null {
  if (!template || !id) {
    return null;
  }
  return template.replaceAll('{id}', encodeURIComponent(id));
}

export function idpUserUrl(id: string): string | null {
  return buildUrl(accessConfig.IDP_USER_URL_TEMPLATE, id);
}

export function idpGroupUrl(id: string): string | null {
  return buildUrl(accessConfig.IDP_GROUP_URL_TEMPLATE, id);
}
