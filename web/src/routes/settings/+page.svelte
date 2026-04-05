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

  // Backup settings
  let backup_enabled = $state(true);
  let backup_dir = $state('backups');
  let backup_interval = $state(1);
  let backups = $state([]);
  let backing_up = $state(false);

  // Retention settings
  let retention_audio_days = $state(90);
  let retention_transcript_days = $state(-1);
  let retention_minutes_days = $state(-1);
  let retention_backup_days = $state(30);
  let retention_status = $state(null);
  let cleaning_up = $state(false);

  // Security settings
  let security_encryption_enabled = $state(false);
  let security_encryption_key = $state('');
  let generating_key = $state(false);

  // Obsidian settings
  let obsidian_enabled = $state(false);
  let obsidian_vault_path = $state('');
  let testing_obsidian = $state(false);

  // Custom models (successfully used, loaded from API)
  let custom_models = $state({ anthropic: [], openai: [], openrouter: [], ollama: [] });
  let llm_custom_model = $state('');  // text input for typing a custom model

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

        const bk = c.backup || {};
        backup_enabled = bk.enabled !== false;
        backup_dir = bk.backup_dir || 'backups';
        backup_interval = bk.interval_hours || 1;

        const sec = c.security || {};
        security_encryption_enabled = sec.encryption_enabled === true;
        security_encryption_key = sec.encryption_key || '';

        const ob = c.obsidian || {};
        obsidian_enabled = ob.enabled === true;
        obsidian_vault_path = ob.vault_path || '';

        const ret = c.retention || {};
        retention_audio_days = ret.audio_days ?? 90;
        retention_transcript_days = ret.transcript_days ?? -1;
        retention_minutes_days = ret.minutes_days ?? -1;
        retention_backup_days = ret.backup_days ?? 30;
      }

      // Load custom models
      try {
        custom_models = await api.getCustomModels();
      } catch (_) {
        custom_models = { anthropic: [], openai: [], openrouter: [], ollama: [] };
      }

      // Load backup list
      try {
        backups = await api.getBackups();
      } catch (_) {
        backups = [];
      }

      // Load retention status
      try {
        retention_status = await api.getRetentionStatus();
      } catch (_) {
        retention_status = null;
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
        },
        backup: {
          enabled: backup_enabled,
          backup_dir: backup_dir,
          interval_hours: backup_interval
        },
        obsidian: {
          enabled: obsidian_enabled,
          vault_path: obsidian_vault_path
        },
        security: {
          encryption_enabled: security_encryption_enabled,
          encryption_key: security_encryption_key
        },
        retention: {
          audio_days: retention_audio_days,
          transcript_days: retention_transcript_days,
          minutes_days: retention_minutes_days,
          backup_days: retention_backup_days
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
              <optgroup label="Standard Models">
                <option value="tiny">tiny (fastest, least accurate)</option>
                <option value="base">base (fast, okay accuracy)</option>
                <option value="small">small (balanced)</option>
                <option value="medium">medium (recommended)</option>
                <option value="large-v3">large-v3 (best accuracy, ~10GB RAM)</option>
              </optgroup>
              <optgroup label="Distil-Whisper (5-6x faster, <1% quality loss)">
                <option value="distil-small.en">distil-small.en (English, very fast)</option>
                <option value="distil-medium.en">distil-medium.en (English, fast + accurate)</option>
                <option value="distil-large-v3">distil-large-v3 (all languages, fast + accurate)</option>
              </optgroup>
            </select>
            <p class="text-xs text-[var(--text-muted)] mt-1">
              Distil models are 5-6x faster with &lt;1% quality loss. Apple Silicon Metal acceleration is auto-detected.
            </p>
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
              onchange={() => {
                const defaults = {
                  anthropic: 'claude-sonnet-4-6-20250514',
                  openai: 'gpt-4o',
                  openrouter: 'anthropic/claude-sonnet-4',
                  ollama: 'llama3'
                };
                llm_model = defaults[llm_provider] || '';
                llm_custom_model = '';
              }}
              class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            >
              <option value="anthropic">Anthropic</option>
              <option value="openai">OpenAI</option>
              <option value="openrouter">OpenRouter</option>
              <option value="ollama">Ollama (local)</option>
            </select>
            {#if llm_provider === 'openrouter'}
              <p class="text-xs text-[var(--text-muted)] mt-1">Access 200+ models from multiple providers via OpenRouter.</p>
            {/if}
          </div>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Model</label>
            {#if llm_provider === 'openrouter'}
              <select
                bind:value={llm_model}
                class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                       focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
              >
                <optgroup label="Anthropic">
                  <option value="anthropic/claude-sonnet-4">Claude Sonnet 4</option>
                  <option value="anthropic/claude-haiku-4">Claude Haiku 4</option>
                </optgroup>
                <optgroup label="Google">
                  <option value="google/gemini-2.5-pro-preview">Gemini 2.5 Pro</option>
                  <option value="google/gemini-2.5-flash-preview">Gemini 2.5 Flash</option>
                </optgroup>
                <optgroup label="OpenAI">
                  <option value="openai/gpt-4o">GPT-4o</option>
                  <option value="openai/gpt-4o-mini">GPT-4o Mini</option>
                </optgroup>
                <optgroup label="Meta">
                  <option value="meta-llama/llama-4-maverick">Llama 4 Maverick</option>
                </optgroup>
                <optgroup label="DeepSeek">
                  <option value="deepseek/deepseek-r1">DeepSeek R1</option>
                </optgroup>
                <optgroup label="Mistral">
                  <option value="mistralai/mistral-medium-3">Mistral Medium 3</option>
                </optgroup>
                {#if custom_models.openrouter?.length > 0}
                  <optgroup label="Previously used">
                    {#each custom_models.openrouter as m}
                      <option value={m}>{m}</option>
                    {/each}
                  </optgroup>
                {/if}
              </select>
            {:else if llm_provider === 'anthropic'}
              <select
                bind:value={llm_model}
                class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                       focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
              >
                <option value="claude-sonnet-4-6-20250514">Claude Sonnet 4.6</option>
                <option value="claude-3-5-sonnet-20241022">Claude 3.5 Sonnet</option>
                <option value="claude-3-haiku-20240307">Claude 3 Haiku</option>
                {#if custom_models.anthropic?.length > 0}
                  <optgroup label="Previously used">
                    {#each custom_models.anthropic as m}
                      <option value={m}>{m}</option>
                    {/each}
                  </optgroup>
                {/if}
              </select>
            {:else if llm_provider === 'openai'}
              <select
                bind:value={llm_model}
                class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                       focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
              >
                <option value="gpt-4o">GPT-4o</option>
                <option value="gpt-4o-mini">GPT-4o Mini</option>
                {#if custom_models.openai?.length > 0}
                  <optgroup label="Previously used">
                    {#each custom_models.openai as m}
                      <option value={m}>{m}</option>
                    {/each}
                  </optgroup>
                {/if}
              </select>
            {:else}
              <select
                bind:value={llm_model}
                class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                       focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
              >
                {#if custom_models.ollama?.length > 0}
                  {#each custom_models.ollama as m}
                    <option value={m}>{m}</option>
                  {/each}
                {:else}
                  <option value="" disabled>No models used yet — enter one below</option>
                {/if}
              </select>
            {/if}

            <!-- Custom model input for all providers -->
            <div class="mt-2">
              <div class="flex gap-2">
                <input
                  type="text"
                  bind:value={llm_custom_model}
                  placeholder={llm_provider === 'openrouter' ? 'e.g., anthropic/claude-opus-4' : llm_provider === 'ollama' ? 'e.g., llama3' : `Enter a custom ${llm_provider} model ID`}
                  class="flex-1 px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)] font-mono
                         focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
                />
                <button
                  onclick={() => {
                    if (llm_custom_model.trim()) {
                      llm_model = llm_custom_model.trim();
                      llm_custom_model = '';
                    }
                  }}
                  disabled={!llm_custom_model.trim()}
                  class="px-4 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm font-medium text-[var(--text-primary)]
                         hover:bg-[var(--bg-hover)] disabled:opacity-50 transition-colors duration-150 whitespace-nowrap"
                >
                  Use
                </button>
              </div>
              <p class="text-xs text-[var(--text-muted)] mt-1">
                {#if llm_provider === 'openrouter'}
                  Enter any model from <a href="https://openrouter.ai/models" target="_blank" class="text-[var(--accent)] hover:underline">openrouter.ai/models</a>. It will be added to the list after successful use.
                {:else}
                  Type a custom model ID and click Use. It will be saved to the dropdown after a successful generation.
                {/if}
              </p>
            </div>

            {#if llm_model}
              <p class="text-xs text-[var(--text-secondary)] mt-2 font-mono">
                Active model: {llm_model}
              </p>
            {/if}
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

      <!-- Backup -->
      <section>
        <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-1">Database Backups</h2>
        <p class="text-sm text-[var(--text-muted)] mb-4">Automatic SQLite backups with rotation.</p>

        <div class="space-y-4">
          <label class="flex items-center gap-3 cursor-pointer">
            <input
              type="checkbox"
              bind:checked={backup_enabled}
              class="w-4 h-4 rounded border-[var(--border-subtle)] text-[var(--accent)]
                     focus:ring-[var(--accent)] focus:ring-2"
            />
            <div>
              <span class="text-sm font-medium text-[var(--text-primary)]">Enable automatic backups</span>
              <p class="text-xs text-[var(--text-muted)]">Create a backup after each pipeline run (rate-limited).</p>
            </div>
          </label>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Backup Directory</label>
            <input
              type="text"
              bind:value={backup_dir}
              placeholder="backups"
              class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)] font-mono
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            />
          </div>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">
              Auto-backup interval: {backup_interval} hour{backup_interval !== 1 ? 's' : ''}
            </label>
            <input
              type="range"
              bind:value={backup_interval}
              min="1" max="24" step="1"
              class="w-full accent-[var(--accent)]"
            />
            <p class="text-xs text-[var(--text-muted)] mt-1">Minimum hours between automatic backups.</p>
          </div>

          <div class="flex items-center gap-3">
            <button
              onclick={async () => {
                backing_up = true;
                try {
                  const result = await api.createBackup();
                  addToast(`Backup created: ${result.filename}`, 'success');
                  backups = await api.getBackups();
                } catch (e) {
                  addToast(`Backup failed: ${e.message}`, 'error');
                } finally {
                  backing_up = false;
                }
              }}
              disabled={backing_up}
              class="px-4 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm font-medium text-[var(--text-primary)]
                     hover:bg-[var(--bg-hover)] disabled:opacity-50 transition-colors duration-150"
            >
              {backing_up ? 'Backing up...' : 'Backup Now'}
            </button>
          </div>

          {#if backups.length > 0}
            <div>
              <p class="text-sm font-medium text-[var(--text-primary)] mb-2">Recent Backups</p>
              <div class="border border-[var(--border-subtle)] rounded-lg overflow-hidden">
                <table class="w-full text-sm">
                  <thead class="bg-[var(--bg-surface)]">
                    <tr>
                      <th class="px-3 py-2 text-left text-[var(--text-muted)] font-medium">Filename</th>
                      <th class="px-3 py-2 text-left text-[var(--text-muted)] font-medium">Size</th>
                      <th class="px-3 py-2 text-left text-[var(--text-muted)] font-medium">Created</th>
                    </tr>
                  </thead>
                  <tbody>
                    {#each backups.slice(0, 5) as b}
                      <tr class="border-t border-[var(--border-subtle)]">
                        <td class="px-3 py-2 text-[var(--text-primary)] font-mono text-xs">{b.filename}</td>
                        <td class="px-3 py-2 text-[var(--text-secondary)]">{b.size_mb} MB</td>
                        <td class="px-3 py-2 text-[var(--text-secondary)]">{new Date(b.created).toLocaleString()}</td>
                      </tr>
                    {/each}
                  </tbody>
                </table>
              </div>
              {#if backups.length > 5}
                <p class="text-xs text-[var(--text-muted)] mt-1">Showing 5 of {backups.length} backups.</p>
              {/if}
            </div>
          {/if}
        </div>
      </section>

      <!-- Data Retention -->
      <section>
        <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-1">Data Retention</h2>
        <p class="text-sm text-[var(--text-muted)] mb-4">Automatically delete old files. Set to -1 to keep forever.</p>

        <div class="space-y-4">
          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">
              Audio recordings: {retention_audio_days === -1 ? 'Keep forever' : `${retention_audio_days} days`}
            </label>
            <input
              type="range"
              bind:value={retention_audio_days}
              min="-1" max="365" step="1"
              class="w-full accent-[var(--accent)]"
            />
            {#if retention_status?.audio}
              <p class="text-xs text-[var(--text-muted)] mt-1">
                {retention_status.audio.count} file{retention_status.audio.count !== 1 ? 's' : ''}
                {retention_status.audio.oldest_days != null ? ` (oldest: ${retention_status.audio.oldest_days} days)` : ''}
              </p>
            {/if}
          </div>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">
              Transcripts: {retention_transcript_days === -1 ? 'Keep forever' : `${retention_transcript_days} days`}
            </label>
            <input
              type="range"
              bind:value={retention_transcript_days}
              min="-1" max="365" step="1"
              class="w-full accent-[var(--accent)]"
            />
            {#if retention_status?.transcripts}
              <p class="text-xs text-[var(--text-muted)] mt-1">
                {retention_status.transcripts.count} file{retention_status.transcripts.count !== 1 ? 's' : ''}
                {retention_status.transcripts.oldest_days != null ? ` (oldest: ${retention_status.transcripts.oldest_days} days)` : ''}
              </p>
            {/if}
          </div>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">
              Minutes: {retention_minutes_days === -1 ? 'Keep forever' : `${retention_minutes_days} days`}
            </label>
            <input
              type="range"
              bind:value={retention_minutes_days}
              min="-1" max="365" step="1"
              class="w-full accent-[var(--accent)]"
            />
            {#if retention_status?.minutes}
              <p class="text-xs text-[var(--text-muted)] mt-1">
                {retention_status.minutes.count} file{retention_status.minutes.count !== 1 ? 's' : ''}
                {retention_status.minutes.oldest_days != null ? ` (oldest: ${retention_status.minutes.oldest_days} days)` : ''}
              </p>
            {/if}
          </div>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">
              Backups: {retention_backup_days === -1 ? 'Keep forever' : `${retention_backup_days} days`}
            </label>
            <input
              type="range"
              bind:value={retention_backup_days}
              min="-1" max="365" step="1"
              class="w-full accent-[var(--accent)]"
            />
            {#if retention_status?.backups}
              <p class="text-xs text-[var(--text-muted)] mt-1">
                {retention_status.backups.count} file{retention_status.backups.count !== 1 ? 's' : ''}
                {retention_status.backups.oldest_days != null ? ` (oldest: ${retention_status.backups.oldest_days} days)` : ''}
              </p>
            {/if}
          </div>

          <div class="flex items-center gap-3">
            <button
              onclick={async () => {
                cleaning_up = true;
                try {
                  await saveConfig();
                  const result = await api.runRetentionCleanup();
                  if (result.total > 0) {
                    addToast(`Cleaned up ${result.total} files`, 'success');
                  } else {
                    addToast('No files to clean up', 'info');
                  }
                  retention_status = await api.getRetentionStatus();
                } catch (e) {
                  addToast(`Cleanup failed: ${e.message}`, 'error');
                } finally {
                  cleaning_up = false;
                }
              }}
              disabled={cleaning_up}
              class="px-4 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm font-medium text-[var(--text-primary)]
                     hover:bg-[var(--bg-hover)] disabled:opacity-50 transition-colors duration-150"
            >
              {cleaning_up ? 'Cleaning up...' : 'Run Cleanup Now'}
            </button>
          </div>
        </div>
      </section>

      <!-- Security -->
      <section>
        <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-1">Security</h2>
        <p class="text-sm text-[var(--text-muted)] mb-4">Encrypt transcripts and minutes at rest using Fernet symmetric encryption.</p>

        <div class="space-y-4">
          <label class="flex items-center gap-3 cursor-pointer">
            <input
              type="checkbox"
              bind:checked={security_encryption_enabled}
              class="w-4 h-4 rounded border-[var(--border-subtle)] text-[var(--accent)]
                     focus:ring-[var(--accent)] focus:ring-2"
            />
            <div>
              <span class="text-sm font-medium text-[var(--text-primary)]">Enable encryption at rest</span>
              <p class="text-xs text-[var(--text-muted)]">Encrypt transcript and minutes files after they are written.</p>
            </div>
          </label>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Encryption Key</label>
            <div class="flex gap-2">
              <input
                type="password"
                bind:value={security_encryption_key}
                placeholder="Fernet encryption key"
                class="flex-1 px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)] font-mono
                       focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
              />
              <button
                onclick={async () => {
                  generating_key = true;
                  try {
                    const result = await api.generateEncryptionKey();
                    security_encryption_key = result.key;
                    addToast('Encryption key generated. Save settings to apply.', 'success');
                  } catch (e) {
                    addToast(`Key generation failed: ${e.message}`, 'error');
                  } finally {
                    generating_key = false;
                  }
                }}
                disabled={generating_key}
                class="px-4 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm font-medium text-[var(--text-primary)]
                       hover:bg-[var(--bg-hover)] disabled:opacity-50 transition-colors duration-150 whitespace-nowrap"
              >
                {generating_key ? 'Generating...' : 'Generate Key'}
              </button>
            </div>
            <p class="text-xs text-[var(--text-muted)] mt-1">
              A Fernet symmetric encryption key. Use the Generate button or run: <code>mm generate-key</code>
            </p>
          </div>

          {#if security_encryption_enabled}
            <div class="p-3 bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 rounded-lg">
              <p class="text-sm text-yellow-800 dark:text-yellow-200 font-medium">Warning</p>
              <p class="text-xs text-yellow-700 dark:text-yellow-300 mt-1">
                Losing the encryption key means losing access to all encrypted data. Store the key securely and keep a backup.
              </p>
            </div>
          {/if}
        </div>
      </section>

      <!-- Obsidian -->
      <section>
        <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-1">Obsidian Export</h2>
        <p class="text-sm text-[var(--text-muted)] mb-4">Auto-export meeting minutes to an Obsidian vault.</p>

        <div class="space-y-4">
          <label class="flex items-center gap-3 cursor-pointer">
            <input
              type="checkbox"
              bind:checked={obsidian_enabled}
              class="w-4 h-4 rounded border-[var(--border-subtle)] text-[var(--accent)]
                     focus:ring-[var(--accent)] focus:ring-2"
            />
            <div>
              <span class="text-sm font-medium text-[var(--text-primary)]">Enable Obsidian export</span>
              <p class="text-xs text-[var(--text-muted)]">Export minutes as Markdown with YAML frontmatter after each pipeline run.</p>
            </div>
          </label>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Vault Path</label>
            <input
              type="text"
              bind:value={obsidian_vault_path}
              placeholder="~/Documents/Obsidian Vault"
              class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)] font-mono
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            />
            <p class="text-xs text-[var(--text-muted)] mt-1">Absolute path to your Obsidian vault root folder.</p>
          </div>

          <div class="flex items-center gap-3">
            <button
              onclick={async () => {
                testing_obsidian = true;
                try {
                  // Save config first so the API has the vault path
                  await saveConfig();
                  const result = await api.testObsidian();
                  if (result.success) {
                    addToast(`Test note written: ${result.path}`, 'success');
                  } else {
                    addToast(`Obsidian test failed: ${result.error}`, 'error');
                  }
                } catch (e) {
                  addToast(`Obsidian test failed: ${e.message}`, 'error');
                } finally {
                  testing_obsidian = false;
                }
              }}
              disabled={testing_obsidian || !obsidian_vault_path}
              class="px-4 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm font-medium text-[var(--text-primary)]
                     hover:bg-[var(--bg-hover)] disabled:opacity-50 transition-colors duration-150"
            >
              {testing_obsidian ? 'Testing...' : 'Test Connection'}
            </button>
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
