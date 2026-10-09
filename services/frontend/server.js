const express = require('express');
const http = require('http');
const path = require('path');
const url = require('url');

const app = express();
const PORT = process.env.PORT || 3000;
const BACKEND_URL = process.env.BACKEND_URL || 'http://backend:8000';

// Transparent proxy to backend
function proxyToBackend(req, res, targetPath) {
  const backendParsed = url.parse(BACKEND_URL);
  const options = {
    hostname: backendParsed.hostname,
    port: backendParsed.port || (backendParsed.protocol === 'https:' ? 443 : 80),
    path: targetPath,
    method: req.method,
    headers: {
      ...req.headers,
      host: backendParsed.host,
      'x-forwarded-for': req.ip || req.connection.remoteAddress,
      'x-forwarded-proto': req.protocol,
    },
  };

  const proxyReq = http.request(options, (backendRes) => {
    // Pass headers
    res.writeHead(backendRes.statusCode, backendRes.headers);

    // Stream response directly to client (critical for SSE text/event-stream)
    backendRes.pipe(res);
  });

  proxyReq.on('error', (err) => {
    console.error(`[Frontend Proxy Error] Failed to reach backend at ${BACKEND_URL}${targetPath}:`, err.message);
    if (!res.headersSent) {
      res.status(502).json({
        error: 'Backend gateway unavailable',
        detail: err.message,
        target: `${BACKEND_URL}${targetPath}`,
      });
    }
  });

  // Forward client request body if present
  req.pipe(proxyReq);
}

// Health check endpoints
app.get(['/health', '/diflector/neurobet/health'], (req, res) => {
  res.json({
    status: 'healthy',
    service: 'neurobet-frontend',
    backend_target: BACKEND_URL,
    timestamp: new Date().toISOString(),
  });
});

// Proxy API requests under both root (/api/*) and base path (/diflector/neurobet/api/*)
app.use((req, res, next) => {
  const parsedUrl = url.parse(req.url);
  const pathname = parsedUrl.pathname || '/';

  if (pathname.startsWith('/diflector/neurobet/api/')) {
    const backendPath = pathname.replace('/diflector/neurobet', '') + (parsedUrl.search || '');
    return proxyToBackend(req, res, backendPath);
  }

  if (pathname.startsWith('/api/')) {
    const backendPath = pathname + (parsedUrl.search || '');
    return proxyToBackend(req, res, backendPath);
  }

  next();
});

// Serve static assets from public directory under both root and basePath
const publicDir = path.join(__dirname, 'public');
app.use('/diflector/neurobet', express.static(publicDir));
app.use(express.static(publicDir));

// Fallback routing to index.html for SPA support
app.get(['/diflector/neurobet/*', '*'], (req, res) => {
  res.sendFile(path.join(publicDir, 'index.html'));
});

app.listen(PORT, '0.0.0.0', () => {
  console.log(`[Neurobet Frontend] Server listening on port ${PORT} (UTC: ${new Date().toISOString()})`);
  console.log(`[Neurobet Frontend] Proxying /api/* -> ${BACKEND_URL}/api/*`);
});
