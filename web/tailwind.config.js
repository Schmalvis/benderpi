/** @type {import('tailwindcss').Config} */
export default {
  content: ['./src/**/*.{svelte,js}', './index.html'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        bg: 'var(--bg)',
        'bg-card': 'var(--bg-card)',
        'bg-sidebar': 'var(--bg-sidebar)',
        'bg-input': 'var(--bg-input)',
        accent: 'var(--accent)',
        'accent-red': 'var(--accent-red)',
        'text-default': 'var(--text)',
        'text-muted': 'var(--text-muted)',
        success: 'var(--success)',
        warning: 'var(--warning)',
        error: 'var(--error)',
        border: 'var(--border)',
      },
      borderRadius: {
        DEFAULT: 'var(--radius)',
        lg: 'var(--radius-lg)',
      },
      boxShadow: {
        DEFAULT: 'var(--shadow)',
        lg: 'var(--shadow-lg)',
        glow: '0 0 12px var(--glow)',
      },
      fontFamily: {
        sans: ['var(--font-sans)'],
        mono: ['var(--font-mono)'],
      },
      animation: {
        'avatar-glow': 'avatar-glow 3s ease-in-out infinite',
        'status-pulse': 'status-pulse 2s ease-in-out infinite',
        'timer-flash': 'timer-flash 0.8s ease-in-out infinite',
      },
      keyframes: {
        'avatar-glow': {
          '0%, 100%': { boxShadow: '0 0 8px var(--glow)' },
          '50%': { boxShadow: '0 0 20px var(--glow), 0 0 40px rgba(74,158,255,0.08)' },
        },
        'status-pulse': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.6' },
        },
        'timer-flash': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.3' },
        },
      },
    },
  },
  plugins: [],
};
