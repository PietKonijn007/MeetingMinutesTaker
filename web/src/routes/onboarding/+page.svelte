<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api.js';
  import { addToast } from '$lib/stores/toasts.js';

  let checks = $state([]);
  let overallStatus = $state('pending');
  let loading = $state(true);
  let retryingName = $state(null);

  async function load() {
    loading = true;
    try {
      const data = await api.getDoctorChecks();
      checks = data.checks || [];
      overallStatus = data.overall_status || 'pending';
    } catch (e) {
      addToast(`Diagnostic failed: ${e.message}`, 'error');
    } finally {
      loading = false;
    }
  }

  async function retrySingle(name) {
    retryingName = name;
    try {
      const updated = await api.request(`/doctor/${name}`);
      const idx = checks.findIndex(c => c.name === name);
      if (idx >= 0) {
        checks[idx] = updated;
        checks = checks; // trigger reactivity
      }
      overallStatus = checks.some(c => c.status === 'fail')
        ? 'fail'
        : checks.some(c => c.status === 'warn') ? 'warn' : 'ok';
    } catch (e) {
      addToast(`Retry failed: ${e.message}`, 'error');
    } finally {
      retryingName = null;
    }
  }

  async function copyToClipboard(text) {
    try {
      await navigator.clipboard.writeText(text);
      addToast('Copied to clipboard', 'success');
    } catch {
      addToast('Could not access clipboard', 'error');
    }
  }

  function badgeClass(status) {
    if (status === 'ok') return 'bg-green-500/20 text-green-500';
    if (status === 'warn') return 'bg-yellow-500/20 text-yellow-500';
    return 'bg-red-500/20 text-red-500';
  }

  function badgeLabel(status) {
    return status.toUpperCase();
  }

  onMount(load);
</script>

<div class="max-w-3xl mx-auto p-6">
  <header class="mb-6">
    <h1 class="text-2xl font-bold text-[var(--text-primary)]">Welcome — let's check your setup</h1>
    <p class="mt-2 text-sm text-[var(--text-secondary)]">
      Each check below verifies one part of the stack. Yellow and red items have a copy-paste fix command.
    </p>
  </header>

  {#if loading}
    <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-6">
      <p class="text-sm text-[var(--text-secondary)]">Running diagnostics…</p>
    </div>
  {:else}
    <div class="mb-4 flex items-center justify-between">
      <span class="text-sm text-[var(--text-secondary)]">
        Overall status:
        <span class="ml-2 px-2 py-1 rounded font-mono text-xs {badgeClass(overallStatus)}">
          {badgeLabel(overallStatus)}
        </span>
      </span>
      <button
        onclick={load}
        class="text-sm px-3 py-1.5 border border-[var(--border-subtle)] rounded-lg hover:bg-[var(--bg-primary)]"
      >
        Re-run all
      </button>
    </div>

    <div class="space-y-3">
      {#each checks as c (c.name)}
        <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-4">
          <div class="flex items-start justify-between gap-3">
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2">
                <span class="text-xs font-mono px-2 py-0.5 rounded {badgeClass(c.status)}">
                  {badgeLabel(c.status)}
                </span>
                <h3 class="text-sm font-medium text-[var(--text-primary)]">{c.name}</h3>
              </div>
              <p class="mt-2 text-sm text-[var(--text-secondary)]">{c.detail}</p>
              {#if c.fix_hint}
                <p class="mt-2 text-xs text-[var(--text-muted)]">Hint: {c.fix_hint}</p>
              {/if}
              {#if c.fix_command}
                <div class="mt-2 flex items-center gap-2">
                  <code class="flex-1 text-xs bg-[var(--bg-primary)] border border-[var(--border-subtle)] rounded px-2 py-1 font-mono overflow-x-auto">
                    {c.fix_command}
                  </code>
                  <button
                    onclick={() => copyToClipboard(c.fix_command)}
                    class="text-xs px-2 py-1 border border-[var(--border-subtle)] rounded hover:bg-[var(--bg-primary)]"
                  >
                    Copy
                  </button>
                </div>
              {/if}
            </div>
            {#if c.status !== 'ok'}
              <button
                onclick={() => retrySingle(c.name)}
                disabled={retryingName === c.name}
                class="text-xs px-3 py-1.5 border border-[var(--border-subtle)] rounded-lg hover:bg-[var(--bg-primary)] disabled:opacity-50"
              >
                {retryingName === c.name ? 'Retrying…' : 'Retry'}
              </button>
            {/if}
          </div>
        </div>
      {/each}
    </div>

    <div class="mt-6 flex justify-end">
      <a
        href="/"
        class="px-4 py-2 bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white rounded-lg text-sm"
      >
        Continue to app
      </a>
    </div>
  {/if}
</div>
