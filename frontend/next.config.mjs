/** API calls go to same-origin /api/* and are proxied to FastAPI, so no API keys or backend URLs reach the browser. */
const backend = process.env.API_INTERNAL_URL || "http://127.0.0.1:8000";
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
  async headers() {
    return [{ source: "/:path*", headers: [
      { key: "X-Content-Type-Options", value: "nosniff" },
      { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
      { key: "Permissions-Policy", value: "microphone=(self), camera=()" },
      { key: "X-Frame-Options", value: "DENY" },
    ]}];
  },
};
export default nextConfig;
