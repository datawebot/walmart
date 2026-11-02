import { readFileSync, appendFileSync } from 'node:fs';
const replies = JSON.parse(readFileSync(process.env.FAKE_REPLIES, 'utf8'));
globalThis.fetch = async (url, options = {}) => {
  appendFileSync(process.env.FAKE_LOG, JSON.stringify({ url: String(url), method: options.method,
    body: options.body ? JSON.parse(options.body) : null, headers: options.headers,
    redirect: options.redirect }) + '\n');
  if (!replies.length) throw new Error('Unexpected transport call');
  const reply = replies.shift();
  return new Response(reply.status === 202 ? null : JSON.stringify(reply.body), {
    status: reply.status ?? 200, headers: { 'Content-Type': 'application/json' },
  });
};
// Only shorten polling waits; leave AbortSignal's real request deadline alone.
globalThis.setTimeout = (callback, delay, ...args) => { queueMicrotask(() => callback(...args)); return 0; };
