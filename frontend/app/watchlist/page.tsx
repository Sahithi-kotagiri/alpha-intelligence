"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import { dt, pct } from "@/lib/format";
import { Badge, Card, Empty, ErrorBox, Loading, RecBadge, Status } from "@/components/ui";
import { ReadAloud } from "@/components/Voice";

export default function Watchlist() {
  const wl = useApi<any>("/watchlist");
  const alerts = useApi<any>("/alerts");
  const [t, setT] = useState("");
  const [target, setTarget] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [result, setResult] = useState<any>(null);

  const add = async () => {
    setErr(null);
    try {
      await api("/watchlist", { method: "POST", json: { ticker: t, target_price: target ? Number(target) : null } });
      setT(""); setTarget(""); wl.reload();
    } catch (e: any) { setErr(e.message); }
  };
  const remove = async (ticker: string) => { await api(`/watchlist/${ticker}`, { method: "DELETE" }); wl.reload(); };
  const prefs = async (w: any, k: string) => {
    await api("/watchlist", { method: "POST", json: { ticker: w.ticker, note: w.note, target_price: w.target_price, alert_prefs: { ...w.alert_prefs, [k]: !w.alert_prefs?.[k] } } });
    wl.reload();
  };
  const check = async () => {
    setChecking(true);
    try { setResult(await api("/monitoring/run", { method: "POST" })); alerts.reload(); wl.reload(); } catch (e: any) { setErr(e.message); }
    setChecking(false);
  };
  const read = async (id: number) => { await api(`/alerts/${id}/read`, { method: "POST" }); alerts.reload(); };

  return (
    <>
      <div className="page-head">
        <div><h1>Watchlist & alerts</h1><p>Monitored stocks, price conditions and thesis-change alerts. Checks run on a schedule and on demand.</p></div>
        <button onClick={check} disabled={checking}>{checking ? <><span className="pulse-dot" /> Checking…</> : "Run checks now"}</button>
      </div>
      {result && <div className="callout small mb">Checked {result.checked} theses · {result.events} new events · {result.reassessments.length} reassessments started{result.errors.length ? ` · ${result.errors.length} errors` : ""}.</div>}
      {err && <ErrorBox error={err} />}
      <div className="grid g2">
        <Card title="Watchlist" actions={
          <div className="row">
            <input placeholder="Ticker" value={t} onChange={(e) => setT(e.target.value.toUpperCase())} style={{ width: 90 }} aria-label="Ticker" />
            <input placeholder="Alert at ≤ $" value={target} onChange={(e) => setTarget(e.target.value)} style={{ width: 100 }} aria-label="Target price" inputMode="decimal" />
            <button onClick={add} disabled={!t}>Add</button>
          </div>}>
          {wl.loading && <Loading />}
          {wl.data && !wl.data.items.length && <Empty title="Nothing on your watchlist">Add a ticker here or from an analysis page.</Empty>}
          {(wl.data?.items || []).map((w: any) => (
            <div key={w.ticker} className="claim">
              <div className="row between">
                <span><strong className="mono">{w.ticker}</strong> <span className="small muted">{w.last_analysis?.company_name}</span></span>
                <span className="row">{w.last_analysis ? <Link href={`/analysis/${w.last_analysis.id}`}><RecBadge rec={w.last_analysis.recommendation} /></Link> : <Link className="btn btn-sm" href={`/analyze?ticker=${w.ticker}`}>Analyse</Link>}
                  {w.thesis_status && <Status s={w.thesis_status} />}<button className="btn-sm btn-ghost" onClick={() => remove(w.ticker)} aria-label={`Remove ${w.ticker}`}>Remove</button></span>
              </div>
              <div className="small muted mt-s">
                {w.last_analysis ? `Reference $${w.last_analysis.reference_price?.toFixed(2)} (${dt(w.last_analysis.price_date)}), confidence ${pct(w.last_analysis.confidence, 0)}. ` : "Not yet analysed. "}
                {w.target_price ? `Price alert at ≤ $${w.target_price}. ` : ""}{w.next_event ? `${w.next_event.title}: ${dt(w.next_event.date)}.` : ""}
              </div>
              <div className="row mt-s xs">
                {["thesis", "filings", "price", "earnings"].map((k) => (
                  <label key={k} className="check xs"><input type="checkbox" checked={!!w.alert_prefs?.[k]} onChange={() => prefs(w, k)} /> {k}</label>))}
              </div>
            </div>))}
        </Card>
        <Card title="Alerts" sub="Generated only from real monitoring events"
          actions={<ReadAloud label="Read unread alerts" parts={(alerts.data?.alerts || []).filter((a: any) => !a.read).map((a: any) => `${a.title}. ${a.message}`)} />}>
          {alerts.data && !alerts.data.alerts.length && <p className="muted small">No alerts yet.</p>}
          {(alerts.data?.alerts || []).map((a: any) => (
            <div key={a.id} className="claim small" style={{ opacity: a.read ? 0.6 : 1 }}>
              <div className="row between"><span><Badge tone={a.severity === "high" ? "neg" : a.severity === "medium" ? "warn" : "neu"}>{a.severity}</Badge> <strong>{a.title}</strong></span>
                {!a.read && <button className="btn-sm btn-ghost" onClick={() => read(a.id)}>Mark read</button>}</div>
              <div className="muted">{a.message}</div>
              <div className="xs faint">{dt(a.created_at, true)} · {a.kind}{a.ticker ? ` · ` : ""}{a.ticker && <Link href={`/thesis/${a.ticker}`}>{a.ticker} thesis</Link>}</div>
            </div>))}
        </Card>
      </div>
    </>
  );
}
