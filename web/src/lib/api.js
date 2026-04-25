const BASE = '/api';

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    // FastAPI validation errors return detail as an array of objects
    let msg = err.error || err.detail || res.statusText;
    if (Array.isArray(msg)) {
      msg = msg.map(e => e.msg || JSON.stringify(e)).join('; ');
    } else if (typeof msg === 'object') {
      msg = JSON.stringify(msg);
    }
    throw new Error(msg);
  }
  return res.json();
}

export const api = {
  // Raw request helper for endpoints not yet wrapped
  request,

  // Meetings
  getMeetings: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return request(`/meetings?${qs}`);
  },
  getMeeting: (id) => request(`/meetings/${id}`),
  getTranscript: (id) => request(`/meetings/${id}/transcript`),
  getAnalytics: (id) => request(`/meetings/${id}/analytics`),
  deleteMeeting: (id) => request(`/meetings/${id}`, { method: 'DELETE' }),
  updateMeeting: (id, data) => request(`/meetings/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  regenerateMeeting: (id) => request(`/meetings/${id}/regenerate`, { method: 'POST' }),
  updateTranscriptSpeakers: (id, body) => request(`/meetings/${id}/transcript/speakers`, {
    method: 'PATCH', body: JSON.stringify(body),
  }),
  getSpeakerSuggestions: (id) => request(`/meetings/${id}/speaker-suggestions`),
  // Post-hoc notes pasted from a meeting app (Teams/Zoom/Meet/Otter/…). Returns
  // 202 — the server kicks off a background job that renames speakers and
  // re-runs minutes generation. Poll getMeeting(id) and watch
  // external_notes_status for "ready" (or "error").
  submitExternalNotes: (id, text) => request(`/meetings/${id}/external-notes`, {
    method: 'POST', body: JSON.stringify({ text }),
  }),
  // Switch a meeting's type and rebuild the summary against the new template.
  // Returns 202 — server kicks off a background reprocess. Poll getMeeting(id)
  // and watch meeting_type_status for "ready" (or "error").
  changeMeetingType: (id, meetingType) => request(`/meetings/${id}/meeting-type`, {
    method: 'POST', body: JSON.stringify({ meeting_type: meetingType }),
  }),

  // Search
  search: (params) => {
    const qs = new URLSearchParams(params).toString();
    return request(`/search?${qs}`);
  },

  // Actions
  getActionItems: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return request(`/action-items?${qs}`);
  },
  updateActionItem: (id, data) => request(`/action-items/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),

  // Decisions
  getDecisions: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return request(`/decisions?${qs}`);
  },

  // People
  getPeople: () => request('/people'),
  getPerson: (id) => request(`/people/${id}`),
  getPersonMeetings: (id) => request(`/people/${id}/meetings`),
  createPerson: (data) => request('/people', {
    method: 'POST', body: JSON.stringify(data),
  }),
  updatePerson: (id, data) => request(`/people/${id}`, {
    method: 'PATCH', body: JSON.stringify(data),
  }),
  deletePerson: (id) => request(`/people/${id}`, { method: 'DELETE' }),
  mergePerson: (sourceId, targetId, renameActions = true) => request(`/people/${sourceId}/merge`, {
    method: 'POST', body: JSON.stringify({ target_id: targetId, rename_actions: renameActions }),
  }),

  // Stats
  getStats: () => request('/stats'),
  getMeetingsOverTime: () => request('/stats/meetings-over-time'),
  getByType: () => request('/stats/by-type'),
  getActionVelocity: () => request('/stats/action-velocity'),

  // ANA-1 analytics panels
  getStatsCommitments: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return request(`/stats/commitments${qs ? `?${qs}` : ''}`);
  },
  getStatsSentiment: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return request(`/stats/sentiment${qs ? `?${qs}` : ''}`);
  },
  getStatsEffectiveness: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return request(`/stats/effectiveness${qs ? `?${qs}` : ''}`);
  },
  getStatsUnresolvedTopics: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return request(`/stats/unresolved-topics${qs ? `?${qs}` : ''}`);
  },

  // REC-1 series
  getSeriesList: () => request('/series'),
  getSeries: (id) => request(`/series/${id}`),
  detectSeries: () => request('/series/detect', { method: 'POST' }),
  getMeetingSeries: (id) => request(`/meetings/${id}/series`),

  // BRF-1 briefing
  getBriefing: (peopleIds, type = null) => {
    const params = new URLSearchParams();
    for (const pid of peopleIds) params.append('people', pid);
    if (type) params.set('type', type);
    return request(`/brief?${params.toString()}`);
  },

  // EXP-1 export
  exportMeeting: (meetingId, format = 'md', withTranscript = false) => {
    const qs = new URLSearchParams({ format, with_transcript: String(withTranscript) }).toString();
    return fetch(`/api/meetings/${meetingId}/export?${qs}`).then(res => {
      if (!res.ok) {
        return res.json().then(err => { throw new Error(err.detail || res.statusText); });
      }
      return res.blob();
    });
  },
  exportSeries: (seriesId, format = 'pdf', withTranscript = false) => {
    const qs = new URLSearchParams({ format, with_transcript: String(withTranscript) }).toString();
    return fetch(`/api/series/${seriesId}/export?${qs}`).then(res => {
      if (!res.ok) {
        return res.json().then(err => { throw new Error(err.detail || res.statusText); });
      }
      return res.blob();
    });
  },

  // Recording
  autoDetectDevice: () => request('/auto-detect-device'),
  startRecording: (data = {}) => request('/recording/start', { method: 'POST', body: JSON.stringify(data) }),
  stopRecording: (data = {}) => request('/recording/stop', { method: 'POST', body: JSON.stringify(data) }),
  getRecordingStatus: () => request('/recording/status'),
  // Pass { refresh: true } for the Refresh button (forces PortAudio re-scan to
  // pick up newly-plugged Bluetooth/USB devices). The default path avoids the
  // re-scan entirely, so the periodic 3s poll can't race with a live recording
  // stream (see PR #16 / #17 fallout).
  getAudioDevices: ({ refresh = false } = {}) =>
    request('/audio-devices' + (refresh ? '?refresh=true' : '')),
  getLanguages: () => request('/languages'),
  getPipelines: () => request('/pipelines'),

  // Config
  getConfig: () => request('/config'),
  updateConfig: (data) => request('/config', { method: 'PATCH', body: JSON.stringify({ config: data }) }),
  getCustomModels: () => request('/config/custom-models'),
  getProviderModels: (provider, refresh = false) =>
    request(`/config/provider-models?provider=${provider}&refresh=${refresh}`),

  // Upload
  uploadTranscript: (formData) => fetch('/api/upload-transcript', { method: 'POST', body: formData }).then(res => {
    if (!res.ok) return res.json().then(err => { throw new Error(err.detail || res.statusText); });
    return res.json();
  }),

  // Backups
  getBackups: () => request('/backups'),
  createBackup: () => request('/backups', { method: 'POST' }),
  testObsidian: () => request('/backups/obsidian-test', { method: 'POST' }),

  // Security
  generateEncryptionKey: () => request('/security/generate-key', { method: 'POST' }),

  // Retention
  getRetentionStatus: () => request('/retention/status'),
  runRetentionCleanup: () => request('/retention/cleanup', { method: 'POST' }),
  getOldestAudio: (limit = 20) => request(`/retention/oldest-audio?limit=${limit}`),
  deleteAudioBulk: (meetingIds) => request('/retention/audio', {
    method: 'DELETE', body: JSON.stringify({ meeting_ids: meetingIds }),
  }),

  // DSK-1 recording preflight
  recordingPreflight: (plannedMinutes = null) => request(
    `/recording/preflight${plannedMinutes != null ? `?planned_minutes=${plannedMinutes}` : ''}`,
  ),

  // ONB-1 diagnostic doctor
  getDoctorChecks: () => request('/doctor'),

  // Chat (talk to your notes)
  sendChatMessage: (message, sessionId = null) => request('/chat', {
    method: 'POST', body: JSON.stringify({ message, session_id: sessionId }),
  }),
  getChatSessions: () => request('/chat/sessions'),
  getChatMessages: (sessionId) => request(`/chat/sessions/${sessionId}/messages`),
  deleteChatSession: (sessionId) => request(`/chat/sessions/${sessionId}`, { method: 'DELETE' }),

  // Templates
  getTemplates: () => request('/templates'),
  getTemplate: (type) => request(`/templates/${type}`),
  updateTemplate: (type, data) => request(`/templates/${type}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteTemplate: (type) => request(`/templates/${type}`, { method: 'DELETE' }),
};
