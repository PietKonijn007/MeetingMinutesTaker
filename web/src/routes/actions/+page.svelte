<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api.js';
  import ActionItemRow from '$lib/components/ActionItemRow.svelte';
  import Skeleton from '$lib/components/Skeleton.svelte';
  import EmptyState from '$lib/components/EmptyState.svelte';

  let items = $state([]);
  let loading = $state(true);
  let ownerFilter = $state('');
  let statusFilter = $state('all');
  let showCompleted = $state(false);

  const openItems = $derived(items.filter((i) => i.status !== 'done'));
  const doneItems = $derived(items.filter((i) => i.status === 'done'));

  const owners = $derived([...new Set(items.map((i) => i.owner).filter(Boolean))].sort());

  async function loadItems() {
    loading = true;
    try {
      const params = {};
      if (ownerFilter) params.owner = ownerFilter;
      if (statusFilter !== 'all') params.status = statusFilter;
      const data = await api.getActionItems(params);
      items = data.items || [];
    } catch (e) {
      console.error('Failed to load action items:', e);
      items = [];
    } finally {
      loading = false;
    }
  }

  onMount(loadItems);

  $effect(() => {
    // Re-load when filters change
    ownerFilter;
    statusFilter;
    loadItems();
  });
</script>

<div class="max-w-3xl mx-auto">
  <div class="flex items-center justify-between mb-6">
    <h1 class="text-2xl font-bold text-[var(--text-primary)]">Action Items</h1>

    <div class="flex items-center gap-3">
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

  {#if loading}
    <div class="space-y-3">
      {#each Array(5) as _}
        <Skeleton type="text" lines={2} />
      {/each}
    </div>
  {:else if items.length === 0}
    <EmptyState
      icon="&#9989;"
      title="No action items"
      description="Action items from your meetings will appear here."
    />
  {:else}
    <!-- Open items -->
    {#if openItems.length > 0}
      <div class="space-y-1 mb-6">
        {#each openItems as item (item.action_item_id)}
          <ActionItemRow {item} />
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
              <ActionItemRow {item} />
            {/each}
          </div>
        {/if}
      </div>
    {/if}
  {/if}
</div>
