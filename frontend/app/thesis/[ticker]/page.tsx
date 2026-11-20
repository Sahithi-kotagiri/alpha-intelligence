"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { use, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import { byUnit, dt } from "@/lib/format";
import { Badge, Card, ErrorBox, Loading, RecBadge, Status } from "@/components/ui";
import { ReadAloud } from "@/components/Voice";

export default function ThesisDetail({ params }: { params: Promise<{ ticker: string }> }) {
  const { ticker } = use(params);
  const th = useApi<any>(`/thesis/${ticker}`);
  const ev = useApi<any>(`/thesis/${ticker}/events`);
  const [checking, setChecking] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const check = async () => {
    setChecking(true);
    try {
      const r: any = await api("/monitoring/run", { method: "POST" });
      setMsg(`Checked ${r.checked} theses: ${r.events} new events${r.reassessments.length ? `, ${r.reassessments.length} reassessment(s) started` : ""}.`);
      th.reload(); ev.reload();
    } catch (e: any) { setMsg(e.message); }
    setChecking(false);
  };
  if (th.error) return <ErrorBox error={th.error} />;
  if (!th.data) return <Loading />;
  const c = th.data.current;
  return (
    <>
      <div className="page-head">
        <div><h1>{ticker} thesis</h1><p>Version {c.version} from <Link href={`/analysis/${c.analysis_id}`}>analysis of {dt(c.created_at)}</Link> · last checked {c.last_checked_at ? dt(c.last_checked_at, true) : "not yet"}</p></div>
        <div className="row"><Status s={c.overall_status} /><button onClick={check} disabled={checking}>{checking ? "Checking…" : "Check now"}</button></div>
      </div>
      {msg && <div className="callout small mb">{msg}</div>}
      <Card title="Assumptions" sub="Status changes only when a value crosses a documented materiality band">
        <div className="table-wrap"><table>
          <thead><tr><th>Assumption</th><th className="r">Original</th><th className="r">Current</th><th>Review / at risk</th><th>Status</th><th>Checked</th></tr></thead>
          <tbody>{c.assumptions.map((a: any) => (
            <tr key={a.id}><td>{a.description}{a.invalidation && <div className="xs faint">Invalidated if: {a.invalidation}</div>}</td>
              <td className="r num">{byUnit(a.original_value, a.unit)}</td><td className="r num">{a.kind === "qualitative" ? "manual" : byUnit(a.current_value, a.unit)}</td>
              <td className="small num">{a.comparator ? `${a.comparator} ${byUnit(a.review_threshold, a.unit)} / ${byUnit(a.threshold, a.unit)}` : "—"}</td>
              <td><Status s={a.status} /></td><td className="xs faint">{a.last_checked_at ? dt(a.last_checked_at, true) : "—"}</td></tr>))}</tbody>
        </table></div>
      </Card>
      <div className="grid g2 mt">
        <Card title="Events" sub="Each event links to its evidence and any reassessment it triggered"
          actions={<ReadAloud label="Read changes" parts={(ev.data?.events || []).slice(0, 8).map((e: any) => `${e.title}.${e.status_before ? ` Status changed from ${e.status_before} to ${e.status_after}.` : ""}`)} />}>
          {ev.data && !ev.data.events.length && <p className="muted small">No events since the thesis was created.</p>}
          {(ev.data?.events || []).map((e: any) => (
            <div key={e.id} className="claim small">
              <div className="row between"><span><Badge tone={e.materiality === "high" ? "neg" : e.materiality === "medium" ? "warn" : "neu"}>{e.materiality}</Badge> <strong>{e.title}</strong></span><span className="xs faint">{dt(e.created_at, true)}</span></div>
              {e.status_before && <div className="muted">Status {e.status_before} → {e.status_after}</div>}
              {e.detail?.evidence && <div className="xs faint">Evidence: {e.detail.evidence}</div>}
              {e.detail?.current !== undefined && e.detail?.original !== undefined && <div className="xs faint">Original {String(e.detail.original)} · now {String(e.detail.current)}</div>}
              <div className="row xs">{e.source_url && <a href={e.source_url} target="_blank" rel="noreferrer noopener">source</a>}
                {e.reassessment_analysis_id && <Link href={`/analysis/${e.reassessment_analysis_id}`}>reassessment →</Link>}</div>
            </div>))}
        </Card>
        <Card title="Decision history" sub="Older theses are preserved, never overwritten">
          <table><tbody>{th.data.versions.map((v: any) => (
            <tr key={v.version}><td>v{v.version}</td><td><RecBadge rec={v.recommendation} /></td><td><Badge tone={v.status === "active" ? "pos" : "neu"}>{v.status}</Badge></td>
              <td className="small faint">{dt(v.created_at)}</td><td><Link href={`/analysis/${v.analysis_id}`}>open</Link></td></tr>))}</tbody></table>
          {th.data.versions.length > 1 && <p className="small mt-s"><Link href={`/history/compare?a=${th.data.versions[1].analysis_id}&b=${th.data.versions[0].analysis_id}`}>Compare the last two versions →</Link></p>}
        </Card>
      </div>
    </>
  );
}
