import { useEffect, useMemo, useState } from "react";
import { CATEGORY } from "./components/Feed";
import { Delta, Kicker, SourceChip, Spark } from "./components/ui";
import { cr, day, pct } from "./format";
import type { BriefEvent, BriefSnapshot, HoldingRow, HoldingsSnapshot } from "./types";
import { ALERT_CATS, ALERTS_KEY, type Alerts, read, readAlerts, trips, write } from "./alerts";

/* ---------------------------------------------------------------- alerts (shared with Today) */

/** Back-test the thresholds on the last year of EOD data, client-side. */
function estimate(rows: HoldingRow[], a: Alerts, counts: Record<string, number>) {
  let hits = 0, sessions = 0;
  for (const r of rows) {
    const c = r.closes, v = r.volumes;
    for (let i = 21; i < c.length; i++) {
      sessions = Math.max(sessions, c.length - 21);
      const ret = c[i] / c[i - 1] - 1;
      let avg = 0;
      for (let k = i - 20; k < i; k++) avg += v[k];
      avg /= 20;
      if (trips(ret, avg > 0 ? v[i] / avg : null, a)) hits++;
    }
  }
  const weeks = sessions / 5 || 1;
  const pv = hits / weeks;
  const filings = a.cats.reduce((n, k) => n + (counts[k] ?? 0), 0) / (30 / 7);
  return { pv, filings, total: pv + filings, weeks: Math.round(weeks) };
}

function AlertSettings({ rows, counts }: { rows: HoldingRow[]; counts: Record<string, number> }) {
  const [a, setA] = useState<Alerts>(readAlerts);
  useEffect(() => write(ALERTS_KEY, a), [a]);
  const est = useMemo(() => estimate(rows, a, counts), [rows, a, counts]);
  return (
    <div className="space-y-4 pb-1">
      <label className="block">
        <span className="flex items-baseline justify-between text-[12.5px]"><span>Price move</span><span className="num">±{a.price.toFixed(1)}%</span></span>
        <input type="range" min={1} max={10} step={0.5} value={a.price} onChange={(e) => setA({ ...a, price: Number(e.target.value) })} className="mt-1 w-full accent-[rgb(var(--ink))]" />
      </label>
      <label className="block">
        <span className="flex items-baseline justify-between text-[12.5px]"><span>Volume vs 20-day average</span><span className="num">{a.volume.toFixed(1)}×</span></span>
        <input type="range" min={1.5} max={6} step={0.5} value={a.volume} onChange={(e) => setA({ ...a, volume: Number(e.target.value) })} className="mt-1 w-full accent-[rgb(var(--ink))]" />
      </label>
      <div>
        <span className="text-[12.5px]">Filing categories</span>
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {ALERT_CATS.map((k) => {
            const on = a.cats.includes(k);
            return (
              <button key={k} onClick={() => setA({ ...a, cats: on ? a.cats.filter((x) => x !== k) : [...a.cats, k] })} aria-pressed={on}
                className={`h-6 rounded-full border px-2 text-[11px] transition-colors duration-150 ease-quiet ${on ? "border-ink bg-ink text-paper" : "hair text-sub"}`}>
                {CATEGORY[k] ?? k}
              </button>
            );
          })}
        </div>
      </div>
      <p className="num border-t hair pt-2 text-[12.5px] leading-snug">
        At this setting you’d get <span className="font-semibold">~{est.total < 10 ? est.total.toFixed(1) : Math.round(est.total)} alerts/week</span>
        <span className="text-sub"> ({est.pv.toFixed(1)} price/volume over the last {est.weeks} weeks, {est.filings.toFixed(1)} from filings at the last 30 days’ rate, which was results season, so high)</span>
      </p>
      <p className="text-[11px] text-faint">Saved on this device. The price and volume thresholds also decide what Today’s unusual-moves strip shows. Back-tested on EOD closes and volumes for your 18 holdings.</p>
    </div>
  );
}

