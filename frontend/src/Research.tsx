import { useEffect, useMemo, useRef, useState } from "react";
import { Delta, Kicker, SourceChip } from "./components/ui";
import { cr, day, pct } from "./format";
import type { Cite, Grade, LedgerPromise, Point, ResearchSnapshot, Section } from "./types";

const BASE = import.meta.env.BASE_URL;

/* ---------------------------------------------------------------- loading */
type Load =
  | { state: "idle" }
  | { state: "loading"; stages: string[]; live: boolean }
  | { state: "ready"; snap: ResearchSnapshot; live: boolean }
  | { state: "error"; message: string };

async function getJSON<T>(url: string): Promise<T | null> {
  try {
    const r = await fetch(url);
    if (!r.ok) return null;
    const ct = r.headers.get("content-type") ?? "";
    if (!ct.includes("json")) return null; // the dev server answers unknown paths with index.html
    return (await r.json()) as T;
  } catch {
    return null;
  }
}

function useResearch(symbol: string | null): Load {
  const [load, setLoad] = useState<Load>({ state: "idle" });
  useEffect(() => {
    if (!symbol) return setLoad({ state: "idle" });
    let cancelled = false;
    (async () => {
      setLoad({ state: "loading", stages: ["Opening the precomputed dossier…"], live: false });
      // 1. static snapshot shipped with the bundle: instant, works with the backend asleep
      const snap = await getJSON<ResearchSnapshot>(`${BASE}data/research/${symbol}.json`);
      if (cancelled) return;
      if (snap) return setLoad({ state: "ready", snap, live: false });
      // 2. a ticker FINN has never seen: live build with staged progress
      const start = await fetch(`/api/research/${symbol}/build`, { method: "POST" }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
      if (!start?.job_id) {
        return setLoad({ state: "error", message: `No precomputed dossier for ${symbol}, and the research backend isn't reachable. Precomputed: TITAN, POLYCAB, INFY.` });
      }
      for (;;) {
        await new Promise((r) => setTimeout(r, 1500));
        if (cancelled) return;
        const j = await getJSON<{ status: string; stages: string[]; error?: string }>(`/api/jobs/${start.job_id}`);
        if (!j) return setLoad({ state: "error", message: "Lost the research backend mid-build." });
        if (j.status === "error") return setLoad({ state: "error", message: j.error ?? "Build failed." });
        if (j.status === "done") {
          const s = await getJSON<ResearchSnapshot>(`/api/research/${symbol}`);
          return setLoad(s ? { state: "ready", snap: s, live: true } : { state: "error", message: "Build finished but the dossier is missing." });
        }
        setLoad({ state: "loading", stages: j.stages.length ? j.stages : ["Queued…"], live: true });
      }
    })();
    return () => { cancelled = true; };
  }, [symbol]);
  return load;
}

/* ---------------------------------------------------------------- bits */
const GRADE: Record<Grade, { text: string; cls: string }> = {
  exceeded: { text: "Exceeded", cls: "text-up" },
  met: { text: "Met", cls: "text-up" },
  missed: { text: "Missed", cls: "text-down" },
  revised: { text: "Revised", cls: "text-ink" },
  pending: { text: "Pending", cls: "text-sub" },
  not_comparable: { text: "Can’t grade", cls: "text-faint" },
};

function Cites({ cites }: { cites: Cite[] }) {
  return (
    <>
      {cites.slice(0, 3).map((c, i) => (
        <SourceChip key={i} label={c.label.replace(/ call p\./, " p.").replace(/^screener · /, "scr ")} quote={c.quote} href={c.doc_id} />
      ))}
    </>
  );
}

function PointList({ points, numbered = false }: { points: Point[]; numbered?: boolean }) {
  return (
    <ol className="divide-y hair">
      {points.map((p, i) => (
        <li key={i} className="flex gap-2.5 py-2.5 text-[13px] leading-snug">
          {numbered && <span className="num w-4 shrink-0 text-faint">{i + 1}</span>}
          <span className="min-w-0">
            {p.text}
            {p.flagged && <span className="ml-1 text-[11px] font-semibold text-down" title="A number here wasn't found in its cited source">unverified number</span>}
            <Cites cites={p.cites} />
          </span>
        </li>
      ))}
    </ol>
  );
}

/** Collapsible section: closed, it still shows its one-line conclusion. */
function Fold({ title, conclusion, defaultOpen = false, children }: { title: string; conclusion: string; defaultOpen?: boolean; children: React.ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="border-t border-ink">
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open} className="flex w-full items-start gap-3 py-3 text-left">
        <span className="min-w-0 flex-1">
          <Kicker className="text-ink">{title}</Kicker>
          <span className="mt-1 block text-[13px] leading-snug text-sub">{conclusion}</span>
        </span>
        <span className={`num shrink-0 pt-0.5 text-[13px] text-faint transition-transform duration-150 ease-quiet ${open ? "rotate-45" : ""}`}>+</span>
      </button>
      {open && <div className="pb-4">{children}</div>}
    </section>
  );
}

