"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import Link from "next/link";
import { useApi } from "@/hooks/useApi";
import { ago, dt, pct } from "@/lib/format";
import { Badge, Card, Empty, Loading, RecBadge, Status } from "@/components/ui";

export default function Overview() {
  const runs = useApi<any>("/analysis?limit=8");
  const theses = useApi<any>("/thesis");
  const alerts = useApi<any>("/alerts?unread=true");
  const watch = useApi<any>("/watchlist");
  const pf = useApi<any>("/portfolio");
  const sources = useApi<any>("/data-sources");
  const settings = useApi<any>("/settings");
  const recent = runs.data?.analyses || [];
  const atRisk = (theses.data?.theses || []).filter((t: any) => t.overall_status !== "Valid");
  const upcoming = (watch.data?.items || []).filter((w: any) => w.next_event);
  const p = pf.data?.portfolios?.[0];
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Overview</h1>
          <p>Your saved research, thesis changes and data health. Nothing here is simulated; empty sections mean there is no data yet.</p>
        </div>
        <Link className="btn btn-primary" href="/analyze">Analyse a stock</Link>
      </div>
      {settings.data && !settings.data.server.llm_configured && (
        <div className="callout warn mb small">
          No LLM provider is configured. Analyses run in rule-based mode: every number, threshold, score and decision gate works, but agents do not
          interpret qualitative evidence (news sentiment, moat, management). See <Link href="/settings">Settings</Link>.
        </div>
      )}
      <div className="grid g3 mb">
        <div className="card stat"><span className="label">Saved analyses</span><span className="value">{runs.data ? recent.length : "…"}</span></div>
        <div className="card stat"><span className="label">Active theses needing review</span><span className="value">{theses.data ? atRisk.length : "…"}</span></div>
        <div className="card stat"><span className="label">Unread alerts</span><span className="value">{alerts.data ? alerts.data.alerts.length : "…"}</span></div>
      </div>
      <div className="grid g2">
        <Card title="Recent decisions" actions={<Link href="/history" className="small">All history →</Link>}>
          {runs.loading && <Loading />}
          {runs.data && !recent.length && <Empty title="No analyses yet"><p>Start by analysing any S&P 500 company.</p><Link href="/analyze">New analysis →</Link></Empty>}
          {recent.length > 0 && (
            <table><tbody>{recent.map((r: any) => (
              <tr key={r.id}>
                <td><Link href={`/analysis/${r.id}`}><strong>{r.ticker}</strong></Link> <span className="muted small">{r.company_name}</span></td>
                <td>{r.status === "completed" ? <RecBadge rec={r.recommendation} /> : <Badge tone={r.status === "failed" ? "neg" : "acc"}>{r.status}</Badge>}</td>
                <td className="num small">{r.confidence ? pct(r.confidence, 0) : ""}</td>
                <td className="small faint">{ago(r.created_at)}</td>
              </tr>))}</tbody></table>
          )}
        </Card>
        <Card title="Thesis changes" actions={<Link href="/thesis" className="small">Thesis monitor →</Link>}>
          {theses.data && !theses.data.theses.length && <Empty title="No theses monitored">Each completed analysis stores a testable thesis.</Empty>}
          {(theses.data?.theses || []).slice(0, 6).map((t: any) => (
            <div key={t.id} className="row between claim">
              <span><Link href={`/thesis/${t.ticker}`}><strong>{t.ticker}</strong></Link> <span className="small muted">v{t.version} · {t.recommendation}</span></span>
              <span className="row"><Status s={t.overall_status} /><span className="xs faint">checked {t.last_checked_at ? ago(t.last_checked_at) : "not yet"}</span></span>
            </div>))}
        </Card>
        <Card title="Alerts" actions={<Link href="/watchlist" className="small">Watchlist & alerts →</Link>}>
          {alerts.data && !alerts.data.alerts.length && <p className="muted small">No unread alerts.</p>}
          {(alerts.data?.alerts || []).slice(0, 6).map((a: any) => (
            <div key={a.id} className="claim small"><Badge tone={a.severity === "high" ? "neg" : a.severity === "medium" ? "warn" : "neu"}>{a.severity}</Badge> {a.title}
              <div className="xs faint">{dt(a.created_at, true)}</div></div>))}
        </Card>
        <Card title="Upcoming events (watchlist)" sub="Vendor calendar dates; confirm with the company">
          {!upcoming.length && <p className="muted small">No upcoming events on watched companies.</p>}
          {upcoming.map((w: any) => <div key={w.ticker} className="claim small"><strong>{w.ticker}</strong> {w.next_event.title}: {dt(w.next_event.date)}</div>)}
        </Card>
        <Card title="Portfolio" actions={<Link href="/portfolio" className="small">Portfolio intelligence →</Link>}>
          {p ? <p className="small">{p.name}: {p.holdings.length} holdings, cash ${p.cash.toLocaleString()}, risk tolerance {p.risk_tolerance || "not set"}.</p>
            : <p className="muted small">No portfolio saved. Add holdings to assess stocks as portfolio additions.</p>}
        </Card>
        <Card title="Data sources" actions={<Link href="/settings" className="small">Details →</Link>}>
          {(sources.data?.sources || []).map((s: any) => (
            <div key={s.name} className="row between small claim">
              <span>{s.name}</span>
              <span className="row">{s.last_failure_at && (!s.last_success_at || s.last_failure_at > s.last_success_at) ? <Badge tone="neg">failing</Badge> : <Badge tone="pos">ok</Badge>}
                <span className="xs faint">last success {ago(s.last_success_at)}</span></span>
            </div>))}
          {sources.data && !sources.data.sources.length && <p className="muted small">Sources are recorded after the first analysis.</p>}
          {sources.data?.universe_retrieved_at && <p className="xs faint mt-s">S&P 500 list refreshed {ago(sources.data.universe_retrieved_at)}.</p>}
        </Card>
      </div>
    </>
  );
}
