<script>
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { api } from '$lib/api.js';
  import PersonAvatar from '$lib/components/PersonAvatar.svelte';
  import MeetingCard from '$lib/components/MeetingCard.svelte';
  import ActionItemRow from '$lib/components/ActionItemRow.svelte';
  import Skeleton from '$lib/components/Skeleton.svelte';

  let person = $state(null);
  let meetings = $state([]);
  let loading = $state(true);

  const id = $derived($page.params.id);

  async function loadPerson() {
    loading = true;
    try {
      const [personData, meetingsData] = await Promise.all([
        api.getPerson(id),
        api.getPersonMeetings(id)
      ]);
      person = personData;
      meetings = meetingsData.items || meetingsData || [];
    } catch (e) {
      console.error('Failed to load person:', e);
    } finally {
      loading = false;
    }
  }

  onMount(loadPerson);
</script>

<div class="max-w-3xl mx-auto">
  <!-- Back -->
  <a
    href="/people"
    class="inline-flex items-center gap-1.5 text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)] mb-4 transition-colors"
  >
    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"/>
    </svg>
    Back to People
  </a>

  {#if loading}
    <Skeleton type="avatar" />
    <div class="mt-6"><Skeleton type="text" lines={4} /></div>
  {:else if person}
    <!-- Person header -->
    <div class="flex items-center gap-4 mb-8">
      <PersonAvatar name={person.name || person.email || '?'} size="lg" />
      <div>
        <h1 class="text-2xl font-bold text-[var(--text-primary)]">{person.name || 'Unknown'}</h1>
        {#if person.email}
          <p class="text-sm text-[var(--text-secondary)]">{person.email}</p>
        {/if}
      </div>
    </div>

    <!-- Stats row -->
    <div class="grid grid-cols-3 gap-4 mb-8">
      <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-4 text-center">
        <div class="text-2xl font-bold text-[var(--text-primary)]">{person.meeting_count ?? meetings.length}</div>
        <div class="text-xs text-[var(--text-muted)] mt-1">Meetings</div>
      </div>
      <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-4 text-center">
        <div class="text-2xl font-bold text-[var(--text-primary)]">{person.open_action_count ?? 0}</div>
        <div class="text-xs text-[var(--text-muted)] mt-1">Open Actions</div>
      </div>
      <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-4 text-center">
        <div class="text-2xl font-bold text-[var(--text-primary)]">{person.decision_count ?? 0}</div>
        <div class="text-xs text-[var(--text-muted)] mt-1">Decisions</div>
      </div>
    </div>

    <!-- Meeting history -->
    <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-4">Meeting History</h2>
    {#if meetings.length > 0}
      <div class="space-y-3 mb-8">
        {#each meetings as meeting (meeting.id)}
          <MeetingCard {meeting} view="list" />
        {/each}
      </div>
    {:else}
      <p class="text-sm text-[var(--text-muted)] italic mb-8">No meetings found.</p>
    {/if}

    <!-- Action items -->
    {#if person.action_items?.length}
      <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-4">Action Items</h2>
      <div class="space-y-1">
        {#each person.action_items as item}
          <ActionItemRow {item} />
        {/each}
      </div>
    {/if}
  {:else}
    <p class="text-sm text-[var(--text-muted)]">Person not found.</p>
  {/if}
</div>
