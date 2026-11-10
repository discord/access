// Minimal GitHub REST and GraphQL client shared by the gate, runner, and publisher.

/** Raised for a non-2xx GitHub response; `status` holds the HTTP status. */
export class GitHubError extends Error {
  constructor(status, message) {
    super(message);
    this.name = 'GitHubError';
    this.status = status;
  }
}

/**
 * Create a GitHub API client.
 *
 * @param {string} token - The token sent as a bearer credential.
 * @param {typeof fetch} [fetchImpl] - Replaces `fetch`, for tests.
 * @returns {{request: Function, graphql: Function}}
 *   - `request(route, options)` calls `https://api.github.com${route}` with `fetch` options and
 *     returns the parsed JSON body, or null for 204. It throws `GitHubError` for a non-2xx
 *     response; the message names the method and path but never the query string.
 *   - `graphql(query, variables)` returns the `data` field and throws if the response carries
 *     errors.
 */
export function createGitHub(token, fetchImpl = fetch) {
  async function request(route, options = {}) {
    const response = await fetchImpl(`https://api.github.com${route}`, {
      ...options,
      headers: {
        Accept: 'application/vnd.github+json',
        Authorization: `Bearer ${token}`,
        'X-GitHub-Api-Version': '2022-11-28',
        ...(options.body ? {'Content-Type': 'application/json'} : {}),
        ...options.headers,
      },
    });
    if (!response.ok) {
      throw new GitHubError(
        response.status,
        `GitHub API ${response.status} for ${options.method ?? 'GET'} ${route.split('?')[0]}`,
      );
    }
    if (response.status === 204) return null;
    return response.json();
  }
  async function graphql(query, variables) {
    const result = await request('/graphql', {method: 'POST', body: JSON.stringify({query, variables})});
    if (result.errors?.length) throw new Error(`GitHub GraphQL: ${result.errors.map((error) => error.message).join('; ')}`);
    return result.data;
  }
  return {request, graphql};
}
