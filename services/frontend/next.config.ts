import type { NextConfig } from "next";

const backendUrl = process.env.BACKEND_URL || "http://backend:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  basePath: "/diflector/neurobet",
  assetPrefix: "/diflector/neurobet",
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendUrl}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
