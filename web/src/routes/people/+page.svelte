<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api.js';
  import PersonAvatar from '$lib/components/PersonAvatar.svelte';
  import Skeleton from '$lib/components/Skeleton.svelte';
  import EmptyState from '$lib/components/EmptyState.svelte';

  let people = $state([]);
  let loading = $state(true);
  let searchQuery = $state('');

  const filteredPeople = $derived(
    searchQuery
      ? people.filter((p) =>
          (p.name || '').toLowerCase().includes(searchQuery.toLowerCase()) ||
          (p.email || '').toLowerCase().includes(searchQuery.toLowerCase())
        )
      : people
  );

  async function loadPeople() {
    loading = true;
    try {
      const data = await api.getPeople();
      people = data.items || data || [];
    } catch (e) {
      console.error('Failed to load people:', e);
      people = [];
    } finally {
      loading = false;
    }
  }

  function formatDate(dateStr) {
    if (!dateStr) return 'N/A';
    const d = new Date(dateStr);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  }

  onMount(loadPeople);
</script>

<div class="max-w-3xl mx-auto">
  <div class="flex items-center justify-between mb-6">
    <h1 class="text-2xl font-bold text-[var(--text-primary)]">People</h1>

    <div class="relative w-64">
      <svg
        class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[var(--text-muted)]"
        fill="none" stroke="currentColor" viewBox="0 0 24 24"
      >
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
      </svg>
      <input
        bind:value={searchQuery}
        placeholder="Search people..."
        class="w-full pl-10 pr-4 py-1.5 bg-[var(--bg-surface)] border border-[var(--border-subtle)]
               rounded-lg text-sm text-[var(--text-primary)] placeholder-[var(--text-muted)]
               focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
      />
    </div>
  </div>

  {#if loading}
    <div class="space-y-4">
      {#each Array(5) as _}
        <Skeleton type="avatar" />
      {/each}
    </div>
  {:else if people.length === 0}
    <EmptyState
      icon="&#128100;"
      title="No people yet"
      description="People from your meetings will appear here automatically."
    />
  {:else}
    <div class="space-y-2">
      {#each filteredPeople as person (person.id)}
        <a
          href="/people/{person.id}"
          class="flex items-center gap-4 p-4 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg
                 hover:bg-[var(--bg-surface-hover)] hover:shadow-sm transition-all duration-150"
        >
          <PersonAvatar name={person.name || person.email || '?'} size="md" />

          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-2">
              <span class="font-medium text-sm text-[var(--text-primary)] truncate">
                {person.name || 'Unknown'}
              </span>
              {#if person.email}
                <span class="text-xs text-[var(--text-muted)] truncate">({person.email})</span>
              {/if}
            </div>
            <div class="flex items-center gap-3 mt-1 text-xs text-[var(--text-secondary)]">
              {#if person.meeting_count != null}
                <span>{person.meeting_count} meetings</span>
              {/if}
              {#if person.open_action_count != null}
                <span>{person.open_action_count} open actions</span>
              {/if}
              {#if person.last_meeting_date}
                <span>Last: {formatDate(person.last_meeting_date)}</span>
              {/if}
            </div>
          </div>

          <svg class="w-4 h-4 text-[var(--text-muted)] shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
          </svg>
        </a>
      {/each}
    </div>
  {/if}
</div>
