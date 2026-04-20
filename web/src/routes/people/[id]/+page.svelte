<script>
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  import { api } from '$lib/api.js';
  import PersonAvatar from '$lib/components/PersonAvatar.svelte';
  import MeetingCard from '$lib/components/MeetingCard.svelte';
  import ActionItemRow from '$lib/components/ActionItemRow.svelte';
  import Skeleton from '$lib/components/Skeleton.svelte';
  import ConfirmModal from '$lib/components/ConfirmModal.svelte';
  import { addToast } from '$lib/stores/toasts.js';

  let person = $state(null);
  let meetings = $state([]);
  let loading = $state(true);
  let editing = $state(false);
  let editName = $state('');
  let editEmail = $state('');
  let saving = $state(false);
  let showDeleteModal = $state(false);
  let showMergeModal = $state(false);
  let allPeople = $state([]);
  let mergeTargetId = $state('');
  let mergeRenameActions = $state(true);
  let merging = $state(false);

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
      editName = person?.name || '';
      editEmail = person?.email || '';
    } catch (e) {
      console.error('Failed to load person:', e);
      addToast('Failed to load person', 'error');
    } finally {
      loading = false;
    }
  }

  async function savePerson() {
    if (!editName.trim()) {
      addToast('Name cannot be empty', 'error');
      return;
    }
    saving = true;
    try {
      const updated = await api.updatePerson(id, {
        name: editName.trim(),
        email: editEmail.trim() || null,
      });
      person = updated;
      editing = false;
      addToast('Person updated', 'success');
    } catch (e) {
      addToast(`Failed to update: ${e.message}`, 'error');
    } finally {
      saving = false;
    }
  }

  async function handleDelete() {
    try {
      await api.deletePerson(id);
      addToast('Person deleted', 'success');
      goto('/people');
    } catch (e) {
      addToast(`Failed to delete: ${e.message}`, 'error');
    }
  }

  async function openMergeModal() {
    showMergeModal = true;
    mergeTargetId = '';
    try {
      const data = await api.getPeople();
      const items = data.items || data || [];
      // Exclude the current person from the target list
      allPeople = items
        .map((p) => ({ ...p, id: p.id || p.person_id }))
        .filter((p) => p.id !== id)
        .sort((a, b) => (a.name || '').localeCompare(b.name || ''));
    } catch (e) {
      addToast('Failed to load people list', 'error');
    }
  }

  async function performMerge() {
    if (!mergeTargetId) {
      addToast('Select a target person', 'error');
      return;
    }
    merging = true;
    try {
      const result = await api.mergePerson(id, mergeTargetId, mergeRenameActions);
      addToast(
        `Merged into ${result.target_name}. ${result.renamed_action_items} action(s) and ${result.renamed_decisions} decision(s) updated.`,
        'success'
      );
      goto(`/people/${mergeTargetId}`);
    } catch (e) {
      addToast(`Merge failed: ${e.message}`, 'error');
    } finally {
      merging = false;
      showMergeModal = false;
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
    <div class="flex items-start justify-between gap-4 mb-8">
      <div class="flex items-center gap-4 flex-1 min-w-0">
        <PersonAvatar name={person.name || person.email || '?'} size="lg" />
        <div class="flex-1 min-w-0">
          {#if editing}
            <div class="space-y-2">
              <input
                type="text"
                bind:value={editName}
                placeholder="Name"
                class="w-full px-2 py-1 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded text-lg font-bold text-[var(--text-primary)]
                       focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
              />
              <input
                type="email"
                bind:value={editEmail}
                placeholder="Email (optional)"
                class="w-full px-2 py-1 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded text-sm text-[var(--text-secondary)]
                       focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
              />
            </div>
          {:else}
            <h1 class="text-2xl font-bold text-[var(--text-primary)] truncate">{person.name || 'Unknown'}</h1>
            {#if person.email}
              <p class="text-sm text-[var(--text-secondary)] truncate">{person.email}</p>
            {/if}
          {/if}
        </div>
      </div>

      <div class="flex items-center gap-2 shrink-0">
        {#if editing}
          <button
            onclick={savePerson}
            disabled={saving}
            class="px-3 py-1.5 bg-[var(--accent)] text-white text-sm font-medium rounded-lg
                   hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            {saving ? 'Saving...' : 'Save'}
          </button>
          <button
            onclick={() => { editing = false; editName = person.name || ''; editEmail = person.email || ''; }}
            disabled={saving}
            class="px-3 py-1.5 text-sm text-[var(--text-secondary)] border border-[var(--border-subtle)] rounded-lg
                   hover:bg-[var(--bg-surface-hover)] transition-colors"
          >
            Cancel
          </button>
        {:else}
          <a
            href={`/brief?person=${id}`}
            class="px-3 py-1.5 text-sm text-white bg-[var(--accent)] rounded-lg
                   hover:opacity-90 transition-opacity"
            title="Open the pre-meeting briefing for this person"
          >
            Start a briefing →
          </a>
          <button
            onclick={() => { editing = true; }}
            class="px-3 py-1.5 text-sm text-[var(--text-secondary)] border border-[var(--border-subtle)] rounded-lg
                   hover:bg-[var(--bg-surface-hover)] hover:text-[var(--text-primary)] transition-colors"
            title="Edit name/email"
          >
            ✎ Edit
          </button>
          <button
            onclick={openMergeModal}
            class="px-3 py-1.5 text-sm text-[var(--text-secondary)] border border-[var(--border-subtle)] rounded-lg
                   hover:bg-[var(--bg-surface-hover)] hover:text-[var(--text-primary)] transition-colors"
            title="Merge this person into another"
          >
            Merge…
          </button>
          <button
            onclick={() => { showDeleteModal = true; }}
            class="px-3 py-1.5 text-sm text-[var(--danger)] border border-[var(--danger)] border-opacity-30 rounded-lg
                   hover:bg-[var(--danger)] hover:bg-opacity-10 transition-colors"
          >
            Delete
          </button>
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
        {#each meetings as meeting (meeting.id || meeting.meeting_id)}
          <MeetingCard meeting={{ ...meeting, id: meeting.id || meeting.meeting_id, type: meeting.type || meeting.meeting_type }} view="list" />
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

<!-- Merge modal -->
{#if showMergeModal}
  <div
    class="fixed inset-0 bg-black/50 flex items-center justify-center z-50"
    onclick={(e) => { if (e.target === e.currentTarget) showMergeModal = false; }}
    role="dialog"
    aria-modal="true"
  >
    <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl max-w-md w-full mx-4 p-6">
      <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-1">
        Merge into another person
      </h2>
      <p class="text-sm text-[var(--text-muted)] mb-4">
        All meetings <strong>{person?.name}</strong> attended will be reassigned. The selected person keeps their name.
      </p>

      <div class="mb-4">
        <label for="merge-target" class="block text-xs font-medium text-[var(--text-secondary)] mb-1.5 uppercase tracking-wider">Target person</label>
        <select
          id="merge-target"
          bind:value={mergeTargetId}
          class="w-full px-3 py-2 bg-[var(--bg-surface-hover)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                 focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
        >
          <option value="">Select a person…</option>
          {#each allPeople as p}
            <option value={p.id}>
              {p.name}{p.email ? ` (${p.email})` : ''}{p.meeting_count ? ` — ${p.meeting_count} meetings` : ''}
            </option>
          {/each}
        </select>
      </div>

      <label class="flex items-start gap-2 mb-5 cursor-pointer">
        <input
          type="checkbox"
          bind:checked={mergeRenameActions}
          class="mt-0.5 w-4 h-4 rounded border-[var(--border-subtle)] text-[var(--accent)] focus:ring-[var(--accent)]"
        />
        <div class="text-xs">
          <span class="text-[var(--text-primary)]">Rename owner/maker in action items and decisions</span>
          <p class="text-[var(--text-muted)] mt-0.5">
            If checked, "{person?.name}" will be replaced with the target's name across all historical records. Uncheck to keep the old name for historical accuracy.
          </p>
        </div>
      </label>

      <div class="flex items-center justify-end gap-2">
        <button
          onclick={() => (showMergeModal = false)}
          disabled={merging}
          class="px-3 py-1.5 text-sm text-[var(--text-secondary)] border border-[var(--border-subtle)] rounded-lg
                 hover:bg-[var(--bg-surface-hover)] transition-colors"
        >
          Cancel
        </button>
        <button
          onclick={performMerge}
          disabled={merging || !mergeTargetId}
          class="px-3 py-1.5 text-sm font-medium text-white bg-[var(--accent)] rounded-lg
                 hover:opacity-90 disabled:opacity-50 transition-opacity"
        >
          {merging ? 'Merging…' : 'Merge'}
        </button>
      </div>
    </div>
  </div>
{/if}

<ConfirmModal
  bind:open={showDeleteModal}
  title="Delete Person"
  message="This will remove {person?.name || 'this person'} from all meeting attendee lists. Historical attributions in action items and decisions will stay. Continue?"
  confirmLabel="Delete"
  danger={true}
  onConfirm={handleDelete}
/>