/* ---------------------------------------------------------------- ledger */
type Filter = "all" | "graded" | "pending" | "revised";

function PromiseRow({ p }: { p: LedgerPromise }) {
  const [open, setOpen] = useState(false);
  const seg = p.segment && !p.metric.toLowerCase().includes(p.segment.toLowerCase()) ? ` · ${p.segment}` : "";
  return (
    <li className="border-t hair">
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open} className="block w-full py-2.5 text-left">
        <div className="flex items-baseline gap-2">
          <span className="min-w-0 flex-1 text-[13px] font-medium leading-snug">{p.metric[0].toUpperCase() + p.metric.slice(1)}<span className="font-normal text-sub">{seg}</span></span>
          <span className={`kicker shrink-0 ${GRADE[p.grade].cls}`}>{GRADE[p.grade].text}</span>
        </div>
        <div className="num mt-0.5 flex flex-wrap gap-x-1.5 text-[12px] leading-snug">
          {p.target_text ? <span>{p.target_text}</span> : <span className="italic text-sub">qualitative</span>}
          {p.horizon && <span className="text-sub">· {p.horizon}</span>}
          {p.actual_text && p.kind === "numeric" && (<><span className="text-faint">→</span><span className="font-semibold">{p.actual_text}</span></>)}
          {p.progress && (
            <span className={p.progress_flag === "behind" ? "text-down" : "text-sub"}>· {p.progress_flag === "behind" ? "behind: " : ""}{p.progress.replace(/ vs (guided|target) .*/, "")}</span>
          )}
        </div>
        {p.revised_from.length > 0 && p.revision_dir !== "same" && (
          <div className="num mt-0.5 text-[11.5px] text-sub">Revised {p.revision_dir}: {[...p.revised_from, `${p.target_text} (${p.call_label})`].join(" → ")}</div>
        )}
        {p.revised_from.length > 0 && p.revision_dir === "same" && (
          <div className="mt-0.5 text-[11.5px] text-faint">Reiterated on {p.revised_from.length + 1} calls</div>
        )}
      </button>
      {open && (
        <div className="space-y-1.5 pb-3 text-[12px] leading-snug">
          <p className="border-l-2 border-ink/20 pl-2 italic text-sub">“{p.source.quote}”</p>
          <p className="text-faint">
            {p.speaker ? `${p.speaker}, ` : ""}{p.call_label} ({day(p.call_date)}){p.horizon_text ? ` · horizon as said: “${p.horizon_text}”` : ""}
            {p.source.doc_id && <> · <a className="underline decoration-rule underline-offset-2 hover:text-ink" href={p.source.doc_id} target="_blank" rel="noreferrer">transcript p.{p.source.page} ↗</a></>}
          </p>
          {p.grade_note && <p className="text-sub">{p.grade_note}</p>}
          {p.actual_source && (
            <p className="text-faint">
              Delivered: {p.actual_source.label}
              {p.actual_source.quote && <span className="italic"> “{p.actual_source.quote.slice(0, 220)}”</span>}
              {p.actual_source.doc_id && <> <a className="underline decoration-rule underline-offset-2" href={p.actual_source.doc_id} target="_blank" rel="noreferrer">↗</a></>}
            </p>
          )}
          <p className="text-faint">Graded by {p.judged_by === "python" ? "Python, against the reported number" : `${p.judged_by}, citing the later call`}</p>
        </div>
      )}
    </li>
  );
}

