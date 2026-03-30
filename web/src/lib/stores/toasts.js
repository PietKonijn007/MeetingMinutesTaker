import { writable } from 'svelte/store';

export const toasts = writable([]);

let toastId = 0;

export function addToast(message, type = 'info', duration = 3000) {
  const id = ++toastId;
  toasts.update((all) => [...all, { id, message, type }]);
  if (duration > 0) {
    setTimeout(() => {
      toasts.update((all) => all.filter((t) => t.id !== id));
    }, duration);
  }
  return id;
}

export function removeToast(id) {
  toasts.update((all) => all.filter((t) => t.id !== id));
}
