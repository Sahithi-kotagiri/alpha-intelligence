export function pct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

export function num(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function money(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  return `$${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

export function byUnit(v: number | null | undefined, unit?: string | null): string {
  if (v === null || v === undefined) return "—";
  switch (unit) {
    case "pct":
      return pct(v);
    case "x":
      return `${v.toFixed(1)}x`;
    case "USD":
      return money(v);
    case "USD/share":
      return `$${v.toFixed(2)}`;
    case "shares":
      return `${(v / 1e6).toFixed(1)}M`;
    default:
      return num(v);
  }
}

export function dt(s: string | null | undefined, withTime = false): string {
  if (!s) return "—";
  const d = new Date(s.length === 10 ? `${s}T00:00:00` : s);
  if (Number.isNaN(d.getTime())) return s;
  return withTime
    ? d.toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function ago(s: string | null | undefined): string {
  if (!s) return "—";
  const ms = Date.now() - new Date(s).getTime();
  const m = Math.round(ms / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.round(h / 24)} days ago`;
}

export const recTone: Record<string, string> = {
  "Strong Buy": "strong", Buy: "pos", "Buy Now": "pos", Wait: "warn", Watchlist: "acc", Reject: "neg", "Insufficient data": "neu",
};
export const RECS = ["Strong Buy", "Buy", "Wait", "Watchlist", "Reject"];
export const verdictTone: Record<string, string> = {
  favourable: "pos", unfavourable: "neg", neutral: "neu", informational: "neu", not_meaningful: "neu",
  insufficient_data: "warn", not_applicable: "neu",
};
export const statusTone: Record<string, string> = {
  Valid: "pos", Improving: "pos", Review: "warn", "At risk": "neg", Invalidated: "neg", Unknown: "neu",
  good: "pos", acceptable: "acc", poor: "warn", critical: "neg",
};
export const AGENT_TITLES: Record<string, string> = {
  fundamental: "Fundamental Analyst", valuation: "Valuation Analyst", technical: "Technical Analyst", news: "News & Estimates Analyst",
  risk_portfolio: "Risk & Portfolio Analyst", devils_advocate: "Devil's Advocate", committee: "Investment Committee",
  final_decision: "Final decision", rulebook: "Rulebook",
};
