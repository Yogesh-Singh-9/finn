import { useCallback, useState } from "react";
import { useOpenSignal } from "./openSignal";
import { cr, day, dayTime, label, pct, sigma, tone } from "../format";
import type { BriefEvent, Move } from "../types";
import { MgmtCardView } from "./MgmtCardView";
import { ResultsCardView, cleanHeadline } from "./ResultsCardView";
import { Delta, FilingLink, Kicker, NeedsDot, SourceChip, Spark, WhyButton } from "./ui";

export const CATEGORY: Record<string, string> = {
  results: "Results", "M&A": "M&A", fund_raise: "Fund raise", management_change: "Management",
  litigation: "Litigation / regulatory", insider_trading: "Insider trading", earnings_call: "Earnings call",
  investor_meetings: "Investor meetings", credit_rating: "Credit rating", dividend: "Dividend",
  bonus_split: "Bonus / split", capex: "Capex", press_release: "Press release", postal_ballot: "Postal ballot",
  agm_egm: "AGM / EGM", pledging: "Pledging", unclassified: "Routine",
};

export function headlineOf(e: BriefEvent): string {
  if (e.results_card) return cleanHeadline(e.results_card);
  if (e.mgmt_card) return e.mgmt_card.short ?? e.mgmt_card.headline;
  return e.summary ?? e.title;
}

export function GenericCard({ event }: { event: BriefEvent }) {
  const latest = [...event.filings].sort((a, b) => b.filed_at.localeCompare(a.filed_at))[0];
  return (
    <article className="py-5">
      <div className="flex items-center gap-2">
        {event.tier === "needs_you" && <NeedsDot />}
        <Kicker className="text-ink">{event.symbol}</Kicker>
        <Kicker>· {CATEGORY[event.category] ?? label(event.category)}{event.subtype && event.subtype !== "uncertain" ? ` / ${label(event.subtype)}` : ""}</Kicker>
        <span className="num ml-auto text-[11px] text-faint">{day(event.occurred_at)}</span>
      </div>
      {event.amount_cr != null && (
        <div className="mt-3 flex items-end gap-2">
          <span className="num text-[40px] font-medium leading-[0.95] tracking-[-0.03em]">₹{cr(event.amount_cr)}</span>
          <span className="pb-1 text-[12px] text-sub">crore</span>
        </div>
      )}
      <h3 className="mt-2 text-[17px] font-semibold leading-snug tracking-[-0.01em]">
        {event.summary ?? event.title}
        {event.summary && <SourceChip label="filing" quote={event.title} href={latest?.url} note={event.summary_by ? `Summary: ${event.summary_by}; numbers checked against the filing.` : null} />}
      </h3>
      {event.filings.length > 1 && (
        <p className="mt-1 text-[12px] text-sub">{event.filings.length} filings, {day(event.filings[event.filings.length - 1].filed_at)} → {day(event.filings[0].filed_at)}</p>
      )}
      <div className="mt-3 flex items-center gap-3">
        <FilingLink href={latest?.url} />
        <span className="ml-auto"><WhyButton event={event} /></span>
      </div>
    </article>
  );
}

/** "Challenges your thesis: driver: reason". Only when the event touches a driver. */
export function ThesisLine({ event }: { event: BriefEvent }) {
  const t = event.thesis_tag;
  if (!t || t.stance === "neutral" || !t.driver_text) return null;
  const ch = t.stance === "challenges";
  return (
    <p className="mt-1.5 text-[12px] leading-snug">
      <span className={`kicker ${ch ? "text-signal" : "text-sub"}`}>{ch ? "Challenges your thesis" : "Supports your thesis"}</span>{" "}
      <span className="font-medium">{t.driver_text}</span>
      {t.reason && <span className="text-sub">: {t.reason}</span>}
    </p>
  );
}

export function EventCard({ event }: { event: BriefEvent }) {
  const body = event.results_card ? <ResultsCardView event={event} card={event.results_card} />
    : event.mgmt_card ? <MgmtCardView event={event} card={event.mgmt_card} />
    : <GenericCard event={event} />;
  if (!event.thesis_tag || event.thesis_tag.stance === "neutral") return body;
  return <div>{body}<div className="-mt-2 pb-3"><ThesisLine event={event} /></div></div>;
}

/** Worth a glance: one dense line; tap to open the full card in place. */
export function GlanceRow({ event }: { event: BriefEvent }) {
  const [open, setOpen] = useState(false);
  useOpenSignal(event.id, useCallback(() => setOpen(true), []));
  return (
    <li className="border-t hair">
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open} className="flex w-full items-baseline gap-2.5 py-[9px] text-left">
        <span className="num w-[44px] shrink-0 text-[11px] text-faint">{day(event.occurred_at)}</span>
        <span className="num w-[78px] shrink-0 truncate text-[11.5px] font-medium">{event.symbol}</span>
        <span className={`min-w-0 flex-1 text-[13px] leading-snug ${open ? "" : "truncate"}`}>{headlineOf(event)}</span>
        <span className="num shrink-0 text-[11px] text-faint">{Math.round(event.score)}</span>
      </button>
      {open && <div className="-mt-3 border-t border-dashed hair"><EventCard event={event} /></div>}
    </li>
  );
}

