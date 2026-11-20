"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useMemo, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import { ago, money, pct } from "@/lib/format";
import { Badge, Card, ErrorBox, Loading } from "@/components/ui";

const COLS: { key: string; label: string; get: (r: any) => number | null; fmt: (v: number) => string }[] = [
  { key: "market_cap", label: "Mkt cap", get: (r) => r.market_cap, fmt: money },
  { key: "trailing_pe", label: "P/E", get: (r) => r.metrics?.trailing_pe, fmt: (v) => v.toFixed(1) },
  { key: "forward_pe", label: "Fwd P/E", get: (r) => r.metrics?.forward_pe, fmt: (v) => v.toFixed(1) },
  { key: "peg_forward", label: "PEG", get: (r) => r.metrics?.peg_forward, fmt: (v) => v.toFixed(2) },
  { key: "ev_to_ebitda", label: "EV/EBITDA", get: (r) => r.metrics?.ev_to_ebitda, fmt: (v) => v.toFixed(1) },
  { key: "debt_to_equity", label: "D/E", get: (r) => r.metrics?.debt_to_equity, fmt: (v) => v.toFixed(2) },
  { key: "revenue_yoy_quarterly", label: "Rev YoY", get: (r) => r.metrics?.revenue_yoy_quarterly, fmt: (v) => pct(v) },
  { key: "operating_margin_ttm", label: "Op margin", get: (r) => r.metrics?.operating_margin_ttm, fmt: (v) => pct(v) },
  { key: "roe_ttm", label: "ROE", get: (r) => r.metrics?.roe_ttm, fmt: (v) => pct(v) },
  { key: "value_rank", label: "Value rank", get: (r) => r.value_rank, fmt: (v) => pct(v, 0) },
  { key: "quality_rank", label: "Quality rank", get: (r) => r.quality_rank, fmt: (v) => pct(v, 0) },
];

export default function Screener() {
  const { data, error, loading, reload } = useApi<any>("/screener", undefined);
  const [sector, setSector] = useState("");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 }>({ key: "market_cap", dir: -1 });
  const [busy, setBusy] = useState(false);
  const run = data?.screen_run;
  const running = run && (run.status === "running" || run.status === "queued");
  useApi<any>(running ? "/screener" : null, 4000);

  const sectors = useMemo(() => [...new Set((data?.rows || []).map((r: any) => r.sector))].sort() as string[], [data]);
  const rows = useMemo(() => {
    const col = COLS.find((c) => c.key === sort.key);
    return (data?.rows || [])
      .filter((r: any) => (!sector || r.sector === sector) && (!q || `${r.symbol} ${r.name} ${r.sub_industry}`.toLowerCase().includes(q.toLowerCase())))
      .sort((a: any, b: any) => {
        const va = col ? col.get(a) : a.symbol, vb = col ? col.get(b) : b.symbol;
        if (va === null || va === undefined) return 1;
        if (vb === null || vb === undefined) return -1;
        return va < vb ? -sort.dir : va > vb ? sort.dir : 0;
      });
  }, [data, sector, q, sort]);

  const startScreen = async () => {
    setBusy(true);
    await api("/screener/run", { method: "POST" }).catch(() => {});
    setBusy(false);
    reload();
  };

  return (
    <>
      <div className="page-head">
        <div><h1>S&P 500 screener</h1>
          <p>{data ? `${data.count} constituents` : "Loading constituents"}. Vendor-reported metrics with sector-relative percentiles. Ranks are screening aids, not recommendations.</p></div>
        <div className="row">
          {run && <span className="small muted">Last screen: {run.status}{run.total ? ` · ${run.succeeded}/${run.total} ok, ${run.failed} failed` : ""}{run.completed_at ? ` · ${ago(run.completed_at)}` : ""}</span>}
          <button onClick={startScreen} disabled={busy || !!running}>{running ? <><span className="pulse-dot" /> Screening…</> : "Refresh universe metrics"}</button>
        </div>
      </div>
      {error && <ErrorBox error={error} onRetry={reload} />}
      {loading && <Loading />}
      {data && (
        <Card>
          <div className="row mb">
            <input placeholder="Filter by ticker, name or sub-industry" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Filter" style={{ minWidth: 260 }} />
            <select value={sector} onChange={(e) => setSector(e.target.value)} aria-label="Sector">
              <option value="">All sectors</option>{sectors.map((s) => <option key={s}>{s}</option>)}
            </select>
            <span className="small muted">{rows.length} shown</span>
          </div>
          {!data.rows.some((r: any) => r.metrics) && <div className="callout small mb">No metrics yet. “Refresh universe metrics” fetches vendor data for all constituents (throttled; takes a few minutes). It also speeds up peer comparisons in analyses.</div>}
          <div className="table-wrap" style={{ maxHeight: "70vh", overflowY: "auto" }}>
            <table>
              <thead style={{ position: "sticky", top: 0, background: "var(--surface)" }}><tr>
                <th className="sortable" onClick={() => setSort({ key: "symbol", dir: sort.key === "symbol" ? (-sort.dir as 1 | -1) : 1 })}>Company</th>
                <th>Sector / sub-industry</th>
                {COLS.map((c) => <th key={c.key} className="r sortable" onClick={() => setSort({ key: c.key, dir: sort.key === c.key ? (-sort.dir as 1 | -1) : -1 })}
                  aria-sort={sort.key === c.key ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>{c.label}{sort.key === c.key ? (sort.dir === 1 ? " ↑" : " ↓") : ""}</th>)}
                <th>200d</th><th />
              </tr></thead>
              <tbody>{rows.map((r: any) => (
                <tr key={r.symbol}>
                  <td><strong className="mono">{r.symbol}</strong> <span className="small">{r.name}</span></td>
                  <td className="xs muted">{r.sector}<br />{r.sub_industry}</td>
                  {COLS.map((c) => {
                    const v = c.get(r);
                    const p = r.sector_percentiles?.[c.key];
                    return <td key={c.key} className="r num" title={p !== undefined ? `Better than ${pct(p, 0)} of sector` : undefined}
                      style={p !== undefined ? { color: p >= 0.6 ? "var(--pos)" : p < 0.3 ? "var(--neg)" : undefined } : undefined}>{v === null || v === undefined ? "—" : c.fmt(v)}</td>;
                  })}
                  <td>{r.above_200d === null || r.above_200d === undefined ? "—" : r.above_200d ? <Badge tone="pos">above</Badge> : <Badge tone="neg">below</Badge>}</td>
                  <td><Link className="btn btn-sm" href={`/analyze?ticker=${r.symbol}`}>Analyse</Link></td>
                </tr>))}</tbody>
            </table>
          </div>
          <p className="xs faint mt">{data.method} Colour: green ≥60th, red &lt;30th sector percentile (hover for value). Source: {data.rows[0]?.retrieved_at ? `snapshot ${ago(data.rows[0].retrieved_at)}` : "constituent list"}; Wikipedia constituents table + Yahoo Finance via yfinance.</p>
        </Card>
      )}
    </>
  );
}
