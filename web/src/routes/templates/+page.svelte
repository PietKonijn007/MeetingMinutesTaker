<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api.js';
  import { addToast } from '$lib/stores/toasts.js';
  import Skeleton from '$lib/components/Skeleton.svelte';

  let templates = $state([]);
  let loading = $state(true);
  let selected = $state(null);
  let saving = $state(false);
  let isNew = $state(false);
  let showVariables = $state(false);
  let showDeleteConfirm = $state(false);

  // Editor state
  let editType = $state('');
  let editSystemPrompt = $state('');
  let editUserPrompt = $state('');

  function humanize(slug) {
    return slug.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  }

  async function loadTemplates() {
    loading = true;
    try {
      templates = await api.getTemplates();
    } catch (e) {
      addToast(`Failed to load templates: ${e.message}`, 'error');
    } finally {
      loading = false;
    }
  }

  async function selectTemplate(t) {
    isNew = false;
    showDeleteConfirm = false;
    try {
      const detail = await api.getTemplate(t.meeting_type);
      selected = detail;
      editType = detail.meeting_type;
      editSystemPrompt = detail.system_prompt;
      editUserPrompt = detail.user_prompt_template;
    } catch (e) {
      addToast(`Failed to load template: ${e.message}`, 'error');
    }
  }

  function startNew() {
    selected = null;
    isNew = true;
    showDeleteConfirm = false;
    editType = '';
    editSystemPrompt = '';
    editUserPrompt = '';
  }

  async function save() {
    const slug = editType.trim().toLowerCase().replace(/\s+/g, '_');
    if (!slug) {
      addToast('Meeting type is required', 'error');
      return;
    }
    if (!/^[a-z][a-z0-9_]*$/.test(slug)) {
      addToast('Meeting type must start with a letter and contain only lowercase letters, digits, and underscores', 'error');
      return;
    }

    saving = true;
    try {
      const result = await api.updateTemplate(slug, {
        system_prompt: editSystemPrompt,
        user_prompt_template: editUserPrompt,
      });
      addToast('Template saved!', 'success');

      // Refresh list
      await loadTemplates();

      // Select the saved template
      selected = result;
      editType = result.meeting_type;
      isNew = false;
    } catch (e) {
      addToast(`Failed to save: ${e.message}`, 'error');
    } finally {
      saving = false;
    }
  }

  async function deleteTemplate() {
    if (!selected) return;
    try {
      await api.deleteTemplate(selected.meeting_type);
      addToast('Template deleted', 'success');
      selected = null;
      isNew = false;
      showDeleteConfirm = false;
      await loadTemplates();
    } catch (e) {
      addToast(`Failed to delete: ${e.message}`, 'error');
    }
  }

  onMount(loadTemplates);
</script>

