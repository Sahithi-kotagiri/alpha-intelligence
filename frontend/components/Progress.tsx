"use client";
import { EventRow } from "@/lib/api";
import { AGENT_TITLES } from "@/lib/format";
import { Badge } from "./ui";

const AGENTS = ["fundamental", "technical", "news", "risk_portfolio", "devils_advocate"];
const STATUS_LABEL: Record<string, string> = {
  queued: "Queued", retrieving_evidence: "Retrieving evidence", analysing: "Analysing", reviewing: "Reviewing",
  completed: "Completed", failed: "Failed",
};
const tone = (s?: string) => (s === "completed" ? "pos" : s === "failed" ? "neg" : s === "queued" || !s ? "neu" : "acc");

/** Agent status derived only from real backend events; nothing is simulated. */
export function AgentStatus({ events }: { events: EventRow[] }) {
  const last: Record<string, EventRow> = {};
  for (const e of events) if (e.agent && AGENTS.includes(e.agent) && e.stage !== "review") last[e.agent] = e;
  const reviewing = new Set(events.filter((e) => e.stage === "review" && e.status === "started").map((e) => e.agent));
  return (
    <div className="stack" style={{ gap: 6 }}>
      {AGENTS.map((a) => {
        const e = last[a];
        const st = e?.status;
        return (
          <div key={a} className="row between small" style={{ padding: "6px 0", borderBottom: "1px solid var(--border)" }}>
            <span>{AGENT_TITLES[a]}</span>
            <span className="row" style={{ gap: 6 }}>
              {reviewing.has(a) && <Badge tone="acc">Answered committee</Badge>}
              <Badge tone={tone(st)}>
                {st && st !== "completed" && st !== "failed" && st !== "queued" && <span className="pulse-dot" />}
                {st ? STATUS_LABEL[st] || st : "Not started"}
                {st === "completed" && e?.detail?.mode === "llm" ? " · LLM" : st === "completed" ? " · rule-based" : ""}
              </Badge>
            </span>
          </div>
        );
      })}
    </div>
  );
}

export function Timeline({ events, status }: { events: EventRow[]; status: string }) {
  const t0 = events[0] ? new Date(events[0].ts).getTime() : 0;
  return (
    <div>
      <div className="row between mb" style={{ marginBottom: 8 }}>
        <span className="section-title" style={{ margin: 0 }}>Backend progress</span>
        <span className="xs faint">
          {status === "live" ? "Live stream" : status === "polling" ? "Polling" : status === "waiting" ? "Waiting on data providers" : status}
        </span>
      </div>
      <ol className="timeline" aria-live="polite">
        {events.map((e) => (
          <li key={e.seq} className="fade-in">
            <span className="t">+{((new Date(e.ts).getTime() - t0) / 1000).toFixed(1)}s</span>
            <span className={`dot ${e.status === "completed" ? "pos" : e.status === "failed" ? "neg" : "acc"}`} style={{ marginTop: 6 }} />
            <span className={e.status === "failed" ? "" : "muted"} style={e.status === "failed" ? { color: "var(--neg)" } : undefined}>{e.message}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}
