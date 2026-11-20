"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import { dt, pct } from "@/lib/format";
import { Badge, Card, Empty, ErrorBox, Loading, RecBadge } from "@/components/ui";

function Agg({ rows, label }: { rows: any[]; label: string }) {
  if (!rows.length) return <p className="muted small">No completed observations yet.</p>;
  return (
    <table><thead><tr><th>{label}</th><th>Horizon</th><th className="r">n</th><th className="r">Mean excess</th><th className="r">Median excess</th><th className="r">Hit rate</th></tr></thead>
      <tbody>{rows.map((r, i) => (
        <tr key={i}><td>{r.group}</td><td className="small">{r.horizon}</td><td className="r num">{r.n}{r.small_sample && <span className="faint" title="Small sample: not statistically meaningful">*</span>}</td>
          <td className="r num">{pct(r.mean_excess)}</td><td className="r num">{pct(r.median_excess)}</td><td className="r num">{r.hit_rate === null ? "n/a" : pct(r.hit_rate, 0)}</td></tr>))}</tbody></table>
  );
}

export default function Performance() {
  const { data, loading, error, reload } = useApi<any>("/performance");
  const runs = useApi<any>("/analysis?limit=50");
  const [busy, setBusy] = useState(false);
  const [aid, setAid] = useState("");
  const [evalRes, setEvalRes] = useState<any>(null);
  const [evalErr, setEvalErr] = useState<string | null>(null);
  const update = async () => { setBusy(true); await api("/performance/update", { method: "POST" }).catch(() => {}); setBusy(false); reload(); };
  const baseline = async () => {
    setEvalErr(null); setEvalRes(null);
    try { setEvalRes(await api("/evaluation/baseline", { method: "POST", json: { analysis_id: aid, repeats: 2 } })); } catch (e: any) { setEvalErr(e.message); }
  };
  const s = data?.summary;
  return (
    <>
      <div className="page-head">
        <div><h1>Performance & evaluation</h1><p>Outcomes of past recommendations versus their benchmark, and quality checks against a single-LLM baseline.</p></div>
        <button onClick={update} disabled={busy}>{busy ? "Updating…" : "Update outcomes"}</button>
      </div>
      {error && <ErrorBox error={error} />}
      {loading && <Loading />}
      {s && (
        <>
          <div className="grid g3 mb">
            <div className="card stat"><span className="label">Completed observations</span><span className="value">{s.observations_complete}</span></div>
            <div className="card stat"><span className="label">Pending (horizon not reached)</span><span className="value">{s.observations_pending}</span></div>
            <div className="card stat"><span className="label">Recommendations tracked</span><span className="value">{data.recommendations.length}</span></div>
          </div>
          <div className="callout small mb"><ul className="clean">{s.caveats.map((c: string) => <li key={c}>{c}</li>)}</ul></div>
          <div className="grid g2">
            <Card title="By recommendation"><Agg rows={s.by_recommendation} label="Recommendation" /></Card>
            <Card title="By business-quality score band"><Agg rows={s.by_quality_band} label="Band" /></Card>
            <Card title="By sector"><Agg rows={s.by_sector} label="Sector" /></Card>
            <Card title="Acted vs not acted" sub="From the user-action field on each recommendation">
              {s.by_user_action.length ? <table><tbody>{s.by_user_action.map((r: any) => <tr key={r.action}><td>{r.action}</td><td className="r num">n={r.n}</td><td className="r num">{pct(r.mean_excess)}</td></tr>)}</tbody></table> : <p className="muted small">No data.</p>}
            </Card>
          </div>
          <Card title="Recommendation outcomes" sub="Entry = adjusted close on the decision's price date; horizons in trading sessions" className="mt">
            {!data.recommendations.length ? <Empty title="No recommendations yet" /> : (
              <div className="table-wrap"><table><thead><tr><th>Decision</th><th>Ticker</th><th>Rec.</th>{["1m", "3m", "6m", "12m"].map((h) => <th key={h} className="r">{h} excess</th>)}<th>Action</th></tr></thead>
                <tbody>{data.recommendations.map((r: any) => (
                  <tr key={r.analysis_id}><td className="small num">{dt(r.price_date)}</td><td><Link href={`/analysis/${r.analysis_id}`} className="mono">{r.ticker}</Link></td><td><RecBadge rec={r.recommendation} /></td>
                    {r.observations.map((o: any) => <td key={o.horizon} className="r num">{o.status === "complete" ? pct(o.excess_return) : <span className="faint xs">pending</span>}</td>)}
                    <td className="xs">{r.user_action}</td></tr>))}</tbody></table></div>)}
          </Card>
        </>
      )}
      <Card title="Single-LLM baseline comparison" sub="Runs the same evidence pack through one comprehensive prompt and compares traceability, missing-data detection and risk coverage" className="mt">
        <div className="row mb">
          <select value={aid} onChange={(e) => setAid(e.target.value)} aria-label="Analysis">
            <option value="">Select an analysis</option>
            {(runs.data?.analyses || []).filter((r: any) => r.status === "completed").map((r: any) => <option key={r.id} value={r.id}>{r.ticker} v{r.version} · {dt(r.created_at)}</option>)}
          </select>
          <button onClick={baseline} disabled={!aid}>Run comparison</button>
        </div>
        {evalErr && <ErrorBox error={evalErr} />}
        {evalRes && !evalRes.available && <div className="callout warn small">{evalRes.reason}</div>}
        {evalRes?.platform && (
          <table className="mt-s"><thead><tr><th>Metric</th><th className="r">Platform</th>{evalRes.available && <th className="r">Baseline (mean)</th>}<th>Better</th></tr></thead>
            <tbody>{(evalRes.available ? evalRes.comparison.rows : Object.entries(evalRes.platform).filter(([, v]) => typeof v === "number").map(([k, v]) => ({ metric: k, platform: v, better: "" })))
              .map((r: any) => <tr key={r.metric}><td>{r.metric.replace(/_/g, " ")}</td><td className="r num">{r.platform === null ? "—" : Number(r.platform).toFixed(3)}</td>
                {evalRes.available && <td className="r num">{r.baseline_mean === null ? "—" : Number(r.baseline_mean).toFixed(3)}</td>}<td className="xs">{r.better}</td></tr>)}</tbody></table>)}
        {evalRes?.available && <p className="small mt-s">Baseline recommendations: {evalRes.comparison.baseline_recommendations.join(", ")} (consistency {pct(evalRes.comparison.baseline_consistency, 0)}); platform: <Badge>{evalRes.comparison.platform_recommendation}</Badge>. {evalRes.comparison.note}</p>}
      </Card>
    </>
  );
}
