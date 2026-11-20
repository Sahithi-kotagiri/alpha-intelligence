"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import { money, pct } from "@/lib/format";
import { Card, ErrorBox, Loading } from "@/components/ui";
import { CorrelationTable, WeightBars } from "@/components/charts";

type H = { ticker: string; quantity?: string; market_value?: string; weight?: string };

export default function Portfolio() {
  const pf = useApi<any>("/portfolio");
  const [form, setForm] = useState<any>({ name: "My portfolio", cash: "0", risk_tolerance: "", horizon: "", benchmark: "SPY", excluded_sectors: "", max_position: "" });
  const [rows, setRows] = useState<H[]>([{ ticker: "" }]);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [cand, setCand] = useState("");
  const [riskPath, setRiskPath] = useState<string | null>(null);
  const risk = useApi<any>(riskPath);

  useEffect(() => {
    const p = pf.data?.portfolios?.[0];
    if (!p) return;
    setForm({ name: p.name, cash: String(p.cash), risk_tolerance: p.risk_tolerance || "", horizon: p.horizon || "", benchmark: p.benchmark,
      excluded_sectors: (p.excluded_sectors || []).join(", "), max_position: p.max_position ? String(p.max_position * 100) : "" });
    setRows(p.holdings.length ? p.holdings.map((h: any) => ({ ticker: h.ticker, quantity: h.quantity?.toString() ?? "", market_value: h.market_value?.toString() ?? "", weight: h.weight?.toString() ?? "" })) : [{ ticker: "" }]);
    if (p.holdings.length) setRiskPath("/portfolio/risk");
  }, [pf.data]);

  const save = async () => {
    setErr(null);
    setSaved(false);
    const n = (s?: string) => (s && s.trim() !== "" ? Number(s) : null);
    try {
      await api("/portfolio", { method: "POST", json: {
        name: form.name, cash: Number(form.cash || 0), risk_tolerance: form.risk_tolerance || null, horizon: form.horizon || null, benchmark: form.benchmark || "SPY",
        excluded_sectors: form.excluded_sectors.split(",").map((s: string) => s.trim()).filter(Boolean), max_position: form.max_position ? Number(form.max_position) / 100 : null,
        holdings: rows.filter((r) => r.ticker.trim()).map((r) => ({ ticker: r.ticker.trim().toUpperCase(), quantity: n(r.quantity), market_value: n(r.market_value), weight: n(r.weight) })) } });
      setSaved(true);
      setRiskPath(`/portfolio/risk?t=${Date.now()}`);
    } catch (e: any) { setErr(e.message); }
  };
  const set = (i: number, k: keyof H, v: string) => setRows((rs) => rs.map((r, j) => (j === i ? { ...r, [k]: v } : r)));
  const d = risk.data;
  const imp = d?.candidate_impact;
  return (
    <>
      <div className="page-head"><div><h1>Portfolio intelligence</h1><p>Holdings are stored only for your account and used to evaluate candidates as additions. Supply quantity, market value or weight per holding.</p></div></div>
      <div className="grid g2">
        <Card title="Holdings & constraints" actions={<button className="btn-primary" onClick={save}>Save</button>}>
          {err && <div className="mb"><ErrorBox error={err} /></div>}
          {saved && <div className="callout pos small mb">Saved.</div>}
          <div className="grid g3 mb">
            <label className="field">Cash ($)<input value={form.cash} onChange={(e) => setForm({ ...form, cash: e.target.value })} inputMode="decimal" /></label>
            <label className="field">Risk tolerance<select value={form.risk_tolerance} onChange={(e) => setForm({ ...form, risk_tolerance: e.target.value })}>
              <option value="">Not set</option><option value="conservative">Conservative</option><option value="moderate">Moderate</option><option value="aggressive">Aggressive</option></select></label>
            <label className="field">Horizon<input value={form.horizon} onChange={(e) => setForm({ ...form, horizon: e.target.value })} placeholder="e.g. 5 years" /></label>
            <label className="field">Benchmark<input value={form.benchmark} onChange={(e) => setForm({ ...form, benchmark: e.target.value.toUpperCase() })} /></label>
            <label className="field">Max position (%)<input value={form.max_position} onChange={(e) => setForm({ ...form, max_position: e.target.value })} inputMode="decimal" /></label>
            <label className="field">Excluded sectors<input value={form.excluded_sectors} onChange={(e) => setForm({ ...form, excluded_sectors: e.target.value })} placeholder="comma separated" /></label>
          </div>
          <table><thead><tr><th>Ticker</th><th>Quantity</th><th>Market value $</th><th>Weight %</th><th /></tr></thead>
            <tbody>{rows.map((r, i) => (
              <tr key={i}>
                <td><input value={r.ticker} onChange={(e) => set(i, "ticker", e.target.value.toUpperCase())} style={{ width: 80 }} aria-label="Ticker" /></td>
                <td><input value={r.quantity || ""} onChange={(e) => set(i, "quantity", e.target.value)} style={{ width: 90 }} inputMode="decimal" aria-label="Quantity" /></td>
                <td><input value={r.market_value || ""} onChange={(e) => set(i, "market_value", e.target.value)} style={{ width: 110 }} inputMode="decimal" aria-label="Market value" /></td>
                <td><input value={r.weight || ""} onChange={(e) => set(i, "weight", e.target.value)} style={{ width: 70 }} inputMode="decimal" aria-label="Weight" /></td>
                <td><button className="btn-sm btn-ghost" onClick={() => setRows(rows.filter((_, j) => j !== i))} aria-label="Remove row">✕</button></td>
              </tr>))}</tbody></table>
          <button className="btn-sm mt-s" onClick={() => setRows([...rows, { ticker: "" }])}>Add holding</button>
        </Card>
        <Card title="Candidate impact" sub="How would a stock change this portfolio? (illustrative weight by risk tolerance)">
          <div className="row mb">
            <input value={cand} onChange={(e) => setCand(e.target.value.toUpperCase())} placeholder="Candidate ticker" aria-label="Candidate ticker" />
            <button onClick={() => setRiskPath(`/portfolio/risk?candidate=${cand}`)} disabled={!cand}>Assess</button>
          </div>
          {risk.loading && riskPath && <Loading label="Calculating" />}
          {risk.error && <ErrorBox error={risk.error} />}
          {imp ? (
            <>
              <dl className="kv small">
                <dt>Illustrative weight</dt><dd>{pct(imp.candidate_weight, 0)} ({imp.funding})</dd>
                <dt>Volatility</dt><dd className="num">{pct(imp.vol_before)} → {pct(imp.vol_after)}</dd>
                <dt>Max drawdown (1y)</dt><dd className="num">{pct(imp.max_drawdown_before)} → {pct(imp.max_drawdown_after)}</dd>
                <dt>Effective positions</dt><dd className="num">{imp.effective_positions_before?.toFixed(1)} → {imp.effective_positions_after?.toFixed(1)}</dd>
                <dt>Correlation to portfolio</dt><dd className="num">{imp.candidate_corr_to_portfolio?.toFixed(2) ?? "—"}</dd>
                <dt>Sectors after</dt><dd>{Object.entries(imp.sector_after).map(([k, v]) => `${k} ${pct(v as number, 0)}`).join(", ")}</dd>
              </dl>
              {imp.constraint_breach && <div className="callout neg small mt-s">Constraint: {imp.constraint_breach}</div>}
              {imp.market_stress?.length > 0 && <ul className="clean small mt-s">{imp.market_stress.map((s: any) => <li key={s.market_move}>Market {pct(s.market_move, 0)}: portfolio ≈ {pct(s.portfolio_before)} → {pct(s.portfolio_after)} ({s.method})</li>)}</ul>}
              <p className="xs faint mt-s">For a full assessment, run an analysis with “Include portfolio context”.</p>
            </>
          ) : <p className="muted small">Enter a ticker to see diversification, volatility and concentration effects.</p>}
        </Card>
      </div>
      {d?.available && (
        <div className="grid g2 mt">
          <Card title="Weights" sub={`${d.weight_method}${d.total_value ? ` · total ${money(d.total_value)}` : ""} · cash ${pct(d.cash_weight)}`}>
            <WeightBars weights={d.weights} />
            {d.missing_prices.length > 0 && <div className="callout warn small mt-s">Missing prices: {d.missing_prices.map((m: any) => (typeof m === "string" ? m : m.ticker)).join(", ")} — excluded from weights.</div>}
          </Card>
          <Card title="Risk" sub={`Lookback ${d.lookback}`}>
            <dl className="kv small">
              <dt>Volatility (annualised)</dt><dd className="num">{pct(d.volatility)}</dd>
              <dt>Max drawdown (1y)</dt><dd className="num">{pct(d.max_drawdown_1y)}</dd>
              <dt>Beta vs {d.portfolio.benchmark}</dt><dd className="num">{d.beta?.toFixed(2) ?? "—"}</dd>
              <dt>Effective positions (1/HHI)</dt><dd className="num">{d.effective_positions?.toFixed(1)}</dd>
              <dt>Largest position</dt><dd>{d.largest_position ? `${d.largest_position[0]} ${pct(d.largest_position[1])}` : "—"}</dd>
            </dl>
            <h3 className="small muted mt">Sector concentration</h3>
            <WeightBars weights={d.sector_exposure} height={120} />
            <h3 className="small muted mt">Risk contribution</h3>
            <ul className="clean small">{Object.entries(d.risk_contributions).map(([t, v]) => <li key={t}>{t}: {pct(v as number)}</li>)}</ul>
            <ul className="clean xs faint mt-s">{d.limitations.map((l: string) => <li key={l}>{l}</li>)}</ul>
          </Card>
          {d.correlation.tickers.length > 1 && <Card title="Correlation matrix" sub={`Daily returns, ${d.correlation.lookback_sessions} sessions`}><CorrelationTable tickers={d.correlation.tickers} matrix={d.correlation.matrix} /></Card>}
        </div>
      )}
    </>
  );
}
