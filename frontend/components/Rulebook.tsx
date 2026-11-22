"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useState } from "react";
import { pct } from "@/lib/format";
import { Badge, Cites, RecBadge } from "./ui";

export const PILLARS: { key: string; label: string; blurb: string }[] = [
  { key: "quality", label: "Quality", blurb: "5-year ROIC, its stability, and free cash flow after stock pay vs net income" },
  { key: "value", label: "Value", blurb: "FCF yield, achieved vs price-implied growth, P/E vs its own 10-year range" },
  { key: "momentum", label: "Momentum", blurb: "12-month return excluding the last month; price vs a rising or falling 200-day average" },
  { key: "revisions", label: "Revisions", blurb: "90-day change in consensus EPS; last four earnings surprises" },
];

function fmtMeasure(v: number | null | undefined, unit: string) {
  if (v === null || v === undefined) return "—";
  if (unit === "pct") return `${(v * 100).toFixed(1)}%`;
  if (unit === "x") return `${v.toFixed(2)}×`;
  return v.toFixed(2);
}

function band(score: number) {
  return score >= 75 ? "pos" : score >= 50 ? "acc" : score >= 25 ? "warn" : "neg";
}

/** One 0–100 bar with quartile ticks. Score is a sector percentile. */
export function PillarBar({ score, label, compact = false }: { score: number | null | undefined; label?: string; compact?: boolean }) {
  const s = score ?? 0;
  return (
    <div className={`pbar ${compact ? "pbar-sm" : ""}`} role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={score ?? undefined}
      aria-label={label ? `${label} ${score === null || score === undefined ? "n/a" : Math.round(s)} of 100` : undefined}>
      <span className={`pbar-fill t-${band(s)}`} style={{ width: `${score === null || score === undefined ? 0 : Math.max(2, s)}%` }} />
      {!compact && [25, 50, 75].map((t) => <i key={t} style={{ left: `${t}%` }} />)}
    </div>
  );
}

