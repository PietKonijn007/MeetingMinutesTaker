<script>
  import { onMount, onDestroy } from 'svelte';
  import { api } from '$lib/api.js';
  import { addToast } from '$lib/stores/toasts.js';
  import { MEETING_TYPE_GROUPS } from '$lib/meetingTypes.js';

  let audioDevices = $state([]);
  let languages = $state([]);
  let selectedDevice = $state('');
  let selectedLanguage = $state('auto');
  let startingRecording = $state(false);
  let stoppingRecording = $state(false);

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

  // WebSocket connection for real-time push updates
  let ws = null;
  let wsReconnectTimer = null;

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
        if (data.recording && !startingRecording && !stoppingRecording) {
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
      addToast('Recording stopped. Processing in background...', 'info');
    } catch (e) {
      addToast(`Failed to stop recording: ${e.message}`, 'error');
    } finally {
      stoppingRecording = false;
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

<div class="max-w-xl mx-auto">
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
      <div class="w-full max-w-2xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-4 mb-6">
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
          <label for="meeting-notes" class="block text-xs font-medium text-[var(--text-secondary)] mb-1">
            Your notes
          </label>
          <textarea
            id="meeting-notes"
            bind:value={meetingNotes}
            placeholder="Jot things down as the meeting happens...&#10;&#10;- Key point discussed&#10;- Action for Bob&#10;- Decision: go with option A"
            rows="8"
            class="w-full px-3 py-2 bg-[var(--bg-primary)] border border-[var(--border-subtle)] rounded-lg
                   text-[var(--text-primary)] text-sm font-mono leading-relaxed
                   focus:outline-none focus:ring-2 focus:ring-[var(--accent)]
                   focus:border-transparent placeholder-[var(--text-muted)] resize-y"
          ></textarea>
          <p class="text-xs text-[var(--text-muted)] mt-1">
            We'll blend your notes into the final minutes so nothing important gets missed.
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

      <!-- Stop button -->
      <div class="flex items-center gap-4">
        <button
          onclick={stopRecording}
          disabled={stoppingRecording}
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
