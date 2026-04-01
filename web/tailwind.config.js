/** @type {import("tailwindcss").Config} */
export default {
  content: ["./src/**/*.{svelte,js}", "./index.html"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        bg: "var(--bg)",
        "bg-card": "var(--bg-card)",
        "bg-sidebar": "var(--bg-sidebar)",
        "bg-input": "var(--bg-input)",
        accent: "var(--accent)",
        "accent-bright": "var(--accent-bright)",
        "accent-red": "var(--accent-red)",
        "text-default": "var(--text)",
        "text-muted": "var(--text-muted)",
        success: "var(--success)",
        warning: "var(--warning)",
        error: "var(--error)",
        border: "var(--border)",
      },
      borderRadius: {
        DEFAULT: "var(--radius)",
        lg: "var(--radius-lg)",
      },
      boxShadow: {
        DEFAULT: "var(--shadow)",
        lg: "var(--shadow-lg)",
        glow: "0 0 12px var(--glow)",
        "glow-strong": "0 0 24px var(--glow-strong)",
      },
      fontFamily: {
        sans: ["var(--font-sans)"],
        mono: ["var(--font-mono)"],
        display: ["var(--font-display)"],
      },
      animation: {
        "avatar-glow": "avatar-glow 3s ease-in-out infinite",
        "status-pulse": "status-pulse 2s ease-in-out infinite",
        "timer-flash": "timer-flash 1s ease-in-out infinite",
        "scan-in": "scan-in 0.3s ease-out",
      },
      keyframes: {
        "avatar-glow": {
          "0%, 100%": { boxShadow: "0 0 6px var(--glow)" },
          "50%": { boxShadow: "0 0 18px var(--glow-strong)" },
        },
        "status-pulse": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.5" },
        },
        "timer-flash": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.3" },
        },
        "scan-in": {
          from: { opacity: "0", transform: "translateY(8px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
    },
  },
  plugins: [],
};
