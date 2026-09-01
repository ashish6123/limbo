import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        bg: "var(--bg)",
        surface: "var(--surface)",
        "surface-raised": "var(--surface-raised)",
        border: {
          DEFAULT: "var(--border)",
          subtle: "var(--border-subtle)",
        },
        text: {
          primary: "var(--text-primary)",
          secondary: "var(--text-secondary)",
          tertiary: "var(--text-tertiary)",
        },
        money: {
          DEFAULT: "var(--accent-emerald)",
          dim: "var(--accent-emerald-dim)",
        },
        risk: {
          DEFAULT: "var(--accent-amber)",
          dim: "var(--accent-amber-dim)",
        },
        danger: {
          DEFAULT: "var(--accent-rose)",
          dim: "var(--accent-rose-dim)",
        },
        dup: {
          DEFAULT: "var(--accent-purple)",
          dim: "var(--accent-purple-dim)",
        },
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};
export default config;
