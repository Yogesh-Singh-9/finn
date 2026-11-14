/** Swiss ledger: paper, ink, one signal colour. Colours are CSS variables so dark mode
 *  is a true inverse, not a second palette bolted on. */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        paper: "rgb(var(--paper) / <alpha-value>)",
        ink: "rgb(var(--ink) / <alpha-value>)",
        sub: "rgb(var(--sub) / <alpha-value>)",
        faint: "rgb(var(--faint) / <alpha-value>)",
        rule: "rgb(var(--rule) / <alpha-value>)",
        wash: "rgb(var(--wash) / <alpha-value>)",
        signal: "rgb(var(--signal) / <alpha-value>)",
        up: "rgb(var(--up) / <alpha-value>)",
        down: "rgb(var(--down) / <alpha-value>)",
      },
      fontFamily: {
        sans: ['"Inter Tight"', "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "monospace"],
      },
      fontSize: { "2xs": ["10px", "14px"] },
      transitionTimingFunction: { quiet: "cubic-bezier(0.2, 0, 0, 1)" },
    },
  },
  plugins: [],
};
