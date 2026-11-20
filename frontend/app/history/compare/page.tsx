"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useApi } from "@/hooks/useApi";
import { dt, pct } from "@/lib/format";
import { Card, ErrorBox, Loading, RecBadge, Verdict } from "@/components/ui";

function CompareInner() {
  const sp = useSearchParams();
  const { data, error } = useApi<any>(`/analysis/compare?a=${sp.get("a")}&b=${sp.get("b")}`);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const side = (s: any, label: string) => (
    <Card title={label} sub={<Link href={`/analysis/${s.id}`}>open analysis</Link>}>
      <RecBadge rec={s.recommendation} large />
      <dl className="kv small mt"><dt>Confidence</dt><dd>{pct(s.confidence, 0)}</dd><dt>Prices through</dt><dd>{dt(s.price_date)}</dd><dt>Financials</dt><dd>{s.financials_through}</dd></dl>
    </Card>
  );
  return (
    <>
      <div className="page-head"><div><h1>What changed</h1><p>{data.recommendation_changed ? "The recommendation changed." : "The recommendation did not change."}</p></div></div>
      <div className="grid g2 mb">{side(data.a, "Earlier")}{side(data.b, "Later")}</div>
      <Card title="Drivers of the change" className="mb"><ul className="clean small">{data.drivers.map((d: string) => <li key={d}>{d}</li>)}</ul></Card>
      <div className="grid g2">
        <Card title="Scores">
          <table><thead><tr><th>Score</th><th className="r">Earlier</th><th className="r">Later</th><th className="r">Change</th></tr></thead>
            <tbody>{data.scores.map((s: any) => <tr key={s.score}><td>{s.score.replace("_", " ")}</td><td className="r num">{s.a ?? "—"}</td><td className="r num">{s.b ?? "—"}</td>
              <td className="r num" style={{ color: s.change > 0 ? "var(--pos)" : s.change < 0 ? "var(--neg)" : undefined }}>{s.change === null ? "—" : `${s.change > 0 ? "+" : ""}${s.change.toFixed(2)}`}</td></tr>)}</tbody></table>
        </Card>
        <Card title="Key metrics">
          <table><thead><tr><th>Metric</th><th className="r">Earlier</th><th className="r">Later</th><th>Verdict</th></tr></thead>
            <tbody>{data.metrics.map((m: any) => <tr key={m.metric}><td>{m.label}</td><td className="r num">{m.a ?? "—"}</td><td className="r num">{m.b ?? "—"}</td>
              <td>{m.verdict_changed ? <span className="row"><Verdict v={m.a_verdict} />→<Verdict v={m.b_verdict} /></span> : m.b_verdict ? <Verdict v={m.b_verdict} /> : "—"}</td></tr>)}</tbody></table>
        </Card>
        <Card title="Thesis assumptions">
          <table><tbody>{data.thesis.map((t: any) => <tr key={t.key}><td className="small">{t.a || <span className="faint">—</span>}</td><td className="small">{t.b || <span className="faint">—</span>}</td></tr>)}</tbody></table>
        </Card>
        <Card title="Decision traces">
          <h3 className="small muted">Earlier</h3><ul className="clean small">{(data.decision_trace.a || []).map((x: string) => <li key={x}>{x}</li>)}</ul>
          <h3 className="small muted mt-s">Later</h3><ul className="clean small">{(data.decision_trace.b || []).map((x: string) => <li key={x}>{x}</li>)}</ul>
        </Card>
      </div>
    </>
  );
}

export default function ComparePage() {
  return <Suspense fallback={<Loading />}><CompareInner /></Suspense>;
}
