'use strict';

const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = __dirname;
const PORT = Number(process.env.PORT || 4173);
const HOST = process.env.HOST || '0.0.0.0';
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.ico': 'image/x-icon',
};

function send(res, status, body, contentType = 'text/plain; charset=utf-8') {
  res.writeHead(status, {
    'Content-Type': contentType,
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'SAMEORIGIN',
    'Referrer-Policy': 'strict-origin-when-cross-origin',
    'Cache-Control': 'no-store',
    'Content-Security-Policy': "default-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; base-uri 'self'; form-action 'self'",
  });
  res.end(body);
}

function createServer() {
  return http.createServer((req, res) => {
    let pathname;
    try {
      pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
    } catch {
      return send(res, 400, 'Bad request');
    }

    if (pathname === '/healthz') {
      return send(res, 200, JSON.stringify({ status: 'ok' }), 'application/json; charset=utf-8');
    }

    if (req.method !== 'GET' && req.method !== 'HEAD') {
      return send(res, 405, 'Method not allowed');
    }

    if (pathname === '/') pathname = '/index.html';
    if (pathname.includes('\0') || pathname.split('/').some((part) => part === '.' || part === '..')) {
      return send(res, 400, 'Invalid path');
    }
    const publicFiles = new Set(['/index.html','/app.js','/auth.js','/styles.css','/auth.css','/print.css']);
    if (!publicFiles.has(pathname)) return send(res, 404, 'Not found');

    const filePath = path.resolve(ROOT, `.${pathname}`);
    if (!filePath.startsWith(`${ROOT}${path.sep}`)) return send(res, 403, 'Forbidden');

    fs.stat(filePath, (statError, stat) => {
      if (statError || !stat.isFile()) return send(res, 404, 'Not found');
      const contentType = MIME[path.extname(filePath).toLowerCase()] || 'application/octet-stream';
      res.writeHead(200, {
        'Content-Type': contentType,
        'X-Content-Type-Options': 'nosniff',
        'X-Frame-Options': 'SAMEORIGIN',
        'Referrer-Policy': 'strict-origin-when-cross-origin',
        'Cache-Control': 'no-store',
        'Content-Security-Policy': "default-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; base-uri 'self'; form-action 'self'",
      });
      if (req.method === 'HEAD') return res.end();
      fs.createReadStream(filePath).pipe(res);
    });
  });
}

if (require.main === module) {
  createServer().listen(PORT, HOST, () => {
    console.log(`YD attendance prototype available on http://${HOST}:${PORT}`);
  });
}

module.exports = { createServer };
