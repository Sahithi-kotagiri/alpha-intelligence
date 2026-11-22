"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { Fragment, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import { ago, money, pct } from "@/lib/format";
import { Badge, Card, ErrorBox, Loading, RecBadge } from "@/components/ui";
import { LabelDistribution, LABEL_ORDER, PillarBar, PillarPanel, PILLARS, PriceLadder } from "@/components/Rulebook";

const METRIC_COLS: { key: string; label: string; get: (r: any) => number | null; fmt: (v: number) => string }[] = [
  { key: "market_cap", label: "Mkt cap", get: (r) => r.market_cap, fmt: money },
  { key: "trailing_pe", label: "P/E", get: (r) => r.metrics?.trailing_pe, fmt: (v) => v.toFixed(1) },
  { key: "forward_pe", label: "Fwd P/E", get: (r) => r.metrics?.forward_pe, fmt: (v) => v.toFixed(1) },
  { key: "peg_forward", label: "PEG", get: (r) => r.metrics?.peg_forward, fmt: (v) => v.toFixed(2) },
  { key: "ev_to_ebitda", label: "EV/EBITDA", get: (r) => r.metrics?.ev_to_ebitda, fmt: (v) => v.toFixed(1) },
  { key: "debt_to_equity", label: "D/E", get: (r) => r.metrics?.debt_to_equity, fmt: (v) => v.toFixed(2) },
  { key: "revenue_yoy_quarterly", label: "Rev YoY", get: (r) => r.metrics?.revenue_yoy_quarterly, fmt: (v) => pct(v) },
  { key: "operating_margin_ttm", label: "Op margin", get: (r) => r.metrics?.operating_margin_ttm, fmt: (v) => pct(v) },
  { key: "roe_ttm", label: "ROE", get: (r) => r.metrics?.roe_ttm, fmt: (v) => pct(v) },
];

const RB_COLS: { key: string; label: string; get: (r: any) => number | null }[] = [
  { key: "label", label: "Label", get: (r) => (r.label ? LABEL_ORDER.indexOf(r.label) : null) },
  { key: "composite", label: "Composite", get: (r) => r.composite ?? null },
  ...PILLARS.map((p) => ({ key: p.key, label: p.label, get: (r: any) => r.pillars?.[p.key] ?? null })),
  { key: "price_vs_fair", label: "Price/fair", get: (r) => r.price_vs_fair ?? null },
];

function Detail({ ticker }: { ticker: string }) {
  const { data, error, loading } = useApi<any>(`/screener/${ticker}`);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;
  return (
    <div className="grid g2 drawer-inner" style={{ padding: "8px 4px" }}>
      <PillarPanel pillars={data.pillar_detail} composite={data.composite} />
      <div className="stack">
        <PriceLadder price={data.price} fair={data.fair_value} buyBelow={data.buy_below} />
        {data.fair_value_methods?.length > 0 && <ul className="clean xs faint">{data.fair_value_methods.map((m: any) => <li key={m.method}>{m.method}: ${m.value.toFixed(2)}</li>)}</ul>}
        {data.fair_value_low_precision && <div className="callout warn xs">Fair-value methods disagree by more than 2×: low precision.</div>}
        {data.gates?.length > 0 && <ul className="clean small">{data.gates.map((g: any) => <li key={g.id}><Badge tone="warn">≤ {g.effect}</Badge> {g.reason}</li>)}</ul>}
        <ol className="xs muted">{data.trace.map((t: string, i: number) => <li key={i}>{t}</li>)}</ol>
        <div><Link className="btn btn-primary btn-sm" href={`/analyze?ticker=${ticker}`}>Run full analysis</Link>
          <span className="xs faint"> adds data validation, news, filings and the committee</span></div>
      </div>
    </div>
  );
}

