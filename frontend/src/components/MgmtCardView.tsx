import { useState } from "react";
import { day } from "../format";
import type { BriefEvent, MgmtCard, PersonChange } from "../types";
import { FilingLink, Kicker, NeedsDot, SourceChip, WhyButton } from "./ui";

const VERB: Record<PersonChange["change"], string> = {
  appointed: "Joins", "re-appointed": "Re-appointed", redesignated: "Redesignated",
  resigned: "Resigns", retired: "Retires", ceased: "Exits", other: "Changes",
};
const OUT = new Set(["resigned", "retired", "ceased"]);

function effective(s: string | null): string | null {
  if (!s) return null;
  return s.replace(/^with effect from\s*/i, "w.e.f. ").replace(/the close of business hours on/i, "close of business,");
}

/** One person, possibly with several changes (Noel Tata: retires as Chairman, becomes
 *  Chairman Emeritus). */
function Person({ group }: { group: PersonChange[] }) {
  const [open, setOpen] = useState(false);
  const p = group[0];
  const bio = group.find((g) => g.background)?.background ?? null;
  const prior = group.flatMap((g) => g.prior_roles);
  const reasons = group.flatMap((g) => (g.reason ? [g.reason] : []));
  return (
    <li className="border-t hair py-3">
      <div className="flex items-baseline gap-2">
        <span className="text-[14.5px] font-semibold tracking-[-0.01em]">{p.name}</span>
        <a href={p.linkedin_search} target="_blank" rel="noreferrer" className="ml-auto shrink-0 text-[11px] text-faint underline decoration-rule underline-offset-2 hover:text-ink">
          LinkedIn search ↗
        </a>
      </div>
      {group.map((g, i) => (
        <div key={i} className="mt-1 flex items-baseline gap-2 text-[12.5px] leading-snug">
          <span className={`kicker w-[78px] shrink-0 ${OUT.has(g.change) ? "text-ink" : "text-sub"}`}>{VERB[g.change]}</span>
          <span className="min-w-0">
            {g.role ?? "role not stated"}
            {effective(g.effective) && <span className="text-sub"> · {effective(g.effective)}</span>}
          </span>
        </div>
      ))}
      {prior.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1 pl-[86px]">
          {prior.map((r, i) => (
            <span key={i} className="inline-block max-w-full rounded-[3px] bg-wash px-1.5 py-[2px] text-[11.5px] leading-snug">
              {r.org}
              {r.role && <span className="text-sub">&nbsp;· {r.role}</span>}
            </span>
          ))}
        </div>
      )}
      {(bio || reasons.length > 0 || prior.length > 0) && (
        <div className="pl-[86px]">
          <button onClick={() => setOpen((o) => !o)} className="mt-1.5 text-left text-[12px] text-sub underline decoration-rule underline-offset-[3px] hover:text-ink">
            {open ? "Hide" : bio ? "Background & quotes" : "Quotes from the filing"}
          </button>
          {open && (
            <div className="mt-1.5 space-y-1.5 text-[12px] leading-snug">
              {bio && <p className="text-ink">{bio}</p>}
              {[...reasons, ...prior.map((r) => r.quote)].filter((q, i, a) => a.indexOf(q) === i).map((q, i) => (
                <p key={i} className="border-l-2 border-ink/20 pl-2 italic text-sub">“{q}”</p>
              ))}
            </div>
          )}
        </div>
      )}
    </li>
  );
}

export function MgmtCardView({ event, card }: { event: BriefEvent; card: MgmtCard }) {
  const groups: PersonChange[][] = [];
  for (const p of card.people) {
    const k = p.name.replace(/[.\s]/g, "").toLowerCase();
    const g = groups.find((x) => x[0].name.replace(/[.\s]/g, "").toLowerCase() === k);
    if (g) g.push(p);
    else groups.push([p]);
  }
  const [head, more] = (card.short ?? card.headline).split(" · ");
  return (
    <article className="py-5">
      <div className="flex items-center gap-2">
        {event.tier === "needs_you" && <NeedsDot />}
        <Kicker className="text-ink">{event.symbol}</Kicker>
        <Kicker>· Board &amp; management</Kicker>
        <span className="num ml-auto text-[11px] text-faint">{day(event.occurred_at)}</span>
      </div>
      <h3 className="mt-3 text-[24px] font-semibold leading-[1.15] tracking-[-0.02em]">{head}</h3>
      {more && <div className="mt-0.5 text-[12.5px] text-sub">{more.replace(/more$/, "more board changes")} in the same filing</div>}
      {groups.length > 0 ? (
        <ul className="mt-3 border-b hair">{groups.map((g, i) => <Person key={i} group={g} />)}</ul>
      ) : (
        <p className="mt-3 text-[12.5px] text-sub">The filing names no change of position.</p>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <span className="inline-flex items-center text-[11.5px] text-sub">
          From the Reg 30 annexure
          <SourceChip label={card.extracted_by.startsWith("regex") ? "rules" : "verified"}
            note={`Extracted by ${card.extracted_by}. Every employer and quote was checked against the filing text; ${card.people.reduce((n, p) => n + p.dropped.length, 0)} dropped.`} />
        </span>
        <FilingLink href={card.filing_urls[0]} />
        <span className="ml-auto"><WhyButton event={event} /></span>
      </div>
    </article>
  );
}
