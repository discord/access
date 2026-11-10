#!/usr/bin/env node
// Credential-injecting proxy for the AI review agent.
//
// The agent container has no API key and no route to the internet; its only network peer is this
// proxy, which runs in a separate container that holds the key. The proxy forwards only the
// provider's model endpoints, drops every request header it does not recognize (including any
// credential the agent sends), adds the real credential, and stops forwarding once the request
// budget or body-size limit is exceeded.

import http from 'node:http';
import path from 'node:path';
import process from 'node:process';
import {Readable} from 'node:stream';
import {pathToFileURL} from 'node:url';

import {PROXY_PORT, getProvider} from './providers.mjs';

/** The default cap on forwarded requests per review. */
export const DEFAULT_MAX_REQUESTS = 300;
const DEFAULT_MAX_BODY_BYTES = 16 * 1024 * 1024;
// Response headers worth passing back; hop-by-hop and encoding headers are recomputed by Node.
const RESPONSE_HEADERS = ['content-type', 'request-id', 'x-request-id', 'retry-after'];

/** End a request with a provider-shaped JSON error. */
function reject(response, status, message) {
  response.writeHead(status, {'content-type': 'application/json'});
  response.end(JSON.stringify({type: 'error', error: {type: 'proxy_rejected', message}}));
}

/** Read a request body, or return null once it exceeds `limit` bytes. */
async function readBody(request, limit) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > limit) return null;
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}

/**
 * Create the proxy server. The caller is responsible for calling `listen`.
 *
 * @param {object} options
 * @param {object} options.provider - A provider adapter from `getProvider`.
 * @param {string} options.apiKey - The real credential; only this process ever holds it.
 * @param {string} [options.upstream] - Origin to forward to. Defaults to the provider's; tests
 *   point it at a local fake.
 * @param {number} [options.maxRequests] - Requests forwarded before every further one gets 429.
 * @param {number} [options.maxBodyBytes] - Largest request body forwarded; larger gets 413.
 * @param {(line: string) => void} [options.log] - Receives one line per request (no bodies).
 * @returns {{server: http.Server, stats: {forwarded: number, rejected: number}}}
 * @throws {Error} If no API key is given.
 */
export function createProxy({
  provider,
  apiKey,
  upstream = provider.upstream,
  maxRequests = DEFAULT_MAX_REQUESTS,
  maxBodyBytes = DEFAULT_MAX_BODY_BYTES,
  log = (line) => console.log(line),
}) {
  if (!apiKey) throw new Error('the proxy needs an API key');
  const stats = {forwarded: 0, rejected: 0};

  const server = http.createServer(async (request, response) => {
    const url = new URL(request.url ?? '/', 'http://proxy.invalid');
    const route = `${request.method} ${url.pathname}`;
    try {
      if (request.method !== 'POST' || !provider.allowedPaths.includes(url.pathname)) {
        stats.rejected += 1;
        log(`rejected ${route}: not an allowed model endpoint`);
        return reject(response, 403, 'endpoint not allowed by the review proxy');
      }
      if (stats.forwarded >= maxRequests) {
        stats.rejected += 1;
        log(`rejected ${route}: request budget of ${maxRequests} exhausted`);
        return reject(response, 429, 'review proxy request budget exhausted');
      }
      const body = await readBody(request, maxBodyBytes);
      if (body === null) {
        stats.rejected += 1;
        log(`rejected ${route}: body larger than ${maxBodyBytes} bytes`);
        return reject(response, 413, 'request body too large for the review proxy');
      }

      const headers = {};
      for (const name of provider.forwardHeaders) {
        const value = request.headers[name];
        if (typeof value === 'string') headers[name] = value;
      }
      Object.assign(headers, provider.authHeaders(apiKey));

      stats.forwarded += 1;
      const upstreamResponse = await fetch(`${upstream}${url.pathname}${url.search}`, {
        method: 'POST',
        headers,
        body,
      });
      log(`forwarded ${route} -> ${upstreamResponse.status} (${stats.forwarded}/${maxRequests})`);

      const responseHeaders = {};
      for (const name of RESPONSE_HEADERS) {
        const value = upstreamResponse.headers.get(name);
        if (value !== null) responseHeaders[name] = value;
      }
      response.writeHead(upstreamResponse.status, responseHeaders);
      if (upstreamResponse.body) {
        Readable.fromWeb(upstreamResponse.body).pipe(response);
      } else {
        response.end();
      }
    } catch (error) {
      log(`failed ${route}: ${error instanceof Error ? error.name : 'error'}`);
      if (!response.headersSent) reject(response, 502, 'review proxy could not reach the model provider');
      else response.destroy();
    }
  });
  return {server, stats};
}

/** Start the proxy from environment configuration. */
async function main() {
  const provider = getProvider(process.env.AI_REVIEW_PROVIDER);
  const maxRequests = Number(process.env.AI_REVIEW_MAX_REQUESTS || DEFAULT_MAX_REQUESTS);
  if (!Number.isSafeInteger(maxRequests) || maxRequests < 1) {
    throw new Error('AI_REVIEW_MAX_REQUESTS must be a positive integer');
  }
  const {server} = createProxy({provider, apiKey: process.env.AI_REVIEW_API_KEY, maxRequests});
  server.listen(PROXY_PORT, '0.0.0.0', () => console.log(`review proxy listening on ${PROXY_PORT}`));
  const stop = () => server.close(() => process.exit(0));
  process.on('SIGTERM', stop);
  process.on('SIGINT', stop);
}

const isMain = process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href;
if (isMain) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
}
