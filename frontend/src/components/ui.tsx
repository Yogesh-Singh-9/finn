import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { MINUS, host, tone } from "../format";
import type { BriefEvent } from "../types";

/* ---------- source chip: tiny tag after a number/claim, expands to quote + link ---------- */
export function SourceChip({ label, quote, href, note }: { label: string; quote?: string | null; href?: string | null; note?: string | null }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  const expandable = Boolean(quote || href || note);
  // keep the popover inside the phone column: shift it left when the chip sits near the right edge
  const [shift, setShift] = useState(0);
  useEffect(() => {
    if (!open || !ref.current) return;
    const r = ref.current.getBoundingClientRect();
    const col = ref.current.closest("main")?.getBoundingClientRect() ?? { left: 0, right: window.innerWidth };
    const width = Math.min(300, window.innerWidth * 0.82);
    const overflow = r.left + width - (col.right - 12);
    setShift(overflow > 0 ? -Math.min(overflow, r.left - col.left - 12) : 0);
  }, [open]);
  return (
    <span ref={ref} className="relative inline-block align-baseline">
      <button
        type="button"
        onClick={() => expandable && setOpen((o) => !o)}
        aria-expanded={open}
        className={`num ml-1 inline-flex h-[15px] items-center rounded-[3px] border hair px-1 text-[9.5px] leading-none text-sub transition-colors duration-150 ease-quiet ${
          expandable ? "hover:border-ink/40 hover:text-ink" : "cursor-default"
        } ${open ? "border-ink/50 text-ink" : ""}`}
      >
        {label}
      </button>
      {open && (
        <span style={{ left: shift }} className="absolute top-[19px] z-30 block w-[min(300px,82vw)] rounded-md border hair bg-paper p-3 text-left text-[12.5px] leading-snug text-ink shadow-[0_8px_24px_-12px_rgba(0,0,0,0.25)]">
          {quote && <span className="block border-l-2 border-ink/20 pl-2 italic text-sub">“{quote}”</span>}
          {note && <span className={`block text-sub ${quote ? "mt-2" : ""}`}>{note}</span>}
          {href && (
            <a href={href} target="_blank" rel="noreferrer" className="mt-2 block truncate text-[11.5px] text-ink underline decoration-rule underline-offset-2">
              {host(href)} ↗
            </a>
          )}
        </span>
      )}
    </span>
  );
}

/* ---------- coloured delta: green/red only for direction ---------- */
export function Delta({ v, text, className = "" }: { v: number | null | undefined; text: string; className?: string }) {
  const t = tone(v);
  const c = t === "up" ? "text-up" : t === "down" ? "text-down" : "text-faint";
  return <span className={`num ${c} ${className}`}>{text}</span>;
}

export function Kicker({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`kicker text-sub ${className}`}>{children}</div>;
}

/* ---------- sparkline (scrubbable when `onScrub` is given) ---------- */
export function Spark({ data, w = 64, h = 20, strokeTone }: { data: number[]; w?: number; h?: number; strokeTone?: "up" | "down" | "flat" }) {
  if (data.length < 2) return null;
  const lo = Math.min(...data), hi = Math.max(...data), span = hi - lo || 1;
  const pts = data.map((v, i) => `${((i / (data.length - 1)) * w).toFixed(1)},${(h - ((v - lo) / span) * (h - 2) - 1).toFixed(1)}`).join(" ");
  const stroke = strokeTone === "up" ? "rgb(var(--up))" : strokeTone === "down" ? "rgb(var(--down))" : "rgb(var(--sub))";
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden className="block overflow-visible">
      <polyline points={pts} fill="none" stroke={stroke} strokeWidth="1.25" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

/* ---------- bottom sheet ---------- */
export function Sheet({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode }) {
  const [mounted, setMounted] = useState(open);
  useEffect(() => {
    if (open) setMounted(true);
    else {
      const t = setTimeout(() => setMounted(false), 200);
      return () => clearTimeout(t);
    }
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [open, onClose]);
  if (!mounted) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-center" role="dialog" aria-modal="true">
      <button aria-label="Close" onClick={onClose} className={`absolute inset-0 bg-black/30 transition-opacity duration-200 ease-quiet ${open ? "opacity-100" : "opacity-0"}`} />
      <div
        className={`absolute bottom-0 w-full max-w-[440px] rounded-t-xl border-t hair bg-paper pb-[max(16px,env(safe-area-inset-bottom))] transition-transform duration-200 ease-quiet ${
          open ? "translate-y-0" : "translate-y-full"
        }`}
      >
        <div className="mx-auto mt-2 h-1 w-9 rounded-full bg-rule" />
        <div className="flex items-start justify-between gap-3 px-4 pb-2 pt-3">
          <div className="min-w-0 flex-1">{title}</div>
          <button onClick={onClose} className="kicker shrink-0 pt-0.5 text-sub hover:text-ink">Close</button>
        </div>
        <div className="max-h-[70vh] overflow-y-auto px-4">{children}</div>
      </div>
    </div>
  );
}

/* ---------- "why it's here" ---------- */
export const WhyContext = createContext<(e: BriefEvent) => void>(() => {});

export function WhyButton({ event }: { event: BriefEvent }) {
  const open = useContext(WhyContext);
  return (
    <button onClick={() => open(event)} className="group inline-flex items-center gap-1.5 text-[11.5px] text-sub hover:text-ink">
      <span className="underline decoration-rule underline-offset-[3px] group-hover:decoration-ink/40">Why it’s here</span>
      <span className="num text-ink">{Math.round(event.score)}</span>
    </button>
  );
}

export function FilingLink({ href, children = "Filing" }: { href: string | null | undefined; children?: ReactNode }) {
  if (!href) return null;
  return (
    <a href={href} target="_blank" rel="noreferrer" className="text-[11.5px] text-sub underline decoration-rule underline-offset-[3px] hover:text-ink">
      {children} ↗
    </a>
  );
}

export function NeedsDot() {
  return <span className="inline-block h-[7px] w-[7px] shrink-0 rounded-full bg-signal" aria-label="needs you" />;
}

export const minus = MINUS;
