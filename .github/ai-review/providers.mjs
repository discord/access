// Model-provider adapters for the AI review gate.
//
// An adapter describes both halves of the credential split: what the proxy (which holds the API
// key) may forward and how it authenticates, and how the agent container (which holds no key)
// is invoked and how its output is parsed. Selecting a provider is a repository variable
// (`AI_REVIEW_PROVIDER`); adding one means adding an entry here.

/** Hostname the agent container uses to reach the proxy on the internal Docker network. */
export const PROXY_HOST = 'ai-review-proxy';
/** Port the proxy listens on inside its container. */
export const PROXY_PORT = 8080;
/** Mount points inside the agent container. */
export const PATHS = Object.freeze({
  head: '/review/head',
  base: '/review/base',
  input: '/review/input',
  home: '/home/agent',
});

// The agent CLIs refuse to start without a credential. This placeholder satisfies them; the
// proxy discards whatever auth the agent sends and substitutes the real key.
const PLACEHOLDER_CREDENTIAL = 'proxy-injects-the-real-credential';

/**
 * Settings passed to Claude Code with `--settings`. `--tools` already removes every tool except
 * Read, Grep, and Glob; these rules additionally confine those tools to `/review` and deny the
 * filesystem areas where process or system state lives.
 */
export const CLAUDE_SETTINGS = Object.freeze({
  permissions: {
    defaultMode: 'dontAsk',
    allow: ['Read(//review/**)', 'Grep(//review/**)', 'Glob(//review/**)'],
    deny: ['//proc', '//sys', '//dev', '//etc', '//root', '//home', '//tmp', '//var', '//usr', '//opt', '~'].flatMap(
      (root) => [`Read(${root}/**)`, `Grep(${root}/**)`, `Glob(${root}/**)`],
    ),
  },
});

/** Extract the review from Claude Code's `--output-format json` result. */
function parseClaudeOutput(stdout) {
  const result = JSON.parse(stdout);
  if (result.is_error) {
    throw new Error(`Claude Code reported an error (${result.subtype ?? 'unknown'})`);
  }
  if (result.structured_output && typeof result.structured_output === 'object') {
    return {review: result.structured_output, costUsd: result.total_cost_usd ?? null, turns: result.num_turns ?? null};
  }
  if (typeof result.result === 'string') {
    return {review: JSON.parse(result.result), costUsd: result.total_cost_usd ?? null, turns: result.num_turns ?? null};
  }
  throw new Error('Claude Code returned no structured review');
}

/** Parse the review Codex wrote as its last message. */
function parseCodexOutput(stdout) {
  return {review: JSON.parse(stdout), costUsd: null, turns: null};
}

/**
 * Provider adapters, keyed by the value of `AI_REVIEW_PROVIDER`.
 *
 * Each adapter has:
 * - `upstream`: the only origin the proxy forwards to.
 * - `allowedPaths`: the only request paths the proxy forwards (POST only).
 * - `forwardHeaders`: request headers copied from the agent; everything else is dropped, which
 *   is how the agent's placeholder credential is discarded.
 * - `authHeaders(key)`: headers the proxy adds.
 * - `defaultModel`: used when `AI_REVIEW_MODEL` is unset.
 * - `agentEnv()`: environment for the agent container. Must never contain the key.
 * - `agentCommand(options)`: argv for the agent container. The untrusted bundle arrives on stdin.
 * - `parseOutput(stdout)`: returns `{review, costUsd, turns}` or throws.
 * - `name`: the key the adapter is registered under.
 * - `agentModelEnv`: whether the agent reads the model from `AI_REVIEW_MODEL` in its
 *   environment rather than from its argv.
 * - `combinedPrompt`: whether the agent lacks a system-prompt channel, so the runner sends the
 *   trusted instructions and the untrusted bundle together on stdin.
 */
