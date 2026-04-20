<script>
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  import { api } from '$lib/api.js';
  import Skeleton from '$lib/components/Skeleton.svelte';
  import { addToast } from '$lib/stores/toasts.js';

  let brief = $state(null);
  let loading = $state(true);
  let peopleIds = $state([]);
  let meetingType = $state(null);

  // Start Recording panel state
  let title = $state('');
  let selectedType = $state('other');
  let attendeesText = $state('');
  let carryNote = $state('');
  let starting = $state(false);

  const MEETING_TYPES = [
    { value: 'one_on_one', label: '1:1' },
    { value: 'standup', label: 'Standup' },
    { value: 'team_meeting', label: 'Team meeting' },
    { value: 'customer_meeting', label: 'Customer meeting' },
    { value: 'decision_meeting', label: 'Decision meeting' },
    { value: 'brainstorm', label: 'Brainstorm' },
    { value: 'retrospective', label: 'Retrospective' },
    { value: 'planning', label: 'Planning' },
    { value: 'other', label: 'Other' }
  ];

  async function load() {
    const sp = $page.url.searchParams;
    peopleIds = sp.getAll('person');
    meetingType = sp.get('type');

    if (peopleIds.length === 0) {
      loading = false;
      return;
    }

    loading = true;
    try {
      brief = await api.getBriefing(peopleIds, meetingType);
      // Seed the Start Recording panel from the suggested_start block.
      title = brief.suggested_start?.title || '';
      selectedType = brief.suggested_start?.meeting_type || 'other';
      attendeesText = (brief.suggested_start?.attendee_labels || []).join(', ');
      carryNote = brief.suggested_start?.carry_forward_note || '';
    } catch (e) {
      addToast(`Could not load briefing: ${e.message}`, 'error');
    } finally {
      loading = false;
    }
  }

  async function startRecording() {
    starting = true;
    try {
      const body = {};
      if (attendeesText.trim()) body.planned_minutes = 60;
      await api.startRecording(body);

      // Save notes + speakers so the pipeline picks them up on stop.
      // Recording already started; we pass notes on the stop call instead.
      // Here we only pre-fill the local session storage so /record can show them.
      try {
        sessionStorage.setItem('mm_brief_prefill', JSON.stringify({
          title,
          meeting_type: selectedType,
          attendees: attendeesText,
          notes: carryNote,
        }));
      } catch {}

      addToast('Recording started', 'success');
      goto('/record');
    } catch (e) {
      addToast(`Failed to start recording: ${e.message}`, 'error');
    } finally {
      starting = false;
    }
  }

  function formatDate(iso) {
    if (!iso) return '';
    return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  }

  function sparklinePath(points, width = 120, height = 30) {
    if (!points || points.length < 2) return '';
    const xs = points.map((_, i) => (i / (points.length - 1)) * width);
    const ys = points.map(p => height - p.score * height);
    return 'M ' + xs.map((x, i) => `${x.toFixed(1)} ${ys[i].toFixed(1)}`).join(' L ');
  }

  onMount(load);
</script>

