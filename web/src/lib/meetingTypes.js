// Single source of truth for meeting-type display metadata.
// Used by MeetingTypeBadge for colored pills and by the recording page
// picker for the user-selectable dropdown.
//
// Keep in sync with src/meeting_minutes/models.py:MeetingType and the color
// table in specs/04-web-ui.md §4.2. Unknown types fall back to `other`.

export const MEETING_TYPES = [
  // Team meetings
  { value: 'standup',                  label: 'Daily standup',             group: 'Team meetings',               color: '#22C55E', bg: 'rgba(34,197,94,0.12)' },
  { value: 'team_meeting',             label: 'Team meeting',              group: 'Team meetings',               color: '#6366F1', bg: 'rgba(99,102,241,0.12)' },
  { value: 'retrospective',            label: 'Retrospective',             group: 'Team meetings',               color: '#F97316', bg: 'rgba(249,115,22,0.12)' },
  { value: 'planning',                 label: 'Planning session',          group: 'Team meetings',               color: '#14B8A6', bg: 'rgba(20,184,166,0.12)' },
  { value: 'brainstorm',               label: 'Brainstorm',                group: 'Team meetings',               color: '#EC4899', bg: 'rgba(236,72,153,0.12)' },
  { value: 'decision_meeting',         label: 'Decision meeting',          group: 'Team meetings',               color: '#F59E0B', bg: 'rgba(245,158,11,0.12)' },

  // 1-on-1s (perspective-aware)
  { value: 'one_on_one_direct_report', label: '1-on-1 with my report',     group: '1-on-1s',                     color: '#0EA5E9', bg: 'rgba(14,165,233,0.12)' },
  { value: 'one_on_one_leader',        label: '1-on-1 with my manager',    group: '1-on-1s',                     color: '#0284C7', bg: 'rgba(2,132,199,0.12)' },
  { value: 'one_on_one_peer',          label: '1-on-1 with a peer',        group: '1-on-1s',                     color: '#38BDF8', bg: 'rgba(56,189,248,0.12)' },
  { value: 'one_on_one',               label: '1-on-1 (other)',            group: '1-on-1s',                     color: '#0EA5E9', bg: 'rgba(14,165,233,0.12)' },

  // Leadership & reviews
  { value: 'leadership_meeting',       label: 'Leadership / staff meeting', group: 'Leadership & reviews',       color: '#8B5CF6', bg: 'rgba(139,92,246,0.12)' },
  { value: 'board_meeting',            label: 'Board meeting',             group: 'Leadership & reviews',        color: '#334155', bg: 'rgba(51,65,85,0.12)' },
  { value: 'architecture_review',      label: 'Architecture / design review', group: 'Leadership & reviews',     color: '#3B82F6', bg: 'rgba(59,130,246,0.12)' },
  { value: 'incident_review',          label: 'Incident review / post-mortem', group: 'Leadership & reviews',    color: '#EF4444', bg: 'rgba(239,68,68,0.12)' },

  // Customers, vendors & interviews
  { value: 'customer_meeting',         label: 'Customer meeting',          group: 'Customers, vendors & interviews', color: '#A855F7', bg: 'rgba(168,85,247,0.12)' },
  { value: 'vendor_meeting',           label: 'Vendor / partner meeting',  group: 'Customers, vendors & interviews', color: '#D946EF', bg: 'rgba(217,70,239,0.12)' },
  { value: 'interview_debrief',        label: 'Interview debrief',         group: 'Customers, vendors & interviews', color: '#84CC16', bg: 'rgba(132,204,22,0.12)' },

  // Other
  { value: 'other',                    label: 'Something else',            group: 'Other',                       color: '#6B7280', bg: 'rgba(107,114,128,0.12)' }
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
