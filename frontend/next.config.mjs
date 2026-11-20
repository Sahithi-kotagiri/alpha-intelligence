/** API calls go to same-origin /api/* and are proxied at runtime by app/api/[...path]/route.ts (API_INTERNAL_URL). */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
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
