// Tests for the Claude Code plugin's stdio bridge.
//
// Each test runs the bridge as Claude Code does, as a child process that reads JSON-RPC from stdin and
// writes it to stdout, against a fake cmon-mcp that answers the way the real server's Streamable HTTP
// endpoint does (the forms are listed in the bridge's header comment).
//
// Run: node --test tools/bridge.test.mjs

import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import http from 'node:http';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

const BRIDGE = fileURLToPath(new URL('../claude/clustercontrol-mcp/bridge/cmon-mcp-bridge.mjs', import.meta.url));
const TOKEN = 'test-token-that-must-never-be-printed-7f3a';
const INIT = {
  jsonrpc: '2.0',
  id: 1,
  method: 'initialize',
  params: { protocolVersion: '2025-06-18', capabilities: { elicitation: {} }, clientInfo: { name: 'test', version: '0' } },
};
const INITIALIZED = { jsonrpc: '2.0', method: 'notifications/initialized' };

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function until(what, condition, ms = 5000) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    const value = condition();
    if (value) return value;
    await sleep(20);
  }
  throw new Error(`timed out waiting for ${what}`);
}

// A fake cmon-mcp. `post(entry, res, state)` may answer a POST itself and return true; everything else
// gets the default: a session on initialize, 202 for notifications and client responses, an echo for
// requests, an open stream for GET, and 404 for a session listed in state.stale.
async function mcpServer(t, { post, sessionIds = ['session-A', 'session-B', 'session-C'] } = {}) {
  const requests = [];
  const sockets = new Set();
  const state = { streams: [], initializes: 0, stale: new Set() };
  const server = http.createServer((req, res) => {
    const chunks = [];
    req.on('data', (chunk) => chunks.push(chunk));
    req.on('end', () => {
      const entry = { method: req.method, url: req.url, headers: req.headers, body: Buffer.concat(chunks).toString('utf8') };
      try {
        entry.json = entry.body ? JSON.parse(entry.body) : undefined;
      } catch {
        entry.json = undefined;
      }
      requests.push(entry);
      const session = entry.headers['mcp-session-id'];
      if (session && state.stale.has(session)) {
        res.writeHead(404, { 'Content-Type': 'text/plain' }).end('Invalid session ID\n');
        return;
      }
      if (entry.method === 'GET') {
        res.writeHead(200, { 'Content-Type': 'text/event-stream' });
        res.flushHeaders();
        state.streams.push(res);
        return;
      }
      if (entry.method === 'DELETE') {
        res.writeHead(200).end();
        return;
      }
      if (post && post(entry, res, state) === true) return;
      const message = entry.json;
      if (message?.method === 'initialize') {
        res.writeHead(200, { 'Content-Type': 'application/json', 'Mcp-Session-Id': sessionIds[state.initializes++] });
        res.end(`${JSON.stringify({
          jsonrpc: '2.0',
          id: message.id,
          result: { protocolVersion: '2025-06-18', capabilities: {}, serverInfo: { name: 'fake-cmon-mcp', version: '0' } },
        })}\n`);
        return;
      }
      if (message?.method === undefined || message.id === undefined) {
        res.writeHead(202).end();
        return;
      }
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(`${JSON.stringify({ jsonrpc: '2.0', id: message.id, result: { echoed: message.method } })}\n`);
    });
  });
  server.on('connection', (socket) => {
    sockets.add(socket);
    socket.on('close', () => sockets.delete(socket));
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise((resolve) => {
    for (const socket of sockets) socket.destroy();
    server.close(resolve);
  }));
  return { url: `http://127.0.0.1:${server.address().port}/mcp`, requests, state };
}

function startBridge(t, url, { token = TOKEN, env = {} } = {}) {
  const child = spawn(process.execPath, [BRIDGE], {
    env: { PATH: process.env.PATH, CLUSTERCONTROL_MCP_URL: url, CLUSTERCONTROL_MCP_TOKEN: token, ...env },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  const messages = [];
  let taken = 0;
  let stdout = '';
  let stderr = '';
  let pending = '';
  child.stdout.setEncoding('utf8');
  child.stderr.setEncoding('utf8');
  child.stdout.on('data', (chunk) => {
    stdout += chunk;
    pending += chunk;
    for (let at = pending.indexOf('\n'); at >= 0; at = pending.indexOf('\n')) {
      const line = pending.slice(0, at);
      pending = pending.slice(at + 1);
      if (line.trim()) messages.push({ line, json: JSON.parse(line) });
    }
  });
  child.stderr.on('data', (chunk) => {
    stderr += chunk;
  });
  const exited = new Promise((resolve) => child.on('exit', (code, signal) => resolve({ code, signal })));
  t.after(async () => {
    child.kill('SIGKILL');
    await exited;
    // Whatever a test did, the token must not have reached either stream.
    if (token) assert.ok(!stdout.includes(token) && !stderr.includes(token), 'the token was printed');
  });
  return {
    exited,
    messages,
    stdout: () => stdout,
    stderr: () => stderr,
    send: (message) => child.stdin.write(`${typeof message === 'string' ? message : JSON.stringify(message)}\n`),
    end: () => child.stdin.end(),
    // The next message not yet returned by next(), in arrival order.
    next: async (ms = 5000) => {
      await until(`a message on stdout (stderr: ${stderr.trim()})`, () => messages.length > taken, ms);
      return messages[taken++].json;
    },
  };
}

async function handshake(bridge) {
  bridge.send(INIT);
  const answer = await bridge.next();
  assert.equal(answer.id, 1);
  bridge.send(INITIALIZED);
  return answer;
}

const posts = (server, method) => server.requests.filter((r) => r.method === 'POST' && r.json?.method === method);

test('forwards initialize with the bearer token and relays the answer', async (t) => {
  const server = await mcpServer(t);
  const bridge = startBridge(t, server.url);
  bridge.send(INIT);
  const answer = await bridge.next();
  assert.equal(answer.result.serverInfo.name, 'fake-cmon-mcp');

  const [request] = server.requests;
  assert.equal(request.method, 'POST');
  assert.equal(request.url, '/mcp');
  assert.equal(request.headers.authorization, `Bearer ${TOKEN}`);
  assert.equal(request.headers['content-type'], 'application/json');
  assert.match(request.headers.accept, /application\/json/);
  assert.match(request.headers.accept, /text\/event-stream/);
  assert.equal(request.headers['mcp-session-id'], undefined);
  assert.deepEqual(request.json, INIT);
  assert.doesNotMatch(bridge.stderr(), /unencrypted/);
});

test('carries the session id and the negotiated protocol version on later requests', async (t) => {
  const server = await mcpServer(t);
  const bridge = startBridge(t, server.url);
  await handshake(bridge);
  bridge.send({ jsonrpc: '2.0', id: 2, method: 'tools/list' });
  assert.deepEqual(await bridge.next(), { jsonrpc: '2.0', id: 2, result: { echoed: 'tools/list' } });

  const [request] = posts(server, 'tools/list');
  assert.equal(request.headers['mcp-session-id'], 'session-A');
  assert.equal(request.headers['mcp-protocol-version'], '2025-06-18');
  assert.equal(request.headers.authorization, `Bearer ${TOKEN}`);
});

test('writes nothing for a message the server only accepts', async (t) => {
  const server = await mcpServer(t);
  const bridge = startBridge(t, server.url);
  await handshake(bridge);
  bridge.send({ jsonrpc: '2.0', method: 'notifications/cancelled', params: { requestId: 9 } });
  bridge.send({ jsonrpc: '2.0', id: 2, method: 'ping' });
  assert.equal((await bridge.next()).id, 2);
  await until('the notification to arrive', () => posts(server, 'notifications/cancelled').length === 1);
  assert.equal(bridge.messages.length, 2, 'only the two answers were written');
});

test('relays every message of an event-stream answer, in order and unchanged', async (t) => {
  const progress = '{"jsonrpc":"2.0","method":"notifications/progress","params":{"progress":1}}';
  const stream = Buffer.from(
    `event: message\r\ndata: ${progress}\r\n\r\n`
      + ': a comment, as a proxy keep-alive would send\n\n'
      + 'event: message\ndata: {"jsonrpc":"2.0",\ndata: "id":7,"result":{"text":"héllo ✓"}}\n\n',
    'utf8',
  );
  const server = await mcpServer(t, {
    post: (entry, res) => {
      if (entry.json?.method !== 'tools/call') return false;
      res.writeHead(200, { 'Content-Type': 'text/event-stream' });
      // Three-byte slices split lines, field names and the multi-byte characters.
      for (let at = 0; at < stream.length; at += 3) res.write(stream.subarray(at, at + 3));
      res.end();
      return true;
    },
  });
  const bridge = startBridge(t, server.url);
  await handshake(bridge);
  bridge.send({ jsonrpc: '2.0', id: 7, method: 'tools/call', params: { name: 'list_clusters' } });
  assert.deepEqual(await bridge.next(), JSON.parse(progress));
  assert.deepEqual(await bridge.next(), { jsonrpc: '2.0', id: 7, result: { text: 'héllo ✓' } });
});

test('keeps a large answer on one line and its digits as the server wrote them', async (t) => {
  const filler = 'x'.repeat(3 * 1024 * 1024);
  // 1731400000123456789 does not survive a JSON.parse and JSON.stringify round trip.
  const body = `{\n  "jsonrpc": "2.0",\n  "id": 3,\n  "result": {"ns": 1731400000123456789, "filler": "${filler}"}\n}\n`;
  const server = await mcpServer(t, {
    post: (entry, res) => {
      if (entry.json?.id !== 3) return false;
      res.writeHead(200, { 'Content-Type': 'application/json' }).end(body);
      return true;
    },
  });
  const bridge = startBridge(t, server.url);
  await handshake(bridge);
  bridge.send({ jsonrpc: '2.0', id: 3, method: 'tools/call' });
  const answer = await bridge.next(15000);
  assert.equal(answer.result.filler.length, filler.length);
  assert.match(bridge.messages[1].line, /"ns": 1731400000123456789,/);
});

test('opens the listening stream once the client is initialized, and relays what arrives on it', async (t) => {
  const server = await mcpServer(t);
  const bridge = startBridge(t, server.url);
  bridge.send(INIT);
  await bridge.next();
  await sleep(150);
  assert.equal(server.state.streams.length, 0, 'no stream before notifications/initialized');

  bridge.send(INITIALIZED);
  await until('the listening stream', () => server.state.streams.length === 1);
  const listen = server.requests.find((r) => r.method === 'GET');
  assert.equal(listen.url, '/mcp');
  assert.match(listen.headers.accept, /text\/event-stream/);
  assert.equal(listen.headers.authorization, `Bearer ${TOKEN}`);
  assert.equal(listen.headers['mcp-session-id'], 'session-A');

  // An approval prompt is a request FROM the server; the client's answer goes back as a POST.
  const prompt = { jsonrpc: '2.0', id: 41, method: 'elicitation/create', params: { message: 'Approve?' } };
  server.state.streams[0].write(`event: message\ndata: ${JSON.stringify(prompt)}\n\n`);
  assert.deepEqual(await bridge.next(), prompt);
  bridge.send({ jsonrpc: '2.0', id: 41, result: { action: 'accept' } });
  const reply = await until('the reply', () => server.requests.find((r) => r.method === 'POST' && r.json?.id === 41));
  assert.deepEqual(reply.json.result, { action: 'accept' });
  assert.equal(reply.headers['mcp-session-id'], 'session-A');
});

test('reopens the listening stream when it drops', async (t) => {
  const server = await mcpServer(t);
  const bridge = startBridge(t, server.url);
  await handshake(bridge);
  await until('the listening stream', () => server.state.streams.length === 1);
  server.state.streams[0].end();
  await until('a second listening stream', () => server.state.streams.length === 2, 8000);
  server.state.streams[1].write('data: {"jsonrpc":"2.0","method":"notifications/tools/list_changed"}\n\n');
  assert.equal((await bridge.next()).method, 'notifications/tools/list_changed');
});

test('answers a refused token with an error that says what to fix', async (t) => {
  const server = await mcpServer(t, {
    post: (entry, res) => {
      res.writeHead(401, { 'WWW-Authenticate': 'Bearer', 'Content-Type': 'text/plain' }).end('Unauthorized\n');
      return true;
    },
  });
  const bridge = startBridge(t, server.url);
  bridge.send(INIT);
  const answer = await bridge.next();
  assert.equal(answer.id, 1);
  assert.equal(answer.result, undefined);
  assert.match(answer.error.message, /401/);
  assert.match(answer.error.message, /token/i);
  assert.match(answer.error.message, /\/plugin configure/);
});

test('answers a request with an error when the server cannot be reached', async (t) => {
  // Nothing listens on port 1, so the connection is refused.
  const bridge = startBridge(t, 'http://127.0.0.1:1/mcp');
  bridge.send(INIT);
  const answer = await bridge.next();
  assert.equal(answer.id, 1);
  assert.equal(answer.result, undefined);
  assert.match(answer.error.message, /127\.0\.0\.1:1/);
  assert.match(answer.error.message, /ECONNREFUSED/);
  // A notification has no id to answer, so its failure goes to the log only.
  bridge.send(INITIALIZED);
  bridge.send({ jsonrpc: '2.0', id: 2, method: 'ping' });
  assert.equal((await bridge.next()).id, 2);
  assert.equal(bridge.messages.length, 2);
});

test('reports any other HTTP status with what the server said', async (t) => {
  const server = await mcpServer(t, {
    post: (entry, res) => {
      if (entry.json?.method !== 'initialize') return false;
      res.writeHead(503, { 'Content-Type': 'text/plain' }).end('Session capacity reached\n');
      return true;
    },
  });
  const bridge = startBridge(t, server.url);
  bridge.send(INIT);
  const answer = await bridge.next();
  assert.equal(answer.id, 1);
  assert.match(answer.error.message, /503/);
  assert.match(answer.error.message, /Session capacity reached/);
});

test('answers with an error when the connection is lost before the answer', async (t) => {
  const server = await mcpServer(t, {
    post: (entry, res) => {
      if (entry.json?.method !== 'tools/call') return false;
      if (entry.json.id === 5) {
        res.writeHead(200, { 'Content-Type': 'text/event-stream' });
        res.write('data: {"jsonrpc":"2.0","method":"notifications/progress","params":{}}\n\n');
      } else {
        res.writeHead(200, { 'Content-Type': 'application/json', 'Content-Length': '500' });
        res.write('{"jsonrpc":"2.0","id":6,');
      }
      setTimeout(() => res.socket.destroy(), 50);
      return true;
    },
  });
  const bridge = startBridge(t, server.url);
  await handshake(bridge);
  bridge.send({ jsonrpc: '2.0', id: 5, method: 'tools/call' });
  assert.equal((await bridge.next()).method, 'notifications/progress');
  const lostStream = await bridge.next();
  assert.equal(lostStream.id, 5);
  assert.match(lostStream.error.message, /lost|closed/i);

  bridge.send({ jsonrpc: '2.0', id: 6, method: 'tools/call' });
  const lostBody = await bridge.next();
  assert.equal(lostBody.id, 6);
  assert.match(lostBody.error.message, /lost|closed/i);
});

test('survives an empty 200, which the server sends for an empty client response', async (t) => {
  const server = await mcpServer(t, {
    post: (entry, res) => {
      if (entry.json?.id !== 50) return false;
      res.writeHead(200).end();
      return true;
    },
  });
  const bridge = startBridge(t, server.url);
  await handshake(bridge);
  bridge.send({ jsonrpc: '2.0', id: 50 });
  bridge.send({ jsonrpc: '2.0', id: 2, method: 'ping' });
  assert.equal((await bridge.next()).id, 2);
});

test('starts a new session and retries once when the server no longer knows the session', async (t) => {
  const server = await mcpServer(t);
  const bridge = startBridge(t, server.url);
  await handshake(bridge);
  await until('the listening stream', () => server.state.streams.length === 1);

  // A restarted cmon-mcp has forgotten every session.
  server.state.stale.add('session-A');
  server.state.streams[0].end();
  bridge.send({ jsonrpc: '2.0', id: 5, method: 'tools/list' });
  assert.deepEqual(await bridge.next(), { jsonrpc: '2.0', id: 5, result: { echoed: 'tools/list' } });

  const [first, second] = posts(server, 'initialize');
  assert.deepEqual(second.json.params, INIT.params, 'the client’s own initialize is replayed');
  assert.notEqual(second.json.id, first.json.id);
  assert.equal(second.headers['mcp-session-id'], undefined);
  assert.equal(posts(server, 'notifications/initialized').at(-1).headers['mcp-session-id'], 'session-B');
  assert.equal(posts(server, 'tools/list').at(-1).headers['mcp-session-id'], 'session-B');
  await until('a listening stream on the new session', () => server.requests.some(
    (r) => r.method === 'GET' && r.headers['mcp-session-id'] === 'session-B'));
  // The client asked twice and was answered twice; the replayed handshake is the bridge's own business.
  assert.deepEqual(bridge.messages.map((m) => m.json.id), [1, 5]);
});

test('gives up after one new session, and says the session ended', async (t) => {
  let refuseEverySession = false;
  const server = await mcpServer(t, {
    post: (entry, res) => {
      if (!refuseEverySession || entry.json?.method === 'initialize') return false;
      res.writeHead(404, { 'Content-Type': 'text/plain' }).end('Invalid session ID\n');
      return true;
    },
  });
  const bridge = startBridge(t, server.url);
  await handshake(bridge);
  refuseEverySession = true;
  bridge.send({ jsonrpc: '2.0', id: 5, method: 'tools/list' });
  const answer = await bridge.next();
  assert.equal(answer.id, 5);
  assert.match(answer.error.message, /session/i);
  assert.equal(posts(server, 'initialize').length, 2, 'one replayed initialize, not a loop');
});

test('does not start a new session for a client response the server refuses', async (t) => {
  const server = await mcpServer(t, {
    post: (entry, res) => {
      if (entry.json?.id !== 41 || entry.json.method !== undefined) return false;
      res.writeHead(404, { 'Content-Type': 'text/plain' }).end('Invalid session ID\n');
      return true;
    },
  });
  const bridge = startBridge(t, server.url);
  await handshake(bridge);
  bridge.send({ jsonrpc: '2.0', id: 41, result: { action: 'accept' } });
  bridge.send({ jsonrpc: '2.0', id: 2, method: 'ping' });
  assert.equal((await bridge.next()).id, 2);
  assert.equal(posts(server, 'initialize').length, 1);
  assert.equal(bridge.messages.length, 2);
});

test('answers a line that is not JSON with a parse error, and keeps going', async (t) => {
  const server = await mcpServer(t);
  const bridge = startBridge(t, server.url);
  bridge.send('{"jsonrpc": "2.0", "id": 1, "method": ');
  const answer = await bridge.next();
  assert.equal(answer.id, null);
  assert.equal(answer.error.code, -32700);
  assert.equal(server.requests.length, 0);
  bridge.send(INIT);
  assert.equal((await bridge.next()).id, 1);
});

test('ends the session when stdin closes, then exits 0', async (t) => {
  const server = await mcpServer(t);
  const bridge = startBridge(t, server.url);
  await handshake(bridge);
  await until('the listening stream', () => server.state.streams.length === 1);
  bridge.end();
  assert.deepEqual(await bridge.exited, { code: 0, signal: null });
  const end = server.requests.find((r) => r.method === 'DELETE');
  assert.ok(end, 'a DELETE was sent');
  assert.equal(end.headers['mcp-session-id'], 'session-A');
  assert.equal(end.headers.authorization, `Bearer ${TOKEN}`);
});

test('lets a request in flight finish when stdin closes', async (t) => {
  const server = await mcpServer(t);
  const bridge = startBridge(t, server.url);
  bridge.send(INIT);
  bridge.end();
  assert.equal((await bridge.next()).id, 1);
  assert.deepEqual(await bridge.exited, { code: 0, signal: null });
});

test('refuses to start with settings it cannot use, and says which', async (t) => {
  const cases = [
    ['no URL', { url: '' }, /MCP URL is not set/],
    ['no token', { token: '' }, /token is not set/],
    ['an unfilled reference', { url: '${user_config.mcp_url}' }, /not a valid URL/],
    ['another scheme', { url: 'ftp://cc.example.com/mcp' }, /http:\/\/ or https:\/\//],
    ['credentials in the URL', { url: 'https://admin:pw-secret-9c1@cc.example.com/mcp' }, /user name or password/],
  ];
  for (const [name, { url = 'http://127.0.0.1:9/mcp', token = TOKEN }, expected] of cases) {
    await t.test(name, async (t) => {
      const bridge = startBridge(t, url, { token });
      assert.deepEqual(await bridge.exited, { code: 78, signal: null });
      assert.match(bridge.stderr(), expected);
      assert.match(bridge.stderr(), /\/plugin configure clustercontrol-mcp@severalnines/);
      assert.doesNotMatch(bridge.stderr(), /pw-secret-9c1/);
      assert.equal(bridge.stdout(), '');
    });
  }
});

test('warns when the token would cross a network unencrypted', async (t) => {
  // 192.0.2.0/24 is reserved for documentation; nothing is sent, because no message is.
  const bridge = startBridge(t, 'http://192.0.2.10:3000/mcp');
  bridge.end();
  assert.deepEqual(await bridge.exited, { code: 0, signal: null });
  assert.match(bridge.stderr(), /unencrypted/);
  assert.match(bridge.stderr(), /192\.0\.2\.10/);
});

test('uses no client with a timeout of its own', () => {
  // An approval can keep a request silent for 30 minutes, and the listening stream is silent for hours.
  // fetch() ends a response that sends nothing for 300 seconds, and a test cannot wait that long to show
  // it, so this pins the choice of node:http instead. See "Timeouts" in the bridge's header comment.
  const source = readFileSync(BRIDGE, 'utf8');
  assert.doesNotMatch(source, /\bfetch\s*\(/);
  assert.doesNotMatch(source, /\.setTimeout\s*\(/);
});
