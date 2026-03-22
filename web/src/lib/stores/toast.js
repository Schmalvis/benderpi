import { writable } from 'svelte/store';

const { subscribe, update } = writable([]);

let _id = 0;

function push(message, type = 'success', duration = 2000) {
  const id = ++_id;
  update(toasts => [...toasts, { id, message, type }]);
  setTimeout(() => remove(id), duration);
}

function remove(id) {
  update(toasts => toasts.filter(t => t.id !== id));
}

export const toast = { subscribe, push, remove };
