<script>
  import { writable } from 'svelte/store';

  /**
   * Global toast store
   * Usage: import { toasts, addToast } from './Toast.svelte';
   */
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

  /** @type {{ items: Array<{id: number, message: string, type: string}> }} */
  let { items = [] } = $props();

  const typeStyles = {
    success: 'border-l-4 border-[var(--success)]',
    error: 'border-l-4 border-[var(--danger)]',
    info: 'border-l-4 border-[var(--accent)]',
    warning: 'border-l-4 border-[var(--warning)]'
  };

  const typeIcons = {
    success: 'M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z',
    error: 'M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z',
    info: 'M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
    warning: 'M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z'
  };
</script>

<div class="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm">
  {#each items as toast (toast.id)}
    <div
      class="bg-[var(--bg-surface)] shadow-lg rounded-lg p-4 flex items-start gap-3
             {typeStyles[toast.type] || typeStyles.info}
             animate-[slideIn_0.2s_ease-out]"
      role="alert"
    >
      <svg class="w-5 h-5 shrink-0 mt-0.5 {
        toast.type === 'success' ? 'text-[var(--success)]' :
        toast.type === 'error' ? 'text-[var(--danger)]' :
        toast.type === 'warning' ? 'text-[var(--warning)]' :
        'text-[var(--accent)]'
      }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d={typeIcons[toast.type] || typeIcons.info}/>
      </svg>
      <p class="text-sm text-[var(--text-primary)] flex-1">{toast.message}</p>
      <button
        onclick={() => removeToast(toast.id)}
        class="text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors shrink-0"
        aria-label="Dismiss"
      >
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
        </svg>
      </button>
    </div>
  {/each}
</div>

<style>
  @keyframes slideIn {
    from { opacity: 0; transform: translateX(100%); }
    to { opacity: 1; transform: translateX(0); }
  }
</style>
