import { readable } from 'svelte/store';
import { getHealth } from '../api.js';

export const health = readable({ status: 'unknown' }, (set) => {
  let active = true;

  const poll = async () => {
    if (!active) return;
    try {
      const data = await getHealth();
      set({ ...data, status: 'online' });
    } catch {
      set({ status: 'offline' });
    }
  };

  poll();
  const interval = setInterval(poll, 5000);
  return () => { active = false; clearInterval(interval); };
});