<div class="flex h-[calc(100vh-3.5rem)] -m-6">
  <!-- Left panel: Template list -->
  <div class="w-72 shrink-0 border-r border-[var(--border-subtle)] bg-[var(--bg-surface)] flex flex-col overflow-hidden">
    <div class="px-4 py-3 border-b border-[var(--border-subtle)] flex items-center justify-between">
      <h2 class="text-sm font-semibold text-[var(--text-primary)]">Templates</h2>
      <button
        onclick={startNew}
        class="px-2.5 py-1 text-xs font-medium rounded-md bg-[var(--accent)] text-white hover:bg-[var(--accent-hover)] transition-colors"
      >
        + New
      </button>
    </div>

    <div class="flex-1 overflow-y-auto">
      {#if loading}
        <div class="p-4 space-y-3">
          {#each Array(6) as _}
            <Skeleton type="text" lines={2} />
          {/each}
        </div>
      {:else}
        <div class="py-1">
          {#each templates as t}
            {@const isSelected = !isNew && selected?.meeting_type === t.meeting_type}
            <button
              onclick={() => selectTemplate(t)}
              class="w-full text-left px-4 py-3 transition-colors duration-100
                     {isSelected
                       ? 'border-l-2 border-[var(--accent)] bg-[var(--accent)]/5 pl-[14px]'
                       : 'border-l-2 border-transparent hover:bg-[var(--bg-surface-hover)]'}"
            >
              <div class="flex items-center gap-2">
                <span class="text-sm font-medium text-[var(--text-primary)] truncate">{humanize(t.meeting_type)}</span>
                {#if t.builtin}
                  <span class="shrink-0 px-1.5 py-0.5 text-[10px] font-medium rounded bg-[var(--accent)]/10 text-[var(--accent)]">Built-in</span>
                {/if}
              </div>
              <p class="text-xs text-[var(--text-muted)] mt-0.5 truncate">{t.description}</p>
            </button>
          {/each}
        </div>
      {/if}
    </div>
  </div>

  <!-- Right panel: Editor -->
  <div class="flex-1 overflow-y-auto">
    {#if !selected && !isNew}
      <!-- Empty state -->
      <div class="flex items-center justify-center h-full text-[var(--text-muted)]">
        <div class="text-center">
          <svg class="w-12 h-12 mx-auto mb-3 opacity-30" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/>
          </svg>
          <p class="text-sm">Select a template to edit or create a new one</p>
        </div>
      </div>
    {:else}
      <div class="max-w-3xl mx-auto p-6">
        <!-- Header -->
        <div class="flex items-center justify-between mb-6">
          <h1 class="text-xl font-bold text-[var(--text-primary)]">
            {#if isNew}
              New Template
            {:else}
              {humanize(selected.meeting_type)}
            {/if}
          </h1>
          {#if selected?.builtin}
            <span class="px-2 py-1 text-xs font-medium rounded bg-[var(--accent)]/10 text-[var(--accent)]">Built-in</span>
          {/if}
        </div>

        <div class="space-y-6">
          <!-- Meeting type slug (only for new) -->
          {#if isNew}
            <div>
              <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Meeting Type Slug</label>
              <input
                type="text"
                bind:value={editType}
                placeholder="e.g., board_meeting"
                class="w-full px-3 py-2 bg-[var(--bg-primary)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)] font-mono
                       focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
              />
              <p class="text-xs text-[var(--text-muted)] mt-1">Lowercase letters, digits, and underscores only. This becomes the filename.</p>
            </div>
          {/if}

          <!-- System Prompt -->
          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">System Prompt (LLM Instructions)</label>
            <textarea
              bind:value={editSystemPrompt}
              placeholder="Instructions that tell the LLM how to analyze this type of meeting..."
              class="w-full min-h-40 px-3 py-2 bg-[var(--bg-primary)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)] font-mono leading-relaxed resize-y
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            ></textarea>
            <p class="text-xs text-[var(--text-muted)] mt-1">Instructions that tell the LLM how to analyze this type of meeting</p>
          </div>

          <!-- User Prompt Template -->
          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">User Prompt Template (Jinja2)</label>
            <textarea
              bind:value={editUserPrompt}
              placeholder="The prompt sent to the LLM with meeting context and transcript..."
              class="w-full min-h-64 px-3 py-2 bg-[var(--bg-primary)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)] font-mono leading-relaxed resize-y
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            ></textarea>
            <p class="text-xs text-[var(--text-muted)] mt-1">The prompt sent to the LLM with meeting context and transcript. Uses Jinja2 syntax.</p>
          </div>

          <!-- Available Variables -->
          <div class="border border-[var(--border-subtle)] rounded-lg overflow-hidden">
            <button
              onclick={() => showVariables = !showVariables}
              class="w-full flex items-center justify-between px-4 py-2.5 text-sm font-medium text-[var(--text-primary)] bg-[var(--bg-surface)] hover:bg-[var(--bg-surface-hover)] transition-colors"
            >
              <span>Available Variables</span>
              <svg class="w-4 h-4 transition-transform {showVariables ? 'rotate-180' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
              </svg>
            </button>
            {#if showVariables}
              <div class="px-4 py-3 bg-[var(--bg-primary)] border-t border-[var(--border-subtle)]">
                <div class="grid grid-cols-2 gap-2">
                  {#each [
                    { var: '{{ title }}', desc: 'Meeting title' },
                    { var: '{{ date }}', desc: 'Meeting date' },
                    { var: '{{ duration }}', desc: 'Meeting duration' },
                    { var: "{{ attendees | join(', ') }}", desc: 'Comma-separated attendees' },
                    { var: '{{ organizer }}', desc: 'Meeting organizer' },
                    { var: '{{ meeting_type }}', desc: 'Meeting type slug' },
                    { var: '{{ transcript_text }}', desc: 'Full transcript text' },
                  ] as v}
                    <div class="flex items-start gap-2 py-1">
                      <code class="shrink-0 text-xs px-1.5 py-0.5 bg-[var(--bg-surface)] rounded text-[var(--accent)] font-mono">{v.var}</code>
                      <span class="text-xs text-[var(--text-muted)]">{v.desc}</span>
                    </div>
                  {/each}
                </div>
              </div>
            {/if}
          </div>

          <!-- Actions -->
          <div class="flex items-center gap-3 pt-4 border-t border-[var(--border-subtle)]">
            <button
              onclick={save}
              disabled={saving}
              class="px-5 py-2 bg-[var(--accent)] text-white rounded-lg text-sm font-medium
                     hover:bg-[var(--accent-hover)] disabled:opacity-50 transition-colors duration-150"
            >
              {saving ? 'Saving...' : 'Save Template'}
            </button>

            {#if selected && !selected.builtin}
              {#if showDeleteConfirm}
                <span class="text-sm text-[var(--text-muted)]">Are you sure?</span>
                <button
                  onclick={deleteTemplate}
                  class="px-4 py-2 bg-red-600 text-white rounded-lg text-sm font-medium hover:bg-red-700 transition-colors"
                >
                  Confirm Delete
                </button>
                <button
                  onclick={() => showDeleteConfirm = false}
                  class="px-4 py-2 text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
                >
                  Cancel
                </button>
              {:else}
                <button
                  onclick={() => showDeleteConfirm = true}
                  class="px-4 py-2 text-red-500 rounded-lg text-sm font-medium hover:bg-red-500/10 transition-colors"
                >
                  Delete
                </button>
              {/if}
            {/if}
          </div>
        </div>
      </div>
    {/if}
  </div>
</div>
