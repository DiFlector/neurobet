import type { NextConfig } from "next"
const SITE_BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "/neurobet"

// Proxies /api/* through the Next.js server to the backend, over the internal Docker
// network (service name "backend") rather than a second externally-exposed port. This
// is what lets the app work behind a single forwarded port (e.g. router 80 -> 3000):
// the browser only ever talks to the one origin it loaded the page from, so there's no
// cross-origin request at all — no CORS, no Private Network Access block, no need to
// open port 8000 to the internet. BACKEND_INTERNAL_URL is a plain (non-NEXT_PUBLIC_)
// env var, so it's read fresh by the Next.js server at container start, not baked into
// the client bundle at build time — changing it just needs a container restart.
//
// С basePath rewrite source "/api/:path*" автоматически матчится как "/neurobet/api/:path*".
const BACKEND_INTERNAL_URL = process.env.BACKEND_INTERNAL_URL || "http://localhost:8000"

const nextConfig: NextConfig = {
  basePath: SITE_BASE_PATH,
  // Иначе /neurobet/ ↔ /neurobet даёт петлю с прокси
  skipTrailingSlashRedirect: true,
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${BACKEND_INTERNAL_URL}/api/:path*`,
      },
    ]
  },
  async redirects() {
    return [
      // Нейроставки moved from /neurobets to the site root — keep old links working.
      { source: "/neurobets", destination: "/", permanent: false },
    ]
  },
}

export default nextConfig