const SET_ASIDE_LABEL: Record<string, [string, string]> = {
  "insider_trading": ["trading-window notice", "trading-window notices"],
  "unclassified/esop": ["ESOP allotment", "ESOP allotments"],
  "unclassified/newspaper_ad": ["newspaper ad", "newspaper ads"],
  "unclassified/compliance": ["compliance filing", "compliance filings"],
  "investor_meetings": ["analyst-meet notice", "analyst-meet notices"],
  "earnings_call": ["call notice / transcript", "call notices & transcripts"],
  "press_release": ["press release", "press releases"],
  "results/newspaper_ad": ["results newspaper ad", "results newspaper ads"],
  "results": ["results paperwork", "results paperwork"],
  "agm_egm": ["AGM/EGM filing", "AGM/EGM filings"],
  "postal_ballot": ["postal ballot", "postal ballots"],
  "dividend": ["dividend notice", "dividend notices"],
  "fund_raise": ["debt / allotment filing", "debt / allotment filings"],
  "M&A": ["subsidiary filing", "subsidiary filings"],
  "management_change": ["minor board change", "minor board changes"],
  "credit_rating": ["rating reaffirmation", "rating reaffirmations"],
};

function setAsideKey(e: BriefEvent): string {
  const k = `${e.category}/${e.subtype}`;
  return SET_ASIDE_LABEL[k] ? k : SET_ASIDE_LABEL[e.category] ? e.category : "other";
}

export function SetAside({ events }: { events: BriefEvent[] }) {
  const [open, setOpen] = useState(false);
  const groups = new Map<string, BriefEvent[]>();
  for (const e of events) groups.set(setAsideKey(e), [...(groups.get(setAsideKey(e)) ?? []), e]);
  const sorted = [...groups.entries()].sort((a, b) => b[1].length - a[1].length);
  const text = sorted.map(([k, v]) => {
    const [one, many] = SET_ASIDE_LABEL[k] ?? ["other filing", "other filings"];
    return `${v.length} ${v.length === 1 ? one : many}`;
  });
  if (!events.length) return null;
  return (
    <section className="mt-8">
      <div className="flex items-baseline justify-between">
        <Kicker>Set aside · {events.length}</Kicker>
        <button onClick={() => setOpen((o) => !o)} className="text-[11.5px] text-sub underline decoration-rule underline-offset-[3px] hover:text-ink">
          {open ? "Hide" : "Show all"}
        </button>
      </div>
      <p className="mt-2 text-[13px] leading-relaxed text-sub">{text.join(", ")}.</p>
      {open && (
        <ul className="mt-3">
          {sorted.flatMap(([, v]) => v).map((e) => (
            <li key={e.id} className="flex items-baseline gap-2.5 border-t hair py-[7px] text-[12px]">
              <span className="num w-[44px] shrink-0 text-[11px] text-faint">{day(e.occurred_at)}</span>
              <span className="num w-[78px] shrink-0 truncate text-[11px]">{e.symbol}</span>
              <span className="min-w-0 flex-1 truncate text-sub">{e.title}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function MovesStrip({ moves, onOpen }: { moves: Move[]; onOpen: (eventId: string) => void }) {
  if (!moves.length) return null;
  return (
    <section className="mt-6">
      <div className="flex items-baseline justify-between">
        <Kicker>Unusual moves · EOD</Kicker>
        <span className="text-[11px] text-faint">vs NIFTY, σ of 60 sessions</span>
      </div>
      <div className="no-scrollbar -mx-4 mt-2 flex gap-0 overflow-x-auto px-4">
        {moves.map((m) => (
          <button
            key={`${m.symbol}-${m.session}`}
            onClick={() => m.event_id && onOpen(m.event_id)}
            className="w-[132px] shrink-0 border-l hair px-3 py-1 text-left first:border-l-0 first:pl-0"
          >
            <div className="flex items-baseline justify-between">
              <span className="num text-[11.5px] font-medium">{m.symbol}</span>
              <span className="num text-[10px] text-faint">{day(m.session)}</span>
            </div>
            <div className="mt-1 flex items-end justify-between gap-2">
              <Delta v={m.ret} text={pct(m.ret)} className="text-[17px] font-medium leading-none" />
              <Spark data={m.spark} w={52} h={18} strokeTone={tone(m.ret)} />
            </div>
            <div className="num mt-1 text-[10.5px] text-sub">
              {sigma(m.z)}{m.vol_x != null && m.vol_x >= 1.5 ? ` · vol ${m.vol_x.toFixed(1)}×` : ""}
            </div>
            <div className="mt-0.5 truncate text-[10.5px] text-faint">{m.event_id ? "filing explains it ›" : "no filing found"}</div>
          </button>
        ))}
      </div>
    </section>
  );
}

export { dayTime };
