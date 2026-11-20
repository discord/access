import assert from 'node:assert/strict';
import http from 'node:http';
import {after, before, beforeEach, describe, it} from 'node:test';

import {PROVIDERS} from './providers.mjs';
import {createProxy} from './proxy.mjs';

function listen(server) {
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server.address().port)));
}

describe('review proxy', () => {
  let upstream;
  let upstreamPort;
  let received;

  before(async () => {
    upstream = http.createServer(async (request, response) => {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      received.push({method: request.method, url: request.url, headers: request.headers, body: Buffer.concat(chunks)});
      response.writeHead(200, {'content-type': 'text/event-stream', 'request-id': 'req_1', 'set-cookie': 'a=b'});
      response.end('event: message_stop\ndata: {}\n\n');
    });
    upstreamPort = await listen(upstream);
  });

  after(() => upstream.close());

  beforeEach(() => {
    received = [];
  });

  async function withProxy(options, run) {
    const lines = [];
    const {server, stats} = createProxy({
      provider: PROVIDERS.anthropic,
      apiKey: 'sk-ant-real-key-value-0123456789',
      upstream: `http://127.0.0.1:${upstreamPort}`,
      log: (line) => lines.push(line),
      ...options,
    });
    const port = await listen(server);
    try {
      await run(`http://127.0.0.1:${port}`, {stats, lines});
    } finally {
      server.close();
    }
  }

  it('injects the real key and drops the credential the agent sent', async () => {
    await withProxy({}, async (base) => {
      const response = await fetch(`${base}/v1/messages?beta=true`, {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          'anthropic-version': '2023-06-01',
          'x-api-key': 'placeholder',
          authorization: 'Bearer placeholder',
          cookie: 'session=1',
        },
        body: JSON.stringify({model: 'm'}),
      });
      assert.equal(response.status, 200);
      assert.equal(await response.text(), 'event: message_stop\ndata: {}\n\n');
      assert.equal(response.headers.get('request-id'), 'req_1');
      assert.equal(response.headers.get('set-cookie'), null);
    });
    assert.equal(received.length, 1);
    const [forwarded] = received;
    assert.equal(forwarded.url, '/v1/messages?beta=true');
    assert.equal(forwarded.headers['x-api-key'], 'sk-ant-real-key-value-0123456789');
    assert.equal(forwarded.headers.authorization, undefined);
    assert.equal(forwarded.headers.cookie, undefined);
    assert.equal(forwarded.headers['anthropic-version'], '2023-06-01');
    assert.equal(forwarded.body.toString(), '{"model":"m"}');
  });

  it('uses bearer auth for the OpenAI adapter', async () => {
    await withProxy({provider: PROVIDERS.openai}, async (base) => {
      const response = await fetch(`${base}/v1/responses`, {method: 'POST', body: '{}'});
      assert.equal(response.status, 200);
    });
    assert.equal(received[0].headers.authorization, 'Bearer sk-ant-real-key-value-0123456789');
    assert.equal(received[0].headers['x-api-key'], undefined);
  });

  it('rejects paths and methods outside the allowlist without forwarding', async () => {
    await withProxy({}, async (base, {stats}) => {
      for (const [method, route] of [
        ['POST', '/v1/files'],
        ['GET', '/v1/messages'],
        ['POST', '/v1/messages/../files'],
        ['POST', '/v1/models'],
      ]) {
        const response = await fetch(`${base}${route}`, {method, body: method === 'GET' ? undefined : '{}'});
        assert.equal(response.status, 403, `${method} ${route}`);
      }
      assert.equal(stats.forwarded, 0);
    });
    assert.equal(received.length, 0);
  });

  it('stops forwarding once the request budget is spent', async () => {
    await withProxy({maxRequests: 2}, async (base, {lines}) => {
      const statuses = [];
      for (let index = 0; index < 3; index += 1) {
        statuses.push((await fetch(`${base}/v1/messages`, {method: 'POST', body: '{}'})).status);
      }
      assert.deepEqual(statuses, [200, 200, 429]);
      assert.match(lines.at(-1), /budget of 2 exhausted/u);
    });
    assert.equal(received.length, 2);
  });

  it('refuses oversized bodies', async () => {
    await withProxy({maxBodyBytes: 8}, async (base) => {
      const response = await fetch(`${base}/v1/messages`, {method: 'POST', body: '0123456789'});
      assert.equal(response.status, 413);
    });
    assert.equal(received.length, 0);
  });

  it('never logs the key', async () => {
    await withProxy({}, async (base, {lines}) => {
      await fetch(`${base}/v1/messages`, {method: 'POST', body: '{}'});
      await fetch(`${base}/v1/nope`, {method: 'POST', body: '{}'});
      assert.ok(lines.length >= 2);
      for (const line of lines) assert.doesNotMatch(line, /sk-ant/u);
    });
  });

  it('requires a key', () => {
    assert.throws(() => createProxy({provider: PROVIDERS.anthropic, apiKey: ''}), /needs an API key/u);
  });
});
