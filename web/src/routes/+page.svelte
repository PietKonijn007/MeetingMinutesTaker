<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api.js';
  import { page } from '$app/stores';
  import MeetingCard from '$lib/components/MeetingCard.svelte';
  import MeetingTypeBadge from '$lib/components/MeetingTypeBadge.svelte';
  import Skeleton from '$lib/components/Skeleton.svelte';
  import EmptyState from '$lib/components/EmptyState.svelte';

  let meetings = $state([]);
  let total = $state(0);
  let loading = $state(true);
  let loadingMore = $state(false);
  let offset = $state(0);
  const limit = 20;

  let viewMode = $state('list'); // 'list' | 'grid'
  let typeFilter = $state('');
  let searchQuery = $state('');

  const meetingTypes = [
    'standup', 'one_on_one', 'customer_meeting', 'decision_meeting',
    'brainstorm', 'retrospective', 'planning', 'other'
  ];

  const hasMore = $derived(meetings.length < total);

  async function loadMeetings(reset = false) {
    if (reset) {
      offset = 0;
      loading = true;
    } else {
      loadingMore = true;
    }

    try {
      const params = { limit: limit.toString(), offset: offset.toString() };
      if (typeFilter) params.type = typeFilter;
      if (searchQuery) params.q = searchQuery;

      const data = await api.getMeetings(params);
      // Normalize API fields to what MeetingCard expects
      const normalized = (data.items || []).map(m => ({
        id: m.meeting_id,
        title: m.title,
        date: m.date,
        type: m.meeting_type,
        duration: m.duration,
        attendees: m.attendee_names || [],
        summary: m.summary,
        action_count: m.action_item_count || 0,
        decision_count: m.decision_count || 0,
      }));
      if (reset) {
        meetings = normalized;
      } else {
        meetings = [...meetings, ...normalized];
      }
      total = data.total || 0;
    } catch (e) {
      console.error('Failed to load meetings:', e);
      meetings = [];
    } finally {
      loading = false;
      loadingMore = false;
    }
  }

  function loadMore() {
    offset += limit;
    loadMeetings(false);
  }

  function handleTypeFilter(type) {
    typeFilter = typeFilter === type ? '' : type;
    loadMeetings(true);
  }

  // Read initial query from URL
  onMount(() => {
    const q = $page.url.searchParams.get('q');
    if (q) searchQuery = q;
    loadMeetings(true);
  });
</script>

<div class="max-w-5xl mx-auto">
  <!-- Header -->
  <div class="flex items-center justify-between mb-6">
    <h1 class="text-2xl font-bold text-[var(--text-primary)]">Meetings</h1>

    <!-- View mode toggle -->
    <div class="flex items-center bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-0.5">
      <button
        onclick={() => viewMode = 'list'}
        class="px-3 py-1.5 rounded-md text-sm transition-colors duration-150
               {viewMode === 'list' ? 'bg-[var(--accent)] text-white' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'}"
        aria-label="List view"
      >
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"/></svg>
      </button>
      <button
        onclick={() => viewMode = 'grid'}
        class="px-3 py-1.5 rounded-md text-sm transition-colors duration-150
               {viewMode === 'grid' ? 'bg-[var(--accent)] text-white' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'}"
        aria-label="Grid view"
      >
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z"/></svg>
      </button>
    </div>
  </div>

  <!-- Filters -->
  <div class="flex items-center gap-2 mb-6 flex-wrap">
    <span class="text-xs text-[var(--text-muted)]">Type:</span>
    {#each meetingTypes as type}
      <button
        onclick={() => handleTypeFilter(type)}
        class="transition-all duration-150
               {typeFilter === type ? 'ring-2 ring-[var(--accent)] rounded-full' : ''}"
      >
        <MeetingTypeBadge {type} />
      </button>
    {/each}
    {#if typeFilter}
      <button
        onclick={() => { typeFilter = ''; loadMeetings(true); }}
        class="text-xs text-[var(--text-muted)] hover:text-[var(--text-primary)] ml-1"
      >
        Clear
      </button>
    {/if}
  </div>

  <!-- Loading state -->
  {#if loading}
    <div class="space-y-4">
      {#each Array(5) as _, i (i)}
        <Skeleton type="card" />
      {/each}
    </div>

  <!-- Empty state -->
  {:else if meetings.length === 0}
    <EmptyState
      icon="&#128203;"
      title="No meetings yet"
      description="Record your first meeting to get started."
      ctaLabel="Start Recording"
      ctaHref="/record"
    />

  <!-- Meeting list -->
  {:else}
    {#if viewMode === 'grid'}
      <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {#each meetings as meeting (meeting.id)}
          <MeetingCard {meeting} view="grid" />
        {/each}
      </div>
    {:else}
      <div class="space-y-3">
        {#each meetings as meeting (meeting.id)}
          <MeetingCard {meeting} view="list" />
        {/each}
      </div>
    {/if}

    <!-- Load more -->
    {#if hasMore}
      <div class="flex justify-center mt-8">
        <button
          onclick={loadMore}
          disabled={loadingMore}
          class="px-6 py-2.5 text-sm font-medium text-[var(--text-secondary)]
                 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg
                 hover:bg-[var(--bg-surface-hover)] hover:text-[var(--text-primary)]
                 disabled:opacity-50 transition-colors duration-150"
        >
          {loadingMore ? 'Loading...' : 'Load More'}
        </button>
      </div>
    {/if}
  {/if}
</div>
