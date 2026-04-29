<script>
  import { onMount, onDestroy, tick } from 'svelte';
  import { browser } from '$app/environment';
  import { marked } from 'marked';
  import DOMPurify from 'dompurify';
  import { api } from '$lib/api.js';
  import { addToast } from '$lib/stores/toasts.js';
  import { MEETING_TYPE_GROUPS } from '$lib/meetingTypes.js';
  import ConfirmModal from '$lib/components/ConfirmModal.svelte';

  // sessionStorage keys — survive tab navigation while the recording is in
  // flight, cleared once the user pushes Stop. Scoped to a single browser
  // session so they never leak across days.
  const FORM_KEY = 'record-form-v1';
  const VIEW_KEY = 'record-notes-view-v1';   // 'normal' | 'markdown'
  const SIZE_KEY = 'record-notes-size-v1';   // { width, height } in px

  let audioDevices = $state([]);
  let languages = $state([]);
  let selectedDevice = $state('');
  let selectedLanguage = $state('auto');
  let startingRecording = $state(false);
  let stoppingRecording = $state(false);
  let cancellingRecording = $state(false);
  // Confirm step before discarding the recording — destructive, no undo.
  let cancelConfirmOpen = $state(false);

  // DSK-1 preflight modal state
  let preflightModal = $state(null); // null | { tier, free_bytes, estimated_bytes, message, oldest: [] }
  let preflightCleanupSelection = $state(new Set());
  let preflightConfirmRed = $state(false);
  let preflightPending = $state(false);
  let plannedMinutes = $state(60);

  // Live note-taking during recording
  let meetingTitle = $state('');
  let speakerNames = $state('');
  // Tells diarization whether the names listed above cover *every* speaker
  // in the meeting. When true → pyannote gets num_speakers=len(names) (exact);
  // when false → pyannote gets min_speakers=len(names) (lower bound, infers
  // ceiling). Defaults checked for small meetings, unchecked for ≥6 named
  // speakers (where you usually can't enumerate everyone).
  let speakersComplete = $state(true);
  let meetingNotes = $state('');
  let customInstructions = $state('');
  // User-picked meeting type override. Empty string = auto-classify.
  let selectedMeetingType = $state('');
  let levelHistory = $state(new Array(24).fill(0));
  let refreshingDevices = $state(false);
  let devicePollTimer = $state(null);

  // Recording state — pushed via WebSocket
  let recState = $state('idle');
  let recElapsed = $state(0);
  let recLevel = $state(0);
  let recMeetingId = $state(null);

  // Active pipeline jobs — pushed via WebSocket
  let activePipelines = $state([]);

  // Notes editor view — 'normal' shows a live rendered markdown preview, 'markdown' shows just the raw textarea.
  let notesView = $state('normal');

  // Don't write to (session|local)Storage until after onMount has had a chance
  // to restore from it. Without this gate the first $effect run — which
  // happens with empty initial $state values — would overwrite a real
  // saved form with an empty one.
  let hydrated = $state(false);

  // User-resizable textarea: persist the box dimensions across navigation.
  // Only updated on pointerup after a drag changes size — avoids capturing
  // the pre-layout intrinsic width of a `cols=20` textarea on first paint.
  let notesSize = $state({ width: null, height: null });
  let notesEditorEl = $state(null);
  let notesPointerDownSize = null;

  // WebSocket connection for real-time push updates
  let ws = null;
  let wsReconnectTimer = null;

  // ───────────────────────── persistence helpers ─────────────────────────
  // We intentionally use sessionStorage (not localStorage) for the form
  // fields so they vanish when the browser session ends — they are tied to
  // an in-flight recording, not the user account. notesView and notesSize
  // are user preferences, so those go in localStorage.

  function saveForm() {
    if (!browser) return;
    try {
      sessionStorage.setItem(FORM_KEY, JSON.stringify({
        meetingTitle,
        speakerNames,
        speakersComplete,
        meetingNotes,
        customInstructions,
        selectedMeetingType,
      }));
    } catch {}
  }

  function clearForm() {
    if (!browser) return;
    try { sessionStorage.removeItem(FORM_KEY); } catch {}
  }

  function restoreForm() {
    if (!browser) return;
    try {
      const raw = sessionStorage.getItem(FORM_KEY);
      if (!raw) return;
      const data = JSON.parse(raw);
      if (typeof data.meetingTitle === 'string') meetingTitle = data.meetingTitle;
      if (typeof data.speakerNames === 'string') speakerNames = data.speakerNames;
      if (typeof data.speakersComplete === 'boolean') speakersComplete = data.speakersComplete;
      if (typeof data.meetingNotes === 'string') meetingNotes = data.meetingNotes;
      if (typeof data.customInstructions === 'string') customInstructions = data.customInstructions;
      if (typeof data.selectedMeetingType === 'string') selectedMeetingType = data.selectedMeetingType;
    } catch {}
  }

  function restorePrefs() {
    if (!browser) return;
    try {
      const v = localStorage.getItem(VIEW_KEY);
      if (v === 'normal' || v === 'markdown') notesView = v;
    } catch {}
    try {
      const s = localStorage.getItem(SIZE_KEY);
      if (s) {
        const obj = JSON.parse(s);
        if (obj && typeof obj === 'object') {
          notesSize = {
            width: typeof obj.width === 'number' ? obj.width : null,
            height: typeof obj.height === 'number' ? obj.height : null,
          };
        }
      }
    } catch {}
  }

  function savePrefs() {
    if (!browser) return;
    try { localStorage.setItem(VIEW_KEY, notesView); } catch {}
    try { localStorage.setItem(SIZE_KEY, JSON.stringify(notesSize)); } catch {}
  }

  // Sanitised, rendered HTML for the live preview. Re-derives every time
  // meetingNotes changes — cheap because marked is fast on small inputs.
  let notesRenderedHtml = $derived.by(() => {
    if (!meetingNotes) return '';
    const raw = marked.parse(meetingNotes, { breaks: true, gfm: true });
    return browser ? DOMPurify.sanitize(raw) : raw;
  });

  // Sync form changes → sessionStorage. Gated on `hydrated` so the first
  // $effect run (with empty $state values) doesn't clobber a saved form.
  $effect(() => {
    void meetingTitle; void speakerNames; void speakersComplete;
    void meetingNotes; void customInstructions; void selectedMeetingType;
    if (hydrated) saveForm();
  });

  $effect(() => {
    void notesView; void notesSize;
    if (hydrated) savePrefs();
  });

  async function connectWebSocket() {
    if (typeof window === 'undefined') return;
    if (ws && ws.readyState <= 1) return; // already open or connecting

    let token;
    try {
      const res = await fetch('/api/security/ws-token', { method: 'POST' });
      if (!res.ok) throw new Error(`ws-token HTTP ${res.status}`);
      token = (await res.json()).token;
    } catch (e) {
      wsReconnectTimer = setTimeout(connectWebSocket, 3000);
      return;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const url = `${protocol}//${host}/ws/recording?token=${encodeURIComponent(token)}`;

    try {
      ws = new WebSocket(url);
    } catch (e) {
      wsReconnectTimer = setTimeout(connectWebSocket, 3000);
      return;
    }

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        // Update recording state (don't override during transitions)
        if (data.recording && !startingRecording && !stoppingRecording && !cancellingRecording) {
          recState = data.recording.state || 'idle';
          recElapsed = data.recording.elapsed_seconds || 0;
          recLevel = data.recording.audio_level || 0;
          recMeetingId = data.recording.meeting_id || null;

          if (recState === 'recording' && recLevel != null) {
            levelHistory = [...levelHistory.slice(1), recLevel];
          }
        }

        // Update pipeline jobs
        if (data.pipelines) {
          activePipelines = data.pipelines;
        }
      } catch {
        // ignore parse errors
      }
    };

    ws.onclose = () => {
      ws = null;
      // Reconnect after 2 seconds
      wsReconnectTimer = setTimeout(connectWebSocket, 2000);
    };

    ws.onerror = () => {
      if (ws) ws.close();
    };
  }

  function disconnectWebSocket() {
    if (wsReconnectTimer) {
      clearTimeout(wsReconnectTimer);
      wsReconnectTimer = null;
    }
    if (ws) {
      ws.close();
      ws = null;
    }
  }

  function formatElapsed(sec) {
    if (sec == null || sec === 0) return '00:00';
    const totalSec = Math.floor(sec);
    const m = Math.floor(totalSec / 60);
    const s = totalSec % 60;
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  }

  function formatBytes(bytes) {
    if (!bytes && bytes !== 0) return '—';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let n = bytes;
    let i = 0;
    while (n >= 1024 && i < units.length - 1) {
      n /= 1024;
      i++;
    }
    return `${n.toFixed(n >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
  }

  async function runPreflight() {
    try {
      const result = await api.recordingPreflight(plannedMinutes);
      if (result.tier === 'green') {
        return { ok: true };
      }
      // For non-green tiers, load oldest-audio so the modal can offer cleanup.
      let oldest = [];
      try {
        const resp = await api.getOldestAudio(20);
        oldest = resp.files || [];
      } catch {
        oldest = [];
      }
      preflightModal = { ...result, oldest };
      preflightCleanupSelection = new Set();
      preflightConfirmRed = false;
      return { ok: false };
    } catch (e) {
      // Preflight failure shouldn't block the user — record is more important
      // than the warning.
      console.warn('Preflight failed', e);
      return { ok: true };
    }
  }

  async function startRecordingActual() {
    const body = {};
    if (selectedDevice) body.audio_device = selectedDevice;
    if (selectedLanguage && selectedLanguage !== 'auto') body.language = selectedLanguage;
    if (plannedMinutes) body.planned_minutes = plannedMinutes;
    await api.startRecording(body);
    recState = 'recording';
    recElapsed = 0;
    levelHistory = new Array(24).fill(0);
    addToast('Recording started', 'success');
  }

  async function startRecording() {
    startingRecording = true;
    try {
      const pre = await runPreflight();
      if (!pre.ok) {
        // Modal handles the start confirmation.
        startingRecording = false;
        return;
      }
      await startRecordingActual();
    } catch (e) {
      addToast(`Failed to start recording: ${e.message}`, 'error');
    } finally {
      startingRecording = false;
    }
  }

  async function startAnyway() {
    if (preflightModal?.tier === 'red' && !preflightConfirmRed) return;
    startingRecording = true;
    try {
      await startRecordingActual();
      preflightModal = null;
    } catch (e) {
      addToast(`Failed to start recording: ${e.message}`, 'error');
    } finally {
      startingRecording = false;
    }
  }

  function togglePreflightSelection(mid) {
    const next = new Set(preflightCleanupSelection);
    if (next.has(mid)) next.delete(mid);
    else next.add(mid);
    preflightCleanupSelection = next;
  }

  async function deleteSelected() {
    if (preflightCleanupSelection.size === 0) return;
    preflightPending = true;
    try {
      const ids = Array.from(preflightCleanupSelection);
      const resp = await api.deleteAudioBulk(ids);
      addToast(`Deleted ${resp.deleted.length} audio file(s)`, 'success');
      // Re-run preflight to refresh tier + list.
      const result = await api.recordingPreflight(plannedMinutes);
      let oldest = [];
      try {
        const r = await api.getOldestAudio(20);
        oldest = r.files || [];
      } catch {}
      if (result.tier === 'green') {
        preflightModal = null;
      } else {
        preflightModal = { ...result, oldest };
      }
      preflightCleanupSelection = new Set();
    } catch (e) {
      addToast(`Bulk delete failed: ${e.message}`, 'error');
    } finally {
      preflightPending = false;
    }
  }

  function dismissPreflight() {
    preflightModal = null;
  }

  async function stopRecording() {
    stoppingRecording = true;
    try {
      const body = {};
      if (meetingTitle.trim()) body.title = meetingTitle.trim();
      if (meetingNotes.trim()) body.notes = meetingNotes.trim();
      if (speakerNames.trim()) {
        body.speakers = speakerNames.trim();
        // Only meaningful when the user actually typed names — without
        // names there's no count to anchor pyannote to.
        body.speakers_complete = speakersComplete;
      }
      if (customInstructions.trim()) body.instructions = customInstructions.trim();
      if (selectedMeetingType) body.meeting_type = selectedMeetingType;
      await api.stopRecording(body);
      // Recording slot is now free — reset local state immediately
      recState = 'idle';
      recMeetingId = null;
      recElapsed = 0;
      levelHistory = new Array(24).fill(0);
      // Clear notes for next recording
      meetingTitle = '';
      meetingNotes = '';
      speakerNames = '';
      speakersComplete = true;
      customInstructions = '';
      selectedMeetingType = '';
      clearForm();
      addToast('Recording stopped. Processing in background...', 'info');
    } catch (e) {
      addToast(`Failed to stop recording: ${e.message}`, 'error');
    } finally {
      stoppingRecording = false;
    }
  }

  async function cancelRecording() {
    cancellingRecording = true;
    try {
      await api.cancelRecording();
      // Same local cleanup as stop — but no pipeline job, no meeting record.
      recState = 'idle';
      recMeetingId = null;
      recElapsed = 0;
      levelHistory = new Array(24).fill(0);
      meetingTitle = '';
      meetingNotes = '';
      speakerNames = '';
      speakersComplete = true;
      customInstructions = '';
      selectedMeetingType = '';
      clearForm();
      addToast('Recording cancelled. Audio discarded.', 'info');
    } catch (e) {
      addToast(`Failed to cancel recording: ${e.message}`, 'error');
    } finally {
      cancellingRecording = false;
      cancelConfirmOpen = false;
    }
  }

  let inputDevices = $derived(audioDevices.filter(d => d.max_input_channels > 0));
  let outputDevices = $derived(audioDevices.filter(d => d.max_output_channels > 0));

  let autoDetectedDevice = $state(null);

  // Default (no arg / refresh=false): a cheap enumeration — safe to call on a
  // 3-second interval during an active recording. The Refresh button passes
  // { refresh: true } to force PortAudio to re-scan for newly-plugged devices.
  async function loadDevices({ refresh = false } = {}) {
    try {
      const data = await api.getAudioDevices({ refresh });
      const newDevices = data.devices || data || [];

      // Check if device list actually changed
      const oldNames = audioDevices.map(d => d.name).sort().join(',');
      const newNames = newDevices.map(d => d.name).sort().join(',');
      if (oldNames !== newNames) {
        audioDevices = newDevices;
      }

      // Auto-detect best device on first load
      if (!selectedDevice) {
        try {
          const autoResult = await api.autoDetectDevice();
          if (autoResult.device) {
            selectedDevice = autoResult.device;
            autoDetectedDevice = autoResult.device;
          }
        } catch {
          // Fallback to first input device
          const inputs = newDevices.filter(d => d.max_input_channels > 0);
          if (inputs.length > 0) {
            selectedDevice = inputs[0].name;
          }
        }
      }
    } catch (e) {
      // Devices might not be available
    }
  }

  async function refreshDevices() {
    refreshingDevices = true;
    await loadDevices({ refresh: true });
    refreshingDevices = false;
  }

  async function loadLanguages() {
    try {
      languages = await api.getLanguages();
    } catch (e) {
      languages = [{ code: 'auto', name: 'Auto-detect' }, { code: 'en', name: 'English' }];
    }
  }

  onMount(() => {
    // Restore in-flight form fields (sessionStorage) and stable preferences
    // (localStorage) before anything else binds to them. Flip `hydrated`
    // last so the persistence $effects only run for *real* user changes.
    restoreForm();
    restorePrefs();
    hydrated = true;

    loadDevices();
    loadLanguages();

    // Connect WebSocket for real-time push updates
    connectWebSocket();

    // Poll for new devices every 3 seconds only when idle
    devicePollTimer = setInterval(() => {
      if (recState === 'idle') loadDevices();
    }, 3000);

    return () => {
      if (devicePollTimer) clearInterval(devicePollTimer);
      disconnectWebSocket();
    };
  });

  // Capture the textarea's box on pointerdown; persist on pointerup only if
  // the box actually changed (i.e. the user dragged the resize corner). A
  // plain click for typing leaves dimensions unchanged → nothing saved.
  function notesEditorPointerDown(e) {
    if (!e.currentTarget) return;
    const r = e.currentTarget.getBoundingClientRect();
    notesPointerDownSize = { width: Math.round(r.width), height: Math.round(r.height) };
  }
  function notesEditorPointerUp(e) {
    if (!notesPointerDownSize || !e.currentTarget) return;
    const r = e.currentTarget.getBoundingClientRect();
    const w = Math.round(r.width);
    const h = Math.round(r.height);
    if (w !== notesPointerDownSize.width || h !== notesPointerDownSize.height) {
      notesSize = { width: w, height: h };
    }
    notesPointerDownSize = null;
  }

  /**
   * Determine step completion status for a pipeline job.
   */
  function stepStatus(job, step) {
    const stepOrder = ['transcribing', 'generating', 'indexing'];
    const stepIdx = stepOrder.indexOf(step);
    const jobIdx = stepOrder.indexOf(job.step);

    if (job.step === 'done' || job.step === 'error') {
      // If done, all steps are done. If error, mark steps up to where it failed.
      return job.step === 'done' ? 'done' : (jobIdx > stepIdx ? 'done' : jobIdx === stepIdx ? 'error' : 'pending');
    }
    if (jobIdx > stepIdx) return 'done';
    if (jobIdx === stepIdx) return 'active';
    return 'pending';
  }
</script>

<div class="{recState === 'recording' ? 'max-w-6xl' : 'max-w-xl'} mx-auto transition-[max-width] duration-200">
  <h1 class="text-2xl font-bold text-[var(--text-primary)] mb-8 text-center">Record</h1>

  {#if preflightModal}
    {@const tier = preflightModal.tier}
    {@const tierColor = tier === 'red' ? 'red' : tier === 'orange' ? 'orange' : 'yellow'}
    <div class="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
      <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl shadow-xl max-w-2xl w-full max-h-[90vh] overflow-y-auto">
        <div class="p-5 border-b border-[var(--border-subtle)]">
          <h2 class="text-lg font-semibold text-{tierColor}-500">Disk space warning — {tier.toUpperCase()}</h2>
          <p class="mt-1 text-sm text-[var(--text-secondary)]">{preflightModal.message}</p>
          <p class="mt-2 text-xs text-[var(--text-muted)]">
            Free: <span class="font-mono">{formatBytes(preflightModal.free_bytes)}</span>
            · Estimated need: <span class="font-mono">{formatBytes(preflightModal.estimated_bytes)}</span>
            · Planned: {preflightModal.planned_minutes} min
          </p>
        </div>

        <div class="p-5">
          <h3 class="text-sm font-medium text-[var(--text-secondary)] mb-2">
            Oldest audio files you can safely delete
          </h3>
          {#if preflightModal.oldest.length === 0}
            <p class="text-xs text-[var(--text-muted)]">No eligible files found (a meeting is only eligible when its pipeline has finished).</p>
          {:else}
            <div class="max-h-64 overflow-y-auto border border-[var(--border-subtle)] rounded-lg">
              <table class="w-full text-xs">
                <thead class="bg-[var(--bg-primary)] sticky top-0">
                  <tr>
                    <th class="px-3 py-2 text-left w-8"></th>
                    <th class="px-3 py-2 text-left">Meeting</th>
                    <th class="px-3 py-2 text-right">Size</th>
                  </tr>
                </thead>
                <tbody>
                  {#each preflightModal.oldest as file}
                    <tr class="border-t border-[var(--border-subtle)]">
                      <td class="px-3 py-2">
                        <input
                          type="checkbox"
                          checked={preflightCleanupSelection.has(file.meeting_id)}
                          onchange={() => togglePreflightSelection(file.meeting_id)}
                        />
                      </td>
                      <td class="px-3 py-2 font-mono truncate max-w-xs">{file.meeting_id.slice(0, 12)}…</td>
                      <td class="px-3 py-2 text-right font-mono">{formatBytes(file.size_bytes)}</td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            </div>
            <button
              onclick={deleteSelected}
              disabled={preflightPending || preflightCleanupSelection.size === 0}
              class="mt-3 px-4 py-2 text-xs bg-red-500 hover:bg-red-600 text-white rounded-lg disabled:opacity-50"
            >
              {preflightPending ? 'Deleting…' : `Delete selected (${preflightCleanupSelection.size})`}
            </button>
          {/if}
        </div>

        <div class="p-5 border-t border-[var(--border-subtle)] flex items-center justify-between gap-3">
          <button
            onclick={dismissPreflight}
            class="px-4 py-2 text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
          >
            Cancel
          </button>

          <div class="flex items-center gap-3">
            {#if tier === 'red'}
              <label class="text-xs text-[var(--text-secondary)] flex items-center gap-2">
                <input type="checkbox" bind:checked={preflightConfirmRed} />
                Yes, I understand the recording may fail
              </label>
            {/if}
            <button
              onclick={startAnyway}
              disabled={startingRecording || (tier === 'red' && !preflightConfirmRed)}
              class="px-4 py-2 text-sm text-white rounded-lg disabled:opacity-50 bg-{tierColor}-500 hover:bg-{tierColor}-600"
            >
              {startingRecording ? 'Starting…' : 'Start anyway'}
            </button>
          </div>
        </div>
      </div>
    </div>
  {/if}

  <!-- Idle state — always show record button when not recording -->
  {#if recState === 'idle'}
    <div class="flex flex-col items-center">
      <!-- Big record button -->
      <button
        onclick={startRecording}
        disabled={startingRecording}
        class="w-32 h-32 rounded-full bg-red-500 hover:bg-red-600 text-white
               flex items-center justify-center shadow-lg hover:shadow-xl
               transition-all duration-200 active:scale-95 disabled:opacity-50 mb-8"
        aria-label="Start recording"
      >
        {#if startingRecording}
          <svg class="w-10 h-10 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
          </svg>
        {:else}
          <svg class="w-12 h-12" fill="currentColor" viewBox="0 0 24 24">
            <circle cx="12" cy="12" r="8"/>
          </svg>
        {/if}
      </button>

      <p class="text-sm text-[var(--text-secondary)] mb-8">Click to start recording</p>

      <!-- Device and language selection -->
      <div class="w-full bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-5 space-y-5">
        <!-- Audio input device -->
        <div>
          <div class="flex items-center justify-between mb-2">
            <label for="audio-device" class="text-sm font-medium text-[var(--text-secondary)]">
              Audio Input Device
            </label>
            <button
              onclick={refreshDevices}
              disabled={refreshingDevices}
              class="text-xs text-[var(--accent)] hover:text-[var(--accent-hover)] transition-colors flex items-center gap-1 disabled:opacity-50"
              title="Refresh device list"
            >
              <svg class="w-3.5 h-3.5 {refreshingDevices ? 'animate-spin' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
              </svg>
              Refresh
            </button>
          </div>
          <select
            id="audio-device"
            bind:value={selectedDevice}
            class="w-full px-3 py-2.5 bg-[var(--bg-primary)] border border-[var(--border-subtle)] rounded-lg
                   text-[var(--text-primary)] text-sm focus:outline-none focus:ring-2 focus:ring-[var(--accent)]
                   focus:border-transparent transition-shadow"
          >
            {#each inputDevices as device}
              <option value={device.name}>
                {device.name} ({device.max_input_channels}ch input{device.max_output_channels > 0 ? ` + ${device.max_output_channels}ch output` : ''})
              </option>
            {/each}
            {#if inputDevices.length === 0}
              <option value="">No input devices detected</option>
            {/if}
          </select>
          <p class="mt-1.5 text-xs text-[var(--text-muted)]">
            {audioDevices.length} device{audioDevices.length !== 1 ? 's' : ''} detected ({inputDevices.length} with input)
            {#if autoDetectedDevice && selectedDevice === autoDetectedDevice}
              <span class="text-[var(--success)]"> — auto-detected</span>
            {/if}
          </p>
        </div>

        <!-- Language -->
        <div>
          <label for="language" class="block text-sm font-medium text-[var(--text-secondary)] mb-2">
            Transcription Language
          </label>
          <select
            id="language"
            bind:value={selectedLanguage}
            class="w-full px-3 py-2.5 bg-[var(--bg-primary)] border border-[var(--border-subtle)] rounded-lg
                   text-[var(--text-primary)] text-sm focus:outline-none focus:ring-2 focus:ring-[var(--accent)]
                   focus:border-transparent transition-shadow"
          >
            {#each languages as lang}
              <option value={lang.code}>{lang.name}</option>
            {/each}
          </select>
          <p class="mt-1.5 text-xs text-[var(--text-muted)]">
            Auto-detect works well for single-language meetings. Select a specific language for better accuracy.
          </p>
        </div>
      </div>
    </div>

  <!-- Recording state -->
  {:else if recState === 'recording'}
    <div class="flex flex-col items-center">
      <!-- Pulsing red dot + time -->
      <div class="flex items-center gap-3 mb-6">
        <span class="w-4 h-4 rounded-full bg-red-500 recording-pulse"></span>
        <span class="text-sm font-medium text-red-500">Recording</span>
      </div>

      <div class="text-5xl font-mono font-bold text-[var(--text-primary)] mb-8">
        {formatElapsed(recElapsed)}
      </div>

      <!-- Audio level visualization — real levels from mic -->
      <div class="flex items-end justify-center gap-1 h-16 mb-8">
        {#each levelHistory as level, i}
          {@const barHeight = Math.max(3, level * 60)}
          {@const isActive = level > 0.02}
          <div
            class="w-2 rounded-full transition-all duration-100"
            style="height: {barHeight}px; background-color: {isActive ? 'var(--accent)' : 'var(--border-subtle)'}; opacity: {isActive ? 0.5 + level * 0.5 : 0.3}"
          ></div>
        {/each}
      </div>

      <!-- Live note-taking area -->
      <div class="w-full bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-4 mb-6">
        <!-- Meeting title -->
        <div class="mb-3">
          <label for="meeting-title" class="block text-xs font-medium text-[var(--text-secondary)] mb-1">
            Title <span class="text-[var(--text-muted)] font-normal">(optional)</span>
          </label>
          <input
            id="meeting-title"
            bind:value={meetingTitle}
            placeholder="e.g., Q2 planning with marketing"
            class="w-full px-3 py-2 bg-[var(--bg-primary)] border border-[var(--border-subtle)] rounded-lg
                   text-[var(--text-primary)] text-sm focus:outline-none focus:ring-2 focus:ring-[var(--accent)]
                   focus:border-transparent placeholder-[var(--text-muted)]"
          />
          <p class="text-xs text-[var(--text-muted)] mt-1">
            Set a title yourself, or leave blank and we'll generate one from the transcript.
          </p>
        </div>

        <!-- Meeting type picker -->
        <div class="mb-3">
          <label for="meeting-type" class="block text-xs font-medium text-[var(--text-secondary)] mb-1">
            What kind of meeting is this?
          </label>
          <select
            id="meeting-type"
            bind:value={selectedMeetingType}
            class="w-full px-3 py-2 bg-[var(--bg-primary)] border border-[var(--border-subtle)] rounded-lg
                   text-[var(--text-primary)] text-sm focus:outline-none focus:ring-2 focus:ring-[var(--accent)]
                   focus:border-transparent"
          >
            <option value="">Figure it out for me</option>
            {#each MEETING_TYPE_GROUPS as group}
              <optgroup label={group.group}>
                {#each group.items as t}
                  <option value={t.value}>{t.label}</option>
                {/each}
              </optgroup>
            {/each}
          </select>
          <p class="text-xs text-[var(--text-muted)] mt-1">
            Picking the right kind gives you tailored minutes (different structure for a 1-on-1 vs. a board meeting). Not sure? Leave it on "Figure it out for me".
          </p>
        </div>

        <!-- Speaker names -->
        <div class="mb-3">
          <label for="speaker-names" class="block text-xs font-medium text-[var(--text-secondary)] mb-1">
            Who's in the meeting?
          </label>
          <input
            id="speaker-names"
            bind:value={speakerNames}
            oninput={() => {
              // Default-uncheck for large meetings: when you've typed 6+
              // names you usually can't be sure you've got everyone (someone
              // joins late, an exec hops in for 5 minutes, etc.). Toggle
              // back on if the user explicitly re-checks.
              const count = speakerNames.split(',').filter(s => s.trim()).length;
              speakersComplete = count > 0 && count <= 5;
            }}
            placeholder="Alice, Bob, Carol"
            class="w-full px-3 py-2 bg-[var(--bg-primary)] border border-[var(--border-subtle)] rounded-lg
                   text-[var(--text-primary)] text-sm focus:outline-none focus:ring-2 focus:ring-[var(--accent)]
                   focus:border-transparent placeholder-[var(--text-muted)]"
          />
          <p class="text-xs text-[var(--text-muted)] mt-1">
            Names separated by commas — helps us label who said what.
          </p>
          <!--
            Speaker-count hint for diarization. When checked: pass
            num_speakers=N to pyannote (exact match). Unchecked: pass
            min_speakers=N (lower bound; pyannote infers the ceiling).
            Disabled when no names are typed — there's nothing to anchor.
          -->
          {#if speakerNames.trim()}
            <label class="flex items-start gap-2 mt-2 cursor-pointer">
              <input
                type="checkbox"
                bind:checked={speakersComplete}
                class="mt-0.5 accent-[var(--accent)]"
              />
              <span class="text-xs text-[var(--text-secondary)]">
                I've named everyone who'll speak
                <span class="block text-[var(--text-muted)] mt-0.5">
                  {#if speakersComplete}
                    Diarization will look for exactly {speakerNames.split(',').filter(s => s.trim()).length} speaker(s).
                  {:else}
                    Diarization will look for at least {speakerNames.split(',').filter(s => s.trim()).length} and detect more if needed — pick this when extra people might join unannounced.
                  {/if}
                </span>
              </span>
            </label>
          {/if}
        </div>

        <!-- Meeting notes -->
        <div>
          <div class="flex items-center justify-between mb-1 gap-3">
            <label for="meeting-notes" class="text-xs font-medium text-[var(--text-secondary)]">
              Your notes
            </label>
            <!--
              View toggle. NORMAL pairs the editor with a live rendered preview
              so the user can see the markdown layout as they type. MARKDOWN
              hides the preview and gives the textarea the full row.
            -->
            <div role="tablist" class="inline-flex rounded-md border border-[var(--border-subtle)] bg-[var(--bg-primary)] p-0.5 text-xs">
              <button
                type="button"
                role="tab"
                aria-selected={notesView === 'normal'}
                onclick={() => notesView = 'normal'}
                class="px-2 py-1 rounded {notesView === 'normal' ? 'bg-[var(--accent)] text-white' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'}"
              >NORMAL</button>
              <button
                type="button"
                role="tab"
                aria-selected={notesView === 'markdown'}
                onclick={() => notesView = 'markdown'}
                class="px-2 py-1 rounded {notesView === 'markdown' ? 'bg-[var(--accent)] text-white' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'}"
              >MARKDOWN</button>
            </div>
          </div>

          <div class="grid {notesView === 'normal' ? 'grid-cols-1 md:grid-cols-2' : 'grid-cols-1'} gap-3">
            <!--
              Editor textarea. resize: both lets the user drag the corner to
              change both width and height; the ResizeObserver writes the
              new size to localStorage so it sticks.
            -->
            <textarea
              id="meeting-notes"
              bind:value={meetingNotes}
              bind:this={notesEditorEl}
              onpointerdown={notesEditorPointerDown}
              onpointerup={notesEditorPointerUp}
              placeholder="Jot things down as the meeting happens...&#10;&#10;# Heading&#10;- Key point discussed&#10;- Action for **Bob**&#10;- Decision: go with option A"
              rows="16"
              cols="40"
              style:width={notesSize.width ? `${notesSize.width}px` : null}
              style:height={notesSize.height ? `${notesSize.height}px` : null}
              style:max-width="100%"
              style:resize="both"
              class="w-full min-h-[16rem] px-3 py-2 bg-[var(--bg-primary)] border border-[var(--border-subtle)] rounded-lg
                     text-[var(--text-primary)] text-sm font-mono leading-relaxed
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]
                     focus:border-transparent placeholder-[var(--text-muted)]"
            ></textarea>

            {#if notesView === 'normal'}
              <!--
                Live rendered preview. Mirrors the editor height (min-h matches)
                so the two panes feel paired. We render via marked + DOMPurify
                inline rather than reaching for MarkdownRenderer because we
                need this derived value to stay reactive to meetingNotes.
              -->
              <div
                aria-label="Rendered preview"
                class="notes-preview w-full min-h-[16rem] px-4 py-3 bg-[var(--bg-primary)] border border-[var(--border-subtle)] rounded-lg
                       text-[var(--text-primary)] text-sm leading-relaxed overflow-auto
                       prose prose-sm max-w-none
                       prose-headings:text-[var(--text-primary)] prose-headings:font-semibold
                       prose-p:text-[var(--text-primary)]
                       prose-strong:text-[var(--text-primary)]
                       prose-a:text-[var(--accent)]
                       prose-code:text-[var(--accent)] prose-code:bg-[var(--bg-surface-hover)] prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:text-xs
                       prose-li:text-[var(--text-primary)]
                       prose-ul:text-[var(--text-primary)]
                       prose-ol:text-[var(--text-primary)]
                       prose-blockquote:border-[var(--accent)] prose-blockquote:text-[var(--text-secondary)]"
              >
                {#if meetingNotes.trim()}
                  {@html notesRenderedHtml}
                {:else}
                  <p class="text-[var(--text-muted)] italic">Rendered preview appears here as you type…</p>
                {/if}
              </div>
            {/if}
          </div>

          <p class="text-xs text-[var(--text-muted)] mt-1">
            We'll blend your notes into the final minutes. Markdown supported — drag the textarea corner to resize.
          </p>
        </div>

        <!-- Custom LLM instructions -->
        <div class="mt-3 pt-3 border-t border-[var(--border-subtle)]">
          <label for="custom-instructions" class="block text-xs font-medium text-[var(--text-secondary)] mb-1">
            Anything specific to focus on? <span class="text-[var(--text-muted)] font-normal">(optional)</span>
          </label>
          <textarea
            id="custom-instructions"
            bind:value={customInstructions}
            placeholder="e.g., Focus on action items for the Q2 migration&#10;This is a training session — capture key concepts taught&#10;Pay special attention to customer feedback about the new pricing"
            rows="3"
            class="w-full px-3 py-2 bg-[var(--bg-primary)] border border-[var(--border-subtle)] rounded-lg
                   text-[var(--text-primary)] text-sm font-mono leading-relaxed
                   focus:outline-none focus:ring-2 focus:ring-[var(--accent)]
                   focus:border-transparent placeholder-[var(--text-muted)] resize-y"
          ></textarea>
          <p class="text-xs text-[var(--text-muted)] mt-1">
            Tell us what matters most — we'll lean the minutes that way.
          </p>
        </div>
      </div>

      <!-- Stop / Cancel buttons -->
      <div class="flex items-center gap-4">
        <button
          onclick={() => (cancelConfirmOpen = true)}
          disabled={stoppingRecording || cancellingRecording}
          class="px-5 py-3 bg-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)]
                 border border-[var(--border-subtle)] hover:border-[var(--text-secondary)] rounded-full
                 text-sm font-medium transition-colors duration-150 disabled:opacity-50
                 flex items-center gap-2"
          title="Discard the recording without processing"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
          </svg>
          {cancellingRecording ? 'Cancelling…' : 'Cancel Recording'}
        </button>
        <button
          onclick={stopRecording}
          disabled={stoppingRecording || cancellingRecording}
          class="px-8 py-3 bg-red-500 hover:bg-red-600 text-white rounded-full
                 text-sm font-medium shadow-lg transition-all duration-150 disabled:opacity-50
                 flex items-center gap-2"
        >
          <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
            <rect x="6" y="6" width="12" height="12" rx="2"/>
          </svg>
          {stoppingRecording ? 'Stopping...' : 'Stop Recording'}
        </button>
      </div>
    </div>
  {/if}

  <ConfirmModal
    bind:open={cancelConfirmOpen}
    title="Discard this recording?"
    message="The audio captured so far will be deleted and no minutes will be generated. This can't be undone."
    confirmLabel={cancellingRecording ? 'Cancelling…' : 'Yes, discard'}
    cancelLabel="Keep recording"
    danger={true}
    onConfirm={cancelRecording}
  />

  <!-- Active Pipelines — always shown below recording controls -->
  {#if activePipelines.length > 0}
    <div class="mt-8 w-full">
      <h3 class="text-sm font-medium text-[var(--text-secondary)] mb-3">Processing</h3>
      {#each activePipelines as job}
        <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-4 mb-3">
          <div class="flex items-center justify-between mb-2">
            <span class="text-sm font-medium text-[var(--text-primary)]">
              Meeting {job.meeting_id.slice(0, 8)}...
            </span>
            <div class="flex items-center gap-3">
              <span class="text-xs text-[var(--text-muted)]">
                {Math.round(job.elapsed_seconds)}s
              </span>
              {#if job.step === 'done'}
                <a
                  href="/meeting/{job.meeting_id}"
                  class="text-xs text-[var(--accent)] hover:underline"
                >
                  View
                </a>
              {/if}
            </div>
          </div>
          <!-- Step indicator -->
          <div class="flex items-center gap-2 text-xs">
            {#each ['transcribing', 'generating', 'indexing'] as step, i}
              {@const status = stepStatus(job, step)}
              <div class="flex items-center gap-1">
                {#if status === 'done'}
                  <span class="text-green-500">&#10003;</span>
                {:else if status === 'active'}
                  <span class="text-[var(--accent)] animate-pulse">&#9203;</span>
                {:else if status === 'error'}
                  <span class="text-red-500">&#10007;</span>
                {:else}
                  <span class="text-[var(--text-muted)]">&#9675;</span>
                {/if}
                <span class="{status === 'active' ? 'text-[var(--accent)] font-medium' : status === 'done' ? 'text-green-500' : status === 'error' ? 'text-red-500' : 'text-[var(--text-muted)]'}">
                  {step === 'transcribing' ? 'Transcribe' : step === 'generating' ? 'Generate' : 'Index'}
                </span>
              </div>
              {#if i < 2}
                <span class="text-[var(--text-muted)]">&rarr;</span>
              {/if}
            {/each}
            {#if job.step === 'done'}
              <span class="text-green-500 font-medium ml-2">&#10003; Done</span>
            {:else if job.step === 'error'}
              <span class="text-red-500 font-medium ml-2">&#10007; Error</span>
            {/if}
          </div>
          {#if job.step === 'error' && job.error}
            <p class="mt-2 text-xs text-red-500 truncate" title={job.error}>{job.error}</p>
          {/if}
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  /*
   * Scoped markdown styles for the live notes preview. Tailwind Preflight
   * resets headings/lists; the project doesn't ship @tailwindcss/typography,
   * so the `prose` classes attached to the preview pane are no-ops. We
   * restore just enough to make headings, bullets, code, and emphasis
   * visible — nothing fancy.
   */
  .notes-preview :global(h1),
  .notes-preview :global(h2),
  .notes-preview :global(h3),
  .notes-preview :global(h4) {
    font-weight: 600;
    color: var(--text-primary);
    line-height: 1.25;
    margin: 0.6em 0 0.3em;
  }
  .notes-preview :global(h1) { font-size: 1.4rem; }
  .notes-preview :global(h2) { font-size: 1.2rem; }
  .notes-preview :global(h3) { font-size: 1.05rem; }
  .notes-preview :global(h4) { font-size: 0.95rem; }
  .notes-preview :global(p) { margin: 0.4em 0; }
  .notes-preview :global(ul) {
    list-style: disc outside;
    padding-left: 1.4em;
    margin: 0.4em 0;
  }
  .notes-preview :global(ol) {
    list-style: decimal outside;
    padding-left: 1.4em;
    margin: 0.4em 0;
  }
  .notes-preview :global(li) { margin: 0.15em 0; }
  .notes-preview :global(li > ul),
  .notes-preview :global(li > ol) { margin: 0.15em 0; }
  .notes-preview :global(strong) { font-weight: 700; color: var(--text-primary); }
  .notes-preview :global(em) { font-style: italic; }
  .notes-preview :global(a) { color: var(--accent); text-decoration: underline; }
  .notes-preview :global(code) {
    background: var(--bg-surface-hover);
    color: var(--accent);
    padding: 0.1em 0.35em;
    border-radius: 0.25rem;
    font-size: 0.85em;
  }
  .notes-preview :global(pre) {
    background: var(--bg-surface-hover);
    border: 1px solid var(--border-subtle);
    border-radius: 0.5rem;
    padding: 0.6em 0.8em;
    margin: 0.5em 0;
    overflow-x: auto;
  }
  .notes-preview :global(pre code) {
    background: transparent;
    color: inherit;
    padding: 0;
    border-radius: 0;
    font-size: 0.85em;
  }
  .notes-preview :global(blockquote) {
    border-left: 3px solid var(--accent);
    color: var(--text-secondary);
    padding-left: 0.8em;
    margin: 0.5em 0;
  }
  .notes-preview :global(hr) {
    border: 0;
    border-top: 1px solid var(--border-subtle);
    margin: 0.8em 0;
  }
  .notes-preview :global(table) {
    border-collapse: collapse;
    margin: 0.5em 0;
  }
  .notes-preview :global(th),
  .notes-preview :global(td) {
    border: 1px solid var(--border-subtle);
    padding: 0.3em 0.6em;
  }
  .notes-preview :global(input[type="checkbox"]) {
    margin-right: 0.4em;
  }
</style>
