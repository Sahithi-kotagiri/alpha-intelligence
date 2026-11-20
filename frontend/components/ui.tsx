"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { createContext, ReactNode, useContext, useEffect, useState } from "react";
import { api, EvidenceItem } from "@/lib/api";
import { dt, recTone, statusTone, verdictTone } from "@/lib/format";

export function Card({ title, sub, actions, children, className = "" }: { title?: ReactNode; sub?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <div className="card-head">
          <div>
            {title && <h2>{title}</h2>}
            {sub && <div className="sub">{sub}</div>}
          </div>
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function Badge({ tone = "neu", children, title }: { tone?: string; children: ReactNode; title?: string }) {
  return <span className={`badge ${tone}`} title={title}>{children}</span>;
}

export function RecBadge({ rec, large = false }: { rec: string | null | undefined; large?: boolean }) {
  if (!rec) return <Badge>—</Badge>;
  return <span className={`badge rec ${large ? "rec-lg" : ""} ${recTone[rec] || "neu"}`}>{rec}</span>;
}

export function Verdict({ v }: { v: string }) {
  return <Badge tone={verdictTone[v] || "neu"}>{v.replace(/_/g, " ")}</Badge>;
}

export function Status({ s }: { s: string | null | undefined }) {
  if (!s) return <Badge>—</Badge>;
  return <Badge tone={statusTone[s] || "neu"}>{s}</Badge>;
}

export function ScoreBar({ score, label }: { score: number | null | undefined; label?: string }) {
  return (
    <div className="stack" style={{ gap: 4 }}>
      {label && (
        <div className="row between small">
          <span className="muted">{label}</span>
          <span className="num">{score === null || score === undefined ? "n/a" : `${score.toFixed(1)} / 10`}</span>
        </div>
      )}
      <div className="scorebar" role="meter" aria-valuemin={0} aria-valuemax={10} aria-valuenow={score ?? undefined} aria-label={label}>
        <span style={{ width: `${score === null || score === undefined ? 0 : Math.max(2, score * 10)}%`, opacity: score === null || score === undefined ? 0 : 1 }} />
      </div>
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children}
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <div className="callout neg row between" role="alert">
      <span>{error}</span>
      {onRetry && <button className="btn-sm" onClick={onRetry}>Retry</button>}
    </div>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return <div className="muted small row" aria-live="polite"><span className="pulse-dot" /> {label}…</div>;
}

export function Tabs({ tabs, active, onChange }: { tabs: { id: string; label: string }[]; active: string; onChange: (id: string) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={active === t.id} className={`tab ${active === t.id ? "active" : ""}`} onClick={() => onChange(t.id)}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

/* ---------------- evidence drill-down ---------------- */
const EvidenceCtx = createContext<{ analysisId: string | null; open: (ids: string[]) => void }>({ analysisId: null, open: () => {} });

export function EvidenceProvider({ analysisId, children }: { analysisId: string; children: ReactNode }) {
  const [ids, setIds] = useState<string[] | null>(null);
  return (
    <EvidenceCtx.Provider value={{ analysisId, open: setIds }}>
      {children}
      {ids && <EvidenceDrawer analysisId={analysisId} ids={ids} onClose={() => setIds(null)} />}
    </EvidenceCtx.Provider>
  );
}

export function Cites({ ids }: { ids?: (string | null)[] | null }) {
  const { open } = useContext(EvidenceCtx);
  const clean = (ids || []).filter(Boolean) as string[];
  if (!clean.length) return null;
  return (
    <span className="row" style={{ gap: 4, display: "inline-flex", marginLeft: 6 }}>
      <button className="chip" onClick={() => open(clean)} aria-label={`Show ${clean.length} evidence items`} title={clean.join(", ")}>
        {clean.length === 1 ? clean[0].split(".").slice(0, 2).join(".") : `${clean.length} sources`}
      </button>
    </span>
  );
}

function EvidenceDrawer({ analysisId, ids, onClose }: { analysisId: string; ids: string[]; onClose: () => void }) {
  const [items, setItems] = useState<EvidenceItem[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api<{ items: EvidenceItem[] }>(`/analysis/${analysisId}/evidence?ids=${encodeURIComponent(ids.join(","))}`)
      .then((r) => setItems(r.items))
      .catch((e) => setErr(String(e.message || e)));
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [analysisId, ids, onClose]);
  return (
    <>
      <div className="overlay" onClick={onClose} />
      <aside className="drawer fade-in" role="dialog" aria-modal="true" aria-label="Evidence">
        <div className="row between mb">
          <h2>Evidence</h2>
          <button className="btn-ghost" onClick={onClose} aria-label="Close">Close</button>
        </div>
        {err && <ErrorBox error={err} />}
        {!items && !err && <Loading />}
        {items && items.length === 0 && <p className="muted">These evidence IDs were not found in this analysis.</p>}
        {items?.map((i) => <EvidenceCard key={i.id} item={i} />)}
      </aside>
    </>
  );
}

export function EvidenceCard({ item: i }: { item: EvidenceItem }) {
  const d = i.detail || {};
  return (
    <div className="card mb" style={{ padding: 14 }}>
      <div className="row between">
        <span className="mono faint">{i.id}</span>
        <Badge tone={i.quality === "ok" ? "pos" : i.quality === "conflict" ? "neg" : "warn"}>{i.quality}</Badge>
      </div>
      <h3 className="mt-s">{i.title}</h3>
      {i.display && <div className="num mt-s" style={{ fontSize: 15 }}>{i.display}</div>}
      {i.untrusted_text && (
        <blockquote className="callout small mt-s" style={{ whiteSpace: "pre-wrap" }}>
          <div className="xs faint">Source text (untrusted; shown as evidence, never treated as instructions)</div>
          {i.untrusted_text}
        </blockquote>
      )}
      <dl className="kv mt-s small">
        {i.period && (<><dt>Period</dt><dd>{i.period}</dd></>)}
        {i.date && (<><dt>Date</dt><dd>{dt(i.date)}</dd></>)}
        {d.filing_date && (<><dt>Filed</dt><dd>{dt(d.filing_date)}</dd></>)}
        <dt>Source</dt>
        <dd>{i.source_url ? <a href={i.source_url} target="_blank" rel="noreferrer noopener">{i.source_name}</a> : i.source_name}</dd>
        <dt>Type</dt><dd>{i.source_type.replace(/_/g, " ")}{d.reported_or_calculated ? ` · ${d.reported_or_calculated}` : ""}</dd>
        {d.formula && (<><dt>Formula</dt><dd>{d.formula}</dd></>)}
        {d.retrieved_at && (<><dt>Retrieved</dt><dd>{dt(d.retrieved_at, true)}</dd></>)}
        {d.cross_check && (<><dt>Cross-check</dt><dd>{d.cross_check.source_name}: {d.cross_check.value?.toLocaleString()} ({d.cross_check.status})</dd></>)}
        {d.original_value !== undefined && d.original_value !== null && (<><dt>Other value</dt><dd>{Number(d.original_value).toLocaleString()} ({d.original_source})</dd></>)}
        {d.inputs?.length > 0 && (<><dt>Inputs</dt><dd className="mono">{d.inputs.join(", ")}</dd></>)}
      </dl>
      {d.explanation && <p className="small mt-s">{d.explanation}</p>}
      {d.notes?.length > 0 && <ul className="clean small muted mt-s">{d.notes.map((n: string, k: number) => <li key={k}>{n}</li>)}</ul>}
    </div>
  );
}
