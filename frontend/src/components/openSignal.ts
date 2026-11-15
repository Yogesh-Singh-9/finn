import { useEffect } from "react";

/** Ask a collapsed card to open itself (used by the Morning Call's chapter taps). */
export function signalOpen(id: string) {
  window.dispatchEvent(new CustomEvent<string>("finn:open", { detail: id }));
}

export function useOpenSignal(id: string, open: () => void) {
  useEffect(() => {
    const h = (e: Event) => { if ((e as CustomEvent<string>).detail === id) open(); };
    window.addEventListener("finn:open", h);
    return () => window.removeEventListener("finn:open", h);
  }, [id, open]);
}
