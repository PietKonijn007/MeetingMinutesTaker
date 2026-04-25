<script>
  import { api } from '$lib/api.js';

  /**
   * @type {{
   *   item: {
   *     id?: string,
   *     action_item_id?: string,
   *     description: string,
   *     owner: string | null,
   *     due_date: string | null,
   *     status: string,
   *     proposal_state?: string,
   *     meeting_id: string,
   *     meeting_title?: string
   *   },
   *   showMeeting?: boolean,
   *   onUpdate?: (item: any) => void
   * }}
   */
  let { item, showMeeting = true, onUpdate } = $props();

  let loading = $state(false);
  let editing = $state(false);
  let draft = $state({ description: '', owner: '', due_date: '' });

  const itemId = $derived(item.action_item_id || item.id);
  const proposalState = $derived(item.proposal_state || 'confirmed');
  const isProposed = $derived(proposalState === 'proposed');
  const isRejected = $derived(proposalState === 'rejected');
  const isDone = $derived(item.status === 'done');
  const isOverdue = $derived(
    item.due_date && !isDone && new Date(item.due_date) < new Date()
  );

  function formatDate(dateStr) {
    if (!dateStr) return null;
    const d = new Date(dateStr);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  }

  async function patch(payload, optimistic) {
    if (!itemId) {
      console.error('Action item missing ID, cannot update:', item);
      return;
    }
    loading = true;
    try {
      const updated = await api.updateActionItem(itemId, payload);
      Object.assign(item, optimistic ?? updated);
      onUpdate?.(item);
    } catch (e) {
      console.error('Failed to update action item:', e);
    } finally {
      loading = false;
    }
  }

  async function toggleStatus() {
    if (isProposed) return; // can't tick a proposal
    const newStatus = isDone ? 'open' : 'done';
    await patch({ status: newStatus }, { status: newStatus });
  }

  async function accept() {
    await patch({ proposal_state: 'confirmed' }, { proposal_state: 'confirmed' });
  }

  async function reject() {
    await patch({ proposal_state: 'rejected' }, { proposal_state: 'rejected' });
  }

  function startEdit() {
    draft = {
      description: item.description || '',
      owner: item.owner || '',
      due_date: item.due_date || '',
    };
    editing = true;
  }

  async function saveEdit() {
    const payload = {
      description: draft.description.trim(),
      owner: draft.owner.trim(),
      due_date: draft.due_date.trim(),
    };
    if (!payload.description) {
      return;
    }
    await patch(payload, payload);
    editing = false;
  }

  function cancelEdit() {
    editing = false;
  }
</script>

<div
  class="flex items-start gap-3 p-3 rounded-lg transition-colors duration-150
         {isProposed
           ? 'bg-[var(--bg-surface)] border border-dashed border-[var(--accent)]/40'
           : 'hover:bg-[var(--bg-surface-hover)]'}
         {isRejected ? 'opacity-50' : ''}
         {isOverdue && !isProposed ? 'border-l-2 border-[var(--warning)]' : ''}"
>
  {#if isProposed}
    <span
      class="mt-0.5 shrink-0 w-5 h-5 rounded-full border-2 border-dashed border-[var(--accent)]/60 flex items-center justify-center text-[var(--accent)]"
      title="Proposed — needs review"
      aria-label="Proposed action — needs review"
    >
      <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M12 6v6m0 4h.01" />
      </svg>
    </span>
  {:else}
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
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" />
        </svg>
      {/if}
    </button>
  {/if}

  <div class="flex-1 min-w-0">
    {#if editing}
      <div class="space-y-2">
        <input
          type="text"
          bind:value={draft.description}
          class="w-full text-sm px-2 py-1 rounded border border-[var(--border-subtle)] bg-[var(--bg-base)] text-[var(--text-primary)]"
          placeholder="Description"
        />
        <div class="flex gap-2 flex-wrap">
          <input
            type="text"
            bind:value={draft.owner}
            class="text-xs px-2 py-1 rounded border border-[var(--border-subtle)] bg-[var(--bg-base)] text-[var(--text-primary)]"
            placeholder="Owner"
          />
          <input
            type="date"
            bind:value={draft.due_date}
            class="text-xs px-2 py-1 rounded border border-[var(--border-subtle)] bg-[var(--bg-base)] text-[var(--text-primary)]"
          />
          <button
            onclick={saveEdit}
            disabled={loading || !draft.description.trim()}
            class="text-xs px-2 py-1 rounded bg-[var(--accent)] text-white hover:opacity-90 disabled:opacity-40"
          >
            Save
          </button>
          <button
            onclick={cancelEdit}
            disabled={loading}
            class="text-xs px-2 py-1 rounded border border-[var(--border-subtle)] hover:bg-[var(--bg-surface-hover)]"
          >
            Cancel
          </button>
        </div>
      </div>
    {:else}
      <p class="text-sm text-[var(--text-primary)]
                {isDone ? 'line-through opacity-60' : ''}
                {isRejected ? 'line-through' : ''}">
        {item.description}
      </p>
      <div class="flex items-center gap-2 mt-1 flex-wrap">
        {#if isProposed}
          <span class="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-[var(--accent)]/10 text-[var(--accent)] font-medium">
            Proposed
          </span>
        {/if}
        {#if isRejected}
          <span class="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-[var(--text-muted)]/15 text-[var(--text-muted)] font-medium">
            Rejected
          </span>
        {/if}
        {#if item.owner}
          <span class="text-xs text-[var(--text-secondary)]">{item.owner}</span>
        {/if}
        {#if item.due_date}
          <span class="text-xs {isOverdue && !isProposed ? 'text-[var(--warning)] font-medium' : 'text-[var(--text-muted)]'}">
            {isOverdue && !isProposed ? 'Overdue: ' : 'Due '}{formatDate(item.due_date)}
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
    {/if}
  </div>

  {#if isProposed && !editing}
    <div class="flex items-center gap-1 shrink-0">
      <button
        onclick={accept}
        disabled={loading}
        class="text-xs px-2 py-1 rounded bg-[var(--success)] text-white hover:opacity-90 disabled:opacity-40"
        aria-label="Accept proposal"
        title="Accept — adds this to the tracked actions"
      >
        Accept
      </button>
      <button
        onclick={startEdit}
        disabled={loading}
        class="text-xs px-2 py-1 rounded border border-[var(--border-subtle)] hover:bg-[var(--bg-surface-hover)] disabled:opacity-40"
        aria-label="Edit proposal"
        title="Edit before accepting"
      >
        Edit
      </button>
      <button
        onclick={reject}
        disabled={loading}
        class="text-xs px-2 py-1 rounded border border-[var(--border-subtle)] text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)] disabled:opacity-40"
        aria-label="Reject proposal"
        title="Reject — drops this proposal"
      >
        Reject
      </button>
    </div>
  {:else if isRejected}
    <button
      onclick={accept}
      disabled={loading}
      class="text-xs px-2 py-1 rounded border border-[var(--border-subtle)] hover:bg-[var(--bg-surface-hover)] disabled:opacity-40 shrink-0"
      title="Restore — confirm this action"
    >
      Restore
    </button>
  {/if}
</div>
