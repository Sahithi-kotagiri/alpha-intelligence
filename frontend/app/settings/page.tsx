"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import { useSpeechSynthesis } from "@/hooks/useSpeech";
import { ago, dt } from "@/lib/format";
import { Badge, Card, ErrorBox, Loading } from "@/components/ui";

export default function Settings() {
  const s = useApi<any>("/settings");
  const src = useApi<any>("/data-sources");
  const uni = useApi<any>("/universe");
  const tts = useSpeechSynthesis();
  const [voice, setVoice] = useState<any>({ enabled: true, provider: "browser", voice: "", rate: 1 });
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [theme, setTheme] = useState("system");

  useEffect(() => {
    try {
      setVoice({ enabled: true, provider: "browser", voice: "", rate: 1, ...JSON.parse(localStorage.getItem("ii_voice") || "{}") });
      setTheme(localStorage.getItem("ii_theme") || "system");
    } catch { /* storage unavailable */ }
  }, []);

  const saveVoice = async () => {
    try { localStorage.setItem("ii_voice", JSON.stringify(voice)); } catch { /* ignore */ }
    await api("/settings", { method: "PUT", json: { voice_enabled: voice.enabled, voice_provider: voice.provider, voice_name: voice.voice || null, voice_rate: voice.rate } }).catch(() => {});
    setMsg("Voice preferences saved.");
  };
  const applyTheme = (t: string) => {
    setTheme(t);
    try { localStorage.setItem("ii_theme", t); } catch { /* ignore */ }
    if (t === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", t);
  };
  const refreshUniverse = async () => {
    setErr(null);
    try { const r: any = await api("/universe/refresh", { method: "POST" }); setMsg(`Constituents refreshed: ${r.count} (${r.added.length} added, ${r.removed.length} removed).`); uni.reload(); }
    catch (e: any) { setErr(e.message); }
  };
  const deleteAll = async () => {
    if (!window.confirm("Delete all of your analyses, theses, portfolio, watchlist, alerts and messages? This cannot be undone.")) return;
    await api("/user/data", { method: "DELETE" });
    setMsg("All your data was deleted.");
  };
  const sv = s.data?.server;
  return (
    <>
      <div className="page-head"><div><h1>Settings & data sources</h1><p>Provider status, voice preferences, data freshness and data controls.</p></div></div>
      {msg && <div className="callout pos small mb">{msg}</div>}
      {err && <div className="mb"><ErrorBox error={err} /></div>}
      {!s.data && <Loading />}
      {sv && (
        <div className="grid g2">
          <Card title="Analysis providers">
            <dl className="kv small">
              <dt>Market data</dt><dd>{sv.market_data_provider} <Badge tone="warn">unofficial, personal use</Badge></dd>
              <dt>SEC EDGAR</dt><dd>{sv.sec_configured ? <Badge tone="pos">configured</Badge> : <Badge tone="neg">SEC_USER_AGENT missing</Badge>}</dd>
              <dt>LLM</dt><dd>{sv.llm_configured ? <><Badge tone="pos">{sv.llm_provider}</Badge> {sv.llm_model}</> : <Badge tone="warn">not configured: rule-based mode</Badge>}</dd>
              <dt>Server voice</dt><dd>{sv.voice_configured ? <Badge tone="pos">{sv.voice_provider}</Badge> : <Badge>browser speech only</Badge>}</dd>
              <dt>Authentication</dt><dd>{sv.auth_mode === "local" ? <Badge tone="warn">local single-user</Badge> : <Badge tone="pos">token</Badge>}</dd>
            </dl>
            {!sv.llm_configured && <p className="xs faint mt-s">To enable LLM interpretation set LLM_PROVIDER=anthropic (with ANTHROPIC_API_KEY) or LLM_PROVIDER=openai_compatible (free: Ollama, Gemini, Groq, OpenRouter; set LLM_BASE_URL and LLM_MODEL) in .env and restart the backend.</p>}
          </Card>
          <Card title="Voice" sub="Microphone is requested only when you press Speak. Audio is never stored.">
            <label className="check mb"><input type="checkbox" checked={voice.enabled} onChange={(e) => setVoice({ ...voice, enabled: e.target.checked })} /> Voice features on</label>
            <div className="grid g2">
              <label className="field">Speech engine
                <select value={voice.provider} onChange={(e) => setVoice({ ...voice, provider: e.target.value })}>
                  <option value="browser">Browser (on-device)</option>
                  <option value="server" disabled={!sv.voice_configured}>Server provider{sv.voice_configured ? "" : " (not configured)"}</option>
                </select></label>
              <label className="field">Voice
                <select value={voice.voice} onChange={(e) => setVoice({ ...voice, voice: e.target.value })} disabled={!tts.supported}>
                  <option value="">Default</option>{tts.voices.map((v) => <option key={v.name} value={v.name}>{v.name}</option>)}
                </select></label>
              <label className="field">Default speed
                <select value={voice.rate} onChange={(e) => setVoice({ ...voice, rate: Number(e.target.value) })}>
                  {[0.75, 1, 1.25, 1.5].map((r) => <option key={r} value={r}>{r}×</option>)}
                </select></label>
            </div>
            <div className="row mt">
              <button onClick={saveVoice}>Save voice settings</button>
              <button className="btn-ghost" onClick={() => (tts.speaking ? tts.stop() : tts.speak(["This is the research briefing voice."], voice.rate, voice.voice))} disabled={!tts.supported}>{tts.speaking ? "Stop" : "Test voice"}</button>
            </div>
            {!tts.supported && <p className="xs faint mt-s">Speech synthesis is not available in this browser; transcripts remain available.</p>}
          </Card>
          <Card title="Appearance">
            <div className="seg">{["system", "dark", "light"].map((t) => <button key={t} className={theme === t ? "on" : ""} onClick={() => applyTheme(t)}>{t}</button>)}</div>
            <p className="xs faint mt-s">Motion is reduced automatically when your system requests it.</p>
          </Card>
          <Card title="S&P 500 constituents" actions={<button className="btn-sm" onClick={refreshUniverse}>Refresh now</button>}>
            {uni.data && <p className="small">{uni.data.count} constituents · source <a href={uni.data.source_url} target="_blank" rel="noreferrer noopener">{uni.data.source}</a> · retrieved {ago(uni.data.retrieved_at)}</p>}
            {uni.data?.changes?.length > 0 && <ul className="clean small">{uni.data.changes.slice(0, 10).map((c: any) => <li key={c.ticker + c.detected_at}>{c.ticker} {c.change} ({dt(c.detected_at)})</li>)}</ul>}
            <p className="xs faint">The official list is licensed by S&P Dow Jones Indices; this public table is cross-referenced with SEC CIKs and refreshed weekly.</p>
          </Card>
          <Card title="Data sources" sub="Health from real retrievals">
            <table><thead><tr><th>Source</th><th>Last success</th><th>Last failure</th></tr></thead>
              <tbody>{(src.data?.sources || []).map((x: any) => <tr key={x.name}><td className="small">{x.name}<div className="xs faint">{x.licence_note}</div></td>
                <td className="small">{ago(x.last_success_at)}</td><td className="small">{x.last_failure_at ? <span title={x.last_error}>{ago(x.last_failure_at)}</span> : "—"}</td></tr>)}</tbody></table>
          </Card>
          <Card title="Your data">
            <p className="small">Portfolios, watchlists and analyses are stored only for your account. Raw audio is never stored.</p>
            <button onClick={deleteAll} style={{ borderColor: "var(--neg)", color: "var(--neg)" }}>Delete all my data</button>
          </Card>
        </div>
      )}
    </>
  );
}
