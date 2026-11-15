import { useEffect, useMemo, useRef, useState } from "react";
import type { MorningCall } from "../types";
import { signalOpen } from "./openSignal";

/** The Morning Call: a precomputed 60-90s script (written once per brief, numbers checked
 *  in Python), read by the browser's own speechSynthesis. One utterance per sentence, so
 *  the current sentence can be highlighted and playback can jump to any chapter. */

function mmss(s: number) {
  const r = Math.max(0, Math.round(s));
  return `${Math.floor(r / 60)}:${String(r % 60).padStart(2, "0")}`;
}

/** What the voice reads (the screen shows the original). */
function say(s: string) {
  return s
    .replace(/₹\s?/g, "rupees ")
    .replace(/\bFY(\d\d)\b/g, "F Y $1")
    .replace(/\bQ([1-4])\b/g, "Q $1")
    .replace(/\bNIFTY\b/g, "Nifty");
}

function pickVoice(): SpeechSynthesisVoice | null {
  const vs = window.speechSynthesis?.getVoices() ?? [];
  for (const lang of ["en-IN", "en-GB", "en-US"]) {
    const v = vs.find((x) => x.lang.replace("_", "-") === lang);
    if (v) return v;
  }
  return vs.find((x) => x.lang.startsWith("en")) ?? null;
}

export function useMorningCall() {
  const [call, setCall] = useState<MorningCall | null>(null);
  useEffect(() => {
    fetch(`${import.meta.env.BASE_URL}data/morning.json`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setCall(d))
      .catch(() => setCall(null));
  }, []);
  return call;
}

export function MorningCallButton({ call, onPlay }: { call: MorningCall; onPlay: () => void }) {
  return (
    <button onClick={onPlay} className="mt-4 flex w-full items-center gap-3 rounded-[4px] border border-ink px-3 py-2.5 text-left hover:bg-wash">
      <span aria-hidden className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-ink text-paper">
        <svg width="10" height="12" viewBox="0 0 10 12"><path d="M0 0 L10 6 L0 12 Z" fill="currentColor" /></svg>
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-[13.5px] font-semibold leading-tight">Morning call</span>
        <span className="block truncate text-[11.5px] text-sub">{call.chapters.length - 2} stories that need you, read aloud</span>
      </span>
      <span className="num shrink-0 text-[12px] text-sub">{mmss(call.est_seconds)}</span>
    </button>
  );
}

