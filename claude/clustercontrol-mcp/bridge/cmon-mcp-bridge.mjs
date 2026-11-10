#!/usr/bin/env node
// cmon-mcp-bridge connects Claude Code to the ClusterControl MCP server, cmon-mcp.
//
// Claude Code starts this file and speaks MCP to it over stdin and stdout, one JSON-RPC message per
// line. The bridge forwards each message to the cmon-mcp Streamable HTTP endpoint that the user
// configured, and writes what the server sends back. It reads two environment variables, which the
// plugin fills from its options:
//
//   CLUSTERCONTROL_MCP_URL    the endpoint, for example http://127.0.0.1:3000/mcp
//   CLUSTERCONTROL_MCP_TOKEN  the bearer token
//
// It connects to that URL and to nothing else. It reads and writes no files, runs no other program,
// and uses only modules that ship with Node.js. The token goes into the Authorization header and is
// never written to stdout or stderr.
//
// What it speaks to the server:
//
//   POST    every message from Claude Code. The server answers 202 (nothing to relay), 200 with one
//           JSON message, or 200 with an event stream of messages.
//   GET     one listening stream, opened once Claude Code has sent notifications/initialized. The
//           server sends its own requests here, such as an approval prompt, so the bridge reopens the
//           stream when it drops.
//   DELETE  ends the session when Claude Code closes stdin.
//
// Messages are relayed as text and never re-encoded: a JSON number above 2^53, such as a nanosecond
// timestamp, would lose digits if it were parsed and written again.
//
// Timeouts. An approval can keep a request without any data for 30 minutes, and the listening stream
// is silent for hours, so nothing here ends a connection for being quiet. That is why this uses
// node:http and not the global fetch function, which ends a response that sends nothing for 300
// seconds. Only connecting is timed.
//
// Retries. CMON jobs are not idempotent, so a request that may have reached the server is never sent
// twice. Each request gets its own connection, which leaves no idle connection for the server to close
// under a request. The one retry is after HTTP 404 for the session: the server refused the request
// before running it, typically because cmon-mcp restarted, so the bridge starts a new session and sends
// that request once more.

import http from 'node:http';
import https from 'node:https';
import { createInterface } from 'node:readline';

const NAME = 'cmon-mcp-bridge';
const CONFIGURE = 'Run /plugin configure clustercontrol-mcp@severalnines to change it.';
const EX_CONFIG = 78;
const CONNECT_TIMEOUT_MS = 15_000;
const LISTEN_RETRY_MS = [1_000, 2_000, 4_000, 8_000, 15_000];
const LISTEN_WAIT_MS = 3_000;
const CLOSE_WAIT_MS = 2_000;
const EVENT_STREAM = 'text/event-stream';

const log = (text) => process.stderr.write(`${NAME}: ${text}\n`);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const oneLine = (text) => text.replace(/[\r\n]+/g, ' ').trim();

