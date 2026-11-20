"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { ReactNode, useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { byUnit, money, pct } from "@/lib/format";

export const SERIES = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)"];
const AXIS = { stroke: "var(--faint)", fontSize: 11, tickLine: false, axisLine: false } as const;

export function Legend({ items }: { items: { label: string; color: string }[] }) {
  if (items.length < 2) return null;
  return (
    <div className="legend" aria-hidden="true">
      {items.map((i) => (
        <span key={i.label}><i style={{ background: i.color }} />{i.label}</span>
      ))}
    </div>
  );
}

function Tip({ active, payload, label, unit }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="tt">
      <div className="faint xs" style={{ marginBottom: 4 }}>{label}</div>
      {payload.map((p: any) => (
        <div className="tt-row" key={p.dataKey}>
          <span><i style={{ display: "inline-block", width: 8, height: 8, borderRadius: 2, background: p.color, marginRight: 6 }} />{p.name}</span>
          <span className="num">{p.value === null || p.value === undefined ? "missing" : byUnit(p.value, unit)}</span>
        </div>
      ))}
    </div>
  );
}

/** Wraps a chart with a title, interpretation note, source/date line and an accessible table view. */
export function ChartFrame({ title, note, source, rows, columns, children, onAsk }: {
  title: string; note?: ReactNode; source?: string; rows: Record<string, any>[]; columns: { key: string; label: string; fmt?: (v: any) => string }[];
  children: ReactNode; onAsk?: () => void;
}) {
  const [table, setTable] = useState(false);
  return (
    <figure className="card" style={{ margin: 0 }}>
      <div className="card-head">
        <h3>{title}</h3>
        <div className="seg" role="group" aria-label={`${title} view`}>
          <button className={!table ? "on" : ""} onClick={() => setTable(false)}>Chart</button>
          <button className={table ? "on" : ""} onClick={() => setTable(true)}>Table</button>
        </div>
      </div>
      {table ? (
        <div className="table-wrap">
          <table>
            <thead><tr>{columns.map((c) => <th key={c.key} className={c.key === columns[0].key ? "" : "r"}>{c.label}</th>)}</tr></thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>{columns.map((c) => <td key={c.key} className={c.key === columns[0].key ? "" : "r num"}>{r[c.key] === null || r[c.key] === undefined ? "—" : c.fmt ? c.fmt(r[c.key]) : r[c.key]}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : children}
      {note && <figcaption className="chart-note">{note}</figcaption>}
      {source && <div className="xs faint mt-s">{source}</div>}
      {onAsk && <button className="btn-sm btn-ghost mt-s" onClick={onAsk}>Ask about this chart</button>}
    </figure>
  );
}

type Pt = { period: string; end: string; value: number | null; id: string; source: string; quality: string };

/** Merge several field series on period label; missing values stay null (rendered as gaps, never interpolated). */
export function mergeSeries(series: Record<string, Pt[] | undefined>, scale = 1): Record<string, any>[] {
  const rows = new Map<string, Record<string, any>>();
  for (const [k, pts] of Object.entries(series)) {
    for (const p of pts || []) {
      const r = rows.get(p.end) || { period: p.period, end: p.end };
      r[k] = p.value === null ? null : p.value / scale;
      rows.set(p.end, r);
    }
  }
  return [...rows.values()].sort((a, b) => (a.end < b.end ? -1 : 1));
}

export function BarsChart({ data, keys, unit = "USD", height = 220 }: { data: any[]; keys: { key: string; label: string }[]; unit?: string; height?: number }) {
  return (
    <>
      <Legend items={keys.map((k, i) => ({ label: k.label, color: SERIES[i] }))} />
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={data} margin={{ top: 6, right: 6, left: 6, bottom: 0 }} barGap={2} barCategoryGap="22%">
          <CartesianGrid vertical={false} stroke="var(--grid)" />
          <XAxis dataKey="period" {...AXIS} interval="preserveStartEnd" minTickGap={12} />
          <YAxis {...AXIS} width={58} tickFormatter={(v) => byUnit(v, unit)} />
          <Tooltip content={<Tip unit={unit} />} cursor={{ fill: "var(--grid)" }} />
          <ReferenceLine y={0} stroke="var(--border-strong)" />
          {keys.map((k, i) => (
            <Bar key={k.key} dataKey={k.key} name={k.label} fill={SERIES[i]} radius={[4, 4, 0, 0]} maxBarSize={38} isAnimationActive={false} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </>
  );
}

export function LinesChart({ data, keys, unit = "pct", height = 220, xKey = "period", ref0 }: {
  data: any[]; keys: { key: string; label: string }[]; unit?: string; height?: number; xKey?: string; ref0?: number;
}) {
  return (
    <>
      <Legend items={keys.map((k, i) => ({ label: k.label, color: SERIES[i] }))} />
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ top: 6, right: 8, left: 6, bottom: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--grid)" />
          <XAxis dataKey={xKey} {...AXIS} interval="preserveStartEnd" minTickGap={24} />
          <YAxis {...AXIS} width={52} tickFormatter={(v) => byUnit(v, unit)} domain={["auto", "auto"]} />
          <Tooltip content={<Tip unit={unit} />} cursor={{ stroke: "var(--border-strong)" }} />
          {ref0 !== undefined && <ReferenceLine y={ref0} stroke="var(--border-strong)" />}
          {keys.map((k, i) => (
            <Line key={k.key} dataKey={k.key} name={k.label} stroke={SERIES[i]} strokeWidth={2} dot={data.length < 16 ? { r: 3 } : false}
              activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface)" }} connectNulls={false} isAnimationActive={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </>
  );
}

/** Single-series bar chart with a labelled reference line (e.g. current P/E vs history). */
export function RefBars({ data, refValue, refLabel, unit = "x", height = 200 }: { data: { label: string; value: number }[]; refValue?: number | null; refLabel?: string; unit?: string; height?: number }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 14, right: 8, left: 6, bottom: 0 }}>
        <CartesianGrid vertical={false} stroke="var(--grid)" />
        <XAxis dataKey="label" {...AXIS} />
        <YAxis {...AXIS} width={46} tickFormatter={(v) => byUnit(v, unit)} />
        <Tooltip content={<Tip unit={unit} />} cursor={{ fill: "var(--grid)" }} />
        <Bar dataKey="value" name="Value" fill="var(--s1)" radius={[4, 4, 0, 0]} maxBarSize={36} isAnimationActive={false} />
        {refValue !== null && refValue !== undefined && (
          <ReferenceLine y={refValue} stroke="var(--text)" strokeDasharray="4 3" label={{ value: refLabel, fill: "var(--muted)", fontSize: 11, position: "insideTopRight" }} />
        )}
      </BarChart>
    </ResponsiveContainer>
  );
}

export function ScenarioChart({ scenarios, price }: { scenarios: Record<string, any>; price: number }) {
  const order = ["bear", "base", "bull"];
  const data = order.filter((k) => scenarios[k]?.price).map((k) => ({ label: k[0].toUpperCase() + k.slice(1), value: scenarios[k].price, ret: scenarios[k].return }));
  return (
    <ResponsiveContainer width="100%" height={170}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 40, left: 6, bottom: 0 }}>
        <CartesianGrid horizontal={false} stroke="var(--grid)" />
        <XAxis type="number" {...AXIS} tickFormatter={(v) => `$${v}`} domain={[0, "auto"]} />
        <YAxis type="category" dataKey="label" {...AXIS} width={44} />
        <Tooltip content={({ active, payload }: any) => active && payload?.length ? (
          <div className="tt"><div className="tt-row"><span>Scenario price</span><span className="num">${payload[0].value}</span></div>
            <div className="tt-row"><span>vs current</span><span className="num">{pct(payload[0].payload.ret)}</span></div></div>) : null} />
        <Bar dataKey="value" radius={[0, 4, 4, 0]} maxBarSize={22} isAnimationActive={false}>
          {data.map((d) => <Cell key={d.label} fill={d.label === "Bear" ? "var(--s2)" : d.label === "Bull" ? "var(--s3)" : "var(--s1)"} />)}
        </Bar>
        <ReferenceLine x={price} stroke="var(--text)" strokeDasharray="4 3" label={{ value: `Current $${price.toFixed(0)}`, fill: "var(--muted)", fontSize: 11, position: "top" }} />
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Diverging heatmap (−1 blue … 0 gray … +1 red) rendered as an accessible table. */
export function CorrelationTable({ tickers, matrix }: { tickers: string[]; matrix: number[][] }) {
  const color = (v: number) => {
    const a = Math.min(1, Math.abs(v));
    const pole = v >= 0 ? "var(--div-neg)" : "var(--div-pos)";
    return `color-mix(in oklab, ${pole} ${Math.round(a * 85)}%, var(--div-mid))`;
  };
  return (
    <div className="table-wrap">
      <table className="heat" aria-label="Correlation matrix">
        <thead><tr><th />{tickers.map((t) => <th key={t} style={{ textAlign: "center" }}>{t}</th>)}</tr></thead>
        <tbody>
          {matrix.map((row, i) => (
            <tr key={tickers[i]}>
              <th>{tickers[i]}</th>
              {row.map((v, j) => (
                <td key={j} style={{ background: color(v), color: Math.abs(v) > 0.55 ? "#fff" : "var(--text)" }} title={`${tickers[i]} / ${tickers[j]}: ${v.toFixed(2)}`}>{v.toFixed(2)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="xs faint mt-s">Blue = negative, gray = none, red = positive correlation. Historical correlations can change.</div>
    </div>
  );
}

export function WeightBars({ weights, height = 200 }: { weights: Record<string, number>; height?: number }) {
  const data = Object.entries(weights).sort((a, b) => b[1] - a[1]).map(([label, value]) => ({ label, value }));
  return (
    <ResponsiveContainer width="100%" height={Math.max(height, data.length * 26)}>
      <BarChart data={data} layout="vertical" margin={{ top: 0, right: 40, left: 0, bottom: 0 }}>
        <XAxis type="number" {...AXIS} tickFormatter={(v) => pct(v, 0)} />
        <YAxis type="category" dataKey="label" {...AXIS} width={150} />
        <Tooltip content={<Tip unit="pct" />} cursor={{ fill: "var(--grid)" }} />
        <Bar dataKey="value" name="Weight" fill="var(--s1)" radius={[0, 4, 4, 0]} maxBarSize={18} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export { money };