export function MorningCallPlayer({ call, stale, onOpen, onClose }: {
  call: MorningCall; stale: boolean; onOpen: (eventId: string) => void; onClose: () => void;
}) {
  const flat = useMemo(() => call.chapters.flatMap((c, ci) => c.sentences.map((s, si) => ({ ci, si, s }))), [call]);
  const [idx, setIdx] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState(1);
  const [expanded, setExpanded] = useState(false);
  const token = useRef(0);
  const voice = useRef<SpeechSynthesisVoice | null>(null);
  const supported = typeof window !== "undefined" && "speechSynthesis" in window;

  useEffect(() => {
    if (!supported) return;
    voice.current = pickVoice();
    const h = () => { voice.current = pickVoice(); };
    window.speechSynthesis.addEventListener("voiceschanged", h);
    return () => {
      window.speechSynthesis.removeEventListener("voiceschanged", h);
      token.current++;
      window.speechSynthesis.cancel();
    };
  }, [supported]);

  const speakFrom = (start: number, r = rate) => {
    if (!supported) { setIdx(start); return; }
    const synth = window.speechSynthesis;
    const my = ++token.current;
    synth.cancel();
    const step = (k: number) => {
      if (my !== token.current) return;
      if (k >= flat.length) { setPlaying(false); setIdx(0); return; }
      setIdx(k);
      const u = new SpeechSynthesisUtterance(say(flat[k].s));
      u.rate = r;
      if (voice.current) { u.voice = voice.current; u.lang = voice.current.lang; } else u.lang = "en-IN";
      u.onend = () => step(k + 1);
      u.onerror = (e) => { if (e.error !== "interrupted" && e.error !== "canceled") step(k + 1); };
      synth.speak(u);
    };
    setPlaying(true);
    step(start);
  };
  const pause = () => { token.current++; if (supported) window.speechSynthesis.cancel(); setPlaying(false); };

  // start on mount: the user just pressed play
  useEffect(() => { speakFrom(0); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleRate = () => {
    const r = rate === 1 ? 1.25 : 1;
    setRate(r);
    if (playing) speakFrom(idx, r);   // restart the current sentence at the new speed
  };
  const chapterStart = (ci: number) => flat.findIndex((f) => f.ci === ci);
  const jump = (ci: number) => {
    const ch = call.chapters[ci];
    speakFrom(chapterStart(ci));
    if (ch.event_id) { onOpen(ch.event_id); signalOpen(ch.event_id); setExpanded(false); }
  };

  const cur = flat[idx];
  const chap = call.chapters[cur?.ci ?? 0];
  const elapsed = flat.slice(0, idx).reduce((n, f) => n + f.s.split(" ").length, 0);
  const remaining = (call.words - elapsed) / 150 * 60 / rate;

  return (
    <div className="fixed bottom-[calc(3rem+env(safe-area-inset-bottom))] left-1/2 z-30 w-full max-w-[440px] -translate-x-1/2 border-t border-ink bg-paper">
      {expanded && (
        <div className="max-h-[52vh] overflow-y-auto border-b hair px-4 pb-3 pt-2">
          {call.chapters.map((c, ci) => (
            <div key={ci} className="border-t hair py-2 first:border-t-0">
              <button onClick={() => jump(ci)} className="flex w-full items-baseline justify-between gap-2 text-left">
                <span className={`kicker ${cur?.ci === ci ? "text-ink" : "text-sub"}`}>{c.title}</span>
                {c.event_id && <span className="shrink-0 text-[11px] text-sub underline decoration-rule underline-offset-[3px]">Open card</span>}
              </button>
              <p className="mt-1 text-[13px] leading-relaxed">
                {c.sentences.map((s, si) => {
                  const on = cur?.ci === ci && cur?.si === si;
                  return <span key={si} className={on ? "bg-ink text-paper" : "text-sub"}>{s} </span>;
                })}
              </p>
            </div>
          ))}
          <p className="mt-2 text-[10.5px] leading-snug text-faint">
            Written by {call.by} from the brief's own figures; any sentence with a number not in its card's facts is dropped
            {call.dropped.length ? ` (${call.dropped.length} dropped)` : " (none dropped)"}. Read by your browser's voice.
            {stale && " Script was written for an earlier brief."}
          </p>
        </div>
      )}
      <div className="px-4 py-2">
        <button onClick={() => (chap?.event_id ? jump(cur.ci) : setExpanded((e) => !e))} className="block w-full text-left">
          <span className="kicker text-sub">
            <span className="num">{(cur?.ci ?? 0) + 1}/{call.chapters.length}</span> · {chap?.title}
          </span>
          <span className="mt-0.5 line-clamp-2 block text-[13px] leading-snug">{cur?.s}</span>
        </button>
        <div className="mt-2 flex items-center gap-2">
          <button onClick={() => (playing ? pause() : speakFrom(idx))} aria-label={playing ? "Pause" : "Play"}
            className="grid h-8 w-8 place-items-center rounded-full bg-ink text-paper">
            {playing
              ? <svg width="10" height="12" viewBox="0 0 10 12"><rect width="3.5" height="12" fill="currentColor" /><rect x="6.5" width="3.5" height="12" fill="currentColor" /></svg>
              : <svg width="10" height="12" viewBox="0 0 10 12"><path d="M0 0 L10 6 L0 12 Z" fill="currentColor" /></svg>}
          </button>
          <button onClick={toggleRate} aria-pressed={rate !== 1}
            className={`num h-8 rounded-full border px-3 text-[12px] ${rate !== 1 ? "border-ink bg-ink text-paper" : "hair text-sub"}`}>
            1.25×
          </button>
          <button onClick={() => setExpanded((e) => !e)} aria-expanded={expanded} className="h-8 rounded-full border hair px-3 text-[12px] text-sub">
            {expanded ? "Hide script" : "Chapters"}
          </button>
          <span className="num ml-auto text-[11px] text-faint">{mmss(remaining)}</span>
          <button onClick={() => { pause(); onClose(); }} aria-label="Close morning call" className="grid h-8 w-8 place-items-center text-sub hover:text-ink">✕</button>
        </div>
        {!supported && <p className="mt-1 text-[11px] text-sub">This browser has no speech voice; the script is shown instead.</p>}
      </div>
    </div>
  );
}
