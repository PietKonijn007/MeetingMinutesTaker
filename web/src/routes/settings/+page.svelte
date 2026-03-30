<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api.js';
  import { theme } from '$lib/stores/theme.js';
  import { addToast } from '$lib/stores/toasts.js';
  import Skeleton from '$lib/components/Skeleton.svelte';

  let config = $state(null);
  let loading = $state(true);
  let saving = $state(false);
  let audioDevices = $state([]);

  // Local editable state — maps to AppConfig fields
  let recording_device = $state('auto');
  let recording_sample_rate = $state(16000);
  let recording_silence_minutes = $state(5);
  let transcription_model = $state('medium');
  let transcription_language = $state('auto');
  let diarization_enabled = $state(true);
  let llm_provider = $state('anthropic');
  let llm_model = $state('claude-sonnet-4-6-20250514');
  let llm_temperature = $state(0.2);
  let llm_max_tokens = $state(4096);
  let pipeline_mode = $state('automatic');
  let storage_db_path = $state('db/meetings.db');
  let storage_data_dir = $state('~/MeetingMinutesTaker/data');

  async function loadConfig() {
    loading = true;
    try {
      const [cfg, devices] = await Promise.allSettled([
        api.getConfig(),
        api.getAudioDevices()
      ]);

      if (cfg.status === 'fulfilled') {
        config = cfg.value;
        // Map from AppConfig structure: config.config.{section}
        const c = config.config || config || {};
        const r = c.recording || {};
        const t = c.transcription || {};
        const d = c.diarization || {};
        const g = c.generation || {};
        const llm = g.llm || {};
        const p = c.pipeline || {};
        const st = c.storage || {};

        recording_device = r.audio_device || 'auto';
        recording_sample_rate = r.sample_rate || 16000;
        recording_silence_minutes = r.auto_stop_silence_minutes || 5;
        transcription_model = t.whisper_model || 'medium';
        transcription_language = t.language || 'auto';
        diarization_enabled = d.enabled !== false;
        llm_provider = llm.primary_provider || 'anthropic';
        llm_model = llm.model || 'claude-sonnet-4-6-20250514';
        llm_temperature = llm.temperature ?? 0.2;
        llm_max_tokens = llm.max_output_tokens || 4096;
        pipeline_mode = p.mode || 'automatic';
        storage_db_path = st.sqlite_path || 'db/meetings.db';
        storage_data_dir = c.data_dir || '~/MeetingMinutesTaker/data';
      }

      if (devices.status === 'fulfilled') {
        audioDevices = devices.value.devices || devices.value || [];
      }
    } catch (e) {
      console.error('Failed to load config:', e);
    } finally {
      loading = false;
    }
  }

  async function saveConfig() {
    saving = true;
    try {
      await api.updateConfig({
        data_dir: storage_data_dir,
        recording: {
          audio_device: recording_device,
          sample_rate: recording_sample_rate,
          auto_stop_silence_minutes: recording_silence_minutes
        },
        transcription: {
          whisper_model: transcription_model,
          language: transcription_language
        },
        diarization: {
          enabled: diarization_enabled
        },
        generation: {
          llm: {
            primary_provider: llm_provider,
            model: llm_model,
            temperature: llm_temperature,
            max_output_tokens: llm_max_tokens
          }
        },
        pipeline: {
          mode: pipeline_mode
        },
        storage: {
          sqlite_path: storage_db_path
        }
      });
      addToast('Settings saved', 'success');
    } catch (e) {
      addToast(`Failed to save settings: ${e.message}`, 'error');
    } finally {
      saving = false;
    }
  }

  onMount(loadConfig);
</script>

