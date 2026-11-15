import { useCallback, useMemo, useState } from "react";
import { EventCard, GlanceRow, MovesStrip, SetAside } from "./components/Feed";
import { MorningCallButton, MorningCallPlayer, useMorningCall } from "./components/MorningCall";
import { readAlerts, trips } from "./alerts";
import { Kicker, Sheet, WhyContext } from "./components/ui";
import { cleanHeadline } from "./components/ResultsCardView";
import { cr, day, dayTime, label, wall } from "./format";
import type { BriefEvent, BriefSnapshot } from "./types";

type WinKey = "24h" | "3d" | "7d" | "30d" | "custom";
const WINDOWS: { key: Exclude<WinKey, "custom">; hours: number; name: string }[] = [
  { key: "24h", hours: 24, name: "last 24 hours" },
  { key: "3d", hours: 72, name: "last 3 days" },
  { key: "7d", hours: 168, name: "last 7 days" },
  { key: "30d", hours: 720, name: "last 30 days" },
];
const H = 3600_000;

// Reading cost assumptions for the funnel's "time saved" (shown in the ladder sheet).
const MIN_PER_FILING = 3, MIN_PER_NEEDS = 1.5, MIN_PER_GLANCE = 0.25;

function fmtMinutes(m: number): string {
  if (m < 90) return `~${Math.max(0, Math.round(m))} min`;
  return `~${(m / 60).toFixed(m < 600 ? 1 : 0)} h`;
}

const PART_LABEL: Record<string, string> = {
  category: "Category", subtype: "Subtype", uncertain: "Unsure → shown", amount: "₹ vs revenue",
  numbers: "Size of the numbers", guidance: "Miss vs guidance", reaction: "Price / volume", thesis: "Your thesis",
};

