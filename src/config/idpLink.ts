import {useAppConfig} from '../api/apiComponents';

function buildUrl(template: string, id: string): string | null {
  if (!template || !id) {
    return null;
  }
  return template.replaceAll('{id}', encodeURIComponent(id));
}

export interface IdpLinks {
  // Display name of the IdP (e.g. "Okta"); empty string when unconfigured.
  idpName: string;
  // Build a deep link to the user / group in the IdP console, or null when
  // the corresponding template is unconfigured (which hides the button).
  idpUserUrl: (id: string) => string | null;
  idpGroupUrl: (id: string) => string | null;
}

/**
 * IdP deep-link config, fetched at runtime from `GET /api/config` rather than
 * baked into the build, so one bundle can target different IdP consoles per
 * deployment env. Until the request resolves (or if it fails) everything is
 * empty/null, so the "Open in IdP" button stays hidden.
 */
export function useIdpLinks(): IdpLinks {
  const {data} = useAppConfig({});
  const idp = data?.idp;
  return {
    idpName: idp?.name ?? '',
    idpUserUrl: (id: string) => buildUrl(idp?.user_url_template ?? '', id),
    idpGroupUrl: (id: string) => buildUrl(idp?.group_url_template ?? '', id),
  };
}
