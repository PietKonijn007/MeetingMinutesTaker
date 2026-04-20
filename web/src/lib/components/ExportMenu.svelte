<script>
  import { tick } from 'svelte';
  import { addToast } from '$lib/stores/toasts.js';

  /**
   * ExportMenu — dropdown for per-meeting exports (EXP-1).
   * Offers PDF / DOCX / Markdown. Obsidian path is a direct link
   * since the existing Obsidian publish action uses a different
   * backend route.
   */
  let { meetingId, meetingTitle = 'meeting', obsidianEnabled = false } = $props();

  let open = $state(false);
  let busy = $state(false);
  let withTranscript = $state(false);
  let triggerRef = $state();
  // 'bottom' opens below the button; 'top' flips upward when there isn't
  // enough room below the trigger (e.g. when the Export button sits near
  // the bottom of the viewport).
  let placement = $state('bottom');

  // Rough menu height: ~44px per row × up to 5 rows (transcript toggle +
  // PDF + DOCX + MD + optional Obsidian) + padding = ~240px. Keep this as
  // an upper bound for the flip heuristic.
  const APPROX_MENU_HEIGHT = 240;

  async function toggle() {
    open = !open;
    if (open) {
      await tick();
      updatePlacement();
    }
  }

  function updatePlacement() {
    if (!triggerRef) return;
    const rect = triggerRef.getBoundingClientRect();
    const spaceBelow = window.innerHeight - rect.bottom;
    const spaceAbove = rect.top;
    // Flip up only if below is tight AND above has more room; otherwise
    // stay below so the menu scrolls if absolutely necessary.
    placement = spaceBelow < APPROX_MENU_HEIGHT && spaceAbove > spaceBelow ? 'top' : 'bottom';
  }

  function close() { open = false; }

  function slug(s) {
    return String(s || 'meeting')
      .replace(/[^A-Za-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .toLowerCase()
      .slice(0, 80) || 'meeting';
  }

  async function download(format) {
    busy = true;
    try {
      const qs = new URLSearchParams({ format, with_transcript: String(withTranscript) });
      const res = await fetch(`/api/meetings/${meetingId}/export?${qs}`);
      if (!res.ok) {
        let msg = `Export failed (${res.status})`;
        try { msg = (await res.json()).detail || msg; } catch {}
        throw new Error(msg);
      }
      const blob = await res.blob();

      // Resolve filename — prefer server-supplied Content-Disposition.
      const disp = res.headers.get('content-disposition') || '';
      const match = /filename="?([^"]+)"?/.exec(disp);
      const filename = match ? match[1] : `${slug(meetingTitle)}.${format}`;

      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      addToast(`Exported ${format.toUpperCase()}`, 'success');
      close();
    } catch (e) {
      addToast(e.message || 'Export failed', 'error');
    } finally {
      busy = false;
    }
  }
</script>

<div class="relative inline-block">
  <button
    bind:this={triggerRef}
    type="button"
    onclick={toggle}
    disabled={busy}
    class="px-4 py-2 text-sm font-medium text-[var(--text-secondary)]
           border border-[var(--border-subtle)] rounded-lg
           hover:bg-[var(--bg-surface-hover)] hover:text-[var(--text-primary)]
           disabled:opacity-50 transition-colors duration-150"
    aria-haspopup="menu"
    aria-expanded={open}
  >
    {busy ? 'Exporting…' : 'Export ▾'}
  </button>

  {#if open}
    <div
      role="menu"
      class="absolute left-0 w-56 rounded-lg border border-[var(--border-subtle)]
             bg-[var(--bg-surface)] shadow-lg z-20
             max-h-[calc(100vh-7rem)] overflow-y-auto
             {placement === 'top' ? 'bottom-full mb-1' : 'top-full mt-1'}"
    >
      <label class="flex items-center gap-2 px-3 py-2 text-xs text-[var(--text-secondary)] border-b border-[var(--border-subtle)]">
        <input type="checkbox" bind:checked={withTranscript} />
        Include full transcript
      </label>
      <button
        role="menuitem"
        onclick={() => download('pdf')}
        class="w-full text-left px-3 py-2 text-sm text-[var(--text-primary)] hover:bg-[var(--bg-surface-hover)]"
      >
        Download as PDF
      </button>
      <button
        role="menuitem"
        onclick={() => download('docx')}
        class="w-full text-left px-3 py-2 text-sm text-[var(--text-primary)] hover:bg-[var(--bg-surface-hover)]"
      >
        Download as Word (.docx)
      </button>
      <button
        role="menuitem"
        onclick={() => download('md')}
        class="w-full text-left px-3 py-2 text-sm text-[var(--text-primary)] hover:bg-[var(--bg-surface-hover)]"
      >
        Download as Markdown
      </button>
      {#if obsidianEnabled}
        <div class="border-t border-[var(--border-subtle)]"></div>
        <a
          href={`/api/meetings/${meetingId}/obsidian`}
          class="block px-3 py-2 text-sm text-[var(--text-primary)] hover:bg-[var(--bg-surface-hover)]"
        >
          Push to Obsidian vault
        </a>
      {/if}
    </div>
  {/if}
</div>

{#if open}
  <!-- overlay to close on outside click -->
  <div
    class="fixed inset-0 z-10"
    onclick={close}
    onkeydown={(e) => { if (e.key === 'Escape') close(); }}
    role="button"
    tabindex="-1"
    aria-label="Close export menu"
  ></div>
{/if}
