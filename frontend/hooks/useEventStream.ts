"use client";
import { useEffect, useRef, useState } from "react";
import { api, EventRow } from "@/lib/api";

/**
 * Subscribes to real backend progress events (SSE). Reconnect-safe: EventSource resends Last-Event-ID;
 * if streaming is unavailable (e.g. a proxy buffers it) the hook falls back to polling with ?after=seq.
 */
export function useEventStream(analysisId: string | null, active: boolean) {
  const [events, setEvents] = useState<EventRow[]>([]);
  const [status, setStatus] = useState<string>("connecting");
  const lastSeq = useRef(0);

  useEffect(() => {
    if (!analysisId) return;
    let closed = false;
    let es: EventSource | null = null;
    let poll: ReturnType<typeof setInterval> | null = null;
    const push = (rows: EventRow[]) => {
      const fresh = rows.filter((r) => r.seq > lastSeq.current);
      if (!fresh.length) return;
      lastSeq.current = fresh[fresh.length - 1].seq;
      setEvents((prev) => [...prev, ...fresh]);
    };
    const startPolling = () => {
      if (poll) return;
      setStatus("polling");
      const tick = async () => {
        try {
          const r = await api<{ status: string; events: EventRow[] }>(
            `/analysis/${analysisId}/events?stream=false&after=${lastSeq.current}`,
          );
          push(r.events);
          if (r.status === "completed" || r.status === "failed") {
            setStatus(r.status);
            if (poll) clearInterval(poll);
          }
        } catch {
          setStatus("reconnecting");
        }
      };
      tick();
      poll = setInterval(tick, 1500);
    };
    // initial backlog (also covers page refresh mid-run)
    api<{ status: string; events: EventRow[] }>(`/analysis/${analysisId}/events?stream=false`)
      .then((r) => {
        if (closed) return;
        push(r.events);
        if (r.status === "completed" || r.status === "failed") {
          setStatus(r.status);
          return;
        }
        if (!active) return;
        if (typeof EventSource === "undefined") return startPolling();
        es = new EventSource(`/api/analysis/${analysisId}/events?after=${lastSeq.current}`);
        setStatus("live");
        let gotAny = false;
        const watchdog = setTimeout(() => {
          if (!gotAny && !closed) {
            es?.close();
            startPolling();
          }
        }, 8000);
        es.addEventListener("progress", (e) => {
          gotAny = true;
          push([JSON.parse((e as MessageEvent).data)]);
        });
        es.addEventListener("waiting", () => {
          gotAny = true;
          setStatus("waiting");
        });
        es.addEventListener("end", (e) => {
          gotAny = true;
          clearTimeout(watchdog);
          setStatus(JSON.parse((e as MessageEvent).data).status);
          es?.close();
        });
        es.onerror = () => {
          if (closed) return;
          es?.close();
          startPolling();
        };
      })
      .catch(() => startPolling());
    return () => {
      closed = true;
      es?.close();
      if (poll) clearInterval(poll);
    };
  }, [analysisId, active]);

  return { events, status };
}
