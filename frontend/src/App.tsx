import { useEffect, useState } from "react";
import { Holdings } from "./Holdings";
import { Research } from "./Research";
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

export default function App() {
  const [tab, setTab] = useState<Tab>("today");
  const [brief, setBrief] = useState<BriefSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dark, toggle] = useTheme();
  const [researchSym, setResearchSym] = useState<{ s: string; n: number } | null>(null);
  const openResearch = (s: string) => { setResearchSym({ s, n: Date.now() }); setTab("research"); window.scrollTo({ top: 0 }); };

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
        {tab === "holdings" && <Holdings brief={brief} onResearch={openResearch} />}
        {tab === "research" && <Research holdings={brief?.symbols ?? []} start={researchSym} />}
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
