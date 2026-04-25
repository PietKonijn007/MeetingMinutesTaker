<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api.js';
  import ActionItemRow from '$lib/components/ActionItemRow.svelte';
  import Skeleton from '$lib/components/Skeleton.svelte';
  import EmptyState from '$lib/components/EmptyState.svelte';
  import { addToast } from '$lib/stores/toasts.js';

  let items = $state([]);
  let loading = $state(true);
  let ownerFilter = $state('');
  let statusFilter = $state('all');
  // Review-state filter chip. The default — "confirmed" — keeps the global
  // tracker clean: extracted-but-unreviewed proposals live on the per-meeting
  // Actions tab until the user blesses them. Switch to "proposed" to triage
  // the backlog, or "all" to see everything.
  let proposalFilter = $state('confirmed');
  let showCompleted = $state(false);

  // Admin-only one-time sweep — confirm every still-proposed item from
  // meetings on or before a chosen date. Surfaces the post-migration
  // backlog clear without forcing the user to walk every old meeting.
  let bulkConfirmOpen = $state(false);
  let bulkConfirmDate = $state('');
  let bulkConfirming = $state(false);

  const openItems = $derived(items.filter((i) => i.status !== 'done'));
  const doneItems = $derived(items.filter((i) => i.status === 'done'));

  const owners = $derived([...new Set(items.map((i) => i.owner).filter(Boolean))].sort());

  async function loadItems() {
    loading = true;
    try {
      const params = {};
      if (ownerFilter) params.owner = ownerFilter;
      if (statusFilter !== 'all') params.status = statusFilter;
      // Always send proposal_state explicitly so the server isn't quietly
      // applying its own default — keeps the chip honest.
      params.proposal_state = proposalFilter;
      const data = await api.getActionItems(params);
      items = data.items || [];
    } catch (e) {
      console.error('Failed to load action items:', e);
      items = [];
    } finally {
      loading = false;
    }
  }

  async function runBulkConfirm() {
    if (!bulkConfirmDate) return;
    bulkConfirming = true;
    try {
      const r = await api.confirmActionsBefore(bulkConfirmDate);
      addToast({
        type: 'success',
        message: r.updated
          ? `Confirmed ${r.updated} action${r.updated === 1 ? '' : 's'} across ${r.affected_meeting_count} meeting${r.affected_meeting_count === 1 ? '' : 's'}.`
          : 'Nothing to confirm — no proposed actions on or before that date.',
      });
      bulkConfirmOpen = false;
      bulkConfirmDate = '';
      await loadItems();
    } catch (e) {
      addToast({ type: 'error', message: `Bulk confirm failed: ${e.message}` });
    } finally {
      bulkConfirming = false;
    }
  }

  onMount(loadItems);

  $effect(() => {
    // Re-load when filters change
    ownerFilter;
    statusFilter;
    proposalFilter;
    loadItems();
  });
</script>

<div class="max-w-3xl mx-auto">
  <div class="flex items-center justify-between mb-6 flex-wrap gap-3">
    <h1 class="text-2xl font-bold text-[var(--text-primary)]">Action Items</h1>

    <div class="flex items-center gap-3 flex-wrap">
      <!-- Review-state chip group: Confirmed | Proposed | All -->
      <div class="inline-flex rounded-lg border border-[var(--border-subtle)] overflow-hidden text-sm">
        {#each [['confirmed', 'Confirmed'], ['proposed', 'Proposed'], ['all', 'All']] as [val, label]}
          <button
            onclick={() => proposalFilter = val}
            class="px-3 py-1.5 transition-colors
                   {proposalFilter === val
                     ? 'bg-[var(--accent)] text-white'
                     : 'bg-[var(--bg-surface)] text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)]'}"
          >
            {label}
          </button>
        {/each}
      </div>

      <!-- Owner filter -->
      <select
        bind:value={ownerFilter}
        class="px-3 py-1.5 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
               focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
      >
        <option value="">All owners</option>
        {#each owners as owner}
          <option value={owner}>{owner}</option>
        {/each}
      </select>

      <!-- Status filter -->
      <select
        bind:value={statusFilter}
        class="px-3 py-1.5 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
               focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
      >
        <option value="all">All status</option>
        <option value="open">Open</option>
        <option value="done">Completed</option>
      </select>
    </div>
  </div>

  <!-- Backlog-clear admin: confirm every still-proposed action from before a
       chosen date. Compact one-liner that opens an inline date picker; only
       useful right after the proposal-state migration brought historical
       meetings into the review queue. -->
  <div class="mb-4">
    {#if !bulkConfirmOpen}
      <button
        onclick={() => { bulkConfirmOpen = true; bulkConfirmDate = new Date().toISOString().slice(0, 10); }}
        class="text-xs text-[var(--text-muted)] hover:text-[var(--accent)] hover:underline"
      >
        Confirm all proposals from before a date…
      </button>
    {:else}
      <div class="p-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] flex items-center gap-2 flex-wrap">
        <span class="text-sm text-[var(--text-secondary)]">Confirm every still-proposed action from meetings on or before</span>
        <input
          type="date"
          bind:value={bulkConfirmDate}
          class="text-sm px-2 py-1 rounded border border-[var(--border-subtle)] bg-[var(--bg-base)] text-[var(--text-primary)]"
        />
        <button
          onclick={runBulkConfirm}
          disabled={bulkConfirming || !bulkConfirmDate}
          class="text-xs px-3 py-1.5 rounded bg-[var(--accent)] text-white hover:opacity-90 disabled:opacity-40"
        >
          {bulkConfirming ? 'Confirming…' : 'Confirm all'}
        </button>
        <button
          onclick={() => { bulkConfirmOpen = false; bulkConfirmDate = ''; }}
          disabled={bulkConfirming}
          class="text-xs px-2 py-1 rounded border border-[var(--border-subtle)] hover:bg-[var(--bg-surface-hover)]"
        >
          Cancel
        </button>
      </div>
    {/if}
  </div>

  {#if loading}
    <div class="space-y-3">
      {#each Array(5) as _}
        <Skeleton type="text" lines={2} />
      {/each}
    </div>
  {:else if items.length === 0}
    <EmptyState
      icon="&#9989;"
      title={proposalFilter === 'proposed' ? 'No proposals to review' : 'No action items'}
      description={proposalFilter === 'proposed'
        ? 'Open a meeting to triage its proposed actions, or switch the filter to Confirmed.'
        : 'Action items from your meetings will appear here once you confirm them.'}
    />
  {:else}
    <!-- Open items -->
    {#if openItems.length > 0}
      <div class="space-y-1 mb-6">
        {#each openItems as item (item.action_item_id)}
          <ActionItemRow {item} onUpdate={loadItems} />
        {/each}
      </div>
    {/if}

    <!-- Completed items -->
    {#if doneItems.length > 0}
      <div class="border-t border-[var(--border-subtle)] pt-4">
        <button
          onclick={() => showCompleted = !showCompleted}
          class="flex items-center gap-2 text-sm text-[var(--text-muted)] hover:text-[var(--text-secondary)] mb-3 transition-colors"
        >
          <svg
            class="w-4 h-4 transition-transform duration-150 {showCompleted ? 'rotate-90' : ''}"
            fill="none" stroke="currentColor" viewBox="0 0 24 24"
          >
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
          </svg>
          Completed ({doneItems.length})
        </button>

        {#if showCompleted}
          <div class="space-y-1">
            {#each doneItems as item (item.action_item_id)}
              <ActionItemRow {item} onUpdate={loadItems} />
            {/each}
          </div>
        {/if}
      </div>
    {/if}
  {/if}
</div>