function parse(text) {
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

// Returns the settings, or reports what is wrong with them. The URL is never echoed, because a
// refused one may contain a password.
function readSettings() {
  const refuse = (problem) => {
    log(`${problem} ${CONFIGURE}`);
    process.exitCode = EX_CONFIG;
  };
  const rawUrl = (process.env.CLUSTERCONTROL_MCP_URL ?? '').trim();
  const token = (process.env.CLUSTERCONTROL_MCP_TOKEN ?? '').trim();
  if (!rawUrl) return refuse('The ClusterControl MCP URL is not set.');
  if (!token || token.startsWith('${')) return refuse('The ClusterControl MCP token is not set.');
  let url;
  try {
    url = new URL(rawUrl);
  } catch {
    return refuse('The ClusterControl MCP URL is not a valid URL.');
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    return refuse('The ClusterControl MCP URL must start with http:// or https://.');
  }
  if (url.username || url.password) {
    return refuse('The ClusterControl MCP URL must not contain a user name or password.');
  }
  return { url, token };
}

// Turns the chunks of an event stream into the text of each "message" event.
function eventStream(onMessage) {
  let rest = '';
  let type = 'message';
  let data = [];
  return (chunk) => {
    const lines = (rest + chunk).split('\n');
    rest = lines.pop();
    for (let line of lines) {
      if (line.endsWith('\r')) line = line.slice(0, -1);
      if (line === '') {
        if (data.length > 0 && type === 'message') onMessage(data.join('\n'));
        type = 'message';
        data = [];
      } else if (line.startsWith('data:')) {
        data.push(line.slice(line[5] === ' ' ? 6 : 5));
      } else if (line.startsWith('event:')) {
        type = line.slice(6).trim();
      }
    }
  };
}

function run({ url, token }) {
  const where = `${url.origin}${url.pathname}`;
  const transport = url.protocol === 'https:' ? https : http;
  const agent = new transport.Agent({ keepAlive: false });
  const inFlight = new Set();
  let session; // the Mcp-Session-Id the server issued
  let protocolVersion; // what initialize negotiated
  let handshake; // the client's own initialize request, kept to start a new session with
  let renewal; // a new session being started, shared by the requests waiting for it
  let listening; // the current listening stream
  let closing = false;

  const emit = (text) => process.stdout.write(`${oneLine(text)}\n`);
  const fail = (id, message, code = -32000) => emit(JSON.stringify({ jsonrpc: '2.0', id, error: { code, message } }));

  // Sends one request and resolves with the response once its headers arrive. `sessionId` is null for
  // a request that must not carry a session.
  function request(method, body, sessionId) {
    const headers = { Authorization: `Bearer ${token}`, Accept: `application/json, ${EVENT_STREAM}` };
    if (body !== undefined) {
      headers['Content-Type'] = 'application/json';
      headers['Content-Length'] = Buffer.byteLength(body);
    }
    if (sessionId) {
      headers['Mcp-Session-Id'] = sessionId;
      if (protocolVersion) headers['Mcp-Protocol-Version'] = protocolVersion;
    }
    return new Promise((resolve, reject) => {
      const req = transport.request(url, { method, headers, agent }, (res) => {
        res.setEncoding('utf8');
        resolve(res);
      });
      const timer = setTimeout(() => {
        req.destroy(Object.assign(new Error('connecting timed out'), { code: 'ETIMEDOUT' }));
      }, CONNECT_TIMEOUT_MS);
      req.on('socket', (socket) => {
        socket.once(transport === https ? 'secureConnect' : 'connect', () => clearTimeout(timer));
      });
      req.on('error', (err) => {
        clearTimeout(timer);
        reject(err);
      });
      req.end(body);
    });
  }

  // Reads a response to its end. Each JSON-RPC message of a 200 goes to onMessage; the body of any other
  // status is returned as text. `whole` is false when the connection was lost before the end.
  async function consume(res, onMessage) {
    const ok = res.statusCode === 200;
    const streaming = (res.headers['content-type'] ?? '').startsWith(EVENT_STREAM);
    let text = '';
    const feed = ok && streaming ? eventStream(onMessage) : (chunk) => { text += chunk; };
    const whole = await new Promise((resolve) => {
      res.on('data', feed);
      res.on('end', () => resolve(true));
      res.on('close', () => resolve(res.complete));
      res.on('error', () => {});
    });
    if (ok && !streaming && whole && text.trim()) onMessage(text);
    return { status: res.statusCode, whole, text: ok ? '' : text };
  }

  function describe(status, text, hadSession) {
    if (status === 401) {
      return `The ClusterControl MCP server at ${where} refused the token (HTTP 401). Compare it with `
        + `MCP_AUTH_TOKEN, or with your own token in the identity map, on the controller. ${CONFIGURE}`;
    }
    if (status === 404 && hadSession) {
      return `The ClusterControl MCP server at ${where} no longer knows this session (HTTP 404). `
        + 'Reconnect the server from /mcp.';
    }
    const said = oneLine(text).slice(0, 300);
    const hint = status === 404 ? ' Check that the URL ends in /mcp.' : '';
    return `The ClusterControl MCP server at ${where} answered HTTP ${status}${said ? `: ${said}` : ''}.${hint}`;
  }

  function explain(err) {
    if (err.described) return err.message;
    const code = err.code ?? err.message;
    let hint = '';
    if (code === 'ECONNREFUSED') {
      hint = ' Check that the SSH tunnel is open, that cmon-mcp is running, and that the URL is right.';
    } else if (/CERT|SELF_SIGNED|UNABLE_TO_|HOSTNAME_MISMATCH|ERR_TLS/.test(code)) {
      // Node's names for a certificate it would not accept, such as UNABLE_TO_VERIFY_LEAF_SIGNATURE for
      // one signed by a CA it does not know.
      hint = ' The server certificate was not trusted. For a private CA, start Claude Code with '
        + 'NODE_EXTRA_CA_CERTS=/path/to/ca.pem.';
    }
    return `Cannot reach the ClusterControl MCP server at ${where} (${code}).${hint}`;
  }

  // Opens the listening stream for the current session and keeps it open. Resolves when the stream is
  // open, when the server refuses it, or after LISTEN_WAIT_MS, whichever is first.
  function listen() {
    stopListening();
    const mine = { session, attempt: 0 };
    listening = mine;
    const current = () => listening === mine && !closing;
    return new Promise((resolve) => {
      const wait = setTimeout(resolve, LISTEN_WAIT_MS);
      const settled = () => {
        clearTimeout(wait);
        resolve();
      };
      const connect = async () => {
        if (!current()) return;
        let retry = true;
        try {
          const res = await request('GET', undefined, mine.session);
          mine.res = res;
          if (!current()) {
            res.destroy();
            return;
          }
          if (res.statusCode === 200) {
            mine.attempt = 0;
            settled();
            await consume(res, emit);
          } else {
            // 404 is a session the server forgot; the next request starts a new one. 405 is a server
            // that has no listening stream. Neither gets better by asking again.
            retry = res.statusCode >= 500;
            res.resume();
            if (!retry) log(`the server refused the listening stream (HTTP ${res.statusCode})`);
          }
        } catch (err) {
          log(`the listening stream failed (${err.code ?? err.message})`);
        }
        settled();
        if (retry && current()) {
          const delay = LISTEN_RETRY_MS[Math.min(mine.attempt, LISTEN_RETRY_MS.length - 1)];
          mine.attempt += 1;
          mine.timer = setTimeout(connect, delay);
        }
      };
      connect();
    });
  }

  function stopListening() {
    if (!listening) return;
    clearTimeout(listening.timer);
    listening.res?.destroy();
    listening = undefined;
  }

  // Replays the client's handshake to get a new session, when the server no longer knows `stale`.
  function renew(stale) {
    if (renewal) return renewal;
    if (session !== stale) return Promise.resolve(); // another request has renewed it already
    renewal = startSession().finally(() => {
      renewal = undefined;
    });
    return renewal;
  }

  async function startSession() {
    const give = (why) => Object.assign(new Error(`Could not start a new session. ${why}`), { described: true });
    stopListening();
    const id = `${NAME}-${Date.now()}`;
    let result;
    const res = await request('POST', JSON.stringify({ ...handshake, id }), null);
    const first = await consume(res, (text) => {
      const reply = parse(text);
      if (reply?.id === id) result = reply.result;
    });
    if (first.status !== 200 || !result) throw give(describe(first.status, first.text, false));
    session = res.headers['mcp-session-id'];
    protocolVersion = result.protocolVersion;
    const done = await request('POST', JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }), session);
    const second = await consume(done, () => {});
    if (second.status >= 300) throw give(describe(second.status, second.text, true));
    await listen();
  }

  async function handle(line) {
    const message = parse(line);
    if (message === undefined || message === null || typeof message !== 'object') {
      fail(null, 'The line is not a JSON message.', -32700);
      return;
    }
    const isRequest = message.method !== undefined && message.id !== undefined;
    const isInitialize = message.method === 'initialize';
    let answered = false;
    const relay = (text) => {
      if (isRequest && !answered) {
        const reply = parse(text);
        if (reply && reply.id === message.id && reply.method === undefined) {
          answered = true;
          if (isInitialize) protocolVersion = reply.result?.protocolVersion;
        }
      }
      emit(text);
    };
    const attempt = async (sessionId) => {
      const res = await request('POST', line, sessionId);
      // Set before the answer is written, so that the client's next message already carries it.
      if (isInitialize && res.statusCode === 200) session = res.headers['mcp-session-id'];
      return consume(res, relay);
    };

    if (isInitialize) {
      stopListening();
      session = undefined;
      protocolVersion = undefined;
      handshake = message;
    }
    let used = isInitialize ? null : session;
    let problem;
    try {
      let reply = await attempt(used ?? null);
      // A client response is not retried: the request it answers belonged to the session that is gone.
      if (reply.status === 404 && used && message.method !== undefined && handshake) {
        await renew(used);
        used = session;
        reply = await attempt(used ?? null);
      }
      if (reply.status === 200 && !reply.whole) {
        problem = `The connection to the ClusterControl MCP server at ${where} was lost before it answered.`;
      } else if (reply.status === 200 || reply.status === 202 || reply.status === 204) {
        if (message.method === 'notifications/initialized') listen();
        if (isRequest && !answered) {
          problem = `The ClusterControl MCP server at ${where} closed the request without an answer.`;
        }
      } else {
        problem = describe(reply.status, reply.text, Boolean(used));
      }
    } catch (err) {
      problem = explain(err);
    }
    if (problem === undefined || answered) return;
    if (isRequest) fail(message.id, problem);
    else log(problem);
  }

  async function close() {
    if (closing) return;
    closing = true;
    stopListening();
    await Promise.race([Promise.allSettled([...inFlight]), sleep(CLOSE_WAIT_MS)]);
    if (session) {
      try {
        const res = await Promise.race([request('DELETE', undefined, session), sleep(CLOSE_WAIT_MS)]);
        res?.resume();
      } catch {
        // The session ends by itself when the server stops hearing from it.
      }
    }
    // The callback runs once everything written before it has been handed to the pipe.
    process.stdout.write('', () => process.exit(0));
  }

  log(`forwarding to ${where}`);
  if (url.protocol === 'http:' && !/^(localhost|127\.\d+\.\d+\.\d+|\[::1\])$/.test(url.hostname)) {
    log(`warning: ${url.hostname} is not this machine and the URL is http://, so the token crosses the `
      + 'network unencrypted. Use an SSH tunnel or an https:// URL.');
  }
  process.stdout.on('error', () => process.exit(0));
  process.on('SIGTERM', close);
  process.on('SIGINT', close);
  const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
  lines.on('line', (line) => {
    if (!line.trim()) return;
    const work = handle(line);
    inFlight.add(work);
    work.finally(() => inFlight.delete(work));
  });
  lines.on('close', close);
}

const settings = readSettings();
if (settings) run(settings);
