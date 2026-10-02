// The production build is a static export that FastAPI serves itself, so the
// product stays one container, one origin, and needs no CORS at all.
//
// `next dev` is the exception: it runs on :3000 and proxies the API to the
// Python server on :8000, so the interface can be worked on with hot reload.
const isDev = process.env.NODE_ENV !== "production";
const API = process.env.API_ORIGIN || "http://127.0.0.1:8000";

/** @type {import('next').NextConfig} */
const config = {
  reactStrictMode: true,
  // /studio/ -> studio/index.html, which is what FastAPI's StaticFiles serves
  // for a directory request.
  trailingSlash: true,
  images: { unoptimized: true },
  ...(isDev
    ? {
        // FastAPI owns its own slashes; don't let Next rewrite them first.
        skipTrailingSlashRedirect: true,
        async rewrites() {
          return [
            { source: "/api/:path*", destination: `${API}/api/:path*` },
            { source: "/assets/:path*", destination: `${API}/assets/:path*` },
          ];
        },
      }
    : { output: "export" }),
};

export default config;
