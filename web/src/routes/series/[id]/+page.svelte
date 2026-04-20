<script>
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { api } from '$lib/api.js';
  import Skeleton from '$lib/components/Skeleton.svelte';
  import { addToast } from '$lib/stores/toasts.js';

  let detail = $state(null);
  let loading = $state(true);

  const seriesId = $derived($page.params.id);

  async function load() {
    loading = true;
    try {
      detail = await api.getSeries(seriesId);
    } catch (e) {
      addToast(`Could not load series: ${e.message}`, 'error');
    } finally {
      loading = false;
    }
  }

  function formatDate(iso) {
    if (!iso) return '';
    return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  }

  function cadenceBadgeClass(cadence) {
    switch (cadence) {
      case 'weekly': return 'bg-green-500/10 text-green-400';
      case 'biweekly': return 'bg-blue-500/10 text-blue-400';
      case 'monthly': return 'bg-purple-500/10 text-purple-400';
      default: return 'bg-gray-500/10 text-gray-400';
    }
  }

  onMount(load);
  $effect(() => {
    // Reload when the :id changes.
    if (seriesId) load();
  });
</script>

<div class="max-w-5xl mx-auto">
  {#if loading}
    <Skeleton type="text" lines={4} />
  {:else if detail}
    <div class="mb-2">
      <a href="/series" class="text-xs text-[var(--text-muted)] hover:text-[var(--accent)]">← All series</a>
    </div>

    <div class="flex items-start justify-between mb-4 gap-4 flex-wrap">
      <div>
        <h1 class="text-2xl font-bold text-[var(--text-primary)] mb-2">{detail.title}</h1>
        <div class="flex items-center gap-2 flex-wrap">
          <span class="px-2 py-0.5 rounded-full text-xs {cadenceBadgeClass(detail.cadence)}">
            {detail.cadence || 'irregular'}
          </span>
          <span class="px-2 py-0.5 rounded-full text-xs bg-[var(--bg-surface)] text-[var(--text-secondary)]">
            {detail.meeting_type}
          </span>
          <span class="text-xs text-[var(--text-muted)]">
            {detail.member_count} meetings
          </span>
        </div>
        {#if detail.attendee_names?.length}
          <div class="mt-2 text-sm text-[var(--text-secondary)]">
            {detail.attendee_names.join(', ')}
          </div>
        {/if}
      </div>
      <div class="flex items-center gap-2">
        {#if detail.attendee_ids?.length}
          {@const briefUrl = `/brief?${detail.attendee_ids.map(id => `person=${encodeURIComponent(id)}`).join('&')}&type=${encodeURIComponent(detail.meeting_type)}`}
          <a
            href={briefUrl}
            class="px-3 py-1.5 text-sm text-white bg-[var(--accent)] rounded-lg hover:opacity-90 transition-opacity"
            title="Prepare for the next meeting in this series"
          >
            Start a briefing for the next one →
          </a>
        {/if}
        <button
          type="button"
          onclick={async () => {
            try {
              const res = await fetch(`/api/series/${seriesId}/export?format=pdf`);
              if (!res.ok) {
                let msg = `Export failed (${res.status})`;
                try { msg = (await res.json()).detail || msg; } catch {}
                throw new Error(msg);
              }
              const blob = await res.blob();
              const disp = res.headers.get('content-disposition') || '';
              const m = /filename="?([^"]+)"?/.exec(disp);
              const filename = m ? m[1] : `${seriesId}.zip`;
              const url = URL.createObjectURL(blob);
              const a = document.createElement('a');
              a.href = url; a.download = filename;
              document.body.appendChild(a); a.click(); a.remove();
              URL.revokeObjectURL(url);
              addToast('Series exported as ZIP', 'success');
            } catch (e) {
              addToast(e.message || 'Export failed', 'error');
            }
          }}
          class="px-3 py-1.5 text-sm text-[var(--text-secondary)] border border-[var(--border-subtle)] rounded-lg
                 hover:bg-[var(--bg-surface-hover)] hover:text-[var(--text-primary)] transition-colors"
          title="Download all meetings in this series as a ZIP"
        >
          Export all meetings (ZIP)
        </button>
      </div>
    </div>

    <!-- Timeline -->
    <section class="mb-8">
      <h2 class="text-sm font-semibold text-[var(--text-primary)] mb-3">Timeline</h2>
      <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-4">
        <ol class="space-y-2">
          {#each detail.members || [] as m}
            <li class="flex items-center gap-3 text-sm">
              <span class="w-2 h-2 rounded-full bg-[var(--accent)] shrink-0"></span>
              <span class="text-[var(--text-muted)] w-24">{formatDate(m.date)}</span>
              <a href="/meeting/{m.meeting_id}" class="text-[var(--accent)] hover:underline flex-1">
                {m.title || 'Untitled'}
              </a>
              {#if m.action_item_count}
                <span class="text-xs text-[var(--text-muted)]">
                  {m.action_item_count} actions
                </span>
              {/if}
              {#if m.decision_count}
                <span class="text-xs text-[var(--text-muted)]">
                  {m.decision_count} decisions
                </span>
              {/if}
              <a
                href="/meeting/{m.meeting_id}"
                class="text-xs text-[var(--text-muted)] hover:text-[var(--accent)]"
              >Open →</a>
            </li>
          {/each}
        </ol>
      </div>
    </section>

    <!-- Cross-meeting action items -->
    <section class="mb-8">
      <h2 class="text-sm font-semibold text-[var(--text-primary)] mb-3">
        Open action items across the series ({detail.aggregates?.open_action_items?.length || 0})
      </h2>
      {#if detail.aggregates?.open_action_items?.length}
        <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg overflow-hidden">
          <table class="w-full text-sm">
            <thead class="bg-[var(--bg-surface-hover)] text-xs uppercase text-[var(--text-muted)]">
              <tr>
                <th class="text-left px-4 py-2">Description</th>
                <th class="text-left px-4 py-2">Owner</th>
                <th class="text-left px-4 py-2">First seen</th>
                <th class="text-left px-4 py-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {#each detail.aggregates.open_action_items as ai}
                <tr class="border-t border-[var(--border-subtle)]">
                  <td class="px-4 py-2 text-[var(--text-primary)]">{ai.description}</td>
                  <td class="px-4 py-2 text-[var(--text-secondary)]">{ai.owner || '—'}</td>
                  <td class="px-4 py-2">
                    <a href="/meeting/{ai.first_seen_meeting_id}" class="text-xs text-[var(--accent)] hover:underline">
                      {ai.first_seen_meeting_id?.slice(0, 8)}…
                    </a>
                  </td>
                  <td class="px-4 py-2 text-xs text-[var(--text-muted)]">{ai.status}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {:else}
        <div class="text-sm text-[var(--text-muted)]">No open actions across this series.</div>
      {/if}
    </section>

    <!-- Recent decisions -->
    <section class="mb-8">
      <h2 class="text-sm font-semibold text-[var(--text-primary)] mb-3">
        Recent decisions ({detail.aggregates?.recent_decisions?.length || 0})
      </h2>
      {#if detail.aggregates?.recent_decisions?.length}
        <ul class="space-y-2">
          {#each detail.aggregates.recent_decisions as d}
            <li class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-3 text-sm">
              <div class="text-[var(--text-primary)]">{d.description}</div>
              <div class="text-xs text-[var(--text-muted)] mt-1">
                {#if d.made_by}by {d.made_by} · {/if}
                <a href="/meeting/{d.meeting_id}" class="hover:text-[var(--accent)]">
                  {formatDate(d.meeting_date)}
                </a>
              </div>
            </li>
          {/each}
        </ul>
      {:else}
        <div class="text-sm text-[var(--text-muted)]">No decisions recorded yet.</div>
      {/if}
    </section>

    <!-- Recurring topics -->
    <section class="mb-8">
      <h2 class="text-sm font-semibold text-[var(--text-primary)] mb-3">
        Recurring topics ({detail.aggregates?.recurring_topics?.length || 0})
      </h2>
      {#if detail.aggregates?.recurring_topics?.length}
        <ul class="space-y-2">
          {#each detail.aggregates.recurring_topics as t}
            <li class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-3 text-sm">
              <div class="text-[var(--text-primary)]">{t.topic_summary}</div>
              <div class="text-xs text-[var(--text-muted)] mt-1">
                Mentioned {t.mention_count}× across {t.meeting_ids.length} meetings
              </div>
            </li>
          {/each}
        </ul>
      {:else}
        <div class="text-sm text-[var(--text-muted)]">No repeating topics yet.</div>
      {/if}
    </section>
  {:else}
    <div class="text-sm text-[var(--text-muted)]">Series not found.</div>
  {/if}
</div>