function LedgerView({ snap }: { snap: ResearchSnapshot }) {
  const [f, setF] = useState<Filter>("all");
  const L = snap.ledger;
  const shown = L.promises.filter((p) =>
    f === "all" ? true : f === "graded" ? ["met", "exceeded", "missed"].includes(p.grade) : f === "pending" ? p.grade === "pending" : p.grade === "revised" || p.revised_from.length > 0,
  );
  const s = L.stats;
  return (
    <div>
      <div className="grid grid-cols-5 border-y hair text-center">
        {([["Beat", (s.exceeded ?? 0)], ["Met", s.met ?? 0], ["Missed", s.missed ?? 0], ["Revised", s.revised ?? 0], ["Pending", s.pending ?? 0]] as [string, number][]).map(([k, v], i) => (
          <div key={k} className={`py-1.5 ${i ? "border-l hair" : ""}`}>
            <div className={`num text-[18px] font-medium ${k === "Missed" && v ? "text-down" : ""}`}>{v}</div>
            <div className="kicker text-faint">{k}</div>
          </div>
        ))}
      </div>
      <div className="mt-2.5 flex gap-1.5">
        {(["all", "graded", "pending", "revised"] as Filter[]).map((k) => (
          <button key={k} onClick={() => setF(k)}
            className={`h-6 rounded-full border px-2.5 text-[11px] capitalize transition-colors duration-150 ease-quiet ${f === k ? "border-ink bg-ink text-paper" : "hair text-sub hover:text-ink"}`}>
            {k}
          </button>
        ))}
      </div>
      <ul className="mt-2 border-b hair">{shown.map((p) => <PromiseRow key={p.id} p={p} />)}</ul>
      <p className="mt-2 text-[11px] leading-snug text-faint">
        From {L.calls.length} concall transcripts. Every quote was found verbatim on its page ({s.dropped_quote ?? 0} dropped because it wasn’t).
        Numbers graded in Python against screener, or against a figure the company stated on a later call; qualitative ones by {L.llm?.split(", ").find((m) => !m.includes("lite")) ?? "the strong model"} citing the later call.
        {(s.not_comparable ?? 0) > 0 && ` ${s.not_comparable} can’t be graded (segment or industry-relative figures FINN has no source for).`}
      </p>
    </div>
  );
}

