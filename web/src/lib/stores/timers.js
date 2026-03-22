import { writable } from 'svelte/store';
import { getTimers } from '../api.js';

export const timers = writable([]);

export function startTimerPolling() {
  const interval = setInterval(async () => {
    try {
      const data = await getTimers();
      timers.set(data.timers || []);
    } catch {
      // Leave existing timer state intact on failure
    }
  }, 2000);
  return () => clearInterval(interval);
}
