"use client";
import { useCallback, useEffect, useRef, useState } from "react";

/* eslint-disable @typescript-eslint/no-explicit-any */

/** Text-to-speech via the browser's SpeechSynthesis. Never autoplays: speak() is only called from user actions. */
export function useSpeechSynthesis() {
  const supported = typeof window !== "undefined" && "speechSynthesis" in window;
  const [speaking, setSpeaking] = useState(false);
  const [paused, setPaused] = useState(false);
  const [index, setIndex] = useState<number>(-1);
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  const queue = useRef<string[]>([]);
  const opts = useRef<{ rate: number; voice?: string }>({ rate: 1 });

  useEffect(() => {
    if (!supported) return;
    const load = () => setVoices(window.speechSynthesis.getVoices().filter((v) => v.lang.startsWith("en")));
    load();
    window.speechSynthesis.addEventListener?.("voiceschanged", load);
    return () => {
      window.speechSynthesis.removeEventListener?.("voiceschanged", load);
      window.speechSynthesis.cancel();
    };
  }, [supported]);

  const playFrom = useCallback(
    (i: number) => {
      if (!supported || i >= queue.current.length) {
        setSpeaking(false);
        setIndex(-1);
        return;
      }
      const u = new SpeechSynthesisUtterance(queue.current[i]);
      u.rate = opts.current.rate;
      const v = window.speechSynthesis.getVoices().find((x) => x.name === opts.current.voice);
      if (v) u.voice = v;
      u.onend = () => playFrom(i + 1);
      u.onerror = () => {
        setSpeaking(false);
        setIndex(-1);
      };
      setIndex(i);
      window.speechSynthesis.speak(u);
    },
    [supported],
  );

  const speak = useCallback(
    (parts: string[], rate = 1, voice?: string) => {
      if (!supported) return;
      window.speechSynthesis.cancel();
      queue.current = parts;
      opts.current = { rate, voice };
      setSpeaking(true);
      setPaused(false);
      playFrom(0);
    },
    [playFrom, supported],
  );

  const stop = useCallback(() => {
    if (!supported) return;
    queue.current = [];
    window.speechSynthesis.cancel();
    setSpeaking(false);
    setPaused(false);
    setIndex(-1);
  }, [supported]);

  const pause = useCallback(() => {
    if (!supported) return;
    window.speechSynthesis.pause();
    setPaused(true);
  }, [supported]);

  const resume = useCallback(() => {
    if (!supported) return;
    window.speechSynthesis.resume();
    setPaused(false);
  }, [supported]);

  return { supported, speaking, paused, index, voices, speak, stop, pause, resume };
}

type RecState = "idle" | "listening" | "processing" | "error" | "unsupported";

/** Speech-to-text via the browser's SpeechRecognition; asks for the microphone only when the user starts it. */
export function useSpeechRecognition(onFinal: (text: string) => void) {
  const Ctor: any = typeof window !== "undefined" ? (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition : null;
  const [state, setState] = useState<RecState>(Ctor ? "idle" : "unsupported");
  const [interim, setInterim] = useState("");
  const [error, setError] = useState<string | null>(null);
  const rec = useRef<any>(null);

  const start = useCallback(() => {
    if (!Ctor) {
      setState("unsupported");
      return;
    }
    setError(null);
    setInterim("");
    const r = new Ctor();
    r.lang = "en-US";
    r.interimResults = true;
    r.continuous = false;
    r.onresult = (e: any) => {
      let text = "";
      let final = false;
      for (let i = e.resultIndex; i < e.results.length; i++) {
        text += e.results[i][0].transcript;
        if (e.results[i].isFinal) final = true;
      }
      setInterim(text);
      if (final) {
        setState("processing");
        onFinal(text.trim());
      }
    };
    r.onerror = (e: any) => {
      setState("error");
      setError(
        e.error === "not-allowed" || e.error === "service-not-allowed"
          ? "Microphone permission was denied. You can type your question instead."
          : e.error === "no-speech"
            ? "No speech detected. Try again or type your question."
            : `Speech recognition failed (${e.error}). You can type your question instead.`,
      );
    };
    r.onend = () => setState((s) => (s === "listening" || s === "processing" ? "idle" : s));
    rec.current = r;
    setState("listening");
    try {
      r.start();
    } catch {
      setState("error");
      setError("Could not start speech recognition.");
    }
  }, [Ctor, onFinal]);

  const stop = useCallback(() => {
    rec.current?.stop();
    setState((s) => (s === "listening" ? "idle" : s));
  }, []);

  return { state, interim, error, start, stop, supported: !!Ctor };
}