export const PROVIDERS = Object.freeze({
  anthropic: Object.freeze({
    name: 'anthropic',
    upstream: 'https://api.anthropic.com',
    allowedPaths: Object.freeze(['/v1/messages', '/v1/messages/count_tokens']),
    forwardHeaders: Object.freeze(['content-type', 'accept', 'anthropic-version', 'anthropic-beta']),
    authHeaders: (key) => ({'x-api-key': key}),
    defaultModel: 'claude-opus-5-5',
    agentEnv: () => ({
      ANTHROPIC_BASE_URL: `http://${PROXY_HOST}:${PROXY_PORT}`,
      ANTHROPIC_API_KEY: PLACEHOLDER_CREDENTIAL,
      CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: '1',
      DISABLE_AUTOUPDATER: '1',
      DISABLE_TELEMETRY: '1',
      DISABLE_ERROR_REPORTING: '1',
      USE_BUILTIN_RIPGREP: '0',
    }),
    agentCommand: ({model, schema, maxBudgetUsd}) => [
      'claude',
      // --bare skips hooks, plugins, and CLAUDE.md discovery, so files planted in the head
      // checkout (.claude/settings.json hooks, CLAUDE.md) are neither executed nor read as
      // instructions.
      '--bare',
      '--print',
      '--output-format',
      'json',
      '--model',
      model,
      '--tools',
      'Read,Grep,Glob',
      '--permission-mode',
      'dontAsk',
      '--strict-mcp-config',
      '--settings',
      `${PATHS.input}/claude-settings.json`,
      '--system-prompt-file',
      `${PATHS.input}/instructions.md`,
      '--json-schema',
      JSON.stringify(schema),
      '--max-budget-usd',
      String(maxBudgetUsd),
    ],
    agentModelEnv: false,
    combinedPrompt: false,
    parseOutput: parseClaudeOutput,
  }),
  openai: Object.freeze({
    name: 'openai',
    upstream: 'https://api.openai.com',
    allowedPaths: Object.freeze(['/v1/responses']),
    forwardHeaders: Object.freeze(['content-type', 'accept', 'openai-beta']),
    authHeaders: (key) => ({authorization: `Bearer ${key}`}),
    // No default: OpenAI model names change often enough that a stale default would fail
    // confusingly, so this provider requires `AI_REVIEW_MODEL`.
    defaultModel: null,
    agentEnv: () => ({CODEX_HOME: `${PATHS.home}/.codex`}),
    // Codex keeps a read-only shell even under `--sandbox read-only`, so this adapter is weaker
    // than the Anthropic one: the container (no credential, internal network, read-only mounts)
    // is the only barrier. Codex also has no system-prompt flag, so the trusted instructions and
    // the untrusted bundle share stdin, separated by the runner. It runs from the input
    // directory rather than the head checkout so it does not load the head's AGENTS.md.
    agentCommand: ({model}) => [
      'sh',
      '-c',
      [
        'codex exec --skip-git-repo-check --sandbox read-only',
        `--cd ${PATHS.input}`,
        `--output-schema ${PATHS.input}/schema.json`,
        '--output-last-message /tmp/review.json',
        `--model "$AI_REVIEW_MODEL"`,
        '--config model_provider=proxy',
        '--config model_providers.proxy.name=proxy',
        `--config model_providers.proxy.base_url=http://${PROXY_HOST}:${PROXY_PORT}/v1`,
        '--config model_providers.proxy.wire_api=responses',
        '- >&2 && cat /tmp/review.json',
      ].join(' '),
    ],
    agentModelEnv: true,
    combinedPrompt: true,
    parseOutput: parseCodexOutput,
  }),
});

/** The provider used when `AI_REVIEW_PROVIDER` is unset. */
export const DEFAULT_PROVIDER = 'anthropic';

/**
 * Look up a provider adapter.
 *
 * @param {string | undefined} name - The `AI_REVIEW_PROVIDER` value; empty means `DEFAULT_PROVIDER`.
 * @returns {object} The adapter.
 * @throws {Error} If the name is not a known provider.
 */
export function getProvider(name) {
  const key = name || DEFAULT_PROVIDER;
  const provider = Object.hasOwn(PROVIDERS, key) ? PROVIDERS[key] : undefined;
  if (!provider) {
    throw new Error(`unknown AI_REVIEW_PROVIDER "${key}"; expected one of ${Object.keys(PROVIDERS).join(', ')}`);
  }
  return provider;
}
