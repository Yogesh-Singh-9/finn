import { useEffect, useState } from "react";
import { Today } from "./Today";
import type { BriefSnapshot } from "./types";

type Tab = "today" | "holdings" | "research";

function useTheme(): [boolean, () => void] {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    try {
      localStorage.setItem("finn-theme", dark ? "dark" : "light");
    } catch {
      /* private mode: theme just won't persist */
    }
  }, [dark]);
  return [dark, () => setDark((d) => !d)];
}

function Placeholder({ title, line }: { title: string; line: string }) {
  return (
    <div className="pt-5">
      <h1 className="text-[30px] font-semibold leading-none tracking-[-0.03em]">{title}</h1>
      <p className="mt-3 max-w-[32ch] text-[13px] leading-relaxed text-sub">{line}</p>
    </div>
  );
}

export default function App() {
  const [tab, setTab] = useState<Tab>("today");
  const [brief, setBrief] = useState<BriefSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dark, toggle] = useTheme();

  useEffect(() => {
    // Static snapshot shipped with the bundle: no backend call for Today.
    fetch(`${import.meta.env.BASE_URL}data/brief.json`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`brief.json ${r.status}`))))
      .then(setBrief)
      .catch((e: Error) => setError(e.message));
  }, []);

  return (
    <div className="mx-auto min-h-screen max-w-[440px] bg-paper sm:border-x sm:hair">
      <div className="flex items-center justify-between px-4 pt-[max(10px,env(safe-area-inset-top))]">
        <span className="text-[13px] font-bold tracking-[0.18em]">FINN</span>
        <button onClick={toggle} className="kicker text-faint hover:text-ink" aria-label="Toggle dark mode">
          {dark ? "Light" : "Dark"}
        </button>
      </div>
      <main className="px-4 pb-24">
        {tab === "today" &&
          (brief ? (
            <Today brief={brief} />
          ) : (
            <p className="pt-10 text-[13px] text-sub">{error ? `Couldn't load the brief (${error}).` : ""}</p>
          ))}
        {tab === "holdings" && <Placeholder title="Holdings" line="Your 18 holdings, each with its thesis and a “watch next quarter” line from the latest guidance. Next phase." />}
        {tab === "research" && <Placeholder title="Research" line="Any NSE ticker, from annual reports and concall transcripts only, with the Promise Ledger. Next phase." />}
      </main>
      <nav className="fixed bottom-0 left-1/2 z-40 w-full max-w-[440px] -translate-x-1/2 border-t hair bg-paper/95 pb-[env(safe-area-inset-bottom)] backdrop-blur-sm">
        <div className="grid grid-cols-3">
          {(["today", "holdings", "research"] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => { setTab(t); window.scrollTo({ top: 0 }); }}
              className={`relative h-12 text-[12.5px] font-medium capitalize transition-colors duration-150 ease-quiet ${tab === t ? "text-ink" : "text-faint hover:text-sub"}`}
            >
              {tab === t && <span className="absolute left-1/2 top-0 h-[2px] w-8 -translate-x-1/2 bg-ink" />}
              {t}
            </button>
          ))}
        </div>
      </nav>
    </div>
  );
}
