import { useCallback, useState } from "react";
import { useOpenSignal } from "./openSignal";
import { bps, cr, day, pct } from "../format";
import type { BriefEvent, GuidedDelivered, Metric, ResultsCard } from "../types";
import { Delta, FilingLink, Kicker, NeedsDot, SourceChip, WhyButton } from "./ui";

const SHORT: Record<string, string> = {
  revenue: "Revenue", ebitda: "EBITDA", pbt: "PBT", pat: "PAT", nii: "NII", ppop: "PPOP",
};
const TINY: Record<string, string> = { revenue: "Rev", ebitda: "EBITDA", pbt: "PBT", pat: "PAT", nii: "NII", ppop: "PPOP" };

type Lead = { value: number; label: string; key: "pat" | "pbt" | "top" };

/** The one number that leads the card: adjusted PAT growth when the company states
 *  one (a one-off is not the business), else PAT, else revenue/NII. */
function lead(card: ResultsCard): Lead | null {
  const adj = card.adjusted.find((a) => a.key === "pat" && a.yoy != null) ?? card.adjusted.find((a) => a.key === "pbt" && a.yoy != null);
  if (adj?.yoy != null) return { value: adj.yoy, label: `${adj.key.toUpperCase()} YoY, company's adjusted`, key: adj.key === "pbt" ? "pbt" : "pat" };
  const pat = card.metrics.find((m) => m.key === "pat");
  if (pat?.yoy != null) return { value: pat.yoy, label: "PAT YoY", key: "pat" };
  const top = card.metrics.find((m) => m.key === "revenue" || m.key === "nii");
  if (top?.yoy != null) return { value: top.yoy, label: `${SHORT[top.key]} YoY`, key: "top" };
  return null;
}

/** Headline without the one-off parenthetical (the one-off box carries it) and, when
 *  the card shows a lead numeral, without the clause that repeats it. */
export function cleanHeadline(card: ResultsCard, dropLead = false): string {
  const base = card.headline.replace(/\s*\((company figure|incl\.)[^)]*\)\s*$/, "");
  const l = dropLead ? lead(card) : null;
  if (!l) return base;
  const re = l.key === "top" ? /^(revenue|nii)\b/i : new RegExp(`^(adjusted )?${l.key}\\b`, "i");
  const kept = base.split(", ").filter((s) => !re.test(s.trim()));
  if (!kept.length) return base;
  const s = kept.join(", ");
  return s[0].toUpperCase() + s.slice(1);
}

const GRADE: Record<GuidedDelivered["grade"], { text: string; cls: string }> = {
  exceeded: { text: "Exceeded", cls: "text-up" },
  met: { text: "Met", cls: "text-up" },
  missed: { text: "Missed", cls: "text-down" },
  revised: { text: "Revised", cls: "text-sub" },
  pending: { text: "Pending", cls: "text-faint" },
  not_comparable: { text: "Can’t grade", cls: "text-faint" },
};

function gradeOf(g: GuidedDelivered) {
  if (g.grade === "pending" && g.progress_flag === "behind") return { text: "Behind", cls: "text-down" };
  if (g.grade === "pending" && g.progress_flag === "on_track") return { text: "On track", cls: "text-sub" };
  return GRADE[g.grade];
}

