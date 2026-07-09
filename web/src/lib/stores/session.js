import { writable } from 'svelte/store';

// Stores the server-issued auth token (NOT the PIN). The token is HMAC-signed
// server-side, expires in ~12h, and is invalidated whenever bender-web
// restarts — in which case the API returns 401 and we re-login.
const TOKEN_KEY = 'benderpi_token';

function createSession() {
  const stored = sessionStorage.getItem(TOKEN_KEY);
  const { subscribe, set } = writable({
    authenticated: false,
    token: stored,
  });

  return {
    subscribe,
    login(token) {
      sessionStorage.setItem(TOKEN_KEY, token);
      set({ authenticated: true, token });
    },
    logout() {
      sessionStorage.removeItem(TOKEN_KEY);
      set({ authenticated: false, token: null });
    },
    // Optimistically mark a stored token as authenticated (validated by the
    // first real API call on mount).
    restore(token) {
      set({ authenticated: true, token });
    },
  };
}

export const session = createSession();
