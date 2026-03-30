import { writable } from 'svelte/store';
import { browser } from '$app/environment';

function createRecordingStore() {
  const initial = {
    state: 'idle', // 'idle' | 'recording' | 'processing' | 'done'
    elapsedSeconds: 0,
    audioLevel: 0,
    step: null,
    progress: 0,
    meetingId: null,
    connected: false
  };

  const { subscribe, set, update } = writable(initial);

  let ws = null;
  let reconnectTimer = null;

  function connect() {
    if (!browser) return;
    if (ws && ws.readyState <= 1) return; // already open or connecting

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    ws = new WebSocket(`${protocol}//${host}/ws/recording`);

    ws.onopen = () => {
      update((s) => ({ ...s, connected: true }));
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        update((s) => ({
          ...s,
          state: data.state || s.state,
          elapsedSeconds: data.elapsed_seconds ?? s.elapsedSeconds,
          audioLevel: data.audio_level ?? s.audioLevel,
          step: data.step ?? s.step,
          progress: data.progress ?? s.progress,
          meetingId: data.meeting_id ?? s.meetingId
        }));
      } catch {
        // ignore malformed messages
      }
    };

    ws.onclose = () => {
      update((s) => ({ ...s, connected: false }));
      // Attempt reconnect after 3 seconds
      reconnectTimer = setTimeout(connect, 3000);
    };

    ws.onerror = () => {
      ws.close();
    };
  }

  function disconnect() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (ws) {
      ws.close();
      ws = null;
    }
  }

  function reset() {
    set(initial);
  }

  return {
    subscribe,
    connect,
    disconnect,
    reset
  };
}

export const recording = createRecordingStore();
