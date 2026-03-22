import { writable } from 'svelte/store';

const PIN_KEY = 'benderpi_pin';

function createSession() {
  const stored = sessionStorage.getItem(PIN_KEY);
  const { subscribe, set, update } = writable({
    authenticated: false,
    pin: stored,
  });

  return {
    subscribe,
    login(pin) {
      sessionStorage.setItem(PIN_KEY, pin);
      set({ authenticated: true, pin });
    },
    logout() {
      sessionStorage.removeItem(PIN_KEY);
      set({ authenticated: false, pin: null });
    },
    restore(pin) {
      set({ authenticated: true, pin });
    },
  };
}

export const session = createSession();