<div class="max-w-5xl mx-auto pb-40">
  <h1 class="text-2xl font-bold mb-1 text-[var(--text-primary)]">Pre-meeting briefing</h1>
  <p class="text-sm text-[var(--text-secondary)] mb-6">
    Everything you should know before you walk in, plus a shortcut to start recording.
  </p>

  {#if loading}
    <div class="space-y-3">
      <Skeleton height="64px" />
      <Skeleton height="120px" />
      <Skeleton height="120px" />
    </div>
  {:else if peopleIds.length === 0}
    <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-6">
      <p class="text-[var(--text-primary)] font-medium mb-2">No attendees selected.</p>
      <p class="text-sm text-[var(--text-secondary)] mb-4">
        Open a briefing from a person's profile or from a series page.
      </p>
      <a href="/people" class="inline-block px-3 py-1.5 text-xs font-medium rounded-lg bg-[var(--accent)] text-white hover:opacity-90">
        Browse people
      </a>
    </div>
  {:else if !brief}
    <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-6">
      <p class="text-[var(--text-secondary)]">No briefing data available.</p>
    </div>
  {:else}
    <!-- Optional LLM summary -->
    {#if brief.summary}
      <div class="bg-[var(--accent)]/5 border border-[var(--accent)]/30 rounded-lg p-4 mb-6">
        <p class="text-sm text-[var(--text-primary)] leading-relaxed">{brief.summary}</p>
      </div>
    {/if}

    <!-- 1. Who & When Last -->
    <section class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-5 mb-4">
      <h2 class="text-sm font-semibold uppercase tracking-wide text-[var(--text-secondary)] mb-3">
        Who &amp; when last
      </h2>
      <div class="flex flex-wrap gap-2 mb-3">
        {#each brief.who_and_when_last.attendees as a}
          <a href={`/people/${a.person_id}`} class="px-3 py-1.5 text-sm rounded-full bg-[var(--bg-surface-hover)] text-[var(--text-primary)] hover:bg-[var(--accent)]/10">
            {a.name}
          </a>
        {/each}
      </div>
      {#if brief.who_and_when_last.total_prior_meetings === 0}
        <p class="text-sm text-[var(--text-secondary)]">No prior meetings yet with this group.</p>
      {:else}
        <p class="text-sm text-[var(--text-secondary)]">
          Last met
          {#if brief.who_and_when_last.last_meeting_date}
            on <strong class="text-[var(--text-primary)]">{formatDate(brief.who_and_when_last.last_meeting_date)}</strong>
          {/if}
          — {brief.who_and_when_last.total_prior_meetings} prior meeting(s){#if brief.who_and_when_last.cadence}, cadence: <strong class="text-[var(--text-primary)]">{brief.who_and_when_last.cadence}</strong>{/if}.
        </p>
      {/if}
      {#if brief.who_and_when_last.series}
        <a href={`/series/${brief.who_and_when_last.series.series_id}`}
           class="mt-3 inline-flex items-center gap-2 px-3 py-1.5 text-xs font-medium rounded-lg bg-blue-500/10 text-blue-400 hover:bg-blue-500/20">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h8m0 0l-3-3m3 3l-3 3"/></svg>
          Series: {brief.who_and_when_last.series.title} ({brief.who_and_when_last.series.member_count} meetings)
        </a>
      {/if}
    </section>

    <!-- 2. Open Commitments -->
    <section class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-5 mb-4">
      <h2 class="text-sm font-semibold uppercase tracking-wide text-[var(--text-secondary)] mb-3">
        Open commitments
      </h2>
      {#if brief.open_commitments.length === 0}
        <p class="text-sm text-[var(--text-secondary)]">No open commitments for these attendees.</p>
      {:else}
        <ul class="space-y-2">
          {#each brief.open_commitments as c}
            <li class="flex items-start gap-3 text-sm">
              <span class="mt-1 w-1.5 h-1.5 rounded-full {c.overdue ? 'bg-red-500' : 'bg-[var(--text-secondary)]'}"></span>
              <div class="flex-1">
                <p class="text-[var(--text-primary)]">{c.description}</p>
                <p class="text-xs text-[var(--text-secondary)] mt-0.5">
                  {#if c.owner}<span>{c.owner}</span>{/if}
                  {#if c.due_date}<span> · due {c.due_date}</span>{/if}
                  {#if c.overdue}<span class="text-red-400 font-medium"> · overdue</span>{/if}
                </p>
              </div>
            </li>
          {/each}
        </ul>
      {/if}
    </section>

    <!-- 3. Unresolved Topics -->
    <section class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-5 mb-4">
      <h2 class="text-sm font-semibold uppercase tracking-wide text-[var(--text-secondary)] mb-3">
        Unresolved topics
      </h2>
      {#if brief.unresolved_topics.length === 0}
        <p class="text-sm text-[var(--text-secondary)]">No recurring unresolved topics.</p>
      {:else}
        <ul class="space-y-2">
          {#each brief.unresolved_topics as t}
            <li class="text-sm">
              <p class="text-[var(--text-primary)]">{t.text}</p>
              <p class="text-xs text-[var(--text-secondary)] mt-0.5">
                Seen in {t.meeting_ids.length} meeting(s)
                {#if t.last_mentioned} · last {formatDate(t.last_mentioned)}{/if}
              </p>
            </li>
          {/each}
        </ul>
      {/if}
    </section>

    <!-- 4. Recent Sentiment -->
    <section class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-5 mb-4">
      <h2 class="text-sm font-semibold uppercase tracking-wide text-[var(--text-secondary)] mb-3">
        Recent sentiment
      </h2>
      {#if Object.keys(brief.recent_sentiment).length === 0}
        <p class="text-sm text-[var(--text-secondary)]">No sentiment data yet.</p>
      {:else}
        <div class="space-y-3">
          {#each Object.values(brief.recent_sentiment) as person}
            <div class="flex items-center gap-4">
              <div class="w-32 text-sm text-[var(--text-primary)]">{person.name}</div>
              <svg width="120" height="30" class="text-[var(--accent)]">
                <path d={sparklinePath(person.scores)} fill="none" stroke="currentColor" stroke-width="1.5" />
                {#each person.scores as pt, i}
                  <circle
                    cx={(i / Math.max(1, person.scores.length - 1)) * 120}
                    cy={30 - pt.score * 30}
                    r="2"
                    fill="currentColor"
                  />
                {/each}
              </svg>
              <div class="text-xs text-[var(--text-secondary)]">
                {person.scores.length} meeting(s) · latest: <strong class="text-[var(--text-primary)]">{person.scores[person.scores.length - 1]?.sentiment}</strong>
              </div>
            </div>
          {/each}
        </div>
      {/if}
    </section>

    <!-- 5. Recent Decisions -->
    <section class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-5 mb-4">
      <h2 class="text-sm font-semibold uppercase tracking-wide text-[var(--text-secondary)] mb-3">
        Recent decisions
      </h2>
      {#if brief.recent_decisions.length === 0}
        <p class="text-sm text-[var(--text-secondary)]">No recent decisions.</p>
      {:else}
        <ul class="space-y-2">
          {#each brief.recent_decisions as d}
            <li class="text-sm">
              <p class="text-[var(--text-primary)]">{d.description}</p>
              <p class="text-xs text-[var(--text-secondary)] mt-0.5">
                {#if d.made_by}<span>{d.made_by}</span>{/if}
                {#if d.date}<span> · {formatDate(d.date)}</span>{/if}
                {#if d.rationale}<span> · {d.rationale}</span>{/if}
              </p>
            </li>
          {/each}
        </ul>
      {/if}
    </section>

    <!-- 6. Context Excerpts -->
    <section class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-5 mb-4">
      <h2 class="text-sm font-semibold uppercase tracking-wide text-[var(--text-secondary)] mb-3">
        Context excerpts
      </h2>
      {#if brief.context_excerpts.length === 0}
        <p class="text-sm text-[var(--text-secondary)]">No context excerpts available.</p>
      {:else}
        <ul class="space-y-3">
          {#each brief.context_excerpts as e}
            <li class="text-sm border-l-2 border-[var(--accent)]/40 pl-3">
              <p class="text-[var(--text-primary)] leading-relaxed">{e.chunk_text}</p>
              <p class="text-xs text-[var(--text-secondary)] mt-1">
                {e.title || 'Meeting'}{#if e.date} · {formatDate(e.date)}{/if}{#if e.chunk_type} · {e.chunk_type}{/if}
              </p>
            </li>
          {/each}
        </ul>
      {/if}
    </section>
  {/if}
</div>

<!-- 7. Start Recording — pinned footer panel -->
{#if brief && !loading}
  <div class="fixed bottom-0 left-0 right-0 bg-[var(--bg-surface)] border-t border-[var(--border-subtle)] px-6 py-4 shadow-lg">
    <div class="max-w-5xl mx-auto">
      <div class="flex items-start gap-4 flex-wrap">
        <div class="flex-1 min-w-[240px]">
          <label class="block text-xs text-[var(--text-secondary)] mb-1" for="brief-title">Title</label>
          <input
            id="brief-title"
            bind:value={title}
            class="w-full px-3 py-1.5 text-sm rounded border border-[var(--border-subtle)] bg-[var(--bg-primary)] text-[var(--text-primary)]"
          />
        </div>
        <div>
          <label class="block text-xs text-[var(--text-secondary)] mb-1" for="brief-type">Type</label>
          <select
            id="brief-type"
            bind:value={selectedType}
            class="px-3 py-1.5 text-sm rounded border border-[var(--border-subtle)] bg-[var(--bg-primary)] text-[var(--text-primary)]"
          >
            {#each MEETING_TYPES as t}
              <option value={t.value}>{t.label}</option>
            {/each}
          </select>
        </div>
        <div class="flex-1 min-w-[200px]">
          <label class="block text-xs text-[var(--text-secondary)] mb-1" for="brief-attendees">Attendees</label>
          <input
            id="brief-attendees"
            bind:value={attendeesText}
            placeholder="Alice, Bob"
            class="w-full px-3 py-1.5 text-sm rounded border border-[var(--border-subtle)] bg-[var(--bg-primary)] text-[var(--text-primary)]"
          />
        </div>
        <button
          onclick={startRecording}
          disabled={starting}
          class="self-end px-4 py-1.5 text-sm font-medium rounded-lg bg-red-500 text-white hover:opacity-90 disabled:opacity-50 transition-opacity"
        >
          {starting ? 'Starting…' : 'Start recording'}
        </button>
      </div>
      <details class="mt-3 text-xs">
        <summary class="text-[var(--text-secondary)] cursor-pointer select-none">Carry-forward note</summary>
        <textarea
          bind:value={carryNote}
          rows="6"
          class="mt-2 w-full px-3 py-2 text-xs font-mono rounded border border-[var(--border-subtle)] bg-[var(--bg-primary)] text-[var(--text-primary)]"
        ></textarea>
      </details>
    </div>
  </div>
{/if}