/** Four pillars with drill-down into the measures behind each score. */
export function PillarPanel({ pillars, composite }: { pillars: Record<string, any>; composite: number }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div className="pillars">
      <div className="composite">
        <Dial value={composite} />
        <div>
          <div className="section-title">Composite</div>
          <div className="xs faint">Average of four equal-weight pillars. Each pillar is a percentile against same-sector S&P 500 companies.</div>
        </div>
      </div>
      {PILLARS.map((p) => {
        const d = pillars[p.key];
        if (!d) return null;
        const isOpen = open === p.key;
        return (
          <div key={p.key} className={`pillar ${isOpen ? "open" : ""}`}>
            <button className="pillar-head" aria-expanded={isOpen} aria-controls={`pm-${p.key}`} onClick={() => setOpen(isOpen ? null : p.key)}>
              <span className="pillar-name">{p.label}</span>
              <PillarBar score={d.score} label={p.label} />
              <span className="num pillar-score">{Math.round(d.score)}</span>
              <span className="chev" aria-hidden>{isOpen ? "−" : "+"}</span>
            </button>
            {d.sparse && <div className="xs" style={{ color: "var(--warn)" }}>Over half of this pillar&apos;s measures are unavailable: scored 50 and confidence reduced.</div>}
            {isOpen && (
              <div id={`pm-${p.key}`} className="pillar-body">
                <p className="xs faint">{p.blurb}.</p>
                <table className="measures"><tbody>
                  {d.measures.map((m: any) => (
                    <tr key={m.key}>
                      <td className="small">{m.label}{m.not_applicable && <span className="xs faint"> (not applicable)</span>}</td>
                      <td className="r num">{fmtMeasure(m.value, m.unit)}</td>
                      <td style={{ width: "34%" }}>{m.score !== null && m.score !== undefined ? <PillarBar score={m.score} compact /> : <span className="xs faint">{m.reason}</span>}</td>
                      <td className="r num small">{m.score !== null && m.score !== undefined ? Math.round(m.score) : ""}</td>
                      <td className="xs faint">{m.basis ? `${m.basis}${m.n ? `, n=${m.n}` : ""}` : ""}<Cites ids={[`rb.m.${m.key}`]} /></td>
                    </tr>))}
                </tbody></table>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** Semi-circular gauge for the composite score. */
export function Dial({ value, size = 92 }: { value: number; size?: number }) {
  const r = 38, c = Math.PI * r, v = Math.max(0, Math.min(100, value));
  return (
    <svg width={size} height={size * 0.62} viewBox="0 0 100 62" role="img" aria-label={`Composite ${Math.round(v)} of 100`}>
      <path d="M12 54 A38 38 0 0 1 88 54" fill="none" stroke="var(--surface-3)" strokeWidth="9" strokeLinecap="round" />
      <path d="M12 54 A38 38 0 0 1 88 54" fill="none" className={`dial t-${band(v)}`} strokeWidth="9" strokeLinecap="round"
        strokeDasharray={`${(c * v) / 100} ${c}`} />
      <text x="50" y="52" textAnchor="middle" className="dial-num">{Math.round(v)}</text>
    </svg>
  );
}

/** Price ladder: where today's price sits against buy-below and fair value. */
export function PriceLadder({ price, fair, buyBelow, date }: { price?: number | null; fair?: number | null; buyBelow?: number | null; date?: string | null }) {
  const [hover, setHover] = useState<string | null>(null);
  if (!price || !fair || !buyBelow) return <p className="muted small">Fair value could not be computed (needs positive free cash flow or earnings with enough history), so no buy-below price is set.</p>;
  const lo = Math.min(price, buyBelow) * 0.85, hi = Math.max(price, fair) * 1.12;
  const x = (v: number) => `${((v - lo) / (hi - lo)) * 100}%`;
  const zone = price <= buyBelow ? "pos" : price <= fair ? "acc" : "neg";
  const gap = price / fair - 1;
  const marks = [
    { k: "buy", v: buyBelow, label: "Buy-below", cls: "m-buy" },
    { k: "fair", v: fair, label: "Fair value", cls: "m-fair" },
    { k: "price", v: price, label: "Price", cls: `m-price t-${zone}` },
  ];
  return (
    <div>
      <div className="ladder" aria-label={`Price ${price.toFixed(2)}, buy-below ${buyBelow.toFixed(2)}, fair value ${fair.toFixed(2)}`}>
        <span className="ladder-zone z-buy" style={{ left: 0, width: x(buyBelow) }} />
        <span className="ladder-zone z-mid" style={{ left: x(buyBelow), width: `calc(${x(fair)} - ${x(buyBelow)})` }} />
        <span className="ladder-zone z-over" style={{ left: x(fair), right: 0 }} />
        {marks.map((m) => (
          <button key={m.k} className={`ladder-mark ${m.cls}`} style={{ left: x(m.v) }} onMouseEnter={() => setHover(m.k)} onMouseLeave={() => setHover(null)}
            onFocus={() => setHover(m.k)} onBlur={() => setHover(null)} aria-label={`${m.label} $${m.v.toFixed(2)}`}>
            {(hover === m.k || m.k === "price") && <span className="ladder-tip">{m.label} ${m.v.toFixed(2)}</span>}
          </button>
        ))}
      </div>
      <div className="row between xs faint" style={{ marginTop: 22 }}>
        <span>Buy-below <span className="num">${buyBelow.toFixed(2)}</span></span>
        <span>Fair value <span className="num">${fair.toFixed(2)}</span></span>
      </div>
      <p className="small mt-s">
        Price is <strong style={{ color: `var(--${zone === "acc" ? "accent" : zone})` }}>{Math.abs(gap * 100).toFixed(0)}% {gap < 0 ? "below" : "above"}</strong> fair value
        {date ? <span className="faint"> · price date {date}</span> : null}.
      </p>
    </div>
  );
}

function Reason({ m, tone }: { m: any; tone: "pos" | "neg" }) {
  return (
    <li className="reason">
      <Badge tone={tone}>{Math.round(m.score)}</Badge> <span>{m.label}: <span className="num">{fmtMeasure(m.value, m.unit)}</span></span>
      <span className="xs faint"> ({m.basis})</span><Cites ids={[`rb.m.${m.key}`]} />
    </li>
  );
}

/** Everything the rulebook requires on a recommendation (rulebook §7). */
export function RulebookDecision({ rb }: { rb: any }) {
  if (!rb) return null;
  const size = rb.position_size?.size;
  return (
    <div className="grid g2 mb">
      <section className="card">
        <div className="card-head"><h2>Rulebook verdict</h2><span className="xs faint">{rb.version}</span></div>
        <div className="row mb" style={{ gap: 12 }}>
          <RecBadge rec={rb.label} large />
          {rb.rulebook_label && rb.rulebook_label !== rb.label && <span className="small muted">rules gave {rb.rulebook_label}; committee lowered it</span>}
          {rb.insufficient_data && <Badge tone="warn">insufficient data</Badge>}
        </div>
        <PillarPanel pillars={rb.pillars} composite={rb.composite} />
        {rb.gates?.length > 0 && (
          <div className="mt">
            <div className="section-title">Gates (can only lower the label)</div>
            <ul className="clean small">{rb.gates.map((g: any) => (
              <li key={g.id} className="claim" style={g.binding ? undefined : { opacity: 0.6 }}>
                <Badge tone={g.binding ? "warn" : "neu"}>≤ {g.effect}</Badge> {g.reason}<Cites ids={g.evidence_ids} />
                <span className="xs faint"> {g.binding ? "— lowered the label" : "— not binding (label already at or below)"}</span></li>))}</ul>
          </div>
        )}
        <details className="mt-s"><summary className="xs muted" style={{ cursor: "pointer" }}>Decision trace</summary>
          <ol className="small mt-s">{rb.trace.map((t: string, i: number) => <li key={i}>{t}</li>)}</ol></details>
      </section>
      <div className="stack">
        <section className="card">
          <div className="card-head"><h2>Price, fair value & size</h2></div>
          <PriceLadder price={rb.price} fair={rb.fair_value} buyBelow={rb.buy_below} date={rb.data_date} />
          {rb.fair_value_low_precision && <div className="callout warn xs mt-s">The two fair-value methods differ by {rb.fair_value_spread?.toFixed(1)}×: treat the fair value as low precision.</div>}
          {rb.fair_value_methods?.length > 0 && (
            <ul className="clean xs faint mt-s">{rb.fair_value_methods.map((m: any) => <li key={m.method}>{m.method}: ${m.value.toFixed(2)}</li>)}</ul>)}
          <div className="divider" />
          <dl className="kv small">
            <dt>Position size</dt><dd><span className="num">{size ? pct(size, 1) : "0%"}</span> of portfolio{rb.position_size?.notes?.length ? ` (${rb.position_size.notes.join("; ")})` : ""}</dd>
            <dt>Market expects</dt><dd>{rb.market_belief || "—"}</dd>
            <dt>Why we differ</dt><dd>{rb.our_difference || "—"} {rb.difference_credible === false && <Badge tone="warn">no credible edge</Badge>}{rb.difference_credible && <Badge tone="pos">evidenced</Badge>}</dd>
          </dl>
        </section>
        <section className="card">
          <div className="card-head"><h2>Reasons</h2></div>
          <div className="grid g2" style={{ gap: 12 }}>
            <div><div className="section-title">For</div>
              {rb.reasons_for?.length ? <ul className="clean small">{rb.reasons_for.map((m: any) => <Reason key={m.key} m={m} tone="pos" />)}</ul> : <p className="muted small">No measure in the top 40% of its sector.</p>}</div>
            <div><div className="section-title">Against</div>
              {rb.reasons_against?.length ? <ul className="clean small">{rb.reasons_against.map((m: any) => <Reason key={m.key} m={m} tone="neg" />)}</ul> : <p className="muted small">No measure in the bottom 40% of its sector.</p>}</div>
          </div>
          {rb.top_risks?.length > 0 && (<>
            <div className="section-title mt-s">Devil&apos;s Advocate: top risks</div>
            <ol className="small">{rb.top_risks.map((o: any, i: number) => <li key={i}><Badge tone={o.severity === "high" ? "neg" : o.severity === "medium" ? "warn" : "neu"}>{o.severity}</Badge> {o.objection}<Cites ids={o.evidence_ids} /></li>)}</ol>
          </>)}
        </section>
        <section className="card">
          <div className="card-head"><h2>What would change the call</h2></div>
          <ul className="clean small">{(rb.what_would_change || []).map((t: string) => <li key={t} className="claim">{t}</li>)}</ul>
          <div className="divider" />
          <div className="row between small"><span className="muted">Confidence</span><span className="num" style={{ fontSize: 16 }}>{pct(rb.confidence, 0)}</span></div>
          <ul className="clean xs faint">{(rb.confidence_reasons || []).map((t: string) => <li key={t}>{t}</li>)}</ul>
        </section>
      </div>
    </div>
  );
}

/** Stacked distribution of labels across the screened universe; segments act as filters. */
export const LABEL_ORDER = ["Strong Buy", "Buy", "Wait", "Watchlist", "Reject", "Insufficient data"];
export function LabelDistribution({ counts, active, onPick }: { counts: Record<string, number>; active?: string; onPick?: (l: string) => void }) {
  const total = Object.values(counts || {}).reduce((a, b) => a + b, 0);
  if (!total) return <p className="muted small">No rulebook labels yet: run a universe screen.</p>;
  return (
    <div>
      <div className="dist" role="group" aria-label="Rulebook label distribution">
        {LABEL_ORDER.filter((l) => counts[l]).map((l) => (
          <button key={l} className={`dist-seg lbl-${l.replace(/\s/g, "-").toLowerCase()} ${active === l ? "on" : ""}`} style={{ flexGrow: counts[l] }}
            onClick={() => onPick?.(active === l ? "" : l)} title={`${l}: ${counts[l]}`} aria-pressed={active === l}>
            <span>{counts[l] / total > 0.06 ? counts[l] : ""}</span>
          </button>))}
      </div>
      <div className="row xs mt-s" style={{ gap: 12, flexWrap: "wrap" }}>
        {LABEL_ORDER.filter((l) => counts[l]).map((l) => (
          <button key={l} className={`chip ${active === l ? "on" : ""}`} onClick={() => onPick?.(active === l ? "" : l)} aria-pressed={active === l}>
            <span className={`dot lbl-${l.replace(/\s/g, "-").toLowerCase()}`} />{l} <span className="num">{counts[l]}</span>
          </button>))}
      </div>
    </div>
  );
}
