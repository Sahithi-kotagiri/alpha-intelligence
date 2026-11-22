"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { use, useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import { useEventStream } from "@/hooks/useEventStream";
import { ago, dt, pct } from "@/lib/format";
import { Badge, Card, ErrorBox, EvidenceProvider, Loading, RecBadge, Status, Tabs } from "@/components/ui";
import { AgentStatus, Timeline } from "@/components/Progress";
import {
  AskView, CommitteeView, ValuationView, DataQualityView, EvidenceView, FundamentalsView, NewsView, RiskView, SummaryView, TechnicalView, ThesisView,
} from "@/components/AnalysisViews";

const TABS = [
  { id: "summary", label: "Decision" }, { id: "committee", label: "Investment committee" }, { id: "fundamentals", label: "Fundamentals" },
  { id: "valuation", label: "Valuation" }, { id: "technical", label: "Technical" }, { id: "news", label: "News & filings" }, { id: "risk", label: "Risk & portfolio" },
  { id: "thesis", label: "Thesis" }, { id: "evidence", label: "Evidence" }, { id: "quality", label: "Data quality" }, { id: "ask", label: "Ask" },
];

export default function AnalysisPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: run, error, reload } = useApi<any>(`/analysis/${id}`);
  const running = run && (run.status === "queued" || run.status === "running");
  const { events, status } = useEventStream(id, !!running);
  const [tab, setTab] = useState("summary");
  const [watching, setWatching] = useState(false);
  const [pendingAsk, setPendingAsk] = useState<string | null>(null);

  useEffect(() => {
    const h = (e: Event) => { setPendingAsk((e as CustomEvent).detail); setTab("ask"); window.scrollTo({ top: 0 }); };
    window.addEventListener("ii-ask", h);
    return () => window.removeEventListener("ii-ask", h);
  }, []);

  useEffect(() => {
    if (status === "completed" || status === "failed") reload();
  }, [status, reload]);

  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!run) return <Loading label="Loading analysis" />;

  const r = run.result;
  const retry = async () => {
    const n: any = await api(`/analysis/${id}/retry`, { method: "POST" });
    window.location.href = `/analysis/${n.analysis_id}`;
  };
  const watch = async () => {
    await api("/watchlist", { method: "POST", json: { ticker: run.ticker } });
    setWatching(true);
  };

  return (
    <EvidenceProvider analysisId={id}>
      <div className="page-head">
        <div>
          <div className="row" style={{ gap: 8 }}>
            <h1>{run.company_name || run.ticker}</h1>
            <span className="mono muted">{run.ticker}</span>
            <Badge>v{run.version}</Badge>
            {run.trigger !== "user" && <Badge tone="acc">{run.trigger === "monitoring" ? "monitoring reassessment" : run.trigger}</Badge>}
          </div>
          <p>
            {r ? `${r.identity.sub_industry || r.identity.vendor_industry || ""} · ${r.identity.exchange || ""} · horizon ${r.params.horizon} · benchmark ${r.params.benchmark}` : "Analysis in progress"}
            {" · started "}{ago(run.created_at)}
          </p>
        </div>
        <div className="row">
          {run.status === "completed" && <button onClick={watch} disabled={watching}>{watching ? "On watchlist" : "Add to watchlist"}</button>}
          <Link className="btn" href={`/history?ticker=${run.ticker}`}>History</Link>
          <Link className="btn" href={`/analyze?ticker=${run.ticker}`}>Re-analyse</Link>
        </div>
      </div>

      {run.status !== "completed" && (
        <div className="grid g2">
          <Card title={run.status === "failed" ? "Analysis failed" : "Research in progress"} sub="Each line is a real backend step">
            {run.status === "failed" && <div className="mb"><ErrorBox error={run.error || "Unknown error"} onRetry={retry} /></div>}
            <Timeline events={events} status={status} />
          </Card>
          <Card title="Analyst status"><AgentStatus events={events} /></Card>
        </div>
      )}

      {run.status === "completed" && r && (
        <>
          <section className="card mb decision-head">
            <div>
              <div className="section-title">Recommendation</div>
              <RecBadge rec={r.final.recommendation} large />
            </div>
            <div className="stack" style={{ gap: 4 }}>
              <div><span className="muted small">Confidence </span><span className="num" style={{ fontSize: 18 }}>{pct(r.final.confidence, 0)}</span>
                <span className="xs faint"> — assessment quality and evidence sufficiency, not probability of profit</span></div>
              <div className="small muted">{r.final.decision_trace[r.final.decision_trace.length - 1]}</div>
              {r.final.rulebook && <div className="row" style={{ gap: 10 }}>
                <span className="small muted">Composite <span className="num">{Math.round(r.final.rulebook.composite)}</span></span>
                {r.final.rulebook.buy_below && <span className="small muted">Buy-below <span className="num">${r.final.rulebook.buy_below.toFixed(2)}</span></span>}
                {r.final.rulebook.position_size?.size > 0 && <span className="small muted">Size <span className="num">{pct(r.final.rulebook.position_size.size, 1)}</span></span>}
              </div>}
              {r.final.overridden && <div className="small" style={{ color: "var(--warn)" }}>Committee lowered the rulebook label ({r.final.policy_recommendation}).</div>}
              {r.mode !== "llm" && <div className="xs" style={{ color: "var(--warn)" }}>Rule-based mode: no LLM was configured, so agents did not interpret qualitative evidence.</div>}
            </div>
            <dl className="kv small">
              <dt>Price</dt><dd className="num">${r.final.meta.reference_price?.toFixed(2)} ({dt(r.final.meta.price_date)})</dd>
              <dt>Financials</dt><dd>{r.final.meta.financials_through}</dd>
              <dt>Data quality</dt><dd><Status s={r.data_quality.status} /></dd>
              <dt>Analysed</dt><dd>{dt(run.completed_at, true)}</dd>
            </dl>
          </section>
          {r.prior && (
            <div className="callout small mb">
              Previous analysis ({dt(r.prior.created_at)}): <strong>{r.prior.recommendation}</strong> at {pct(r.prior.confidence, 0)} —{" "}
              <Link href={`/history/compare?a=${r.prior.analysis_id}&b=${id}`}>compare versions</Link>
            </div>
          )}
          <Tabs tabs={TABS} active={tab} onChange={setTab} />
          {tab === "summary" && <SummaryView r={r} analysisId={id} />}
          {tab === "committee" && <CommitteeView r={r} analysisId={id} />}
          {tab === "fundamentals" && <FundamentalsView r={r} />}
          {tab === "valuation" && <ValuationView r={r} analysisId={id} />}
          {tab === "technical" && <TechnicalView r={r} />}
          {tab === "news" && <NewsView r={r} />}
          {tab === "risk" && <RiskView r={r} />}
          {tab === "thesis" && <ThesisView r={r} ticker={run.ticker} />}
          {tab === "evidence" && <EvidenceView analysisId={id} />}
          {tab === "quality" && <DataQualityView r={r} />}
          {tab === "ask" && <AskView analysisId={id} initial={pendingAsk} />}
          <details className="mt">
            <summary className="small muted" style={{ cursor: "pointer" }}>Research log ({events.length} backend events)</summary>
            <div className="card mt-s"><Timeline events={events} status="completed" /></div>
          </details>
        </>
      )}
    </EvidenceProvider>
  );
}
