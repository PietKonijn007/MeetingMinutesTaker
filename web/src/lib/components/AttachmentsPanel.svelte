<script>
  /**
   * Attachments panel — list + uploader + add-link + global paste handler.
   *
   * Drops into any page that has a meeting_id (meeting detail tab,
   * record page once recording starts). Single self-contained component
   * is simpler than spreading the surface across List/Card/Uploader for
   * the MVP — splitting can come if a second consumer needs only one of
   * the pieces.
   *
   * Polling:
   *   We poll /api/meetings/{id}/attachments every 4s while any row is
   *   pending|extracting|summarizing. As soon as everything is ready or
   *   error, polling stops. This mirrors the external-notes / meeting-
   *   type-change polling shape elsewhere in the app.
   *
   * Paste handler:
   *   When `enablePaste` is true (default), a window-level paste
   *   listener catches clipboard images and uploads them as PNGs. We
   *   only react when the focus isn't inside a text input — pasting
   *   into a caption field shouldn't also create an attachment.
   */
  import { api } from '../api.js';
  import { onMount, onDestroy } from 'svelte';
  import MarkdownRenderer from './MarkdownRenderer.svelte';

  let { meetingId, onChanged = () => {}, enablePaste = true } = $props();

  let attachments = $state([]);
  let loading = $state(true);
  let loadError = $state('');
  let uploading = $state(false);
  let uploadError = $state('');
  let dragOver = $state(false);
  let linkUrl = $state('');
  let linkTitle = $state('');
  let linkCaption = $state('');
  let linkSubmitting = $state(false);
  let linkError = $state('');
  let pollTimer = null;
  let detail = $state(null);
  let detailLoading = $state(false);

  const PENDING_STATES = ['pending', 'extracting', 'summarizing', 'uploading'];

  async function refresh() {
    if (!meetingId) {
      attachments = [];
      loading = false;
      return;
    }
    try {
      attachments = await api.listAttachments(meetingId);
      loadError = '';
    } catch (e) {
      loadError = e.message || String(e);
    } finally {
      loading = false;
    }
    schedulePolling();
  }

  function schedulePolling() {
    const stillWorking = attachments.some((a) => PENDING_STATES.includes(a.status));
    if (stillWorking && !pollTimer) {
      pollTimer = setInterval(refresh, 4000);
    } else if (!stillWorking && pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  async function handleFiles(fileList) {
    if (!meetingId) {
      uploadError = 'No meeting yet — start recording to attach files.';
      return;
    }
    uploadError = '';
    uploading = true;
    try {
      for (const file of fileList) {
        try {
          await api.uploadAttachment(meetingId, file);
        } catch (e) {
          uploadError = `${file.name}: ${e.message || e}`;
        }
      }
      await refresh();
      onChanged();
    } finally {
      uploading = false;
    }
  }

  function onDrop(event) {
    event.preventDefault();
    dragOver = false;
    if (event.dataTransfer?.files?.length) {
      handleFiles(event.dataTransfer.files);
    }
  }

  function onPickFiles(event) {
    if (event.target.files?.length) {
      handleFiles(event.target.files);
      // Reset so picking the same file again still triggers `change`.
      event.target.value = '';
    }
  }

  async function submitLink() {
    if (!meetingId) {
      linkError = 'No meeting yet — start recording to attach links.';
      return;
    }
    const url = linkUrl.trim();
    if (!url) return;
    linkError = '';
    linkSubmitting = true;
    try {
      await api.addLinkAttachment(meetingId, {
        url,
        title: linkTitle.trim(),
        caption: linkCaption.trim(),
      });
      linkUrl = '';
      linkTitle = '';
      linkCaption = '';
      await refresh();
      onChanged();
    } catch (e) {
      linkError = e.message || String(e);
    } finally {
      linkSubmitting = false;
    }
  }

  async function deleteAttachment(att) {
    if (!confirm(`Remove "${att.title}" from this meeting?`)) return;
    try {
      await api.deleteAttachment(att.attachment_id);
      await refresh();
      onChanged();
    } catch (e) {
      alert(`Delete failed: ${e.message || e}`);
    }
  }

  let detailTab = $state('summary');  // 'summary' | 'extracted' | 'metadata'
  let detailReprocessing = $state(false);
  let detailCopyConfirm = $state(false);

  async function openDetail(att) {
    detail = { ...att, summary: '', extracted_text: '', loading: true };
    detailLoading = true;
    detailTab = 'summary';
    detailCopyConfirm = false;
    try {
      const full = await api.getAttachment(att.attachment_id);
      detail = full;
    } catch (e) {
      detail = { ...att, summary: '', extracted_text: `Failed to load: ${e.message}` };
    } finally {
      detailLoading = false;
    }
  }

  function closeDetail() {
    detail = null;
  }

  async function reprocessFromDetail() {
    if (!detail) return;
    detailReprocessing = true;
    try {
      await api.reprocessAttachment(detail.attachment_id);
      // Close the modal — the row in the list will start polling again
      // and the user can re-open once it's ready.
      closeDetail();
      await refresh();
    } catch (e) {
      alert(`Reprocess failed: ${e.message || e}`);
    } finally {
      detailReprocessing = false;
    }
  }

  async function copySummary() {
    if (!detail?.summary) return;
    try {
      await navigator.clipboard.writeText(detail.summary);
      detailCopyConfirm = true;
      setTimeout(() => (detailCopyConfirm = false), 1500);
    } catch {
      // Clipboard API requires HTTPS or localhost — silently no-op
      // if it's blocked.
    }
  }

  function formatBytes(n) {
    if (n == null) return '';
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / 1024 / 1024).toFixed(1)} MB`;
  }

  function statusLabel(status) {
    return {
      pending: 'Queued',
      extracting: 'Extracting…',
      summarizing: 'Summarizing…',
      ready: 'Ready',
      error: 'Error',
      uploading: 'Uploading…',
    }[status] || status;
  }

  function statusClass(status) {
    return {
      ready: 'bg-green-500/10 text-green-300 border-green-500/30',
      error: 'bg-red-500/10 text-red-300 border-red-500/30',
    }[status] || 'bg-blue-500/10 text-blue-300 border-blue-500/30';
  }

  function kindIcon(kind) {
    return { file: '📄', link: '🔗', image: '🖼️' }[kind] || '📎';
  }

  // Paste handler: capture clipboard images globally on the window.
  // Skip when the user is typing in a text input — pasting a URL into
  // the link field would otherwise also try to upload it.
  function onPaste(event) {
    if (!enablePaste) return;
    const target = event.target;
    if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) {
      return;
    }
    const items = event.clipboardData?.items || [];
    const files = [];
    for (const item of items) {
      if (item.kind === 'file' && item.type.startsWith('image/')) {
        const blob = item.getAsFile();
        if (blob) {
          // Browsers give pasted images generic names like "image.png".
          // Synthesize a timestamped name so the user can tell them apart.
          const stamped = new File([blob], `pasted-${Date.now()}.png`, { type: blob.type });
          files.push(stamped);
        }
      }
    }
    if (files.length) {
      event.preventDefault();
      handleFiles(files);
    }
  }

  onMount(() => {
    refresh();
    if (enablePaste) {
      window.addEventListener('paste', onPaste);
    }
  });

  onDestroy(() => {
    if (pollTimer) clearInterval(pollTimer);
    if (enablePaste) window.removeEventListener('paste', onPaste);
  });

  // Re-fetch when the meetingId changes (e.g. recording starts).
  $effect(() => {
    if (meetingId) refresh();
  });
</script>

<div class="space-y-4">
  <!-- Drop zone + file picker -->
  <label
    for="att-file-input-{meetingId || 'none'}"
    class="block cursor-pointer rounded-md border-2 border-dashed transition-colors p-4 text-center text-xs
           {dragOver
             ? 'border-[var(--accent)] bg-[var(--accent)]/10'
             : 'border-[var(--border-subtle)] bg-[var(--bg-surface)] hover:border-[var(--accent)]/60'}"
    ondragover={(e) => { e.preventDefault(); dragOver = true; }}
    ondragleave={() => (dragOver = false)}
    ondrop={onDrop}
  >
    {#if uploading}
      <span class="text-[var(--text-muted)]">Uploading…</span>
    {:else}
      <span class="text-[var(--text-primary)] font-medium">Drop files here</span>
      <span class="text-[var(--text-muted)]"> or click to browse · paste a screenshot</span>
      <div class="text-[10px] text-[var(--text-muted)] mt-1">
        PDF · DOCX · PPTX · PNG · JPG · HEIC (≤ 50 MB)
      </div>
    {/if}
    <input
      id="att-file-input-{meetingId || 'none'}"
      type="file"
      multiple
      class="hidden"
      accept="application/pdf,.docx,.pptx,image/png,image/jpeg,image/heic"
      onchange={onPickFiles}
    />
  </label>

  {#if uploadError}
    <div class="px-3 py-2 rounded-md bg-red-500/10 border border-red-500/30 text-xs text-red-300">
      {uploadError}
    </div>
  {/if}

  <!-- Add link -->
  <details class="rounded-md border border-[var(--border-subtle)] bg-[var(--bg-surface)]">
    <summary class="cursor-pointer px-3 py-2 text-xs font-medium text-[var(--text-primary)]">
      Add a link
    </summary>
    <div class="p-3 pt-0 space-y-2">
      <input
        type="url"
        bind:value={linkUrl}
        placeholder="https://example.com/spec"
        class="w-full px-2 py-1.5 text-xs rounded border border-[var(--border-subtle)] bg-[var(--bg-base)] text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent)]"
      />
      <input
        type="text"
        bind:value={linkTitle}
        placeholder="Title (optional — auto-detected from page)"
        class="w-full px-2 py-1.5 text-xs rounded border border-[var(--border-subtle)] bg-[var(--bg-base)] text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent)]"
      />
      <input
        type="text"
        bind:value={linkCaption}
        placeholder="Caption (why this matters — optional)"
        class="w-full px-2 py-1.5 text-xs rounded border border-[var(--border-subtle)] bg-[var(--bg-base)] text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent)]"
      />
      {#if linkError}
        <div class="text-xs text-red-300">{linkError}</div>
      {/if}
      <button
        onclick={submitLink}
        disabled={linkSubmitting || !linkUrl.trim()}
        class="px-3 py-1.5 text-xs font-medium rounded bg-[var(--accent)] text-white hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {linkSubmitting ? 'Adding…' : 'Add link'}
      </button>
    </div>
  </details>

  <!-- Attachments list -->
  {#if loading}
    <div class="text-xs text-[var(--text-muted)]">Loading attachments…</div>
  {:else if loadError}
    <div class="px-3 py-2 rounded-md bg-red-500/10 border border-red-500/30 text-xs text-red-300">
      {loadError}
    </div>
  {:else if attachments.length === 0}
    <div class="text-xs text-[var(--text-muted)] italic">
      No attachments yet. Drop files here, paste a screenshot, or add a link above.
    </div>
  {:else}
    <ul class="space-y-2">
      {#each attachments as att (att.attachment_id)}
        <li class="flex items-start gap-3 px-3 py-2 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-surface)]">
          <span class="text-lg leading-none mt-0.5">{kindIcon(att.kind)}</span>
          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-2 flex-wrap">
              <button
                class="text-sm font-medium text-[var(--text-primary)] hover:text-[var(--accent)] truncate text-left"
                onclick={() => openDetail(att)}
              >
                {att.title}
              </button>
              <span
                class="text-[10px] px-1.5 py-0.5 rounded border {statusClass(att.status)}"
              >
                {statusLabel(att.status)}
              </span>
            </div>
            <div class="text-[11px] text-[var(--text-muted)] truncate">
              {att.original_filename || att.kind === 'link' ? '' : ''}
              {#if att.original_filename}{att.original_filename}{/if}
              {#if att.size_bytes != null}
                · {formatBytes(att.size_bytes)}
              {/if}
              {#if att.caption}
                · <span class="italic">{att.caption}</span>
              {/if}
            </div>
            {#if att.status === 'error' && att.error}
              <div class="text-[11px] text-red-300 mt-1">{att.error}</div>
            {/if}
          </div>
          <div class="flex items-center gap-1 shrink-0">
            {#if att.kind !== 'link'}
              <a
                href={api.attachmentRawUrl(att.attachment_id)}
                target="_blank"
                rel="noopener"
                class="text-[11px] px-2 py-1 rounded text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-base)]"
                title="Open original"
              >
                Open
              </a>
            {/if}
            <button
              onclick={() => deleteAttachment(att)}
              class="text-[11px] px-2 py-1 rounded text-[var(--text-muted)] hover:text-red-300 hover:bg-red-500/10"
              title="Delete"
            >
              Remove
            </button>
          </div>
        </li>
      {/each}
    </ul>
  {/if}
</div>

<!--
  Window-level keydown — always mounted; checks the modal state at
  fire time. <svelte:window> cannot live inside an {#if}.
-->
<svelte:window onkeydown={(e) => { if (detail && e.key === 'Escape') closeDetail(); }} />

<!--
  Detail modal. Backdrop is a presentation-role wrapper (the actual
  semantic surface is the inner role="dialog"); Escape handled above.
-->
{#if detail}
  <div
    class="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4"
    onclick={closeDetail}
    role="presentation"
  >
    <div
      class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl shadow-2xl max-w-5xl w-full max-h-[88vh] overflow-hidden flex flex-col"
      role="dialog"
      aria-modal="true"
      aria-label={detail.title}
      tabindex="-1"
      onclick={(e) => e.stopPropagation()}
      onkeydown={(e) => e.stopPropagation()}
    >
      <!-- Sticky header: title + toolbar (Open · Reprocess · Copy · Close) -->
      <div class="sticky top-0 z-10 px-6 py-4 border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] flex items-start justify-between gap-4">
        <div class="min-w-0 flex-1">
          <div class="flex items-center gap-2">
            <span class="text-2xl leading-none">{kindIcon(detail.kind)}</span>
            <h2 class="text-base font-semibold text-[var(--text-primary)] truncate">
              {detail.title}
            </h2>
            <span
              class="text-[10px] px-1.5 py-0.5 rounded border whitespace-nowrap {statusClass(detail.status)}"
            >
              {statusLabel(detail.status)}
            </span>
          </div>
          <div class="text-[11px] text-[var(--text-muted)] mt-1 truncate">
            {#if detail.url}
              <a href={detail.url} target="_blank" rel="noopener" class="hover:text-[var(--accent)]">{detail.url}</a>
            {:else}
              {detail.original_filename || ''}
              {#if detail.size_bytes != null} · {formatBytes(detail.size_bytes)}{/if}
            {/if}
          </div>
        </div>

        <!-- Toolbar -->
        <div class="flex items-center gap-1 shrink-0">
          {#if detail.kind !== 'link'}
            <a
              href={api.attachmentRawUrl(detail.attachment_id)}
              target="_blank"
              rel="noopener"
              class="px-2.5 py-1.5 text-xs rounded-md text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-base)] transition-colors"
              title="Open the original file in a new tab"
            >
              Open original
            </a>
          {/if}
          <button
            onclick={reprocessFromDetail}
            disabled={detailReprocessing}
            class="px-2.5 py-1.5 text-xs rounded-md text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-base)] transition-colors disabled:opacity-50"
            title="Re-run extraction + summarization"
          >
            {detailReprocessing ? 'Reprocessing…' : 'Reprocess'}
          </button>
          <button
            onclick={copySummary}
            disabled={!detail.summary}
            class="px-2.5 py-1.5 text-xs rounded-md text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-base)] transition-colors disabled:opacity-50"
            title="Copy summary to clipboard"
          >
            {detailCopyConfirm ? '✓ Copied' : 'Copy summary'}
          </button>
          <button
            onclick={closeDetail}
            class="ml-1 text-[var(--text-muted)] hover:text-[var(--text-primary)] p-1.5 rounded-md hover:bg-[var(--bg-base)] transition-colors"
            aria-label="Close"
            title="Close (Esc)"
          >
            ✕
          </button>
        </div>
      </div>

      <!-- Two-pane body: metadata sidebar (left) + tabbed content (right) -->
      <div class="flex-1 overflow-hidden flex">
        <!-- Sidebar: kind / source / status / extraction method / counts -->
        <aside class="w-56 shrink-0 border-r border-[var(--border-subtle)] bg-[var(--bg-base)] overflow-y-auto p-4 space-y-4 text-[11px]">
          <div>
            <div class="uppercase tracking-wide text-[var(--text-muted)] mb-1">Kind</div>
            <div class="text-[var(--text-primary)] capitalize">{detail.kind}</div>
          </div>
          {#if detail.mime_type}
            <div>
              <div class="uppercase tracking-wide text-[var(--text-muted)] mb-1">MIME</div>
              <div class="text-[var(--text-primary)] font-mono break-all">{detail.mime_type}</div>
            </div>
          {/if}
          {#if detail.size_bytes != null}
            <div>
              <div class="uppercase tracking-wide text-[var(--text-muted)] mb-1">Size</div>
              <div class="text-[var(--text-primary)]">{formatBytes(detail.size_bytes)}</div>
            </div>
          {/if}
          {#if detail.summary_status}
            <div>
              <div class="uppercase tracking-wide text-[var(--text-muted)] mb-1">Summary status</div>
              <div class="text-[var(--text-primary)] capitalize">{detail.summary_status}</div>
            </div>
          {/if}
          {#if detail.extracted_text}
            <div>
              <div class="uppercase tracking-wide text-[var(--text-muted)] mb-1">Extracted</div>
              <div class="text-[var(--text-primary)]">{detail.extracted_text.length.toLocaleString()} chars</div>
            </div>
          {/if}
          {#if detail.caption}
            <div>
              <div class="uppercase tracking-wide text-[var(--text-muted)] mb-1">Caption</div>
              <div class="text-[var(--text-primary)] italic leading-relaxed">{detail.caption}</div>
            </div>
          {/if}
          {#if detail.created_at}
            <div>
              <div class="uppercase tracking-wide text-[var(--text-muted)] mb-1">Added</div>
              <div class="text-[var(--text-primary)]">
                {new Date(detail.created_at).toLocaleString()}
              </div>
            </div>
          {/if}
          {#if detail.error}
            <div class="border-t border-[var(--border-subtle)] pt-3">
              <div class="uppercase tracking-wide text-red-300 mb-1">Error</div>
              <div class="text-red-300 leading-relaxed">{detail.error}</div>
            </div>
          {/if}
        </aside>

        <!-- Right pane: tabs + content -->
        <div class="flex-1 flex flex-col overflow-hidden">
          <!-- Tab strip -->
          <div class="px-6 pt-3 border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] flex gap-1">
            {#each [
              { key: 'summary', label: 'Summary' },
              { key: 'extracted', label: detail.extracted_text ? `Extracted (${detail.extracted_text.length.toLocaleString()} chars)` : 'Extracted' },
            ] as t}
              <button
                onclick={() => (detailTab = t.key)}
                disabled={t.key === 'extracted' && !detail.extracted_text}
                class="px-3 py-2 text-xs font-medium border-b-2 transition-colors disabled:opacity-40
                  {detailTab === t.key
                    ? 'border-[var(--accent)] text-[var(--text-primary)]'
                    : 'border-transparent text-[var(--text-muted)] hover:text-[var(--text-primary)]'}"
              >
                {t.label}
              </button>
            {/each}
          </div>

          <!-- Tab body -->
          <div class="flex-1 overflow-y-auto px-6 py-5 bg-[var(--bg-surface)]">
            {#if detailLoading}
              <div class="text-xs text-[var(--text-muted)]">Loading…</div>
            {:else if detailTab === 'summary'}
              {#if detail.summary}
                <MarkdownRenderer content={detail.summary} />
              {:else if detail.summary_status === 'pending'}
                <div class="text-sm text-[var(--text-muted)] italic">
                  Summary still being generated… The page will refresh automatically.
                </div>
              {:else if detail.summary_status === 'error'}
                <div class="text-sm text-red-300">
                  Summary generation failed. Try <button
                    class="underline hover:text-red-200"
                    onclick={reprocessFromDetail}
                    disabled={detailReprocessing}
                  >reprocessing</button>.
                </div>
              {:else}
                <div class="text-sm text-[var(--text-muted)] italic">No summary available.</div>
              {/if}
            {:else if detailTab === 'extracted'}
              {#if detail.extracted_text}
                <pre class="text-xs text-[var(--text-secondary)] whitespace-pre-wrap font-mono leading-relaxed">{detail.extracted_text}</pre>
              {:else}
                <div class="text-sm text-[var(--text-muted)] italic">No extracted text yet.</div>
              {/if}
            {/if}
          </div>
        </div>
      </div>
    </div>
  </div>
{/if}
