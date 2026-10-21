import type { NextConfig } from "next";

// Production is a static export (web/out) that the Python app serves from the
// same origin as the API -- one process, one port, no CORS. In development the
// Next dev server proxies /api and /auth to the Python app on 8765; start that
// with BSYNC_DEV_ORIGIN=http://localhost:3000 so it accepts writes from here.
const isDev = process.env.NODE_ENV !== "production";
const backend = process.env.BSYNC_BACKEND ?? "http://127.0.0.1:8765";

const nextConfig: NextConfig = {
  output: isDev ? undefined : "export",
  trailingSlash: true,
  images: { unoptimized: true },
  ...(isDev
    ? {
        async rewrites() {
          return [
            { source: "/api/:path*", destination: `${backend}/api/:path*` },
            { source: "/auth/:path*", destination: `${backend}/auth/:path*` },
            { source: "/research/:path*", destination: `${backend}/research/:path*` },
          ];
        },
      }
    : {}),
};

export default nextConfig;