<div class="max-w-2xl mx-auto">
  <h1 class="text-2xl font-bold text-[var(--text-primary)] mb-8">Settings</h1>

  {#if loading}
    <div class="space-y-6">
      {#each Array(5) as _}
        <Skeleton type="text" lines={3} />
      {/each}
    </div>
  {:else}
    <div class="space-y-10">

      <!-- Recording -->
      <section>
        <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-1">Recording</h2>
        <p class="text-sm text-[var(--text-muted)] mb-4">Configure audio capture settings.</p>

        <div class="space-y-4">
          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Audio Device</label>
            <select
              bind:value={recording_device}
              class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            >
              <option value="">Default</option>
              {#each audioDevices as device}
                <option value={device.name || device}>{device.name || device}</option>
              {/each}
            </select>
          </div>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Sample Rate</label>
            <select
              bind:value={recording_sample_rate}
              class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            >
              <option value={16000}>16,000 Hz</option>
              <option value={44100}>44,100 Hz</option>
              <option value={48000}>48,000 Hz</option>
            </select>
          </div>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">
              Auto-stop after silence: {recording_silence_minutes} min
            </label>
            <input
              type="range"
              bind:value={recording_silence_minutes}
              min="1" max="30" step="1"
              class="w-full accent-[var(--accent)]"
            />
            <p class="text-xs text-[var(--text-muted)] mt-1">Stop recording after this many minutes of silence.</p>
          </div>
        </div>
      </section>

      <!-- Transcription -->
      <section>
        <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-1">Transcription</h2>
        <p class="text-sm text-[var(--text-muted)] mb-4">Configure Whisper speech-to-text.</p>

        <div class="space-y-4">
          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Whisper Model</label>
            <select
              bind:value={transcription_model}
              class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            >
              <option value="tiny">tiny (fastest, least accurate)</option>
              <option value="base">base (fast, okay accuracy)</option>
              <option value="small">small (balanced)</option>
              <option value="medium">medium (good accuracy)</option>
              <option value="large-v3">large-v3 (best accuracy, slowest)</option>
            </select>
          </div>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Language</label>
            <input
              type="text"
              bind:value={transcription_language}
              placeholder="en"
              class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            />
            <p class="text-xs text-[var(--text-muted)] mt-1">ISO 639-1 code (e.g., en, es, fr). Leave blank for auto-detect.</p>
          </div>
        </div>
      </section>

      <!-- Speaker ID -->
      <section>
        <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-1">Speaker Identification</h2>
        <p class="text-sm text-[var(--text-muted)] mb-4">Speaker diarization settings.</p>

        <label class="flex items-center gap-3 cursor-pointer">
          <input
            type="checkbox"
            bind:checked={diarization_enabled}
            class="w-4 h-4 rounded border-[var(--border-subtle)] text-[var(--accent)]
                   focus:ring-[var(--accent)] focus:ring-2"
          />
          <div>
            <span class="text-sm font-medium text-[var(--text-primary)]">Enable diarization</span>
            <p class="text-xs text-[var(--text-muted)]">Identify and label individual speakers in the transcript.</p>
          </div>
        </label>
      </section>

      <!-- Minutes Generation -->
      <section>
        <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-1">Minutes Generation</h2>
        <p class="text-sm text-[var(--text-muted)] mb-4">LLM configuration for generating meeting minutes.</p>

        <div class="space-y-4">
          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">LLM Provider</label>
            <select
              bind:value={llm_provider}
              class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            >
              <option value="ollama">Ollama (local)</option>
              <option value="openai">OpenAI</option>
              <option value="anthropic">Anthropic</option>
            </select>
          </div>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Model</label>
            <input
              type="text"
              bind:value={llm_model}
              placeholder="e.g., llama3, gpt-4o, claude-sonnet-4-20250514"
              class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            />
          </div>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">
              Temperature: {llm_temperature.toFixed(1)}
            </label>
            <input
              type="range"
              bind:value={llm_temperature}
              min="0" max="1" step="0.1"
              class="w-full accent-[var(--accent)]"
            />
          </div>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Max Tokens</label>
            <input
              type="number"
              bind:value={llm_max_tokens}
              min="256" max="32768" step="256"
              class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            />
          </div>
        </div>
      </section>

      <!-- Pipeline -->
      <section>
        <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-1">Pipeline</h2>
        <p class="text-sm text-[var(--text-muted)] mb-4">How recordings are processed.</p>

        <div>
          <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Mode</label>
          <select
            bind:value={pipeline_mode}
            class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                   focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
          >
            <option value="automatic">Automatic (process immediately after recording)</option>
            <option value="semi-automatic">Semi-automatic (ask before processing)</option>
            <option value="manual">Manual (process on demand only)</option>
          </select>
        </div>
      </section>

      <!-- Storage -->
      <section>
        <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-1">Storage</h2>
        <p class="text-sm text-[var(--text-muted)] mb-4">Data storage paths.</p>

        <div class="space-y-4">
          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Database Path</label>
            <input
              type="text"
              bind:value={storage_db_path}
              placeholder="data/meetings.db"
              class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)] font-mono
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            />
          </div>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Data Directory</label>
            <input
              type="text"
              bind:value={storage_data_dir}
              placeholder="data/"
              class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)] font-mono
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            />
          </div>
        </div>
      </section>

      <!-- Appearance -->
      <section>
        <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-1">Appearance</h2>
        <p class="text-sm text-[var(--text-muted)] mb-4">UI preferences.</p>

        <label class="flex items-center gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={$theme === 'dark'}
            onchange={() => theme.toggle()}
            class="w-4 h-4 rounded border-[var(--border-subtle)] text-[var(--accent)]
                   focus:ring-[var(--accent)] focus:ring-2"
          />
          <div>
            <span class="text-sm font-medium text-[var(--text-primary)]">Dark mode</span>
            <p class="text-xs text-[var(--text-muted)]">Use dark color scheme.</p>
          </div>
        </label>
      </section>

      <!-- Save button -->
      <div class="pt-6 border-t border-[var(--border-subtle)]">
        <button
          onclick={saveConfig}
          disabled={saving}
          class="px-6 py-2.5 bg-[var(--accent)] text-white rounded-lg text-sm font-medium
                 hover:bg-[var(--accent-hover)] disabled:opacity-50 transition-colors duration-150"
        >
          {saving ? 'Saving...' : 'Save Settings'}
        </button>
      </div>
    </div>
  {/if}
</div>
