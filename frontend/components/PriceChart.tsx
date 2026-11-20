"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useMemo, useRef, useState } from "react";

/** Daily price with 20/50/200-day averages and a volume pane (TradingView Lightweight Charts). */
export default function PriceChart({ series, dataThrough }: { series: any; dataThrough?: string }) {
  const el = useRef<HTMLDivElement>(null);
  const [range, setRange] = useState<"6M" | "1Y" | "3Y">("1Y");
  const [hover, setHover] = useState<{ date: string; close?: number; sma20?: number; sma50?: number; sma200?: number } | null>(null);
  const n = series?.dates?.length || 0;
  const start = useMemo(() => Math.max(0, n - ({ "6M": 126, "1Y": 252, "3Y": 756 } as const)[range]), [n, range]);

  useEffect(() => {
    if (!el.current || !n) return;
    let chart: any;
    let disposed = false;
    (async () => {
      const lw = await import("lightweight-charts");
      if (disposed || !el.current) return;
      const css = getComputedStyle(document.documentElement);
      const v = (name: string) => css.getPropertyValue(name).trim();
      chart = lw.createChart(el.current, {
        autoSize: true,
        layout: { background: { color: "transparent" }, textColor: v("--muted"), fontSize: 11,
          panes: { separatorColor: v("--border"), enableResize: false } },
        grid: { vertLines: { visible: false }, horzLines: { color: v("--grid") } },
        rightPriceScale: { borderVisible: false },
        timeScale: { borderVisible: false },
        crosshair: { mode: 0 },
      });
      const sl = (arr: (number | null)[]) =>
        arr.slice(start).map((y, i) => (y === null ? { time: series.dates[start + i] } : { time: series.dates[start + i], value: y }));
      const close = chart.addSeries(lw.LineSeries, { color: v("--s1"), lineWidth: 2, priceLineVisible: false, title: "" });
      close.setData(sl(series.close));
      const mk = (key: string, color: string) => {
        const s = chart.addSeries(lw.LineSeries, { color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
        s.setData(sl(series[key]));
        return s;
      };
      const s20 = mk("sma20", v("--s2"));
      const s50 = mk("sma50", v("--s3"));
      const s200 = mk("sma200", v("--s4"));
      const vol = chart.addSeries(lw.HistogramSeries, { color: v("--faint"), priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false }, 1);
      vol.setData(series.volume.slice(start).map((y: number, i: number) => ({ time: series.dates[start + i], value: y ?? 0 })));
      chart.panes()[1]?.setHeight(70);
      chart.timeScale().fitContent();
      chart.subscribeCrosshairMove((p: any) => {
        if (!p.time) return setHover(null);
        const g = (s: any) => (p.seriesData.get(s) as any)?.value;
        setHover({ date: String(p.time), close: g(close), sma20: g(s20), sma50: g(s50), sma200: g(s200) });
      });
    })();
    return () => {
      disposed = true;
      chart?.remove();
    };
  }, [series, start, n]);

  if (!n) return <p className="muted">Price history unavailable.</p>;
  const last = n - 1;
  const show = hover || { date: series.dates[last], close: series.close[last], sma20: series.sma20[last], sma50: series.sma50[last], sma200: series.sma200[last] };
  const f = (x?: number | null) => (x === undefined || x === null ? "—" : `$${x.toFixed(2)}`);
  return (
    <div>
      <div className="row between mb" style={{ marginBottom: 8 }}>
        <div className="legend" style={{ marginBottom: 0 }}>
          <span className="num faint">{show.date}</span>
          <span><i style={{ background: "var(--s1)" }} />Close {f(show.close)}</span>
          <span><i style={{ background: "var(--s2)" }} />20-day {f(show.sma20)}</span>
          <span><i style={{ background: "var(--s3)" }} />50-day {f(show.sma50)}</span>
          <span><i style={{ background: "var(--s4)" }} />200-day {f(show.sma200)}</span>
        </div>
        <div className="seg" role="group" aria-label="Time range">
          {(["6M", "1Y", "3Y"] as const).map((r) => <button key={r} className={range === r ? "on" : ""} onClick={() => setRange(r)}>{r}</button>)}
        </div>
      </div>
      <div ref={el} style={{ height: 340 }} role="img" aria-label={`Price chart with moving averages through ${dataThrough}`} />
      <div className="xs faint mt-s">Daily closes (split-adjusted) and volume, Yahoo Finance via yfinance. Data through {dataThrough}. Lower pane: volume.</div>
    </div>
  );
}
