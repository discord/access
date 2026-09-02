import type {ApiContext} from './apiContext';

const baseUrl = import.meta.env.VITE_API_SERVER_URL;

export type ErrorWrapper<TError> = TError | {status: 'unknown'; payload: string};

// RFC 9457 problem-detail envelope emitted by the FastAPI backend
// (`api/exception_handlers.py`). `detail` is the human-readable message the
// React client surfaces; `title`, `status`, `type` are RFC standard.
// Validation errors carry an extra non-standard `errors[]` list.
export type ErrorMessage = {
  type?: string;
  title?: string;
  status?: number;
  detail?: string;
  // Non-standard extension on 401s from an OIDC deployment: the endpoint that
  // starts the login flow.
  login_url?: string;
  errors?: Array<{
    type?: string;
    loc?: Array<string | number>;
    msg?: string;
    ctx?: Record<string, unknown>;
  }>;
};

const DEFAULT_LOGIN_PATH = '/oidc/login';

/**
 * Hand the whole window to the login endpoint, asking to be returned to the
 * page the user is currently on.
 *
 * The backend answers an unauthenticated `/api/*` request with a 401 rather
 * than the 307-to-the-IdP a document navigation gets, because `fetch` can't
 * complete an interactive login: it would follow the redirect to the IdP's
 * cross-origin HTML and fail CORS. So the session-expiry case has to be
 * escalated to a real navigation here, otherwise the user is stuck looking at
 * an error on a page they can't reload their way out of.
 */
export const redirectToLogin = (loginUrl: string = DEFAULT_LOGIN_PATH) => {
  const {pathname, search, hash} = window.location;
  window.location.assign(`${loginUrl}?next=${encodeURIComponent(`${pathname}${search}${hash}`)}`);
};

export type ApiFetcherOptions<TBody, THeaders, TQueryParams, TPathParams> = {
  url: string;
  method: string;
  body?: TBody;
  headers?: THeaders;
  queryParams?: TQueryParams;
  pathParams?: TPathParams;
  signal?: AbortSignal;
} & ApiContext['fetcherOptions'];

export async function apiFetch<
  TData,
  TError,
  TBody extends {} | FormData | undefined | null,
  THeaders extends {},
  TQueryParams extends {},
  TPathParams extends {},
>({
  url,
  method,
  body,
  headers,
  pathParams,
  queryParams,
  signal,
}: ApiFetcherOptions<TBody, THeaders, TQueryParams, TPathParams>): Promise<TData> {
  try {
    const requestHeaders: HeadersInit = {
      'Content-Type': 'application/json',
      ...headers,
    };

    /**
     * As the fetch API is being used, when multipart/form-data is specified
     * the Content-Type header must be deleted so that the browser can set
     * the correct boundary.
     * https://developer.mozilla.org/en-US/docs/Web/API/FormData/Using_FormData_Objects#sending_files_using_a_formdata_object
     */
    if (requestHeaders['Content-Type']?.toLowerCase().includes('multipart/form-data')) {
      delete requestHeaders['Content-Type'];
    }

    const response = await window.fetch(`${baseUrl}${resolveUrl(url, queryParams, pathParams)}`, {
      signal,
      method: method.toUpperCase(),
      body: body ? (body instanceof FormData ? body : JSON.stringify(body)) : undefined,
      headers: requestHeaders,
    });
    if (!response.ok) {
      // Flatten the RFC 9457 problem-detail body to a plain string message the
      // React client renders directly via `error.payload`. `detail` is the
      // human-readable summary; fall back to `title`.
      let payload: string;
      let problem: ErrorMessage = {};
      try {
        problem = (await response.json()) as ErrorMessage;
        payload = problem.detail ?? problem.title ?? 'Unexpected error';
      } catch (e) {
        payload = e instanceof Error ? `Unexpected error (${e.message})` : 'Unexpected error';
      }
      if (response.status === 401) {
        // The OIDC session expired or was never established. Log back in and
        // come back to this page; the returned promise never settles because
        // the navigation is already underway.
        redirectToLogin(problem.login_url);
        return await new Promise<TData>(() => {});
      }
      throw {status: 'unknown' as const, payload};
    }

    if (response.headers.get('content-type')?.includes('json')) {
      return await response.json();
    } else {
      // if it is not a json response, assume it is a blob and cast it to TData
      return (await response.blob()) as unknown as TData;
    }
  } catch (e) {
    // Genuine network/abort failures arrive here as `Error` instances; the
    // re-thrown problem-detail above is already in `{status, payload}` shape,
    // so pass it through untouched.
    if (e instanceof Error) {
      throw {
        status: 'unknown' as const,
        payload: `Network error (${e.message})`,
      };
    }
    throw e;
  }
}

const resolveUrl = (url: string, queryParams: Record<string, string> = {}, pathParams: Record<string, string> = {}) => {
  let query = new URLSearchParams(queryParams).toString();
  if (query) query = `?${query}`;
  return url.replace(/\{\w*\}/g, (key) => pathParams[key.slice(1, -1)] ?? '') + query;
};
