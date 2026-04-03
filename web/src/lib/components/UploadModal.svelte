<script>
  import { api } from '$lib/api.js';

  let {
    open = $bindable(false),
    date = '',
    onClose,
    onUploaded
  } = $props();

  // Form state
  let file = $state(null);
  let title = $state('');
  let timeValue = $state('');
  let attendees = $state('');
  let meetingType = $state('');
  let language = $state('en');

  // UI state
  let uploading = $state(false);
  let errorMsg = $state('');
  let dragging = $state(false);
  let meetingTypes = $state([]);
  let languages = $state([]);
  let fileInputRef = $state(null);

  // Load meeting types and languages when modal opens
  $effect(() => {
    if (open) {
      loadOptions();
      // Reset form
      file = null;
      title = '';
      timeValue = '';
      attendees = '';
      meetingType = '';
      language = 'en';
      errorMsg = '';
      uploading = false;
    }
  });

  async function loadOptions() {
    try {
      const [templates, langs] = await Promise.all([
        api.getTemplates(),
        api.getLanguages(),
      ]);
      meetingTypes = templates || [];
      languages = langs || [];
    } catch {
      // Use defaults if API fails
      meetingTypes = [];
      languages = [{ code: 'en', name: 'English' }];
    }
  }

  function handleClose() {
    if (uploading) return;
    onClose?.();
    open = false;
  }

  function handleKeydown(e) {
    if (e.key === 'Escape') handleClose();
  }

  function handleBackdropClick(e) {
    if (e.target === e.currentTarget) handleClose();
  }

  function handleDragOver(e) {
    e.preventDefault();
    dragging = true;
  }

  function handleDragLeave(e) {
    e.preventDefault();
    dragging = false;
  }

  function handleDrop(e) {
    e.preventDefault();
    dragging = false;
    const dropped = e.dataTransfer?.files?.[0];
    if (dropped) {
      file = dropped;
    }
  }

  function handleFileSelect(e) {
    const selected = e.target.files?.[0];
    if (selected) {
      file = selected;
    }
  }

  function removeFile() {
    file = null;
    if (fileInputRef) fileInputRef.value = '';
  }

  function formatFileSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  async function handleUpload() {
    if (!file || uploading) return;
    errorMsg = '';
    uploading = true;

    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('title', title || file.name.replace(/\.[^.]+$/, ''));
      formData.append('date', date);
      formData.append('time', timeValue);
      formData.append('attendees', attendees);
      formData.append('meeting_type', meetingType);
      formData.append('language', language);

      const result = await api.uploadTranscript(formData);
      onUploaded?.(result.meeting_id);
      open = false;
    } catch (err) {
      errorMsg = err.message || 'Upload failed';
    } finally {
      uploading = false;
    }
  }
</script>

