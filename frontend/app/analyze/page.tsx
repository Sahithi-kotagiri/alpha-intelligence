"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { Badge, Card, ErrorBox, Loading } from "@/components/ui";

function AnalyzeInner() {
  const router = useRouter();
  const sp = useSearchParams();
  const [q, setQ] = useState(sp.get("ticker") || "");
  const [results, setResults] = useState<any[]>([]);
  const [ident, setIdent] = useState<any>(null);
  const [resolving, setResolving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [horizon, setHorizon] = useState("12 months");
  const [benchmark, setBenchmark] = useState("SPY");
  const [withPf, setWithPf] = useState(false);
  const [hasPf, setHasPf] = useState(false);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    api("/portfolio").then((d: any) => setHasPf(d.portfolios.length > 0 && d.portfolios[0].holdings.length > 0)).catch(() => {});
    api("/settings").then((s: any) => { setHorizon(s.default_horizon); setBenchmark(s.default_benchmark); }).catch(() => {});
    const t = sp.get("ticker");
    if (t) resolve(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (q.trim().length < 1 || ident) return setResults([]);
    const h = setTimeout(() => api(`/companies/search?q=${encodeURIComponent(q)}`).then((d: any) => setResults(d.results)).catch(() => {}), 200);
    return () => clearTimeout(h);
  }, [q, ident]);

  async function resolve(t: string) {
    setErr(null);
    setResolving(true);
    setResults([]);
    try {
      const r: any = await api(`/companies/${encodeURIComponent(t)}`);
      if (!r.resolved) {
        setErr(r.message);
        setResults(r.candidates || []);
      } else setIdent(r);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setResolving(false);
    }
  }

  async function start() {
    setStarting(true);
    setErr(null);
    try {
      const r: any = await api("/analysis", { method: "POST", json: { ticker: ident.identity.ticker, horizon, benchmark, include_portfolio: withPf, confirmed: true,
        idempotency_key: `${ident.identity.ticker}-${Date.now()}` } });
      router.push(`/analysis/${r.analysis_id}`);
    } catch (e: any) {
      setErr(e.message);
      setStarting(false);
    }
  }

  const id = ident?.identity;
  return (
    <>
      <div className="page-head"><div><h1>New stock analysis</h1><p>Search any S&P 500 company (other SEC-registered US tickers are supported with reduced peer context).</p></div></div>
      <div className="grid g2">
        <Card title="1 · Find the company">
          <input autoFocus value={q} placeholder="Ticker or company name, e.g. NVDA or Nvidia" aria-label="Search company" style={{ width: "100%" }}
            onChange={(e) => { setQ(e.target.value); setIdent(null); }}
            onKeyDown={(e) => { if (e.key === "Enter" && q.trim()) resolve(results[0]?.symbol || q.trim()); }} />
          {results.length > 0 && (
            <div className="mt-s" role="listbox" aria-label="Matches">
              {results.map((r) => (
                <button key={r.symbol} className="btn-ghost" style={{ width: "100%", justifyContent: "space-between" }} onClick={() => { setQ(r.symbol); resolve(r.symbol); }}>
                  <span><strong className="mono">{r.symbol}</strong> {r.name}</span>
                  <span className="xs faint">{r.in_sp500 ? r.sub_industry : "not in S&P 500"}</span>
                </button>))}
            </div>)}
          {resolving && <div className="mt-s"><Loading label="Resolving identity" /></div>}
          {err && <div className="mt-s"><ErrorBox error={err} /></div>}
        </Card>
        <Card title="2 · Confirm identity" sub="The analysis will not start until you confirm the security">
          {!id ? <p className="muted small">Select a company to see its legal name, exchange, currency and security type.</p> : (
            <>
              <dl className="kv">
                <dt>Company</dt><dd><strong>{id.name}</strong></dd>
                <dt>Ticker</dt><dd className="mono">{id.ticker}</dd>
                <dt>Exchange</dt><dd>{id.exchange || "unknown"}</dd>
                <dt>Currency</dt><dd>{id.currency || "unknown"}{id.financial_currency && id.financial_currency !== id.currency ? ` (reports in ${id.financial_currency})` : ""}</dd>
                <dt>Security type</dt><dd>{id.security_type || "unknown"}</dd>
                <dt>GICS</dt><dd>{id.sector ? `${id.sector} / ${id.sub_industry}` : "n/a"}</dd>
                <dt>SEC CIK</dt><dd className="mono">{id.cik || "n/a"}</dd>
                <dt>S&P 500</dt><dd>{id.in_sp500 ? <Badge tone="pos">constituent</Badge> : <Badge tone="warn">not a constituent</Badge>}</dd>
              </dl>
              {ident.warnings?.map((w: string) => <div key={w} className="callout warn small mt-s">{w}</div>)}
            </>)}
        </Card>
        <Card title="3 · Options">
          <div className="grid g2">
            <label className="field">Investment horizon
              <select value={horizon} onChange={(e) => setHorizon(e.target.value)}>
                {["3 months", "6 months", "12 months", "3 years", "5+ years"].map((h) => <option key={h}>{h}</option>)}
              </select></label>
            <label className="field">Benchmark
              <select value={benchmark} onChange={(e) => setBenchmark(e.target.value)}>
                {["SPY", "QQQ", "IWM", "DIA"].map((b) => <option key={b}>{b}</option>)}
              </select></label>
          </div>
          <label className="check mt"><input type="checkbox" checked={withPf} disabled={!hasPf} onChange={(e) => setWithPf(e.target.checked)} />
            Include portfolio context {hasPf ? "" : <span className="xs faint">(save holdings on the Portfolio page first)</span>}</label>
        </Card>
        <Card title="4 · Run">
          <p className="small muted">The platform will retrieve prices, statements, SEC filings and news; validate and cross-check them; calculate metrics; run five specialist analysts and the committee; and save a versioned, monitorable decision. Progress shown is the real backend state.</p>
          <button className="btn-primary mt-s" disabled={!id || starting} onClick={start}>{starting ? "Starting…" : id ? `Confirm ${id.ticker} and start analysis` : "Confirm a company first"}</button>
        </Card>
      </div>
    </>
  );
}

export default function AnalyzePage() {
  return <Suspense fallback={<Loading />}><AnalyzeInner /></Suspense>;
}
