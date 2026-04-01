import { writable } from 'svelte/store';

const STORAGE_KEY = 'benderpi_theme';

function createThemeStore() {
  const saved = typeof localStorage !== 'undefined'
    ? localStorage.getItem(STORAGE_KEY)
    : null;
  const initial = saved || 'dark';

  const { subscribe, set } = writable(initial);

  return {
    subscribe,
    toggle() {
      const html = document.documentElement;
      const next = html.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      html.setAttribute('data-theme', next);
      localStorage.setItem(STORAGE_KEY, next);
      set(next);
    },
    init() {
      const saved = localStorage.getItem(STORAGE_KEY) || 'dark';
      document.documentElement.setAttribute('data-theme', saved);
      set(saved);
    },
  };
}

export const theme = createThemeStore();