export function Today({ brief }: { brief: BriefSnapshot }) {
  const [win, setWin] = useState<WinKey>("7d");
  const [custom, setCustom] = useState<{ from: string; to: string }>({ from: brief.window_from, to: brief.window_to });
  const [why, setWhy] = useState<BriefEvent | null>(null);
  const [ladder, setLadder] = useState(false);
  const call = useMorningCall();
  const [onAir, setOnAir] = useState(false);
  const ref = wall(brief.generated_at);

  const inRange = useCallback(
    (iso: string, k: WinKey) => {
      const t = wall(iso);
      if (k === "custom") return t >= wall(custom.from) && t < wall(custom.to) + 24 * H;
      const w = WINDOWS.find((x) => x.key === k)!;
      return t >= ref - w.hours * H && t <= ref;
    },
    [ref, custom],
  );

  // Never a blank first screen: if the chosen window has nothing worth showing, widen
  // to the next one and say so.
  const { shown, effective, widened } = useMemo(() => {
    const order: WinKey[] = win === "custom" ? ["custom"] : WINDOWS.map((w) => w.key).slice(WINDOWS.findIndex((w) => w.key === win));
    for (const k of order) {
      const evs = brief.events.filter((e) => inRange(e.occurred_at, k));
      if (evs.some((e) => e.tier !== "set_aside") || k === order[order.length - 1]) {
        return { shown: evs, effective: k, widened: k !== win };
      }
    }
    return { shown: [] as BriefEvent[], effective: win, widened: false };
  }, [brief.events, win, inRange]);

  const needs = shown.filter((e) => e.tier === "needs_you");
  const glance = shown.filter((e) => e.tier === "glance");
  const aside = shown.filter((e) => e.tier === "set_aside");
  const filings = shown.reduce((n, e) => n + e.filings.length, 0);
  const saved = filings * MIN_PER_FILING - needs.length * MIN_PER_NEEDS - glance.length * MIN_PER_GLANCE;
  const effName = effective === "custom" ? `${day(custom.from)} – ${day(custom.to)}` : WINDOWS.find((w) => w.key === effective)!.name;
  // your alert thresholds (Holdings → Alert settings) decide which moves make the strip
  const alerts = useMemo(readAlerts, []);
  const moves = brief.moves.filter((m) => inRange(`${m.session}T15:30:00`, effective) && trips(m.ret, m.vol_x, alerts));
  const latestFiling = brief.events.reduce((a, e) => (e.occurred_at > a ? e.occurred_at : a), "");

  const openEvent = (id: string) => {
    if (!brief.events.some((e) => e.id === id && inRange(e.occurred_at, effective))) setWin("30d");
    setTimeout(() => document.getElementById(`ev-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" }), 60);
  };

  return (
    <WhyContext.Provider value={setWhy}>
      {/* data as of: always visible */}
      <div className="sticky top-0 z-20 -mx-4 border-b hair bg-paper/95 px-4 py-1.5 backdrop-blur-sm">
        <p className="num truncate text-[10.5px] text-faint">
          Filings {dayTime(latestFiling)} · Prices {brief.prices_as_of ? day(brief.prices_as_of) : "n/a"}
        </p>
      </div>

      <header className="pt-5">
        <div className="flex items-baseline justify-between">
          <h1 className="text-[30px] font-semibold leading-none tracking-[-0.03em]">Today</h1>
          <span className="num text-[11px] text-faint">{brief.symbols.length} holdings</span>
        </div>
        <button onClick={() => setLadder(true)} className="mt-3 block text-left">
          <p className="num text-[12.5px] leading-relaxed">
            {filings} filings <span className="text-faint">→</span> {shown.length} events <span className="text-faint">→</span>{" "}
            <span className="whitespace-nowrap font-semibold text-signal">{needs.length} need you</span>
            <span className="whitespace-nowrap text-sub"> · {fmtMinutes(saved)} saved</span>
          </p>
          <p className="mt-0.5 text-[11px] text-faint underline decoration-rule underline-offset-[3px]">How FINN got here</p>
        </button>

        {call && call.chapters.length > 2 && !onAir && <MorningCallButton call={call} onPlay={() => setOnAir(true)} />}

        <div className="mt-4 flex gap-1.5" role="tablist" aria-label="Window">
          {[...WINDOWS.map((w) => w.key), "custom" as const].map((k) => (
            <button
              key={k}
              role="tab"
              aria-selected={win === k}
              onClick={() => setWin(k)}
              className={`num h-7 rounded-full border px-3 text-[11.5px] transition-colors duration-150 ease-quiet ${
                win === k ? "border-ink bg-ink text-paper" : "hair text-sub hover:border-ink/40 hover:text-ink"
              }`}
            >
              {k === "custom" ? "Custom" : k}
            </button>
          ))}
        </div>
        {win === "custom" && (
          <div className="mt-2 flex items-center gap-2 text-[12px]">
            <input type="date" value={custom.from} min={brief.window_from} max={custom.to} onChange={(e) => setCustom({ ...custom, from: e.target.value })}
              className="num rounded border hair bg-paper px-2 py-1 text-[12px]" />
            <span className="text-faint">to</span>
            <input type="date" value={custom.to} min={custom.from} max={brief.window_to} onChange={(e) => setCustom({ ...custom, to: e.target.value })}
              className="num rounded border hair bg-paper px-2 py-1 text-[12px]" />
          </div>
        )}
        {widened && (
          <p className="mt-2 text-[12px] text-sub">
            Quiet {win === "24h" ? "day" : "stretch"}, nothing needed you in the {WINDOWS.find((w) => w.key === win)?.name}. Showing the {effName}.
          </p>
        )}
      </header>

      {onAir && call && (
        <MorningCallPlayer call={call} stale={call.brief_generated_at !== brief.generated_at} onOpen={openEvent} onClose={() => setOnAir(false)} />
      )}

      <MovesStrip moves={moves} onOpen={openEvent} rule={`±${alerts.price}% or ${alerts.volume}× vol`} windowName={effName} />

      {/* needs you */}
      <section className="mt-6">
        <div className="flex items-baseline gap-2 border-b border-ink pb-1.5">
          <Kicker className="text-signal">Needs you</Kicker>
          <span className="num text-[11px] text-signal">{needs.length}</span>
        </div>
        {needs.length === 0 ? (
          <p className="py-4 text-[13px] text-sub">Nothing needs you in the {effName}.</p>
        ) : (
          <div className="divide-y hair">
            {needs.map((e) => (
              <div id={`ev-${e.id}`} key={e.id} className="scroll-mt-10">
                <EventCard event={e} />
              </div>
            ))}
          </div>
        )}
      </section>

      {/* worth a glance */}
      {glance.length > 0 && (
        <section className="mt-6">
          <div className="flex items-baseline gap-2 border-b border-ink pb-1.5">
            <Kicker className="text-ink">Worth a glance</Kicker>
            <span className="num text-[11px] text-sub">{glance.length}</span>
          </div>
          <ul className="border-b hair">
            {glance.map((e) => (
              <div id={`ev-${e.id}`} key={e.id} className="scroll-mt-10"><GlanceRow event={e} /></div>
            ))}
          </ul>
        </section>
      )}

      <SetAside events={aside} />

      {/* caught up */}
      <section className="mb-10 mt-12 border-t border-ink pt-6 text-center">
        <p className="text-[22px] font-semibold tracking-[-0.02em]">You’re caught up.</p>
        <p className="mt-1 text-[12.5px] text-sub">
          {needs.length + glance.length} things worth your time in the {effName}; {aside.length} set aside so you don’t have to.
        </p>
        <p className="num mt-3 text-[10.5px] text-faint">{brief.ladder.never_touched_model_pct >= 0.5 ? `${(brief.ladder.never_touched_model_pct * 100).toFixed(1)}% of filings never touched a model` : ""}</p>
      </section>

      {/* why it's here */}
      <Sheet
        open={why != null}
        onClose={() => setWhy(null)}
        title={
          why && (
            <>
              <Kicker>Why it’s here · {why.symbol}</Kicker>
              <p className="mt-1 line-clamp-2 text-[14px] font-semibold leading-snug">{(why.results_card && cleanHeadline(why.results_card)) ?? why.mgmt_card?.short ?? why.summary ?? why.title}</p>
            </>
          )
        }
      >
        {why && (
          <div className="pb-2">
            <div className="flex items-end gap-3 border-b hair pb-3">
              <span className="num text-[44px] font-medium leading-none tracking-[-0.03em]">{Math.round(why.score)}</span>
              <span className={`pb-1 text-[12px] font-semibold ${why.tier === "needs_you" ? "text-signal" : "text-sub"}`}>
                {why.tier === "needs_you" ? "Needs you (≥ 60)" : why.tier === "glance" ? "Worth a glance (≥ 20)" : "Set aside (< 20)"}
              </span>
            </div>
            <ul>
              {why.breakdown.map((p, i) => (
                <li key={i} className="flex gap-3 border-b hair py-2.5">
                  <span className={`num w-[38px] shrink-0 text-right text-[13px] ${p.points > 0 ? "text-ink" : p.points < 0 ? "text-sub" : "text-faint"}`}>
                    {p.points > 0 ? "+" : p.points < 0 ? "−" : ""}{Math.abs(p.points)}
                  </span>
                  <span className="min-w-0">
                    <span className="block text-[12.5px] font-semibold">{PART_LABEL[p.part] ?? p.part}</span>
                    <span className="block text-[12px] leading-snug text-sub">{p.note}</span>
                  </span>
                </li>
              ))}
            </ul>
            <p className="pt-3 text-[11.5px] leading-snug text-faint">
              Every point comes from a visible rule. No model produced this score. Shown capped at 100.
            </p>
          </div>
        )}
      </Sheet>

      {/* how FINN got here */}
      <Sheet open={ladder} onClose={() => setLadder(false)} title={<><Kicker>How FINN got here</Kicker><p className="mt-1 text-[14px] font-semibold">{effName}</p></>}>
        <div className="pb-2 text-[12.5px]">
          <dl className="divide-y hair border-y hair">
            {[
              ["Filings read", `${filings}`],
              ["Folded into events", `${shown.length}`],
              ["Need you / glance / set aside", `${needs.length} / ${glance.length} / ${aside.length}`],
              ["Time saved", `${fmtMinutes(saved)} (${MIN_PER_FILING} min per filing vs ${MIN_PER_NEEDS} min per card, ${MIN_PER_GLANCE * 60}s per line)`],
            ].map(([k, v]) => (
              <div key={k} className="flex justify-between gap-4 py-2"><dt className="text-sub">{k}</dt><dd className="num text-right">{v}</dd></div>
            ))}
          </dl>
          <Kicker className="mt-4">Classification ladder · whole 30-day snapshot</Kicker>
          <dl className="mt-1 divide-y hair border-y hair">
            {Object.entries(brief.ladder.by_step).map(([k, v]) => (
              <div key={k} className="flex justify-between py-1.5"><dt className="text-sub">{label(k).replace("+", " + ")}</dt><dd className="num">{v}</dd></div>
            ))}
            <div className="flex justify-between py-1.5"><dt className="text-sub">PDFs read</dt><dd className="num">{brief.ladder.pdfs_read}</dd></div>
            <div className="flex justify-between py-1.5"><dt className="text-sub">Never touched a model</dt><dd className="num">{brief.ladder.never_touched_model}/{brief.ladder.filings}</dd></div>
            {brief.llm_usage && <div className="flex justify-between gap-4 py-1.5"><dt className="text-sub">LLM this build</dt><dd className="num text-right">{brief.llm_usage}</dd></div>}
          </dl>
          <p className="pt-3 text-[11.5px] text-faint">Rules first; a model only sees what rules can’t place, and never does arithmetic. Amounts in ₹ crore{brief.cards.results ? ` · ${brief.cards.results} results cards, ${brief.cards.mgmt} management cards` : ""}.</p>
          <span className="hidden">{cr(0)}</span>
        </div>
      </Sheet>
      {onAir && <div aria-hidden className="h-36" />}
    </WhyContext.Provider>
  );
}
