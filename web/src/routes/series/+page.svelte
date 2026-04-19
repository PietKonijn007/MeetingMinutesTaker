<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api.js';
  import Skeleton from '$lib/components/Skeleton.svelte';
  import { addToast } from '$lib/stores/toasts.js';

  let series = $state([]);
  let loading = $state(true);
  let detecting = $state(false);

  async function load() {
    loading = true;
    try {
      const data = await api.getSeriesList();
      series = data.series || [];
    } catch (e) {
      console.error('Failed to load series:', e);
      addToast(`Could not load series: ${e.message}`, 'error');
    } finally {
      loading = false;
    }
  }

  async function detect() {
    detecting = true;
    try {
      const result = await api.detectSeries();
      const created = result.created?.length || 0;
      const updated = result.updated?.length || 0;
      if (created === 0 && updated === 0) {
        addToast('No changes — detection already up to date.', 'info');
      } else {
        addToast(`Detection complete: ${created} created, ${updated} updated.`, 'success');
      }
      await load();
    } catch (e) {
      addToast(`Detection failed: ${e.message}`, 'error');
    } finally {
      detecting = false;
    }
  }

  function cadenceBadgeClass(cadence) {
    switch (cadence) {
      case 'weekly': return 'bg-green-500/10 text-green-400';
      case 'biweekly': return 'bg-blue-500/10 text-blue-400';
      case 'monthly': return 'bg-purple-500/10 text-purple-400';
      default: return 'bg-gray-500/10 text-gray-400';
    }
  }

  function formatDate(iso) {
    if (!iso) return '';
    return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  }

  onMount(load);
</script>

<div class="max-w-5xl mx-auto">
  <div class="flex items-center justify-between mb-6">
    <h1 class="text-2xl font-bold text-[var(--text-primary)]">Meeting Series</h1>
    <button
      onclick={detect}
      disabled={detecting}
      class="px-3 py-1.5 text-xs font-medium rounded-lg bg-[var(--accent)] text-white hover:opacity-90 disabled:opacity-50 transition-opacity"
    >
      {detecting ? 'Detecting…' : 'Re-run detection'}
    </button>
  </div>

  <p class="text-sm text-[var(--text-muted)] mb-6">
    Groups of meetings sharing attendees and type. Series are detected
    automatically after each pipeline run and on demand.
  </p>

  {#if loading}
    <div class="space-y-3">
      {#each Array(3) as _}
        <Skeleton type="card" />
      {/each}
    </div>
  {:else if series.length === 0}
    <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-8 text-center">
      <div class="text-[var(--text-muted)] mb-2">No series detected yet.</div>
      <div class="text-xs text-[var(--text-muted)]">
        A series forms once you have 3+ meetings with the same attendees and meeting type.
      </div>
    </div>
  {:else}
    <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg overflow-hidden">
      <table class="w-full text-sm">
        <thead class="bg-[var(--bg-surface-hover)] text-xs uppercase text-[var(--text-muted)]">
          <tr>
            <th class="text-left px-4 py-3">Title</th>
            <th class="text-left px-4 py-3">Type</th>
            <th class="text-left px-4 py-3">Cadence</th>
            <th class="text-right px-4 py-3">Members</th>
            <th class="text-left px-4 py-3">Last meeting</th>
          </tr>
        </thead>
        <tbody>
          {#each series as s}
            <tr class="border-t border-[var(--border-subtle)] hover:bg-[var(--bg-surface-hover)] cursor-pointer">
              <td class="px-4 py-3">
                <a href="/series/{s.series_id}" class="text-[var(--accent)] font-medium hover:underline">
                  {s.title}
                </a>
                {#if s.attendee_names?.length}
                  <div class="text-xs text-[var(--text-muted)] mt-0.5">
                    {s.attendee_names.slice(0, 4).join(', ')}
                  </div>
                {/if}
              </td>
              <td class="px-4 py-3 text-[var(--text-secondary)]">{s.meeting_type}</td>
              <td class="px-4 py-3">
                <span class="px-2 py-0.5 rounded-full text-xs {cadenceBadgeClass(s.cadence)}">
                  {s.cadence || 'irregular'}
                </span>
              </td>
              <td class="px-4 py-3 text-right text-[var(--text-secondary)]">{s.member_count}</td>
              <td class="px-4 py-3 text-[var(--text-secondary)]">{formatDate(s.last_meeting_date)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>