export default function Screener() {
  const [poll, setPoll] = useState(false);
  const { data, error, loading, reload } = useApi<any>("/screener", poll ? 3000 : undefined);
  const [view, setView] = useState<"rulebook" | "metrics">("rulebook");
  const [sector, setSector] = useState("");
  const [label, setLabel] = useState("");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 }>({ key: "composite", dir: -1 });
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const run = data?.screen_run;
  const running = run && (run.status === "running" || run.status === "queued");
  useEffect(() => { if (data) setPoll(!!running); }, [data, running]);

  const sectors = useMemo(() => [...new Set((data?.rows || []).map((r: any) => r.sector))].sort() as string[], [data]);
  const cols = view === "rulebook" ? RB_COLS : METRIC_COLS;
  const rows = useMemo(() => {
    const col = (cols as any[]).find((c) => c.key === sort.key);
    return (data?.rows || [])
      .filter((r: any) => (!sector || r.sector === sector) && (!label || r.label === label)
        && (!q || `${r.symbol} ${r.name} ${r.sub_industry}`.toLowerCase().includes(q.toLowerCase())))
      .sort((a: any, b: any) => {
        const va = col ? col.get(a) : a.symbol, vb = col ? col.get(b) : b.symbol;
        if (va === null || va === undefined) return 1;
        if (vb === null || vb === undefined) return -1;
        return va < vb ? -sort.dir : va > vb ? sort.dir : 0;
      });
  }, [data, sector, label, q, sort, cols]);
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const r of data?.rows || []) if (r.label && (!sector || r.sector === sector)) c[r.label] = (c[r.label] || 0) + 1;
    return c;
  }, [data, sector]);

  const startScreen = async () => {
    setBusy(true);
    await api("/screener/run", { method: "POST" }).catch(() => {});
    setBusy(false);
    setPoll(true);
    reload();
  };
  const sortBy = (key: string) => setSort({ key, dir: sort.key === key ? (-sort.dir as 1 | -1) : key === "label" || key === "price_vs_fair" ? 1 : -1 });
  const th = (key: string, text: string, cls = "r") => (
    <th key={key} className={`${cls} sortable`} onClick={() => sortBy(key)} aria-sort={sort.key === key ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
      {text}{sort.key === key ? (sort.dir === 1 ? " ↑" : " ↓") : ""}</th>);

  return (
    <>
      <div className="page-head">
        <div><h1>S&P 500 screener</h1>
          <p>{data ? `${data.count} constituents` : "Loading constituents"} classified by the team rulebook: four sector-percentile pillars, gates and a fair value. A screen label is a first pass, not a recommendation.</p></div>
        <div className="row">
          {run && <span className="small muted">Last screen: {run.status}{running && run.phase ? ` · ${run.phase}` : ""}{run.total ? ` · ${run.succeeded}/${run.total} ok` : ""}{run.completed_at ? ` · ${ago(run.completed_at)}` : ""}</span>}
          <button onClick={startScreen} disabled={busy || !!running}>{running ? <><span className="pulse-dot" /> Screening…</> : "Refresh universe"}</button>
        </div>
      </div>
      {error && <ErrorBox error={error} onRetry={reload} />}
      {loading && !data && <Loading />}
      {data && (
        <>
          <Card title="Rulebook labels" sub={run?.rulebook_version ? `${run.rulebook_version} · click a segment to filter` : "click a segment to filter"} className="mb">
            <LabelDistribution counts={counts} active={label} onPick={setLabel} />
          </Card>
          <Card>
            <div className="row mb">
              <div className="seg" role="group" aria-label="View">
                <button className={view === "rulebook" ? "on" : ""} onClick={() => { setView("rulebook"); setSort({ key: "composite", dir: -1 }); }}>Rulebook</button>
                <button className={view === "metrics" ? "on" : ""} onClick={() => { setView("metrics"); setSort({ key: "market_cap", dir: -1 }); }}>Vendor metrics</button>
              </div>
              <input placeholder="Filter by ticker, name or sub-industry" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Filter" style={{ minWidth: 220 }} />
              <select value={sector} onChange={(e) => setSector(e.target.value)} aria-label="Sector">
                <option value="">All sectors</option>{sectors.map((s) => <option key={s}>{s}</option>)}
              </select>
              {label && <button className="chip on" onClick={() => setLabel("")}>{label} ×</button>}
              <span className="small muted">{rows.length} shown</span>
            </div>
            {!data.rows.some((r: any) => r.label) && <div className="callout small mb">No rulebook labels yet. “Refresh universe” fetches prices, SEC filings and estimates for every constituent (batched; about 10–15 minutes) and classifies each one.</div>}
            <div className="table-wrap" style={{ maxHeight: "72vh", overflowY: "auto" }}>
              <table>
                <thead style={{ position: "sticky", top: 0, background: "var(--surface)", zIndex: 1 }}><tr>
                  {th("symbol", "Company", "")}
                  <th>Sector</th>
                  {view === "rulebook" ? (<>
                    {th("label", "Label", "")}{th("composite", "Composite")}
                    <th className="r" title="Quality · Value · Momentum · Revisions"><span className="minis xs faint">{PILLARS.map((p) => <span key={p.key} className="sortable" onClick={() => sortBy(p.key)}>{p.label.slice(0, 4)}{sort.key === p.key ? (sort.dir === 1 ? "↑" : "↓") : ""}</span>)}</span></th>
                    <th className="r">Price</th><th className="r">Buy-below</th>{th("price_vs_fair", "Price/fair")}<th>Gates</th>
                  </>) : (<>{METRIC_COLS.map((c) => th(c.key, c.label))}<th>200d</th></>)}
                  <th />
                </tr></thead>
                <tbody>{rows.map((r: any) => (
                  <Fragment key={r.symbol}>
                    <tr className={view === "rulebook" && r.label ? "clickable" : ""} onClick={() => view === "rulebook" && r.label && setOpen(open === r.symbol ? null : r.symbol)}
                      aria-expanded={view === "rulebook" ? open === r.symbol : undefined}>
                      <td><strong className="mono">{r.symbol}</strong> <span className="small">{r.name}</span></td>
                      <td className="xs muted">{r.sector}</td>
                      {view === "rulebook" ? (<>
                        <td>{r.label ? <RecBadge rec={r.label} /> : <span className="xs faint">not screened</span>}</td>
                        <td className="r num">{r.composite !== undefined ? Math.round(r.composite) : "—"}</td>
                        <td className="r"><span className="minis">{PILLARS.map((p) => <PillarBar key={p.key} score={r.pillars?.[p.key]} label={p.label} compact />)}</span></td>
                        <td className="r num">{r.price ? `$${r.price.toFixed(2)}` : "—"}</td>
                        <td className="r num">{r.buy_below ? `$${r.buy_below.toFixed(2)}` : "—"}</td>
                        <td className="r num" style={{ color: r.price_vs_fair ? (r.price_vs_fair <= 0.85 ? "var(--pos)" : r.price_vs_fair > 1.2 ? "var(--neg)" : undefined) : undefined }}>{r.price_vs_fair ? r.price_vs_fair.toFixed(2) : "—"}</td>
                        <td className="xs gates-cell">{(r.gates || []).map((g: string) => <Badge key={g} tone="warn">{g.replace(/_/g, " ")}</Badge>)}</td>
                      </>) : (<>
                        {METRIC_COLS.map((c) => {
                          const v = c.get(r);
                          const p = r.sector_percentiles?.[c.key];
                          return <td key={c.key} className="r num" title={p !== undefined ? `Better than ${pct(p, 0)} of sector` : undefined}
                            style={p !== undefined ? { color: p >= 0.6 ? "var(--pos)" : p < 0.3 ? "var(--neg)" : undefined } : undefined}>{v === null || v === undefined ? "—" : c.fmt(v)}</td>;
                        })}
                        <td>{r.above_200d === null || r.above_200d === undefined ? "—" : r.above_200d ? <Badge tone="pos">above</Badge> : <Badge tone="neg">below</Badge>}</td>
                      </>)}
                      <td onClick={(e) => e.stopPropagation()}><Link className="btn btn-sm" href={`/analyze?ticker=${r.symbol}`}>Analyse</Link></td>
                    </tr>
                    {open === r.symbol && view === "rulebook" && <tr className="drawer-row"><td colSpan={10}><Detail ticker={r.symbol} /></td></tr>}
                  </Fragment>))}</tbody>
              </table>
            </div>
            <p className="xs faint mt">{view === "rulebook" ? data.rulebook : data.method} Sources: Wikipedia constituents table, SEC EDGAR XBRL filings, Yahoo Finance prices and consensus estimates via yfinance.</p>
          </Card>
        </>
      )}
    </>
  );
}