/** The signature row: what management guided on the last call vs what just landed. */
export function GuidedRows({ items }: { items: GuidedDelivered[] }) {
  if (!items.length) return null;
  return (
    <div className="mt-3 border-y border-ink/80">
      <div className="flex items-baseline justify-between pt-1.5">
        <Kicker className="text-ink">Guided → delivered</Kicker>
        <span className="text-[10.5px] text-faint">guidance from calls before these results</span>
      </div>
      <ul>
        {items.map((g, i) => (
          <li key={i} className={`py-2 ${i ? "border-t hair" : ""}`}>
            <div className="flex items-baseline gap-2">
              <span className="min-w-0 flex-1 text-[12.5px] font-medium leading-snug">{g.metric}</span>
              <span className={`kicker shrink-0 ${gradeOf(g).cls}`}>{gradeOf(g).text}</span>
            </div>
            <div className="num mt-0.5 flex flex-wrap items-baseline gap-x-1.5 text-[12px] leading-snug">
              <span className="text-sub">guided</span> <span>{g.guided}</span>
              <span className="text-faint">→</span>
              <span className="text-sub">delivered</span> <span className="font-semibold">{g.delivered}</span>
              <SourceChip label={g.source.label ?? "call"} quote={g.source.quote} href={g.source.doc_id}
                note={[g.call_label && `${g.call_label}, ${g.source.section ?? ""}`, g.source.note].filter(Boolean).join(" · ") || null} />
            </div>
            {g.note && <div className="num mt-0.5 text-[11px] text-sub">{g.note}</div>}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Row({ m, card }: { m: Metric; card: ResultsCard }) {
  const na = m.value == null;
  // per-row chip only where the source differs from the table's
  const differs = m.source != null && m.source !== card.source;
  return (
    <tr className="border-t hair align-baseline">
      <td className="py-[7px] pr-2 text-[13px]">
        {SHORT[m.key] ?? m.label}
        {m.key === "ebitda" && !na && <span className="text-faint"> (screener)</span>}
        {differs && <SourceChip label={m.source === "NSE XBRL" ? "XBRL" : "scr"} note={[m.label, m.note].filter(Boolean).join(" · ")}
          href={m.source === "NSE XBRL" ? card.xbrl_url : card.filing_url} />}
      </td>
      {na ? (
        <td colSpan={3} className="py-[7px] text-right text-[11.5px] text-faint">{m.note?.split(":")[0] ?? "n/a"}</td>
      ) : (
        <>
          <td className="num py-[7px] text-right text-[13px]">{cr(m.value)}</td>
          <td className="py-[7px] text-right text-[12px]"><Delta v={m.qoq} text={pct(m.qoq)} /></td>
          <td className="py-[7px] text-right text-[12px]"><Delta v={m.yoy} text={pct(m.yoy)} /></td>
        </>
      )}
    </tr>
  );
}

/** Collapsed: one compact line of the four YoY numbers. */
function YoyStrip({ card }: { card: ResultsCard }) {
  const adjPat = card.adjusted.some((a) => a.key === "pat" && a.yoy != null);
  return (
    <div className="mt-3 grid grid-cols-4 border-y hair">
      {card.metrics.slice(0, 4).map((m, i) => (
        <div key={m.key} className={`py-1.5 ${i ? "border-l hair pl-2" : ""}`}>
          <div className="kicker text-faint">{TINY[m.key] ?? m.key}{m.key === "pat" && adjPat ? " rep." : ""}</div>
          {m.yoy != null ? <Delta v={m.yoy} text={pct(m.yoy)} className="text-[13px]" /> : <span className="num text-[12px] text-faint">n/a</span>}
        </div>
      ))}
    </div>
  );
}

export function ResultsCardView({ event, card }: { event: BriefEvent; card: ResultsCard; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  useOpenSignal(event.id, useCallback(() => setOpen(true), []));
  const top = lead(card);
  const adj = card.adjusted.filter((a) => a.yoy != null);
  const pat = card.metrics.find((m) => m.key === "pat");
  const checksOk = card.checks.filter((c) => c.ok).length;
  const srcNote = [
    card.source_note,
    ...card.checks.map((c) => `${c.ok ? "✓" : "✗"} ${c.name}: ${cr(c.expected)} vs ${cr(c.actual)}`),
  ].filter(Boolean).join(" · ");

  return (
    <article className="py-5">
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open} className="block w-full text-left">
        <div className="flex items-center gap-2">
          {event.tier === "needs_you" && <NeedsDot />}
          <Kicker className="text-ink">{event.symbol}</Kicker>
          <Kicker>· {card.period_label} results</Kicker>
          <span className="num ml-auto text-[11px] text-faint">{day(event.occurred_at)}</span>
        </div>
        {top && (
          <div className="mt-2.5 flex items-end gap-3">
            <div className="num text-[40px] font-medium leading-[0.95] tracking-[-0.03em]">{pct(top.value)}</div>
            <div className="pb-1 text-[11.5px] leading-tight text-sub">{top.label}</div>
          </div>
        )}
        <h3 className="mt-2 text-[16px] font-semibold leading-snug tracking-[-0.01em]">
          {cleanHeadline(card, true)}
          {card.one_offs.length > 0 && <span className="ml-1.5 align-[2px] rounded-[3px] border border-ink px-1 text-[9.5px] font-semibold uppercase tracking-[0.06em]">one-off</span>}
          {!card.verified && <span className="ml-1.5 align-[2px] text-[11px] font-semibold text-down">unverified</span>}
        </h3>
        {!open && <YoyStrip card={card} />}
      </button>

      <GuidedRows items={card.guidance} />

      {open && (
        <>
          {card.one_offs.length > 0 && (
            <div className="mt-3 border-l-2 border-ink pl-3">
              <Kicker className="text-ink">One-off in reported profit</Kicker>
              {card.one_offs.map((o, i) => (
                <p key={i} className="mt-1 text-[12.5px] leading-snug">
                  <span className="num">₹{cr(o.amount_cr)} cr</span> {o.effect !== "unclear" ? o.effect : ""} · {o.description}
                  <SourceChip label={o.source === "NSE XBRL" ? "XBRL" : "filing"} quote={o.quote} href={o.source.startsWith("http") ? o.source : card.xbrl_url} />
                </p>
              ))}
              <p className="mt-1 text-[12px] leading-snug text-sub">
                {adj.length > 0 ? (
                  <>Lead number is the company’s own adjusted figure. As reported: PAT <Delta v={pat?.yoy} text={pct(pat?.yoy)} /> YoY.</>
                ) : (
                  "The company states no adjusted figure; the numbers below are as reported."
                )}
              </p>
            </div>
          )}

          <table className="mt-3 w-full border-b hair">
            <thead>
              <tr className="text-faint">
                <th className="kicker pb-1 text-left font-semibold">
                  ₹ cr
                  <SourceChip label={card.source === "NSE XBRL" ? "XBRL" : "screener"} note={srcNote}
                    href={card.source === "NSE XBRL" ? card.xbrl_url : `https://www.screener.in/company/${card.symbol}/consolidated/`} />
                </th>
                <th className="kicker pb-1 text-right font-semibold">{card.period_label.split(" ")[0]}</th>
                <th className="kicker pb-1 text-right font-semibold">QoQ</th>
                <th className="kicker pb-1 text-right font-semibold">YoY</th>
              </tr>
            </thead>
            <tbody>
              {card.metrics.map((m) => <Row key={m.key} m={m} card={card} />)}
              {adj.map((a) => (
                <tr key={a.key} className="border-t hair align-baseline">
                  <td className="py-[7px] pr-2 text-[13px]">
                    Adj. {a.key.toUpperCase()}
                    <SourceChip label="co." quote={a.quote} href={a.source}
                      note={`Company's own figure excluding the one-off. Base ${a.prev_y_source === "company" ? "as stated by the company" : "from screener"}.`} />
                  </td>
                  <td className="num py-[7px] text-right text-[13px]">{cr(a.value)}</td>
                  <td className="num py-[7px] text-right text-[12px] text-faint">—</td>
                  <td className="py-[7px] text-right text-[12px]"><Delta v={a.yoy} text={pct(a.yoy)} /></td>
                </tr>
              ))}
            </tbody>
          </table>

          {card.margins.some((g) => g.value != null) && (
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[12px]">
              {card.margins.filter((g) => g.value != null).map((g) => (
                <div key={g.key} className="flex items-baseline gap-1.5">
                  <span className="text-sub">{g.label.replace(" (screener)", "").replace(" (XBRL)", "")}</span>
                  <span className="num">{pct(g.value, false)}</span>
                  {g.yoy_bps != null && <Delta v={g.yoy_bps} text={`${bps(g.yoy_bps)} bps`} className="text-[11px]" />}
                </div>
              ))}
            </div>
          )}
          <p className={`mt-2 text-[11.5px] ${card.verified ? "text-sub" : "font-semibold text-down"}`}>
            {card.verified ? `✓ ${checksOk}/${card.checks.length} arithmetic checks pass` : "Unverified, open filing"}
            {card.source === "NSE XBRL" && <span className="font-normal text-sub"> · screener not updated yet; current quarter from NSE XBRL</span>}
          </p>
        </>
      )}

      <div className="mt-3 flex items-center gap-3">
        <button onClick={() => setOpen((o) => !o)} className="text-[11.5px] text-sub underline decoration-rule underline-offset-[3px] hover:text-ink">
          {open ? "Less" : card.one_offs.length ? "Table, one-off, checks" : "Full table, checks"}
        </button>
        {open && <FilingLink href={card.filing_url} />}
        <span className="ml-auto"><WhyButton event={event} /></span>
      </div>
    </article>
  );
}
