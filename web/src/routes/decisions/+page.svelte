<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api.js';
  import DecisionCard from '$lib/components/DecisionCard.svelte';
  import Skeleton from '$lib/components/Skeleton.svelte';
  import EmptyState from '$lib/components/EmptyState.svelte';

  let decisions = $state([]);
  let loading = $state(true);
  let searchQuery = $state('');

  // Group decisions by date
  const groupedDecisions = $derived(() => {
    const filtered = searchQuery
      ? decisions.filter((d) => d.description.toLowerCase().includes(searchQuery.toLowerCase()))
      : decisions;

    const groups = {};
    for (const d of filtered) {
      const dateKey = d.date
        ? new Date(d.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
        : 'Unknown Date';
      if (!groups[dateKey]) groups[dateKey] = [];
      groups[dateKey].push(d);
    }
    return groups;
  });

  async function loadDecisions() {
    loading = true;
    try {
      const data = await api.getDecisions({ limit: '100' });
      decisions = data.items || [];
    } catch (e) {
      console.error('Failed to load decisions:', e);
      decisions = [];
    } finally {
      loading = false;
    }
  }

  onMount(loadDecisions);
</script>

<div class="max-w-3xl mx-auto">
  <div class="flex items-center justify-between mb-6">
    <h1 class="text-2xl font-bold text-[var(--text-primary)]">Decision Log</h1>

    <!-- Search -->
    <div class="relative w-64">
      <svg
        class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[var(--text-muted)]"
        fill="none" stroke="currentColor" viewBox="0 0 24 24"
      >
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
      </svg>
      <input
        bind:value={searchQuery}
        placeholder="Search decisions..."
        class="w-full pl-10 pr-4 py-1.5 bg-[var(--bg-surface)] border border-[var(--border-subtle)]
               rounded-lg text-sm text-[var(--text-primary)] placeholder-[var(--text-muted)]
               focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
      />
    </div>
  </div>

  {#if loading}
    <div class="space-y-4">
      {#each Array(4) as _}
        <Skeleton type="card" />
      {/each}
    </div>
  {:else if decisions.length === 0}
    <EmptyState
      icon="&#128204;"
      title="No decisions yet"
      description="Decisions from your meetings will appear here."
    />
  {:else}
    {@const groups = groupedDecisions()}
    {#each Object.entries(groups) as [date, items]}
      <div class="mb-8">
        <h2 class="text-sm font-medium text-[var(--text-muted)] mb-3">{date}</h2>
        <div class="space-y-3">
          {#each items as decision (decision.id)}
            <DecisionCard {decision} />
          {/each}
        </div>
      </div>
    {/each}
  {/if}
</div>
