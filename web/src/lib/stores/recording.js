import { writable } from 'svelte/store';

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

  async function fetchWsToken() {
    const res = await fetch('/api/security/ws-token', { method: 'POST' });
    if (!res.ok) throw new Error(`ws-token HTTP ${res.status}`);
    const data = await res.json();
    return data.token;
  }

  async function connect() {
    if (typeof window === 'undefined') return;
    if (ws && ws.readyState <= 1) return; // already open or connecting

    let token;
    try {
      token = await fetchWsToken();
    } catch (e) {
      console.error('[recording-store] Failed to fetch WS token:', e);
      reconnectTimer = setTimeout(connect, 3000);
      return;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const url = `${protocol}//${host}/ws/recording?token=${encodeURIComponent(token)}`;
    console.log('[recording-store] Connecting to WebSocket');

    try {
      ws = new WebSocket(url);
    } catch (e) {
      console.error('[recording-store] WebSocket creation failed:', e);
      reconnectTimer = setTimeout(connect, 3000);
      return;
    }

    ws.onopen = () => {
      console.log('[recording-store] WebSocket connected');
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
      } catch (e) {
        console.error('[recording-store] Failed to parse WS message:', e);
      }
    };

    ws.onclose = (event) => {
      console.log('[recording-store] WebSocket closed, code:', event.code, 'reason:', event.reason);
      update((s) => ({ ...s, connected: false }));
      ws = null;
      // Attempt reconnect after 3 seconds
      reconnectTimer = setTimeout(connect, 3000);
    };

    ws.onerror = (event) => {
      console.error('[recording-store] WebSocket error:', event);
      if (ws) ws.close();
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
