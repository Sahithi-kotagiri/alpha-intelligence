"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useSpeechRecognition, useSpeechSynthesis } from "@/hooks/useSpeech";

function prefs() {
  try {
    return JSON.parse(localStorage.getItem("ii_voice") || "{}");
  } catch {
    return {};
  }
}

/** "Listen to briefing": transcript always visible; audio only starts on click; stop/pause/speed controls. */
export function BriefingPlayer({ analysisId, agent }: { analysisId: string; agent?: string }) {
  const [brief, setBrief] = useState<{ sections?: { title: string; text: string }[]; text: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [rate, setRate] = useState<number>(prefs().rate || 1);
  const [serverAudio, setServerAudio] = useState<string | null>(null);
  const [serverAvailable, setServerAvailable] = useState(false);
  const audioRef = useRef<HTMLAudioElement>(null);
  const tts = useSpeechSynthesis();

  useEffect(() => {
    api(`/analysis/${analysisId}/briefing${agent ? `?agent=${agent}` : ""}`).then(setBrief).catch((e) => setErr(e.message));
    api("/voice/config").then((c: any) => setServerAvailable(!!c.server_available)).catch(() => {});
    return () => tts.stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [analysisId, agent]);

  useEffect(() => {
    if (audioRef.current) audioRef.current.playbackRate = rate;
  }, [rate, serverAudio]);

  const parts = brief?.sections ? brief.sections.map((s) => `${s.title}. ${s.text}`) : brief ? [brief.text] : [];
  const usesServer = serverAvailable && prefs().provider === "server";

  const play = async () => {
    setErr(null);
    if (usesServer) {
      try {
        const fd = new FormData();
        fd.set("analysis_id", analysisId);
        fd.set("kind", agent ? `agent:${agent}` : "briefing");
        fd.set("speed", String(rate));
        const blob: Blob = await api("/voice/speech", { method: "POST", body: fd });
        setServerAudio(URL.createObjectURL(blob));
        setTimeout(() => audioRef.current?.play(), 0);
      } catch (e: any) {
        setErr(`Server speech failed (${e.message}); using browser speech instead.`);
        tts.speak(parts, rate, prefs().voice);
      }
      return;
    }
    if (!tts.supported) {
      setErr("Speech playback is not supported in this browser. The transcript is shown below.");
      return;
    }
    tts.speak(parts, rate, prefs().voice);
  };
  const stop = () => {
    tts.stop();
    audioRef.current?.pause();
    if (audioRef.current) audioRef.current.currentTime = 0;
  };

  return (
    <div className="stack">
      <div className="row">
        {!tts.speaking ? (
          <button onClick={play} disabled={!brief} aria-label={agent ? "Listen to agent summary" : "Listen to briefing"}>▶ {agent ? "Listen to summary" : "Listen to briefing"}</button>
        ) : tts.paused ? (
          <button onClick={tts.resume}>Resume</button>
        ) : (
          <button onClick={tts.pause}>Pause</button>
        )}
        <button onClick={stop} disabled={!tts.speaking && !serverAudio} aria-label="Stop audio">■ Stop</button>
        <label className="row small muted" style={{ gap: 6 }}>
          Speed
          <select value={rate} onChange={(e) => setRate(Number(e.target.value))} aria-label="Playback speed">
            {[0.75, 1, 1.25, 1.5, 1.75].map((r) => <option key={r} value={r}>{r}×</option>)}
          </select>
        </label>
        {tts.speaking && <span className="xs faint" aria-live="polite">Speaking section {tts.index + 1} of {parts.length}</span>}
      </div>
      {serverAudio && <audio ref={audioRef} src={serverAudio} controls style={{ width: "100%" }} />}
      {err && <div className="callout warn small">{err}</div>}
      {brief?.sections ? (
        <div className="small" aria-label="Briefing transcript">
          {brief.sections.map((s, i) => (
            <p key={s.title} style={{ padding: "4px 8px", borderRadius: 4, background: tts.index === i ? "var(--accent-soft)" : undefined }}>
              <strong>{s.title}.</strong> {s.text}
            </p>
          ))}
        </div>
      ) : brief ? <p className="small">{brief.text}</p> : null}
      <div className="xs faint">Generated from the saved structured report; no new analysis is performed. Audio is never recorded or stored.</div>
    </div>
  );
}

/** Text box with optional voice dictation. The transcript is editable before sending. */
export function AskBox({ onAsk, busy }: { onAsk: (q: string, mode: "text" | "voice") => void; busy: boolean }) {
  const [q, setQ] = useState("");
  const [fromVoice, setFromVoice] = useState(false);
  const onFinal = useCallback((t: string) => {
    setQ(t);
    setFromVoice(true);
  }, []);
  const sr = useSpeechRecognition(onFinal);
  const submit = () => {
    if (q.trim().length < 2) return;
    onAsk(q.trim(), fromVoice ? "voice" : "text");
    setQ("");
    setFromVoice(false);
  };
  return (
    <div className="stack">
      <textarea rows={2} value={sr.state === "listening" ? sr.interim || q : q} onChange={(e) => setQ(e.target.value)}
        placeholder="Ask about this analysis, e.g. “What is the strongest reason not to buy?”" aria-label="Question"
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }} />
      <div className="row between">
        <div className="row">
          {sr.supported ? (
            sr.state === "listening" ? (
              <button onClick={sr.stop} aria-label="Stop listening"><span className="pulse-dot" /> Listening… stop</button>
            ) : (
              <button onClick={sr.start} aria-label="Ask by voice">🎙 Speak</button>
            )
          ) : (
            <span className="xs faint">Voice input isn’t supported in this browser; type your question.</span>
          )}
          {fromVoice && <span className="xs faint">Transcribed — edit before sending if needed.</span>}
        </div>
        <button className="btn-primary" onClick={submit} disabled={busy || q.trim().length < 2}>{busy ? "Answering…" : "Ask"}</button>
      </div>
      {sr.error && <div className="callout warn small">{sr.error}</div>}
    </div>
  );
}