/* ---------------------------------------------------------------- charts (one series each, one axis) */
function Bars({ data, fmt, label }: { data: { x: string; y: number | null }[]; fmt: (v: number) => string; label: string }) {
  const [sel, setSel] = useState<number | null>(null);
  const vals = data.map((d) => d.y ?? 0);
  const max = Math.max(...vals, 0) || 1;
  const W = 343, H = 92, gap = 2, bw = (W - gap * (data.length - 1)) / data.length;
  const i = sel ?? data.length - 1;
  return (
    <figure>
      <figcaption className="flex items-baseline justify-between text-[11.5px]">
        <span className="text-sub">{label}</span>
        <span className="num">{data[i]?.x} · {data[i]?.y != null ? fmt(data[i].y!) : "n/a"}</span>
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H + 14}`} className="mt-1 w-full" role="img" aria-label={label} onMouseLeave={() => setSel(null)}>
        {data.map((d, k) => {
          const h = d.y != null && d.y > 0 ? Math.max(2, (d.y / max) * H) : 0;
          const x = k * (bw + gap);
          return (
            <g key={d.x} onMouseEnter={() => setSel(k)} onClick={() => setSel(k)}>
              <rect x={x} y={0} width={bw} height={H} fill="transparent" />
              <path d={roundedTop(x, H - h, bw, h, Math.min(4, bw / 2, h))} fill="rgb(var(--ink))" opacity={k === i ? 1 : 0.28} />
              {(k === 0 || k === data.length - 1 || k === i) && (
                <text x={x + bw / 2} y={H + 11} textAnchor="middle" className="num" fontSize="9" fill="rgb(var(--faint))">{d.x.replace("FY", "’")}</text>
              )}
            </g>
          );
        })}
        <line x1={0} x2={W} y1={H} y2={H} stroke="rgb(var(--rule))" strokeWidth="1" />
      </svg>
    </figure>
  );
}

function roundedTop(x: number, y: number, w: number, h: number, r: number) {
  if (h <= 0) return "";
  return `M${x},${y + h} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h} Z`;
}

function Line({ data, fmt, label }: { data: { x: string; y: number | null }[]; fmt: (v: number) => string; label: string }) {
  const [sel, setSel] = useState<number | null>(null);
  const pts = data.filter((d) => d.y != null) as { x: string; y: number }[];
  if (pts.length < 2) return null;
  const lo = Math.min(...pts.map((p) => p.y)), hi = Math.max(...pts.map((p) => p.y)), span = hi - lo || 1;
  const W = 343, H = 64, step = W / (pts.length - 1);
  const xy = pts.map((p, k) => [k * step, 6 + (H - 12) * (1 - (p.y - lo) / span)] as const);
  const i = sel ?? pts.length - 1;
  return (
    <figure>
      <figcaption className="flex items-baseline justify-between text-[11.5px]">
        <span className="text-sub">{label}</span>
        <span className="num">{pts[i].x} · {fmt(pts[i].y)}</span>
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} className="mt-1 w-full overflow-visible" role="img" aria-label={label} onMouseLeave={() => setSel(null)}>
        <line x1={0} x2={W} y1={H - 1} y2={H - 1} stroke="rgb(var(--rule))" />
        <polyline points={xy.map((p) => p.join(",")).join(" ")} fill="none" stroke="rgb(var(--ink))" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
        <line x1={xy[i][0]} x2={xy[i][0]} y1={0} y2={H} stroke="rgb(var(--rule))" />
        <circle cx={xy[i][0]} cy={xy[i][1]} r={4} fill="rgb(var(--ink))" stroke="rgb(var(--paper))" strokeWidth="2" />
        {xy.map((p, k) => <rect key={k} x={p[0] - step / 2} y={0} width={step} height={H} fill="transparent" onMouseEnter={() => setSel(k)} onClick={() => setSel(k)} />)}
      </svg>
    </figure>
  );
}

/* ---------------------------------------------------------------- tables */
function MiniTable({ cols, rows }: { cols: { k: string; h: string; f: (v: any) => string }[]; rows: Record<string, any>[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[12px]">
        <thead><tr className="text-faint">{cols.map((c, i) => <th key={c.k} className={`kicker pb-1 font-semibold ${i ? "text-right" : "text-left"}`}>{c.h}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, j) => (
            <tr key={j} className="border-t hair">
              {cols.map((c, i) => <td key={c.k} className={`num py-[6px] ${i ? "text-right" : "text-left"}`}>{c.f(r[c.k])}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const fpct = (v: number | null) => (v == null ? "—" : pct(v, false));
const fx = (v: number | null) => (v == null ? "—" : `${v.toFixed(2)}×`);
const fd = (v: number | null) => (v == null ? "—" : `${Math.round(v)}`);

function SectionBody({ s, url }: { s: Section; url: string }) {
  const notes = s.notes.length > 0 && <p className="mt-2 text-[11px] leading-snug text-faint">{s.notes.join(" ")} <SourceChip label="screener" href={url} /></p>;
  if (s.key === "trajectory") {
    const rev = s.series.map((r) => ({ x: r.fy, y: r.revenue }));
    const mar = s.series.map((r) => ({ x: r.fy, y: r.margin }));
    return (
      <div className="space-y-4">
        <Bars data={rev} fmt={(v) => `₹${cr(v)} cr`} label="Revenue, ₹ cr" />
        <Line data={mar} fmt={(v) => pct(v, false)} label="EBITDA margin (screener)" />
        <MiniTable cols={[{ k: "label", h: "CAGR", f: (v) => v }, { k: "y3", h: "3y", f: fpct }, { k: "y5", h: "5y", f: fpct }, { k: "y10", h: "10y", f: fpct }]} rows={s.rows} />
        {notes}
      </div>
    );
  }
  if (s.key === "balance") {
    return (
      <div className="space-y-3">
        <MiniTable cols={[{ k: "fy", h: "Year", f: (v) => v }, { k: "borrowings", h: "Borrowings", f: (v) => cr(v) }, { k: "equity", h: "Equity", f: (v) => cr(v) }, { k: "de", h: "D/E", f: (v) => (v == null ? "—" : v.toFixed(2)) }]} rows={s.series} />
        <MiniTable cols={[{ k: "fy", h: "Days", f: (v) => v }, { k: "debtor", h: "Debtor", f: fd }, { k: "inventory", h: "Inventory", f: fd }, { k: "payable", h: "Payable", f: fd }, { k: "ccc", h: "CCC", f: fd }]} rows={s.rows} />
        {notes}
      </div>
    );
  }
  if (s.key === "cash") {
    return (
      <div className="space-y-3">
        <MiniTable cols={[{ k: "fy", h: "₹ cr", f: (v) => v }, { k: "pat", h: "PAT", f: (v) => cr(v) }, { k: "cfo", h: "CFO", f: (v) => cr(v) }, { k: "cfo_pat", h: "CFO/PAT", f: fx }, { k: "fcf", h: "FCF", f: (v) => cr(v) }]} rows={s.series} />
        {notes}
      </div>
    );
  }
  if (s.key === "returns") {
    return (
      <div className="space-y-3">
        <MiniTable cols={[{ k: "fy", h: "Year", f: (v) => v }, { k: "roe", h: "ROE", f: fpct }, { k: "roce", h: "ROCE", f: fpct }]} rows={s.series} />
        {notes}
      </div>
    );
  }
  if (s.key === "snapshot") {
    return (
      <div>
        <dl className="divide-y hair border-y hair text-[12.5px]">
          {s.rows.map((r) => (
            <div key={r.label} className="flex justify-between py-1.5">
              <dt className="text-sub">{r.label}</dt>
              <dd className="num">{r.value == null ? "—" : r.unit === "x" ? `${r.value.toFixed(1)}×` : `₹${cr(r.value)} cr`}</dd>
            </div>
          ))}
        </dl>
        {notes}
      </div>
    );
  }
  return null;
}

/* ---------------------------------------------------------------- dossier */
function Dossier({ snap, live, onBack }: { snap: ResearchSnapshot; live: boolean; onBack: () => void }) {
  const [vital, setVital] = useState<string | null>(null);
  const url = snap.sources[0]?.doc_id ?? "";
  const v = snap.vitals.find((x) => x.key === vital);
  const byKey = Object.fromEntries(snap.sections.map((s) => [s.key, s]));
  return (
    <div>
      <header className="sticky top-0 z-20 -mx-4 border-b hair bg-paper/95 px-4 pb-2.5 pt-2 backdrop-blur-sm">
        <div className="flex items-baseline gap-2">
          <button onClick={onBack} className="kicker text-faint hover:text-ink" aria-label="Back to research">←</button>
          <h1 className="text-[20px] font-semibold tracking-[-0.02em]">{snap.symbol}</h1>
          <span className="min-w-0 truncate text-[11.5px] text-sub">{snap.company}</span>
          <span className="ml-auto shrink-0 text-right">
            <span className="num text-[16px] font-medium">{snap.price != null ? `₹${cr(snap.price)}` : "—"}</span>
            {snap.day_change != null && <Delta v={snap.day_change} text={` ${pct(snap.day_change)}`} className="text-[11.5px]" />}
          </span>
        </div>
        <div className="no-scrollbar -mx-4 mt-2 flex gap-1.5 overflow-x-auto px-4">
          {snap.vitals.map((x) => (
            <button key={x.key} onClick={() => setVital(vital === x.key ? null : x.key)} aria-expanded={vital === x.key}
              className={`num shrink-0 rounded-[4px] border px-2 py-1 text-left leading-tight transition-colors duration-150 ease-quiet ${x.off ? "border-signal text-signal" : "hair"} ${vital === x.key ? "bg-wash" : ""}`}>
              <span className={`block text-[9.5px] font-semibold uppercase tracking-[0.08em] ${x.off ? "" : "text-faint"}`}>{x.label}</span>
              <span className="block text-[12.5px]">{x.value}</span>
            </button>
          ))}
        </div>
        {v && <p className="mt-1.5 text-[11.5px] leading-snug text-sub">{v.note} <SourceChip label="scr" href={v.source.doc_id} /></p>}
      </header>

      <section className="pt-4">
        <Kicker>Verdict</Kicker>
        <PointList points={snap.verdict} />
      </section>

      <div className="mt-4 border-b border-ink">
        {byKey.ledger && (
          <Fold title="Promise ledger" conclusion={byKey.ledger.conclusion} defaultOpen>
            <LedgerView snap={snap} />
          </Fold>
        )}
        {(snap.bull.length > 0 || snap.bear.length > 0) && (
          <Fold title="Bull vs bear" conclusion={`${snap.bull.length} for, ${snap.bear.length} against, every point cited`}>
            <Kicker className="mt-1 text-up">Bull</Kicker>
            <PointList points={snap.bull} />
            <Kicker className="mt-3 text-down">Bear</Kicker>
            <PointList points={snap.bear} />
          </Fold>
        )}
        {snap.questions.length > 0 && (
          <Fold title="3 questions for the next concall" conclusion={snap.questions[0].text}>
            <PointList points={snap.questions} numbered />
          </Fold>
        )}
        {["trajectory", "balance", "cash", "returns", "snapshot"].filter((k) => byKey[k]).map((k) => (
          <Fold key={k} title={byKey[k].title} conclusion={byKey[k].conclusion}>
            <SectionBody s={byKey[k]} url={url} />
          </Fold>
        ))}
      </div>

      <footer className="mb-10 mt-6 space-y-1.5 text-[11px] leading-snug text-faint">
        <p>Sources, primary only: {snap.sources.map((c, i) => (
          <span key={i}>{i ? ", " : ""}{c.doc_id ? <a href={c.doc_id} target="_blank" rel="noreferrer" className="underline decoration-rule underline-offset-2 hover:text-ink">{c.label}</a> : c.label}</span>
        ))}. No news, broker notes or model knowledge.</p>
        {snap.notes.map((n, i) => <p key={i}>Note: {n}</p>)}
        <p className="num">Built {day(snap.built_at)} {snap.built_at.slice(11, 16)}{live ? " (live)" : ""} · {snap.cost_line}</p>
      </footer>
    </div>
  );
}

/* ---------------------------------------------------------------- home */
type Idx = { symbol: string; company: string; built_at: string; promises: number }[];

export function Research({ holdings }: { holdings: string[] }) {
  const [symbol, setSymbol] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [idx, setIdx] = useState<Idx>([]);
  const load = useResearch(symbol);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { getJSON<Idx>(`${BASE}data/research/index.json`).then((x) => x && setIdx(x)); }, []);
  useEffect(() => { window.scrollTo({ top: 0 }); }, [symbol]);
  const ready = useMemo(() => new Set(idx.map((x) => x.symbol)), [idx]);
  const open = (s: string) => { const t = s.trim().toUpperCase(); if (/^[A-Z0-9&-]{1,20}$/.test(t)) setSymbol(t); };

  if (symbol && load.state === "ready") return <Dossier snap={load.snap} live={load.live} onBack={() => setSymbol(null)} />;

  return (
    <div className="pt-5">
      <h1 className="text-[30px] font-semibold leading-none tracking-[-0.03em]">Research</h1>
      <p className="mt-2 text-[12.5px] leading-relaxed text-sub">Any NSE ticker, from annual reports, concall transcripts and screener’s tables only. Headline: what management promised, and whether they delivered.</p>
      <form onSubmit={(e) => { e.preventDefault(); open(q); }} className="mt-4 flex gap-2">
        <input ref={input} value={q} onChange={(e) => setQ(e.target.value)} placeholder="NSE symbol, e.g. KPITTECH" autoCapitalize="characters" spellCheck={false}
          className="num h-10 min-w-0 flex-1 rounded-[4px] border hair bg-paper px-3 text-[14px] uppercase outline-none placeholder:normal-case placeholder:text-faint focus:border-ink" />
        <button className="h-10 rounded-[4px] bg-ink px-4 text-[13px] font-medium text-paper">Open</button>
      </form>

      {symbol && load.state === "loading" && (
        <div className="mt-5 border-y hair py-3">
          <Kicker className="text-ink">{symbol} · {load.live ? "building live" : "opening"}</Kicker>
          <ol className="mt-2 space-y-1 text-[12.5px]">
            {load.stages.map((s, i) => (
              <li key={i} className={`flex gap-2 ${i === load.stages.length - 1 ? "text-ink" : "text-faint"}`}>
                <span className="num w-3">{i === load.stages.length - 1 ? "›" : "✓"}</span>{s}
              </li>
            ))}
          </ol>
          {load.live && <p className="mt-2 text-[11px] text-faint">A new ticker takes a minute or two on free-tier models. Precomputed ones open instantly.</p>}
        </div>
      )}
      {symbol && load.state === "error" && <p className="mt-5 border-l-2 border-signal pl-3 text-[12.5px] text-sub">{load.message}</p>}

      {idx.length > 0 && (
        <section className="mt-6">
          <Kicker>Ready now</Kicker>
          <ul className="mt-1 border-b hair">
            {idx.map((x) => (
              <li key={x.symbol} className="border-t hair">
                <button onClick={() => setSymbol(x.symbol)} className="flex w-full items-baseline gap-3 py-2.5 text-left">
                  <span className="num w-[84px] shrink-0 text-[13px] font-medium">{x.symbol}</span>
                  <span className="min-w-0 flex-1 truncate text-[12.5px] text-sub">{x.company}</span>
                  <span className="num shrink-0 text-[11px] text-faint">{x.promises} promises</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      <section className="mt-6">
        <Kicker>Your holdings</Kicker>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {holdings.map((s) => (
            <button key={s} onClick={() => setSymbol(s)}
              className={`num h-7 rounded-full border px-2.5 text-[11.5px] ${ready.has(s) ? "border-ink text-ink" : "hair text-sub"} hover:border-ink`}>
              {s}
            </button>
          ))}
        </div>
        <p className="mt-2 text-[11px] text-faint">Outlined: precomputed. Others build live (needs the backend running).</p>
      </section>
    </div>
  );
}