{#if open}
  <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
  <div
    class="fixed inset-0 z-50 flex items-center justify-center p-4"
    role="dialog"
    aria-modal="true"
    aria-labelledby="upload-modal-title"
    onkeydown={handleKeydown}
  >
    <!-- Backdrop -->
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div
      class="absolute inset-0 bg-black/50 backdrop-blur-sm"
      onclick={handleBackdropClick}
    ></div>

    <!-- Modal -->
    <div class="relative bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl shadow-xl max-w-lg w-full p-6 max-h-[90vh] overflow-y-auto">
      <h3 id="upload-modal-title" class="text-lg font-semibold text-[var(--text-primary)] mb-4">
        Upload Transcript
      </h3>

      <!-- File drop zone -->
      <!-- svelte-ignore a11y_click_events_have_key_events -->
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <div
        class="relative border-2 border-dashed rounded-lg p-6 text-center transition-colors duration-150 mb-4
               {dragging
                 ? 'border-[var(--accent)] bg-[var(--accent)]/5'
                 : file
                   ? 'border-[var(--border-subtle)] bg-[var(--bg-surface-hover)]'
                   : 'border-[var(--border-subtle)] hover:border-[var(--text-muted)]'}"
        ondragover={handleDragOver}
        ondragleave={handleDragLeave}
        ondrop={handleDrop}
        onclick={() => !file && fileInputRef?.click()}
      >
        <input
          bind:this={fileInputRef}
          type="file"
          accept=".txt,.csv,.json"
          class="hidden"
          onchange={handleFileSelect}
        />

        {#if file}
          <div class="flex items-center justify-between gap-3">
            <div class="flex items-center gap-2 min-w-0">
              <svg class="w-5 h-5 text-[var(--accent)] shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/>
              </svg>
              <div class="min-w-0">
                <p class="text-sm font-medium text-[var(--text-primary)] truncate">{file.name}</p>
                <p class="text-xs text-[var(--text-muted)]">{formatFileSize(file.size)}</p>
              </div>
            </div>
            <button
              onclick={(e) => { e.stopPropagation(); removeFile(); }}
              class="p-1 rounded hover:bg-[var(--bg-surface)] text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors"
              title="Remove file"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
              </svg>
            </button>
          </div>
        {:else}
          <svg class="w-8 h-8 mx-auto mb-2 text-[var(--text-muted)] opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"/>
          </svg>
          <p class="text-sm text-[var(--text-muted)]">
            {dragging ? 'Drop file here' : 'Drop a transcript file here or click to browse'}
          </p>
          <p class="text-xs text-[var(--text-muted)] mt-1 opacity-60">.txt, .csv, .json</p>
        {/if}
      </div>

      <!-- Form fields -->
      <div class="space-y-3">
        <!-- Title -->
        <div>
          <label for="upload-title" class="block text-xs font-medium text-[var(--text-muted)] mb-1">Title</label>
          <input
            id="upload-title"
            type="text"
            bind:value={title}
            placeholder="Meeting title"
            class="w-full px-3 py-2 text-sm bg-[var(--bg-base)] border border-[var(--border-subtle)] rounded-lg
                   text-[var(--text-primary)] placeholder:text-[var(--text-muted)]
                   focus:outline-none focus:ring-1 focus:ring-[var(--accent)] focus:border-[var(--accent)]"
          />
        </div>

        <!-- Date (read-only) + Time -->
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label for="upload-date" class="block text-xs font-medium text-[var(--text-muted)] mb-1">Date</label>
            <input
              id="upload-date"
              type="text"
              value={date}
              readonly
              class="w-full px-3 py-2 text-sm bg-[var(--bg-surface-hover)] border border-[var(--border-subtle)] rounded-lg
                     text-[var(--text-secondary)] cursor-not-allowed"
            />
          </div>
          <div>
            <label for="upload-time" class="block text-xs font-medium text-[var(--text-muted)] mb-1">Time (optional)</label>
            <input
              id="upload-time"
              type="time"
              bind:value={timeValue}
              class="w-full px-3 py-2 text-sm bg-[var(--bg-base)] border border-[var(--border-subtle)] rounded-lg
                     text-[var(--text-primary)]
                     focus:outline-none focus:ring-1 focus:ring-[var(--accent)] focus:border-[var(--accent)]"
            />
          </div>
        </div>

        <!-- Attendees -->
        <div>
          <label for="upload-attendees" class="block text-xs font-medium text-[var(--text-muted)] mb-1">Attendees (comma-separated)</label>
          <input
            id="upload-attendees"
            type="text"
            bind:value={attendees}
            placeholder="Alice, Bob, Charlie"
            class="w-full px-3 py-2 text-sm bg-[var(--bg-base)] border border-[var(--border-subtle)] rounded-lg
                   text-[var(--text-primary)] placeholder:text-[var(--text-muted)]
                   focus:outline-none focus:ring-1 focus:ring-[var(--accent)] focus:border-[var(--accent)]"
          />
        </div>

        <!-- Meeting type + Language -->
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label for="upload-type" class="block text-xs font-medium text-[var(--text-muted)] mb-1">Meeting Type</label>
            <select
              id="upload-type"
              bind:value={meetingType}
              class="w-full px-3 py-2 text-sm bg-[var(--bg-base)] border border-[var(--border-subtle)] rounded-lg
                     text-[var(--text-primary)]
                     focus:outline-none focus:ring-1 focus:ring-[var(--accent)] focus:border-[var(--accent)]"
            >
              <option value="">Auto-detect</option>
              {#each meetingTypes as t}
                <option value={t.meeting_type}>{t.name || t.meeting_type}</option>
              {/each}
            </select>
          </div>
          <div>
            <label for="upload-lang" class="block text-xs font-medium text-[var(--text-muted)] mb-1">Language</label>
            <select
              id="upload-lang"
              bind:value={language}
              class="w-full px-3 py-2 text-sm bg-[var(--bg-base)] border border-[var(--border-subtle)] rounded-lg
                     text-[var(--text-primary)]
                     focus:outline-none focus:ring-1 focus:ring-[var(--accent)] focus:border-[var(--accent)]"
            >
              {#each languages as lang}
                <option value={lang.code}>{lang.name}</option>
              {/each}
              {#if languages.length === 0}
                <option value="en">English</option>
              {/if}
            </select>
          </div>
        </div>
      </div>

      <!-- Error message -->
      {#if errorMsg}
        <div class="mt-3 p-2 rounded-lg bg-red-500/10 border border-red-500/20">
          <p class="text-xs text-red-400">{errorMsg}</p>
        </div>
      {/if}

      <!-- Buttons -->
      <div class="flex items-center justify-end gap-3 mt-5">
        <button
          onclick={handleClose}
          disabled={uploading}
          class="px-4 py-2 text-sm font-medium text-[var(--text-secondary)] bg-[var(--bg-surface-hover)]
                 border border-[var(--border-subtle)] rounded-lg
                 hover:text-[var(--text-primary)] transition-colors duration-150
                 disabled:opacity-50"
        >
          Cancel
        </button>
        <button
          onclick={handleUpload}
          disabled={!file || uploading}
          class="px-4 py-2 text-sm font-medium text-white rounded-lg transition-colors duration-150
                 bg-[var(--accent)] hover:bg-[var(--accent-hover)]
                 disabled:opacity-50 disabled:cursor-not-allowed
                 flex items-center gap-2"
        >
          {#if uploading}
            <svg class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            Uploading...
          {:else}
            Upload
          {/if}
        </button>
      </div>
    </div>
  </div>
{/if}
