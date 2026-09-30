'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { createServer } = require('../server');

let server;
let base;
test.before(async () => {
  server = createServer();
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(async () => {
  await new Promise((resolve, reject) => server.close((err) => err ? reject(err) : resolve()));
});

test('serves the application shell with security headers', async () => {
  const response = await fetch(base);
  assert.equal(response.status, 200);
  assert.match(response.headers.get('content-type'), /text\/html/);
  assert.equal(response.headers.get('x-content-type-options'), 'nosniff');
  assert.match(await response.text(), /YD Daily Attendance, Absence and Canteen Reporting System/);
});

test('serves the client application and stylesheet', async () => {
  const app = await fetch(`${base}/app.js`);
  const styles = await fetch(`${base}/styles.css`);
  assert.equal(app.status, 200);
  assert.match(app.headers.get('content-type'), /javascript/);
  assert.equal(styles.status, 200);
  assert.match(styles.headers.get('content-type'), /text\/css/);
});

test('provides a health endpoint', async () => {
  const response = await fetch(`${base}/healthz`);
  assert.deepEqual(await response.json(), { status: 'ok' });
});

test('rejects unsupported methods and missing files', async () => {
  assert.equal((await fetch(base, { method: 'POST' })).status, 405);
  assert.equal((await fetch(`${base}/missing.js`)).status, 404);
});
