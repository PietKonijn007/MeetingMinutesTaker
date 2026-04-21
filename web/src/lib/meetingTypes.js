// Single source of truth for meeting-type display metadata.
// Used by MeetingTypeBadge for colored pills and by the recording page
// picker for the user-selectable dropdown.
//
// Keep in sync with src/meeting_minutes/models.py:MeetingType and the color
// table in specs/04-web-ui.md §4.2. Unknown types fall back to `other`.

export const MEETING_TYPES = [
  // Team & cadence
  { value: 'standup',                  label: 'Standup',             group: 'Team & cadence',     color: '#22C55E', bg: 'rgba(34,197,94,0.12)' },
  { value: 'team_meeting',             label: 'Team Meeting',        group: 'Team & cadence',     color: '#6366F1', bg: 'rgba(99,102,241,0.12)' },
  { value: 'retrospective',            label: 'Retro',               group: 'Team & cadence',     color: '#F97316', bg: 'rgba(249,115,22,0.12)' },
  { value: 'planning',                 label: 'Planning',            group: 'Team & cadence',     color: '#14B8A6', bg: 'rgba(20,184,166,0.12)' },
  { value: 'brainstorm',               label: 'Brainstorm',          group: 'Team & cadence',     color: '#EC4899', bg: 'rgba(236,72,153,0.12)' },
  { value: 'decision_meeting',         label: 'Decision',            group: 'Team & cadence',     color: '#F59E0B', bg: 'rgba(245,158,11,0.12)' },

  // 1:1 (perspective-aware)
  { value: 'one_on_one_direct_report', label: '1:1 · Direct Report', group: '1:1',                color: '#0EA5E9', bg: 'rgba(14,165,233,0.12)' },
  { value: 'one_on_one_leader',        label: '1:1 · With Leader',   group: '1:1',                color: '#0284C7', bg: 'rgba(2,132,199,0.12)' },
  { value: 'one_on_one_peer',          label: '1:1 · Peer',          group: '1:1',                color: '#38BDF8', bg: 'rgba(56,189,248,0.12)' },
  { value: 'one_on_one',               label: '1:1 (Generic)',       group: '1:1',                color: '#0EA5E9', bg: 'rgba(14,165,233,0.12)' },

  // Exec & cross-functional
  { value: 'leadership_meeting',       label: 'Leadership',          group: 'Exec & cross-func',  color: '#8B5CF6', bg: 'rgba(139,92,246,0.12)' },
  { value: 'board_meeting',            label: 'Board',               group: 'Exec & cross-func',  color: '#334155', bg: 'rgba(51,65,85,0.12)' },
  { value: 'architecture_review',      label: 'Architecture Review', group: 'Exec & cross-func',  color: '#3B82F6', bg: 'rgba(59,130,246,0.12)' },
  { value: 'incident_review',          label: 'Incident Review',     group: 'Exec & cross-func',  color: '#EF4444', bg: 'rgba(239,68,68,0.12)' },

  // External
  { value: 'customer_meeting',         label: 'Customer',            group: 'External',           color: '#A855F7', bg: 'rgba(168,85,247,0.12)' },
  { value: 'vendor_meeting',           label: 'Vendor',              group: 'External',           color: '#D946EF', bg: 'rgba(217,70,239,0.12)' },
  { value: 'interview_debrief',        label: 'Interview Debrief',   group: 'External',           color: '#84CC16', bg: 'rgba(132,204,22,0.12)' },

  // Fallback
  { value: 'other',                    label: 'Other',               group: 'Fallback',           color: '#6B7280', bg: 'rgba(107,114,128,0.12)' }
];

// Lookup map keyed by value for O(1) access.
export const MEETING_TYPE_MAP = Object.fromEntries(MEETING_TYPES.map(t => [t.value, t]));

// Groups in display order, each with its members. Used by the recording
// picker's <optgroup> rendering.
export const MEETING_TYPE_GROUPS = MEETING_TYPES.reduce((acc, t) => {
  const bucket = acc.find(g => g.group === t.group);
  if (bucket) bucket.items.push(t);
  else acc.push({ group: t.group, items: [t] });
  return acc;
}, []);

export function getMeetingTypeDisplay(value) {
  return MEETING_TYPE_MAP[value] || MEETING_TYPE_MAP.other;
}