/* ---------------------------------------------------------------- thesis */
function ThesisBlock({ row, events, onResearch }: { row: HoldingRow; events: BriefEvent[]; onResearch: (s: string) => void }) {
  const th = row.thesis;
  const key = `finn-thesis-${row.symbol}`;
  const [mine, setMine] = useState<string | null>(() => read<string | null>(key, null));
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(mine ?? th?.line ?? "");
  const save = (v: string | null) => { setMine(v); write(key, v); setEditing(false); };
  const tagged = events.filter((e) => e.thesis_tag && e.thesis_tag.stance !== "neutral");
  return (
    <div className="space-y-3 pb-4 pl-0.5 text-[12.5px] leading-snug">
      <div>
        <div className="flex items-baseline justify-between">
          <Kicker className="text-ink">Thesis</Kicker>
          {!editing && <button onClick={() => { setDraft(mine ?? th?.line ?? ""); setEditing(true); }} className="text-[11.5px] text-sub underline decoration-rule underline-offset-[3px] hover:text-ink">Edit</button>}
        </div>
        {editing ? (
          <div className="mt-1">
            <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={3} className="w-full rounded-[4px] border hair bg-paper p-2 text-[13px] outline-none focus:border-ink" />
            <div className="mt-1 flex gap-3 text-[11.5px]">
              <button onClick={() => save(draft.trim() && draft.trim() !== th?.line ? draft.trim() : null)} className="font-semibold text-ink">Save</button>
              <button onClick={() => setEditing(false)} className="text-sub">Cancel</button>
              {mine && <button onClick={() => save(null)} className="ml-auto text-sub">Reset to seeded</button>}
            </div>
          </div>
        ) : (
          <p className="mt-1 text-[13.5px]">{mine ?? th?.line ?? "No thesis yet."}</p>
        )}
        {mine && <p className="mt-1 text-[11px] text-faint">Your edit, saved on this device. Event tags below were made against the seeded drivers.</p>}
      </div>
      {th && th.drivers.length > 0 && (
        <ol className="space-y-1">
          {th.drivers.map((d, i) => (
            <li key={i} className="flex gap-2">
              <span className="num w-3 shrink-0 text-faint">{i + 1}</span>
              <span>{d.text}{d.why && <span className="text-sub"> · {d.why}</span>}<SourceChip label={d.source.label.replace(" call", "")} quote={d.source.quote} href={d.source.doc_id} /></span>
            </li>
          ))}
          <li className="pl-5 text-[11px] text-faint">Seeded by {th.seeded_by ?? "rules"} from the {th.seeded_from}; each driver’s quote checked on its page.</li>
        </ol>
      )}
      {tagged.length > 0 && (
        <div>
          <Kicker>Last 30 days vs the thesis</Kicker>
          <ul className="mt-1 divide-y hair border-y hair">
            {tagged.map((e) => (
              <li key={e.id} className="flex gap-2 py-1.5">
                <span className={`kicker w-[74px] shrink-0 ${e.thesis_tag!.stance === "challenges" ? "text-signal" : "text-up"}`}>{e.thesis_tag!.stance}</span>
                <span className="min-w-0"><span className="num text-faint">{day(e.occurred_at)} </span>{e.thesis_tag!.driver_text}: {e.thesis_tag!.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {th?.watch && (
        <p className="border-l-2 border-ink pl-2">
          {th.watch}
          {th.watch_source && <SourceChip label={th.watch_source.label.replace(" call", "")} quote={th.watch_source.quote} href={th.watch_source.doc_id} />}
        </p>
      )}
      {row.research && (
        <button onClick={() => onResearch(row.symbol)} className="text-[11.5px] text-sub underline decoration-rule underline-offset-[3px] hover:text-ink">Open research and the promise ledger →</button>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- rows */
function Row({ row, events, onResearch }: { row: HoldingRow; events: BriefEvent[]; onResearch: (s: string) => void }) {
  const [open, setOpen] = useState(false);
  const spark = row.closes.slice(-30);
  const challenges = events.filter((e) => e.thesis_tag?.stance === "challenges").length;
  return (
    <li className="border-t hair">
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open} className="flex w-full items-center gap-3 py-2.5 text-left">
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            <span className="num text-[13.5px] font-semibold">{row.symbol}</span>
            {challenges > 0 && <span className="h-[6px] w-[6px] rounded-full bg-signal" aria-label={`${challenges} event(s) challenge the thesis`} />}
          </span>
          <span className="block truncate text-[11px] text-faint">{row.thesis?.watch?.replace("Watch next quarter: ", "Watch: ") ?? row.company}</span>
        </span>
        <Spark data={spark} w={56} h={20} strokeTone={spark.length > 1 ? (spark[spark.length - 1] >= spark[0] ? "up" : "down") : "flat"} />
        <span className="w-[76px] shrink-0 text-right">
          <span className="num block text-[13px]">{row.close != null ? cr(row.close) : "—"}</span>
          <Delta v={row.day_change} text={pct(row.day_change)} className="block text-[11.5px]" />
        </span>
      </button>
      {open && <ThesisBlock row={row} events={events} onResearch={onResearch} />}
    </li>
  );
}

export function Holdings({ brief, onResearch }: { brief: BriefSnapshot | null; onResearch: (s: string) => void }) {
  const [snap, setSnap] = useState<HoldingsSnapshot | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [alertsOpen, setAlertsOpen] = useState(false);
  useEffect(() => {
    fetch(`${import.meta.env.BASE_URL}data/holdings.json`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`holdings.json ${r.status}`))))
      .then(setSnap)
      .catch((e: Error) => setErr(e.message));
  }, []);
  const bySym = useMemo(() => {
    const m: Record<string, BriefEvent[]> = {};
    for (const e of brief?.events ?? []) (m[e.symbol] ??= []).push(e);
    return m;
  }, [brief]);
  if (!snap) return <p className="pt-10 text-[13px] text-sub">{err ? `Couldn't load holdings (${err}).` : ""}</p>;
  const up = snap.rows.filter((r) => (r.day_change ?? 0) > 0).length;
  return (
    <div className="pt-5">
      <div className="flex items-baseline justify-between">
        <h1 className="text-[30px] font-semibold leading-none tracking-[-0.03em]">Holdings</h1>
        <span className="num text-[11px] text-faint">{snap.prices_as_of ? `${day(snap.prices_as_of)} EOD` : ""}</span>
      </div>
      <p className="num mt-2 text-[12px] text-sub">{snap.rows.length} holdings · {up} up, {snap.rows.length - up} down or flat · tap a row for its thesis</p>

      <section className="mt-4 border-y border-ink">
        <button onClick={() => setAlertsOpen((o) => !o)} aria-expanded={alertsOpen} className="flex w-full items-baseline justify-between py-2.5 text-left">
          <Kicker className="text-ink">Alert settings</Kicker>
          <span className="text-[11.5px] text-sub">{alertsOpen ? "Close" : "Price, volume, categories"}</span>
        </button>
        {alertsOpen && <AlertSettings rows={snap.rows} counts={snap.category_counts_30d} />}
      </section>

      <ul className="mt-2 border-b hair">
        {snap.rows.map((r) => <Row key={r.symbol} row={r} events={bySym[r.symbol] ?? []} onResearch={onResearch} />)}
      </ul>
      <p className="mb-10 mt-3 text-[11px] leading-snug text-faint">
        Theses seeded from each company’s latest concall; edit to make them yours. Events are tagged to the driver they touch; a challenge adds 15 to the event’s score, support adds 5.
      </p>
    </div>
  );
}
