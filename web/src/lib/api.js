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

  // Stats
  getStats: () => request('/stats'),
  getMeetingsOverTime: () => request('/stats/meetings-over-time'),
  getByType: () => request('/stats/by-type'),
  getActionVelocity: () => request('/stats/action-velocity'),

  // Recording
  autoDetectDevice: () => request('/auto-detect-device'),
  startRecording: (data = {}) => request('/recording/start', { method: 'POST', body: JSON.stringify(data) }),
  stopRecording: (data = {}) => request('/recording/stop', { method: 'POST', body: JSON.stringify(data) }),
  getRecordingStatus: () => request('/recording/status'),
  getAudioDevices: () => request('/audio-devices'),
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

  // Templates
  getTemplates: () => request('/templates'),
  getTemplate: (type) => request(`/templates/${type}`),
  updateTemplate: (type, data) => request(`/templates/${type}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteTemplate: (type) => request(`/templates/${type}`, { method: 'DELETE' }),
};
