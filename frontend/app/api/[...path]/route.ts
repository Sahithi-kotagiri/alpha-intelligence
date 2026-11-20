// Runtime proxy: browser -> same-origin /api/* -> FastAPI (API_INTERNAL_URL, read at request time).
// Streams responses (so Server-Sent Events pass through) and never exposes backend credentials.
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const HOP = new Set(["connection", "keep-alive", "transfer-encoding", "upgrade", "host", "content-length", "content-encoding"]);

async function proxy(req: Request, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const backend = process.env.API_INTERNAL_URL || "http://127.0.0.1:8000";
  const url = new URL(req.url);
  const target = `${backend}/api/${path.map(encodeURIComponent).join("/")}${url.search}`;
  const headers = new Headers();
  req.headers.forEach((v, k) => { if (!HOP.has(k.toLowerCase())) headers.set(k, v); });
  const init: RequestInit & { duplex?: string } = { method: req.method, headers, redirect: "manual", signal: req.signal };
  if (req.method !== "GET" && req.method !== "HEAD") {
    init.body = await req.arrayBuffer();
  }
  let res: Response;
  try {
    res = await fetch(target, init);
  } catch {
    return Response.json({ detail: "The analysis backend is unreachable. Check that the API server is running." }, { status: 502 });
  }
  const out = new Headers();
  res.headers.forEach((v, k) => { if (!HOP.has(k.toLowerCase())) out.set(k, v); });
  if ((res.headers.get("content-type") || "").includes("text/event-stream")) {
    out.set("Cache-Control", "no-cache, no-transform");
    out.set("X-Accel-Buffering", "no");
  }
  return new Response(res.body, { status: res.status, headers: out });
}

export { proxy as GET, proxy as POST, proxy as PUT, proxy as PATCH, proxy as DELETE };
