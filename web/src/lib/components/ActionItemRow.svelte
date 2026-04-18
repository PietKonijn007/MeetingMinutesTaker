<script>
  import { api } from '$lib/api.js';

  /**
   * @type {{
   *   item: {
   *     id: string,
   *     description: string,
   *     owner: string,
   *     due_date: string | null,
   *     status: string,
   *     meeting_id: string,
   *     meeting_title: string
   *   },
   *   showMeeting?: boolean,
   *   onUpdate?: (item: any) => void
   * }}
   */
  let { item, showMeeting = true, onUpdate } = $props();

  let loading = $state(false);

  const itemId = $derived(item.action_item_id || item.id);
  const isDone = $derived(item.status === 'done');
  const isOverdue = $derived(
    item.due_date && !isDone && new Date(item.due_date) < new Date()
  );

  function formatDate(dateStr) {
    if (!dateStr) return null;
    const d = new Date(dateStr);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  }

  async function toggleStatus() {
    if (!itemId) {
      console.error('Action item missing ID, cannot update:', item);
      return;
    }
    loading = true;
    try {
      const newStatus = isDone ? 'open' : 'done';
      await api.updateActionItem(itemId, { status: newStatus });
      item.status = newStatus;
      onUpdate?.(item);
    } catch (e) {
      console.error('Failed to update action item:', e);
    } finally {
      loading = false;
    }
  }
</script>

<div
  class="flex items-start gap-3 p-3 rounded-lg hover:bg-[var(--bg-surface-hover)] transition-colors duration-150
         {isOverdue ? 'border-l-2 border-[var(--warning)]' : ''}"
>
  <button
    onclick={toggleStatus}
    disabled={loading}
    class="mt-0.5 shrink-0 w-5 h-5 rounded border-2 flex items-center justify-center transition-all duration-150
           {isDone
             ? 'bg-[var(--success)] border-[var(--success)] text-white'
             : 'border-[var(--border-subtle)] hover:border-[var(--accent)]'}"
    aria-label={isDone ? 'Mark as open' : 'Mark as done'}
  >
    {#if isDone}
      <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7"/>
      </svg>
    {/if}
  </button>

  <div class="flex-1 min-w-0">
    <p class="text-sm text-[var(--text-primary)] {isDone ? 'line-through opacity-60' : ''}">
      {item.description}
    </p>
    <div class="flex items-center gap-2 mt-1 flex-wrap">
      {#if item.owner}
        <span class="text-xs text-[var(--text-secondary)]">{item.owner}</span>
      {/if}
      {#if item.due_date}
        <span class="text-xs {isOverdue ? 'text-[var(--warning)] font-medium' : 'text-[var(--text-muted)]'}">
          {isOverdue ? 'Overdue: ' : 'Due '}{formatDate(item.due_date)}
        </span>
      {/if}
      {#if showMeeting && item.meeting_title}
        <a
          href="/meeting/{item.meeting_id}"
          class="text-xs text-[var(--accent)] hover:underline"
        >
          from {item.meeting_title}
        </a>
      {/if}
    </div>
  </div>
</div>
