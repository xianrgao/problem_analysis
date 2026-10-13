import type { Health, Issue, Plan, Session, SessionDetail, Settings, Skill, TableData, Variable } from './types';

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, { ...options, headers: { ...(options?.body && !(options.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}), ...options?.headers } });
  if (!response.ok) { let reason = `${response.status} ${response.statusText}`; try { const body = await response.json(); reason = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail || body.message || body); } catch { /* Retain HTTP failure message. */ } throw new Error(reason); }
  return response.status === 204 ? undefined as T : response.json();
}
const sid = (id: string) => `/sessions/${encodeURIComponent(id)}`;
const variable = (session: string, name: string) => `${sid(session)}/variables/${encodeURIComponent(name)}`;
const body = (value: unknown) => JSON.stringify(value);
export const api = {
  health: () => request<Health>('/health'),
  sessions: () => request<Session[]>('/sessions'),
  createSession: (title?: string) => request<Session>('/sessions', { method: 'POST', body: body({ title }) }),
  patchSession: (id: string, patch: Partial<Session>) => request<Session>(sid(id), { method: 'PATCH', body: body(patch) }),
  detail: (id: string) => request<SessionDetail>(sid(id)),
  send: (id: string, content: string) => request(`${sid(id)}/messages`, { method: 'POST', body: body({ content }) }),
  approve: (id: string, plan: Plan) => request(`${sid(id)}/plans/${plan.id}/approve`, { method: 'POST', body: body({ revision: plan.revision }) }),
  revise: (id: string, plan: Plan, feedback: string) => request(`${sid(id)}/plans/${plan.id}/revise`, { method: 'POST', body: body({ revision: plan.revision, feedback }) }),
  cancel: (id: string) => request(`${sid(id)}/cancel`, { method: 'POST' }),
  resume: (id: string) => request(`${sid(id)}/resume`, { method: 'POST' }),
  streamUrl: (id: string) => `/api${sid(id)}/stream`,
  table: (id: string, name: string, offset = 0, search = '', sort = '', direction = 'asc', version?: number, limit = 40) => request<TableData>(`${variable(id, name)}/table?${new URLSearchParams({ offset: String(offset), limit: String(limit), search, sort, direction, ...(version ? { version: String(version) } : {}) })}`),
  preview: (id: string, name: string, version?: number) => request<TableData>(`${variable(id, name)}/preview${version ? `?version=${version}` : ''}`),
  value: (id: string, name: string) => request<unknown>(`${variable(id, name)}/value`),
  exportUrl: (id: string, name: string, format = 'csv') => `/api${variable(id, name)}/export?format=${format}`,
  import: (id: string, file: File) => { const form = new FormData(); form.append('file', file); return request<Variable>(`${sid(id)}/import`, { method: 'POST', body: form }); },
  snapshot: (id: string, source: string, name: string, version: number) => request<Variable>(`${sid(id)}/snapshot`, { method: 'POST', body: body({ source_session_id: source, name, version }) }),
  issues: () => request<Issue[]>('/issues'),
  createIssue: (input: { title: string; description?: string; session_id?: string; owner?: string; due_date?: string }) => request<Issue>('/issues', { method: 'POST', body: body(input) }),
  patchIssue: (id: string, patch: Partial<Issue> & { confirm_close?: boolean }) => request<Issue>(`/issues/${encodeURIComponent(id)}`, { method: 'PATCH', body: body(patch) }),
  settings: () => request<Settings>('/settings'),
  updateSettings: (settings: Partial<Settings>) => request<Settings>('/settings', { method: 'PATCH', body: body(settings) }),
  skills: () => request<Skill[]>('/skills'),
};
