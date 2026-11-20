"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useApi } from "@/hooks/useApi";
import { dt, pct } from "@/lib/format";
import { Badge, Card, Empty, Loading, RecBadge } from "@/components/ui";

function HistoryInner() {
  const sp = useSearchParams();
  const router = useRouter();
  const [ticker, setTicker] = useState(sp.get("ticker") || "");
  const [rec, setRec] = useState("");
  const [sel, setSel] = useState<string[]>([]);
  const q = `/analysis?limit=300${ticker ? `&ticker=${ticker}` : ""}${rec ? `&recommendation=${encodeURIComponent(rec)}` : ""}`;
  const { data, loading } = useApi<any>(q);
  const toggle = (id: string) => setSel((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s.slice(-1), id]));
  return (
    <>
      <div className="page-head">
        <div><h1>Historical decisions</h1><p>Every analysis is an immutable, versioned record. Select two to compare.</p></div>
        <button className="btn-primary" disabled={sel.length !== 2} onClick={() => router.push(`/history/compare?a=${sel[0]}&b=${sel[1]}`)}>Compare selected ({sel.length}/2)</button>
      </div>
      <Card>
        <div className="row mb">
          <input placeholder="Ticker" value={ticker} onChange={(e) => setTicker(e.target.value.toUpperCase())} style={{ width: 110 }} aria-label="Ticker filter" />
          <select value={rec} onChange={(e) => setRec(e.target.value)} aria-label="Recommendation filter">
            <option value="">All recommendations</option>{["Buy Now", "Wait", "Watchlist", "Reject"].map((r) => <option key={r}>{r}</option>)}
          </select>
        </div>
        {loading && <Loading />}
        {data && !data.analyses.length && <Empty title="No matching analyses" />}
        {data?.analyses?.length > 0 && (
          <div className="table-wrap"><table>
            <thead><tr><th /><th>Date</th><th>Company</th><th>Version</th><th>Recommendation</th><th className="r">Confidence</th><th className="r">Quality</th><th className="r">Valuation</th><th className="r">Technical</th><th>Price</th><th>Trigger</th></tr></thead>
            <tbody>{data.analyses.map((r: any) => (
              <tr key={r.id}>
                <td><input type="checkbox" checked={sel.includes(r.id)} onChange={() => toggle(r.id)} disabled={r.status !== "completed"} aria-label={`Select ${r.ticker} v${r.version}`} /></td>
                <td className="small num">{dt(r.created_at, true)}</td>
                <td><Link href={`/analysis/${r.id}`}><strong className="mono">{r.ticker}</strong></Link> <span className="small muted">{r.company_name}</span></td>
                <td>v{r.version}</td>
                <td>{r.status === "completed" ? <RecBadge rec={r.recommendation} /> : <Badge tone={r.status === "failed" ? "neg" : "acc"}>{r.status}</Badge>}</td>
                <td className="r num">{r.confidence ? pct(r.confidence, 0) : "—"}</td>
                <td className="r num">{r.scores?.business_quality ?? "—"}</td><td className="r num">{r.scores?.valuation ?? "—"}</td><td className="r num">{r.scores?.technical ?? "—"}</td>
                <td className="small num">{r.reference_price ? `$${r.reference_price.toFixed(2)}` : "—"}</td>
                <td className="xs">{r.trigger}{r.mode ? ` · ${r.mode}` : ""}</td>
              </tr>))}</tbody>
          </table></div>)}
      </Card>
    </>
  );
}

export default function HistoryPage() {
  return <Suspense fallback={<Loading />}><HistoryInner /></Suspense>;
}
