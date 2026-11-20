"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { api, EvidenceItem } from "@/lib/api";
import { AGENT_TITLES, byUnit, dt, money, pct } from "@/lib/format";
import { Badge, Card, Cites, EvidenceCard, ErrorBox, Loading, RecBadge, ScoreBar, Status, Verdict } from "./ui";
import { BarsChart, ChartFrame, LinesChart, mergeSeries, RefBars, ScenarioChart, CorrelationTable } from "./charts";
import PriceChart from "./PriceChart";
import { AskBox, BriefingPlayer } from "./Voice";

function Claims({ items }: { items?: any[] }) {
  if (!items?.length) return <p className="muted small">None recorded.</p>;
  return (
    <div>
      {items.map((c, i) => (
        <div key={i} className="claim small">
          {c.kind && <span className="kind">{c.kind}</span>}
          {c.text}
          <Cites ids={c.evidence_ids} />
        </div>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- summary */
export function SummaryView({ r, analysisId }: { r: any; analysisId: string }) {
  const f = r.final;
  return (
    <div className="grid g2">
      <Card title="Executive summary" sub={f.mode === "llm" ? "Written by the final-decision agent from the committee record" : "Generated from rule-based committee output"}>
        <p className="serif" style={{ fontSize: 15, lineHeight: 1.6 }}>{f.executive_summary}</p>
        {f.unverified_numbers?.length > 0 && <div className="callout warn small">Numbers not matched to evidence: {f.unverified_numbers.join(", ")}</div>}
        <div className="divider" />
        <h3 className="mb" style={{ marginBottom: 6 }}>Investment thesis</h3>
        <Claims items={f.thesis} />
      </Card>
      <div className="stack">
        <Card title="Scores" sub={f.scores.direction}>
          <div className="stack">
            <ScoreBar label="Business quality" score={f.scores.business_quality} />
            <ScoreBar label="Valuation (vs peers, growth, own history)" score={f.scores.valuation} />
            <ScoreBar label="Technical" score={f.scores.technical} />
            <ScoreBar label="News & sentiment" score={f.scores.news} />
            <ScoreBar label={`Risk (rating: ${f.scores.risk_rating})`} score={f.scores.risk} />
          </div>
          <p className="xs faint mt">Scores are diagnostic inputs under documented rubrics ({f.meta.versions.rubrics}); the decision applies explicit gates, not an average.</p>
        </Card>
        <Card title="Top risks">
          {f.risks?.length ? f.risks.map((x: any, i: number) => (
            <div key={i} className="claim small"><Badge tone={x.impact === "high" ? "neg" : x.impact === "medium" ? "warn" : "neu"}>impact {x.impact}</Badge>{" "}
              <span className="faint xs">likelihood {x.likelihood}</span> {x.text}<Cites ids={x.evidence_ids} /></div>
          )) : <p className="muted small">No rule-based risk flagged.</p>}
        </Card>
      </div>
      <Card title="Entry price & allocation">
        <dl className="kv">
          <dt>Suggested entry</dt><dd>{f.entry_price.text}</dd>
          <dt>Method</dt><dd className="small">{f.entry_price.methods?.length ? <ul className="clean">{f.entry_price.methods.map((m: string) => <li key={m}>{m}</li>)}</ul> : "—"}</dd>
          <dt>Allocation</dt><dd>{f.allocation.text}</dd>
          {f.allocation.method && (<><dt>Sizing</dt><dd className="small"><ul className="clean">{f.allocation.method.map((m: string) => <li key={m}>{m}</li>)}</ul></dd></>)}
        </dl>
        {f.entry_price.note && <p className="xs faint mt-s">{f.entry_price.note}</p>}
      </Card>
      <Card title="Monitoring plan">
        <h3 className="small muted">Review triggers</h3>
        <ul className="clean small">{f.monitoring_plan.triggers.map((t: string) => <li key={t}>{t}</li>)}</ul>
        <h3 className="small muted mt-s">Would invalidate the thesis</h3>
        <ul className="clean small">{(f.monitoring_plan.invalidation_conditions || []).map((t: string) => <li key={t}>{t}</li>)}</ul>
        <h3 className="small muted mt-s">Immediate reassessment</h3>
        <ul className="clean small">{f.monitoring_plan.immediate_reassessment.map((t: string) => <li key={t}>{t}</li>)}</ul>
      </Card>
      <Card title="Listen to briefing" sub="Spoken from the saved report">
        <BriefingPlayer analysisId={analysisId} />
      </Card>
      <Card title="Assumptions & limitations">
        <ul className="clean small">{f.meta.assumptions.map((a: string) => <li key={a}>{a}</li>)}</ul>
        <div className="divider" />
        <ul className="clean small muted">{f.meta.limitations.map((a: string) => <li key={a}>{a}</li>)}</ul>
        <p className="xs faint mt">Versions: {Object.entries(f.meta.versions).map(([k, v]) => `${k} ${v}`).join(" · ")} · analysis {f.meta.analysis_id}</p>
      </Card>
    </div>
  );
}

/* ---------------------------------------------------------------- committee */
function AgentRow({ rep, analysisId }: { rep: any; analysisId: string }) {
  const [open, setOpen] = useState(false);
  const s = rep.score?.score;
  return (
    <div className="agent-row">
      <div className="agent-head" onClick={() => setOpen(!open)} role="button" tabIndex={0} aria-expanded={open}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && setOpen(!open)}>
        <div>
          <div style={{ fontWeight: 600 }}>{rep.title}</div>
          <div className="xs faint">{rep.responsibility}</div>
        </div>
        <div className="hide-sm"><ScoreBar score={s} /><div className="xs faint mt-s">{s === null || s === undefined ? "no score" : `${s.toFixed(1)} / 10`}</div></div>
        <div className="hide-sm"><Badge tone={rep.confidence === "High" ? "pos" : rep.confidence === "Medium" ? "acc" : "warn"}>{rep.confidence} confidence</Badge></div>
        <div><Badge tone={rep.status === "completed" ? "pos" : "warn"}>{rep.status}</Badge> <span className="xs faint">{rep.mode.replace("_", " ")}</span></div>
        <span className="faint">{open ? "▾" : "▸"}</span>
      </div>
      {!open && <div className="small muted" style={{ padding: "0 16px 12px" }}>{rep.summary}</div>}
      {open && (
        <div className="agent-body fade-in">
          <p className="small">{rep.summary}</p>
          {rep.error && <div className="callout warn small">{rep.error}</div>}
          <div className="grid g2 mt-s">
            <div><h3 className="small muted">Strengths</h3><Claims items={rep.strengths} /></div>
            <div><h3 className="small muted">Weaknesses / objections</h3><Claims items={rep.weaknesses} /></div>
          </div>
          {rep.claims?.length > 0 && <div className="mt"><h3 className="small muted">Other findings</h3><Claims items={rep.claims} /></div>}
          {rep.missing_information?.length > 0 && (
            <div className="mt"><h3 className="small muted">Missing or limited information</h3>
              <ul className="clean small">{rep.missing_information.map((m: string) => <li key={m}>{m}</li>)}</ul></div>)}
          {rep.score?.explanation && <p className="xs faint mt">{rep.score.explanation}</p>}
          <p className="xs faint">Confidence: {rep.confidence_reason}</p>
          {rep.guardrails && (rep.guardrails.invalid_citations?.length > 0 || rep.guardrails.unverified_numbers?.length > 0) && (
            <div className="callout warn small mt-s">Guardrails: {rep.guardrails.invalid_citations.length} invalid citation(s) removed
              {rep.guardrails.retried ? " after one corrective retry" : ""}; unverified numbers: {rep.guardrails.unverified_numbers.join(", ") || "none"}.</div>)}
          {rep.agent === "devils_advocate" && rep.deterministic?.thesis_failure && (
            <div className="callout small mt-s"><strong>Thesis failure:</strong> {rep.deterministic.thesis_failure.definition}{" "}
              {rep.deterministic.thesis_failure.low_pct !== undefined && rep.deterministic.thesis_failure.low_pct !== null
                ? `Estimated ${rep.deterministic.thesis_failure.low_pct}–${rep.deterministic.thesis_failure.high_pct}% (${rep.deterministic.thesis_failure.label || rep.deterministic.thesis_failure.basis}).`
                : rep.deterministic.thesis_failure.basis}</div>)}
          {rep.model_run && <p className="xs faint">Model {rep.model_run.model} · prompt {rep.model_run.prompt_version} · {rep.model_run.input_tokens}→{rep.model_run.output_tokens} tokens</p>}
          <div className="mt"><BriefingPlayer analysisId={analysisId} agent={rep.agent} /></div>
        </div>
      )}
    </div>
  );
}

export function CommitteeView({ r, analysisId }: { r: any; analysisId: string }) {
  const c = r.committee;
  const order = ["fundamental", "technical", "news", "risk_portfolio", "devils_advocate"];
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div>{order.filter((a) => r.reports[a]).map((a) => <AgentRow key={a} rep={r.reports[a]} analysisId={analysisId} />)}</div>
      <div className="grid g2">
        <Card title="Areas of agreement"><Claims items={c.agreements} /></Card>
        <Card title="Disagreements" sub="Traced to evidence; nature distinguishes analytical, time-horizon and data disagreements">
          {c.disagreements.length ? c.disagreements.map((d: any, i: number) => (
            <div key={i} className="claim small">
              <div className="row between"><strong>{d.topic}</strong><span className="row"><Badge>{d.nature.replace("_", " ")}</Badge><Badge tone={d.resolved ? "pos" : "warn"}>{d.resolved ? "resolved" : "unresolved"}</Badge></span></div>
              <p className="mt-s">{d.description}<Cites ids={d.evidence_ids} /></p>
              <p className="muted">Resolution: {d.resolution}</p>
            </div>)) : <p className="muted small">No material disagreement detected under the committee rules.</p>}
        </Card>
        <Card title="Review record" sub={`Bounded to ${r.committee_history.length} committee round(s)`}>
          {r.review_log.length ? r.review_log.map((q: any, i: number) => (
            <div key={i} className="claim small">
              <div><span className="kind">Round {q.round} · to {AGENT_TITLES[q.target_agent]}</span></div>
              <div><strong>Q:</strong> {q.question}</div>
              <div><strong>A:</strong> {q.response.answer}<Cites ids={q.response.evidence_ids} /> <Badge tone={q.response.resolved ? "pos" : "warn"}>{q.response.resolved ? "resolved" : "unresolved"}</Badge>
                <span className="xs faint"> ({q.response.mode})</span></div>
              {q.response.cannot_resolve_reason && <div className="muted">Could not resolve: {q.response.cannot_resolve_reason}</div>}
            </div>)) : <p className="muted small">The committee did not need to ask any agent a clarifying question.</p>}
        </Card>
        <Card title="Decision gates & rationale">
          <p className="small">{c.rationale}</p>
          {c.overridden && <div className="callout warn small">Committee chose {c.recommendation} over policy {c.policy_recommendation}: {c.override_reason}</div>}
          <h3 className="small muted mt-s">Gates in force</h3>
          {c.gates.length ? <ul className="clean small">{c.gates.map((g: any) => <li key={g.id}><Badge tone="warn">blocks {g.blocks.join(", ")}</Badge> {g.reason}</li>)}</ul> : <p className="small muted">None.</p>}
          <h3 className="small muted mt-s">Why not the alternatives</h3>
          <ul className="clean small">{c.why_not_alternatives.map((w: string) => <li key={w}>{w}</li>)}</ul>
          {c.unresolved_questions?.length > 0 && (<><h3 className="small muted mt-s">Unresolved</h3><ul className="clean small">{c.unresolved_questions.map((w: string) => <li key={w}>{w}</li>)}</ul></>)}
          <h3 className="small muted mt-s">Consistency checks</h3>
          <ul className="clean small">{c.consistency_checks.map((k: any) => <li key={k.check}>{k.ok ? "✓" : "✗"} {k.check}: {k.result}</li>)}</ul>
        </Card>
      </div>
    </div>
  );
}

/** Opens the Ask tab with a pre-filled, metric-specific question (grounded in the saved analysis). */
export function askAbout(q: string) {
  window.dispatchEvent(new CustomEvent("ii-ask", { detail: q }));
}

/* ---------------------------------------------------------------- fundamentals */
const GROUPS: [string, string[]][] = [
  ["Growth", ["revenue_yoy_annual", "revenue_yoy_quarterly", "revenue_cagr_3y", "revenue_cagr_5y", "diluted_eps_yoy_annual", "diluted_eps_yoy_quarterly", "diluted_eps_cagr_3y", "free_cash_flow_yoy_annual"]],
  ["Profitability & returns", ["gross_margin_ttm", "operating_margin_ttm", "net_margin_ttm", "roe_ttm", "roic_annual", "roa_ttm"]],
  ["Financial health", ["debt_to_equity", "net_debt_to_ebitda", "interest_coverage", "current_ratio", "quick_ratio", "altman_z"]],
  ["Cash flow & quality", ["fcf_margin_ttm", "fcf_conversion_annual", "piotroski_f_score", "accruals_ratio_annual", "share_change_1y"]],
  ["Valuation", ["trailing_pe", "forward_pe", "peg_forward", "peg_trailing", "ev_to_ebitda", "ev_to_sales", "price_to_sales", "price_to_book", "price_to_fcf", "fcf_yield", "pe_vs_own_history"]],
];

function AssessmentTable({ items }: { items: any[] }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Metric</th><th className="r">Value</th><th>Period</th><th className="r">Peer median</th><th className="r">Percentile</th><th>Verdict</th></tr></thead>
        <tbody>
          {items.map((a) => (
            <>
              <tr key={a.metric_key} onClick={() => setOpen(open === a.metric_key ? null : a.metric_key)} style={{ cursor: "pointer" }}>
                <td>{a.label}<Cites ids={[`asm.${a.metric_key}`, a.metric_id]} /></td>
                <td className="r num">{a.display}</td>
                <td className="small muted">{a.period || "—"}</td>
                <td className="r num">{a.peer ? byUnit(a.peer.median, a.unit) : "—"}</td>
                <td className="r num">{a.peer?.percentile !== undefined && a.peer?.percentile !== null ? pct(a.peer.percentile, 0) : "—"}</td>
                <td><Verdict v={a.verdict} /></td>
              </tr>
              {open === a.metric_key && (
                <tr key={a.metric_key + "-x"}><td colSpan={6} className="small" style={{ background: "var(--surface-2)" }}>
                  <p>{a.explanation}</p>
                  {a.peer && <p className="xs faint">Peer group: {a.peer.group} ({a.peer.level.replace("_", "-")}, n={a.peer.n}); basis: {a.peer.basis}; IQR {byUnit(a.peer.p25, a.unit)}–{byUnit(a.peer.p75, a.unit)}.</p>}
                  {a.absolute && <p className="xs faint">Guardrail: {a.absolute.rule} — {a.absolute.source}</p>}
                  <button className="btn-sm mt-s" onClick={(e) => { e.stopPropagation(); askAbout(`Explain the ${a.label} assessment (${a.display}): why is it ${a.verdict.replace("_", " ")}, and what would change it?`); }}>Ask about this metric</button>
                </td></tr>
              )}
            </>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function FundamentalsView({ r }: { r: any }) {
  const [period, setPeriod] = useState<"annual" | "quarterly">("annual");
  const fin = r.charts.financials?.[period] || {};
  const by = Object.fromEntries(r.assessments.map((a: any) => [a.metric_key, a]));
  const rev = mergeSeries({ revenue: fin.revenue }, 1);
  const eps = mergeSeries({ eps: fin.diluted_eps });
  const margins = useMemo(() => {
    const rows = mergeSeries({ rev: fin.revenue, gp: fin.gross_profit, oi: fin.operating_income, ni: fin.net_income });
    return rows.map((x) => ({ period: x.period, end: x.end,
      gross: x.rev && x.gp !== undefined && x.gp !== null ? x.gp / x.rev : null,
      operating: x.rev && x.oi !== undefined && x.oi !== null ? x.oi / x.rev : null,
      net: x.rev && x.ni !== undefined && x.ni !== null ? x.ni / x.rev : null }));
  }, [fin]);
  const cash = mergeSeries({ ocf: fin.operating_cash_flow, fcf: fin.free_cash_flow });
  const debt = mergeSeries({ debt: fin.total_debt, cash: fin.cash_and_st_investments || fin.cash });
  const peHist = (r.charts.valuation_history || []).map((m: any) => ({ label: m.period, value: m.value }));
  const currentPe = by.trailing_pe?.value;
  const src = `Sources: Yahoo Finance via yfinance, cross-checked against SEC EDGAR XBRL; periods per company fiscal calendar. Data through ${r.final.meta.financials_through}.`;
  const cols = (keys: [string, string, (v: any) => string][]) => [{ key: "period", label: "Period" }, ...keys.map(([key, label, fmt]) => ({ key, label, fmt }))];
  const latest = rev.length ? rev[rev.length - 1] : null;
  return (
    <div className="stack" style={{ gap: 16 }}>
      <Card title="Business profile" sub={`Peer group: ${r.peer.level.replace("_", "-")} '${r.peer.name}' · ${r.peer.n_with_data} peers with data`}>
        <div className="row"><Badge tone="acc">{r.profile.primary}</Badge>{r.profile.flags.map((f: string) => <Badge key={f}>{f.replace("_", " ")}</Badge>)}</div>
        <ul className="clean small mt-s">{r.profile.rationale.map((x: string) => <li key={x}>{x}</li>)}</ul>
        <p className="xs faint">{r.peer.fallback_rule} Thresholds {r.final.meta.versions.thresholds}.</p>
      </Card>
      <div className="row between">
        <h2>Financial trends</h2>
        <div className="seg" role="group" aria-label="Period">
          <button className={period === "annual" ? "on" : ""} onClick={() => setPeriod("annual")}>Annual</button>
          <button className={period === "quarterly" ? "on" : ""} onClick={() => setPeriod("quarterly")}>Quarterly</button>
        </div>
      </div>
      <div className="grid g2">
        <ChartFrame title="Revenue" onAsk={() => askAbout("What does the revenue trend show, and is it improving or deteriorating?")} rows={rev} columns={cols([["revenue", "Revenue", money]])} source={src}
          note={latest && by.revenue_yoy_annual ? `Latest annual growth ${by.revenue_yoy_annual.display}; 3-year CAGR ${by.revenue_cagr_3y?.display ?? "n/a"}. Missing periods appear as gaps.` : undefined}>
          <BarsChart data={rev} keys={[{ key: "revenue", label: "Revenue" }]} />
        </ChartFrame>
        <ChartFrame title="Diluted EPS" onAsk={() => askAbout("What does the diluted eps trend show, and is it improving or deteriorating?")} rows={eps} columns={cols([["eps", "EPS", (v) => `$${v.toFixed(2)}`]])} source={src}
          note={by.diluted_eps_cagr_3y ? `3-year diluted EPS CAGR ${by.diluted_eps_cagr_3y.display}.` : undefined}>
          <BarsChart data={eps} keys={[{ key: "eps", label: "Diluted EPS" }]} unit="USD/share" />
        </ChartFrame>
        <ChartFrame title="Margins" onAsk={() => askAbout("What does the margins trend show, and is it improving or deteriorating?")} rows={margins} columns={cols([["gross", "Gross", (v) => pct(v)], ["operating", "Operating", (v) => pct(v)], ["net", "Net", (v) => pct(v)]])} source={src}
          note={by.operating_margin_ttm ? `TTM operating margin ${by.operating_margin_ttm.display} (${by.operating_margin_ttm.verdict} vs peers).` : undefined}>
          <LinesChart data={margins} keys={[{ key: "gross", label: "Gross margin" }, { key: "operating", label: "Operating margin" }, { key: "net", label: "Net margin" }]} />
        </ChartFrame>
        <ChartFrame title="Operating & free cash flow" onAsk={() => askAbout("What does the operating & free cash flow trend show, and is it improving or deteriorating?")} rows={cash} columns={cols([["ocf", "Operating CF", money], ["fcf", "Free CF", money]])} source={src}
          note="Free cash flow = operating cash flow − capital expenditure (calculated).">
          <BarsChart data={cash} keys={[{ key: "ocf", label: "Operating cash flow" }, { key: "fcf", label: "Free cash flow" }]} />
        </ChartFrame>
        <ChartFrame title="Debt and cash" onAsk={() => askAbout("What does the debt and cash trend show, and is it improving or deteriorating?")} rows={debt} columns={cols([["debt", "Total debt", money], ["cash", "Cash & ST inv.", money]])} source={src}
          note={by.net_debt_to_ebitda ? `Net debt / EBITDA ${by.net_debt_to_ebitda.display}; interest coverage ${by.interest_coverage?.display ?? "n/a"}.` : undefined}>
          <BarsChart data={debt} keys={[{ key: "debt", label: "Total debt" }, { key: "cash", label: "Cash & short-term investments" }]} />
        </ChartFrame>
        {peHist.length > 0 && (
          <ChartFrame title="P/E at fiscal year ends" rows={peHist.map((p: any) => ({ period: p.label, pe: p.value }))} columns={cols([["pe", "P/E", (v) => `${v.toFixed(1)}x`]])}
            source="Close on/before each fiscal year end ÷ that year's diluted EPS (ignores filing lag)."
            note={by.pe_vs_own_history ? by.pe_vs_own_history.explanation : undefined}>
            <RefBars data={peHist} refValue={currentPe} refLabel={currentPe ? `Current ${currentPe.toFixed(1)}x` : undefined} />
          </ChartFrame>
        )}
      </div>
      {GROUPS.map(([g, keys]) => {
        const items = keys.map((k) => by[k]).filter(Boolean);
        if (!items.length) return null;
        return <Card key={g} title={g} sub="Click a row for the threshold explanation; chips open the evidence"><AssessmentTable items={items} /></Card>;
      })}
      <AllMetrics analysisId={r.final.meta.analysis_id} />
    </div>
  );
}

function AllMetrics({ analysisId }: { analysisId: string }) {
  const [rows, setRows] = useState<any[] | null>(null);
  const [q, setQ] = useState("");
  const [show, setShow] = useState(false);
  useEffect(() => {
    if (show && !rows) api(`/analysis/${analysisId}/metrics`).then((d: any) => setRows(d.metrics));
  }, [show, rows, analysisId]);
  return (
    <Card title="All calculated metrics" sub="Every value with formula, period and status" actions={<button className="btn-sm" onClick={() => setShow(!show)}>{show ? "Hide" : "Show"}</button>}>
      {show && !rows && <Loading />}
      {show && rows && (
        <>
          <input placeholder="Filter metrics" value={q} onChange={(e) => setQ(e.target.value)} className="mb" aria-label="Filter metrics" />
          <div className="table-wrap" style={{ maxHeight: 480, overflowY: "auto" }}>
            <table>
              <thead><tr><th>Metric</th><th>Period</th><th className="r">Value</th><th>Formula</th><th>Status</th></tr></thead>
              <tbody>{rows.filter((m) => !q || (m.label + m.key).toLowerCase().includes(q.toLowerCase())).map((m) => (
                <tr key={m.id}><td>{m.label}<Cites ids={[m.id]} /></td><td className="small muted">{m.period}</td>
                  <td className="r num">{m.status === "ok" ? byUnit(m.value, m.unit) : "—"}</td><td className="xs muted">{m.formula}</td>
                  <td className="xs">{m.status === "ok" ? "ok" : `${m.status.replace("_", " ")}: ${m.reason}`}</td></tr>))}</tbody>
            </table>
          </div>
        </>
      )}
    </Card>
  );
}

/* ---------------------------------------------------------------- technical */
export function TechnicalView({ r }: { r: any }) {
  const t = r.technicals;
  const rep = r.reports.technical;
  const p = r.charts.price;
  if (!t) return <Card><p className="muted">Price history unavailable; technical analysis was not possible.</p></Card>;
  const rel = (p.dates || []).map((d: string, i: number) => ({ date: d, stock: p.rel_stock?.[i], bench: p.rel_bench?.[i] })).filter((_: any, i: number) => i % 3 === 0);
  const rsi = (p.dates || []).map((d: string, i: number) => ({ date: d, rsi: p.rsi[i] })).slice(-252);
  const ind = rep.deterministic.indicators;
  const ez = rep.deterministic.entry_zone;
  return (
    <div className="stack" style={{ gap: 16 }}>
      <Card title="Price, moving averages and volume"><PriceChart series={p} dataThrough={t.data_through} /></Card>
      <div className="grid g2">
        <ChartFrame title={`Relative performance vs ${r.params.benchmark} (indexed to 100)`} rows={rel.slice(-60)} columns={[{ key: "date", label: "Date" }, { key: "stock", label: r.identity.ticker, fmt: (v) => v.toFixed(1) }, { key: "bench", label: r.params.benchmark, fmt: (v) => v.toFixed(1) }]}
          note={`6-month return vs benchmark: ${pct(t.rs_benchmark?.["6m"])}; 12-month: ${pct(t.rs_benchmark?.["12m"])}. Adjusted closes.`}>
          <LinesChart data={rel} xKey="date" unit="" keys={[{ key: "stock", label: r.identity.ticker }, { key: "bench", label: r.params.benchmark }]} ref0={100} />
        </ChartFrame>
        <ChartFrame title="RSI (14, Wilder)" rows={rsi.slice(-30)} columns={[{ key: "date", label: "Date" }, { key: "rsi", label: "RSI", fmt: (v) => v.toFixed(1) }]}
          note="Above 70 is often read as overbought and below 30 as oversold; one indicator alone is not a signal.">
          <LinesChart data={rsi} xKey="date" unit="" keys={[{ key: "rsi", label: "RSI" }]} ref0={50} />
        </ChartFrame>
      </div>
      <div className="grid g2">
        <Card title="Indicators" sub={`Calculated on data through ${t.data_through}`}>
          <dl className="kv small">
            {[["Close", `$${ind.price.toFixed(2)}`], ["20 / 50 / 200-day", [ind.sma_20, ind.sma_50, ind.sma_200].map((x: number) => (x ? `$${x.toFixed(2)}` : "—")).join(" / ")],
              ["Distance from 200-day", pct(ind.dist_sma_200)], ["RSI (14)", ind.rsi_14?.toFixed(1)], ["MACD (12/26/9) histogram", ind.macd_hist?.toFixed(3)],
              ["ATR (14)", `${ind.atr_14?.toFixed(2)} (${pct(ind.atr_pct)})`], ["Volatility 20d / 1y", `${pct(ind.vol_20d)} / ${pct(ind.vol_1y)}`],
              ["52-week range", `$${ind.low_52w?.toFixed(2)} – $${ind.high_52w?.toFixed(2)}`], ["From 52-week high", pct(ind.drawdown_from_52w_high)],
              ["Volume 20d vs 50d", pct(ind.volume_trend)], ["Breakout", ind.breakout], ["Trend short / medium / long", `${t.trend_short} / ${t.trend_medium} / ${t.trend_long}`]]
              .map(([k, v]) => (<><dt key={k + "k"}>{k}</dt><dd key={k + "v"} className="num">{v ?? "—"}</dd></>))}
          </dl>
        </Card>
        <Card title="Support / resistance & entry" sub={rep.deterministic.levels_method}>
          <div className="table-wrap"><table><thead><tr><th>Zone</th><th className="r">Range</th><th className="r">Touches</th><th className="r">Distance</th></tr></thead>
            <tbody>{(t.levels || []).map((z: any, i: number) => (
              <tr key={i}><td><Badge tone={z.type === "support" ? "pos" : "warn"}>{z.type}</Badge></td><td className="r num">${z.low.toFixed(2)}–${z.high.toFixed(2)}</td>
                <td className="r num">{z.touches}</td><td className="r num">{z.distance_pct.toFixed(1)}%</td></tr>))}</tbody></table></div>
          <div className="callout small mt">{ez.justified ? `Potential entry zone $${ez.low}–$${ez.high} (${ez.method}).` : `No entry zone justified: ${ez.reason}.`}</div>
          {rep.deterministic.invalidation && <p className="small mt-s">Invalidation: {rep.deterministic.invalidation}</p>}
          <p className="xs faint">Zones are estimates from past prices, not guaranteed levels; gaps can move through them.</p>
        </Card>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- news */
export function NewsView({ r }: { r: any }) {
  const rep = r.reports.news;
  const tb = rep.deterministic;
  const cls = Object.fromEntries((tb.classifications || []).map((c: any) => [c.evidence_id, c]));
  return (
    <div className="stack" style={{ gap: 16 }}>
      <Card title="Assessment" sub={rep.mode === "llm" ? "Classified by the News & Sentiment agent" : "Sentiment not classified (no LLM configured)"}>
        <p className="small">{rep.summary}</p>
        <div className="grid g2 mt-s">
          <div><h3 className="small muted">Positive catalysts</h3><Claims items={rep.strengths} /></div>
          <div><h3 className="small muted">Negative catalysts</h3><Claims items={rep.weaknesses} /></div>
        </div>
        {rep.missing_information?.length > 0 && <ul className="clean small muted mt-s">{rep.missing_information.map((m: string) => <li key={m}>{m}</li>)}</ul>}
      </Card>
      <Card title="News items" sub="Deduplicated; publication date shown; headlines are untrusted source text">
        <div className="table-wrap"><table><thead><tr><th>Published</th><th>Headline</th><th>Publisher</th><th>Sentiment</th><th>Materiality</th></tr></thead>
          <tbody>{tb.news.map((n: any) => (
            <tr key={n.id}><td className="small num">{dt(n.date)}</td><td>{n.url ? <a href={n.url} target="_blank" rel="noreferrer noopener">{n.headline}</a> : n.headline}<Cites ids={[n.id]} /></td>
              <td className="small muted">{n.publisher}</td><td>{cls[n.id] ? <Badge tone={cls[n.id].sentiment === "positive" ? "pos" : cls[n.id].sentiment === "negative" ? "neg" : "neu"}>{cls[n.id].sentiment}</Badge> : <span className="xs faint">not assessed</span>}</td>
              <td className="small">{cls[n.id]?.materiality ?? "—"}</td></tr>))}</tbody></table></div>
        {!tb.news.length && <p className="muted small">The news feed returned no items.</p>}
      </Card>
      <Card title="SEC filings" sub="Primary source; 8-K items mapped to documented materiality">
        <div className="table-wrap"><table><thead><tr><th>Filed</th><th>Form</th><th>Items</th><th>Materiality</th></tr></thead>
          <tbody>{tb.filings.map((f: any) => (
            <tr key={f.id}><td className="small num">{dt(f.date)}</td><td><a href={f.url} target="_blank" rel="noreferrer noopener">{f.form}</a></td>
              <td className="small">{(f.items || []).map((i: any) => `${i.item} ${i.description}`).join("; ") || "—"}</td>
              <td><Badge tone={f.materiality === "high" ? "neg" : f.materiality === "medium" ? "warn" : "neu"}>{f.materiality}</Badge></td></tr>))}</tbody></table></div>
      </Card>
      <div className="grid g2">
        <Card title="Analyst actions" sub="Third-party opinions (vendor data)">
          {tb.analyst_actions.length ? <ul className="clean small">{tb.analyst_actions.map((a: any) => <li key={a.id}>{dt(a.date)} — {a.title}</li>)}</ul> : <p className="muted small">None reported.</p>}
        </Card>
        <Card title="Upcoming events" sub="Vendor calendar; confirm with the company">
          {tb.upcoming.length ? <ul className="clean small">{tb.upcoming.map((u: any) => <li key={u.id}>{u.title}: {dt(u.date)}</li>)}</ul> : <p className="muted small">No verified upcoming dates.</p>}
        </Card>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- risk & portfolio */
export function RiskView({ r }: { r: any }) {
  const rep = r.reports.risk_portfolio;
  const d = rep.deterministic;
  const m = d.metrics || {};
  const pf = r.portfolio;
  const scen = r.scenarios;
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="grid g4">
        {[["Risk rating", d.risk_rating], ["1y volatility", pct(m.vol_1y)], ["3y max drawdown", pct(m.max_drawdown_3y)], ["2y beta", m.beta_2y?.toFixed(2)]].map(([k, v]) => (
          <div key={k} className="card stat"><span className="label">{k}</span><span className="value">{v ?? "—"}</span></div>))}
      </div>
      <div className="grid g2">
        <Card title="Standalone risk" sub={d.beta_method}>
          <dl className="kv small">
            <dt>Downside / upside capture</dt><dd className="num">{m.downside_capture?.toFixed(2) ?? "—"} / {m.upside_capture?.toFixed(2) ?? "—"}</dd>
            <dt>1-day 95% VaR / CVaR</dt><dd className="num">{pct(m.var_95_1d)} / {pct(m.cvar_95_1d)}</dd>
            <dt>Avg daily $ volume (20d)</dt><dd className="num">{money(m.avg_dollar_volume_20d)}</dd>
            <dt>Gaps &gt;5% (2y)</dt><dd className="num">{d.gaps?.gaps_over_threshold} (largest {pct(d.gaps?.largest_gap)} on {d.gaps?.largest_gap_date})</dd>
            <dt>Correlation with benchmark</dt><dd className="num">{m.correlation_1y?.toFixed(2) ?? "—"}</dd>
          </dl>
          <div className="divider" />
          <h3 className="small muted">Main risks</h3><Claims items={rep.weaknesses} />
          <p className="small mt-s">{d.stop_loss_considerations}</p>
        </Card>
        <Card title="Bull / base / bear scenarios" sub={scen?.available ? `${scen.method} · ${scen.horizon}` : undefined}>
          {scen?.available ? (
            <>
              <ScenarioChart scenarios={scen.scenarios} price={r.final.meta.reference_price} />
              <p className="xs faint">EPS basis: {scen.eps_basis}. Multiple basis: {scen.multiple_basis ?? "EV/Sales"}. Illustrations under stated assumptions, not forecasts.</p>
            </>
          ) : <p className="muted small">Scenarios unavailable: {scen?.reason}</p>}
          {d.market_stress?.length > 0 && (
            <><div className="divider" /><h3 className="small muted">Market stress (beta-based)</h3>
              <ul className="clean small">{d.market_stress.map((s: any) => <li key={s.market_move}>Market {pct(s.market_move, 0)} → stock ≈ {pct(s.estimated_move)}</li>)}</ul></>)}
        </Card>
      </div>
      <Card title="Portfolio impact" sub={pf?.available ? pf.illustrative_weight_note : undefined}>
        {pf?.available ? (
          <div className="grid g2">
            <dl className="kv small">
              <dt>Volatility</dt><dd className="num">{pct(pf.impact.vol_before)} → {pct(pf.impact.vol_after)}</dd>
              <dt>Max drawdown (1y)</dt><dd className="num">{pct(pf.impact.max_drawdown_before)} → {pct(pf.impact.max_drawdown_after)}</dd>
              <dt>Effective positions</dt><dd className="num">{pf.impact.effective_positions_before?.toFixed(1)} → {pf.impact.effective_positions_after?.toFixed(1)}</dd>
              <dt>Correlation to portfolio</dt><dd className="num">{pf.impact.candidate_corr_to_portfolio?.toFixed(2) ?? "—"}</dd>
              <dt>Sector after</dt><dd>{Object.entries(pf.impact.sector_after || {}).map(([k, v]) => `${k} ${pct(v as number, 0)}`).join(", ")}</dd>
              <dt>Allocation</dt><dd>{r.final.allocation.text}</dd>
            </dl>
            {pf.correlation?.tickers?.length > 1 && <CorrelationTable tickers={pf.correlation.tickers} matrix={pf.correlation.matrix} />}
          </div>
        ) : <p className="muted small">{pf?.reason || "No portfolio supplied."} <Link href="/portfolio">Add holdings</Link> and re-run with portfolio context.</p>}
      </Card>
    </div>
  );
}

/* ---------------------------------------------------------------- evidence & data quality */
export function EvidenceView({ analysisId }: { analysisId: string }) {
  const [cat, setCat] = useState("");
  const [q, setQ] = useState("");
  const [items, setItems] = useState<EvidenceItem[] | null>(null);
  const [sel, setSel] = useState<EvidenceItem | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    setItems(null);
    api<{ items: EvidenceItem[] }>(`/analysis/${analysisId}/evidence?limit=2000${cat ? `&category=${cat}` : ""}`).then((d) => setItems(d.items)).catch((e) => setErr(e.message));
  }, [analysisId, cat]);
  const shown = (items || []).filter((i) => !q || `${i.id} ${i.title}`.toLowerCase().includes(q.toLowerCase())).slice(0, 400);
  return (
    <div className="grid g2">
      <Card title="Evidence pack" sub={items ? `${items.length} items` : undefined}>
        <div className="row mb">
          <select value={cat} onChange={(e) => setCat(e.target.value)} aria-label="Category">
            <option value="">All categories</option>
            {["financial", "metric", "valuation", "assessment", "price", "technical", "risk", "news", "filing", "estimate", "analyst_action", "peer", "governance", "calendar", "earnings_history", "scenario", "portfolio", "data_quality", "identity"].map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <input placeholder="Search id or title" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search evidence" />
        </div>
        {err && <ErrorBox error={err} />}
        {!items && !err && <Loading />}
        <div className="table-wrap" style={{ maxHeight: 560, overflowY: "auto" }}>
          <table><tbody>{shown.map((i) => (
            <tr key={i.id} onClick={() => setSel(i)} style={{ cursor: "pointer" }}>
              <td className="mono xs faint">{i.id}</td><td className="small">{i.title}</td><td className="r num small">{i.display?.slice(0, 40)}</td>
              <td>{i.quality !== "ok" && <Badge tone={i.quality === "conflict" ? "neg" : "warn"}>{i.quality}</Badge>}</td></tr>))}</tbody></table>
        </div>
      </Card>
      <div>{sel ? <EvidenceCard item={sel} /> : <Card><p className="muted small">Select an item to see its full provenance: value, period, filing date, source link, retrieval time, formula and inputs.</p></Card>}</div>
    </div>
  );
}

export function DataQualityView({ r }: { r: any }) {
  const q = r.data_quality;
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="grid g4">
        <div className="card stat"><span className="label">Status</span><span className="value"><Status s={q.status} /></span></div>
        <div className="card stat"><span className="label">Coverage</span><span className="value">{q.coverage_pct}%</span></div>
        <div className="card stat"><span className="label">Latest financial period</span><span className="value small" style={{ fontSize: 14 }}>{q.latest_financial_period || "—"}</span></div>
        <div className="card stat"><span className="label">Latest price</span><span className="value" style={{ fontSize: 14 }}>{dt(q.latest_price_date)}</span></div>
      </div>
      <div className="grid g2">
        <Card title="Vendor vs SEC cross-check" sub="Rule: ≤1% match, ≤5% minor (vendor kept), >5% conflict (SEC filing used)">
          <div className="row">{Object.entries(q.cross_check_summary).map(([k, v]) => <Badge key={k} tone={k === "conflict" ? "neg" : k === "match" ? "pos" : "neu"}>{k}: {String(v)}</Badge>)}</div>
          {q.conflicts.length > 0 && (
            <div className="table-wrap mt"><table><thead><tr><th>Field</th><th>Period</th><th className="r">Used (SEC)</th><th className="r">Vendor</th><th className="r">Diff</th></tr></thead>
              <tbody>{q.conflicts.map((c: any) => <tr key={c.id}><td>{c.field}<Cites ids={[c.id]} /></td><td className="small">{c.period}</td>
                <td className="r num">{Number(c.used_value).toLocaleString()}</td><td className="r num">{Number(c.other_value).toLocaleString()}</td><td className="r num">{pct(c.difference_pct)}</td></tr>)}</tbody></table></div>)}
        </Card>
        <Card title="Issues">
          {q.issues.length ? q.issues.map((i: any, k: number) => (
            <div key={k} className="claim small"><Badge tone={i.severity === "critical" ? "neg" : i.severity === "warning" ? "warn" : "neu"}>{i.severity}</Badge> {i.message}</div>
          )) : <p className="muted small">No issues.</p>}
          {q.missing_critical_fields.length > 0 && <div className="callout neg small mt">Missing critical fields: {q.missing_critical_fields.join(", ")}</div>}
          {q.stale_sources.length > 0 && <div className="callout warn small mt">Stale: {q.stale_sources.join(", ")}</div>}
        </Card>
      </div>
      <Card title="Source retrievals" sub="Every fetch attempt is recorded; a failed fetch is never shown as zero">
        <div className="table-wrap"><table><thead><tr><th>Source</th><th>Item</th><th>Status</th><th>Retrieved</th><th>Detail</th></tr></thead>
          <tbody>{r.fetches.map((f: any, i: number) => <tr key={i}><td className="small">{f.source}</td><td className="small">{f.item}</td>
            <td><Badge tone={f.status === "ok" ? "pos" : f.status === "failed" ? "neg" : "warn"}>{f.status}</Badge></td><td className="small num">{dt(f.retrieved_at, true)}</td>
            <td className="xs muted">{f.error || f.detail || ""}</td></tr>)}</tbody></table></div>
      </Card>
    </div>
  );
}

/* ---------------------------------------------------------------- thesis & ask */
export function ThesisView({ r, ticker }: { r: any; ticker: string }) {
  return (
    <Card title="Thesis assumptions at decision time" sub="Monitored automatically; see the Thesis Monitor for current status" actions={<Link href={`/thesis/${ticker}`}>Open monitor →</Link>}>
      <div className="table-wrap"><table><thead><tr><th>Assumption</th><th>Kind</th><th className="r">Original</th><th>Review / at-risk</th><th>Materiality</th></tr></thead>
        <tbody>{r.thesis.map((a: any) => (
          <tr key={a.key}><td>{a.description}<Cites ids={a.evidence_ids} /></td><td className="small">{a.kind}</td>
            <td className="r num">{a.original_value !== null ? byUnit(a.original_value, a.unit) : "—"}</td>
            <td className="small num">{a.threshold !== null ? `${a.comparator} ${byUnit(a.review_threshold, a.unit)} / ${byUnit(a.threshold, a.unit)}` : "manual review"}</td>
            <td><Badge tone={a.materiality === "high" ? "warn" : "neu"}>{a.materiality}</Badge></td></tr>))}</tbody></table></div>
    </Card>
  );
}

export function AskView({ analysisId, initial }: { analysisId: string; initial?: string | null }) {
  const [msgs, setMsgs] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api(`/analysis/${analysisId}/messages`).then((d: any) => setMsgs(d.messages)).catch(() => {});
  }, [analysisId]);
  useEffect(() => {
    if (initial) ask(initial, "text");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initial]);
  const ask = async (q: string, mode: "text" | "voice") => {
    setBusy(true);
    setErr(null);
    setMsgs((m) => [...m, { role: "user", content: q }]);
    try {
      const a: any = await api(`/analysis/${analysisId}/ask`, { method: "POST", json: { question: q, input_mode: mode } });
      setMsgs((m) => [...m, { role: "assistant", content: a.answer, citations: a.citations, meta: a }]);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };
  const examples = ["Why did the committee choose this recommendation?", "What is the strongest reason not to buy?", "Which metric has deteriorated the most?",
    "What changed since the last analysis?", "What would need to happen for the recommendation to change?", "Explain the valuation in simple terms."];
  return (
    <Card title="Ask about this analysis" sub="Answers are grounded in the saved analysis and its evidence; they say when fresh data is needed">
      <div className="stack mb">
        {msgs.map((m, i) => (
          <div key={i} className={m.role === "user" ? "callout" : "callout pos"} style={m.role === "user" ? undefined : { borderLeftColor: "var(--teal)" }}>
            <div className="xs faint">{m.role === "user" ? "You" : `Assistant · ${m.meta?.mode || ""}`}</div>
            <div className="small" style={{ whiteSpace: "pre-wrap" }}>{m.content}{m.citations?.length > 0 && <Cites ids={m.citations} />}</div>
            {m.meta?.needs_fresh_data && <div className="xs" style={{ color: "var(--warn)" }}>This question needs data newer than the analysis date; re-run the analysis for current figures.</div>}
            {m.meta?.uncertainty && m.role !== "user" && <div className="xs faint">{m.meta.uncertainty}</div>}
          </div>))}
        {!msgs.length && <div className="row">{examples.map((e) => <button key={e} className="btn-sm" onClick={() => ask(e, "text")}>{e}</button>)}</div>}
      </div>
      {err && <ErrorBox error={err} />}
      <AskBox onAsk={ask} busy={busy} />
    </Card>
  );
}

export { RecBadge, AGENT_TITLES };
