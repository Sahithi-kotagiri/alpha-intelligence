// Thin API client. All calls are same-origin (/api/*) and proxied to FastAPI by Next.
/* eslint-disable @typescript-eslint/no-explicit-any */

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function token(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem("ii_token");
  } catch {
    return null;
  }
}

export async function api<T = any>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json", ...(init.headers as Record<string, string>) };
  const t = token();
  if (t) headers.Authorization = `Bearer ${t}`;
  let body = init.body;
  if (init.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(init.json);
  }
  const res = await fetch(`/api${path}`, { ...init, headers, body, cache: "no-store" });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, msg);
  }
  const ct = res.headers.get("content-type") || "";
  return (ct.includes("application/json") ? res.json() : (res.blob() as any)) as Promise<T>;
}

export type Rec = "Strong Buy" | "Buy" | "Wait" | "Watchlist" | "Reject";

export interface RunSummary {
  id: string;
  ticker: string;
  company_name: string | null;
  version: number;
  status: string;
  trigger: string;
  mode: string | null;
  created_at: string;
  completed_at: string | null;
  recommendation: Rec | null;
  confidence: number | null;
  data_quality: string | null;
  reference_price: number | null;
  price_date: string | null;
  parent_id: string | null;
  error: string | null;
  scores: Record<string, any>;
  sector?: string | null;
}

export interface EventRow {
  seq: number;
  ts: string;
  stage: string;
  agent: string | null;
  status: string;
  message: string;
  detail: any;
}

export interface EvidenceItem {
  id: string;
  category: string;
  title: string;
  value: number | null;
  unit: string | null;
  display: string;
  date: string | null;
  period: string | null;
  source_name: string;
  source_url: string | null;
  source_type: string;
  quality: string;
  untrusted_text: string | null;
  detail: Record<string, any>;
}
