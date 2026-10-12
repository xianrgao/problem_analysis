import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../lib/api';
import type { Session, SessionDetail } from '../lib/types';

function savedTabs(): string[] { try { return JSON.parse(localStorage.getItem('linea.tabs') || '[]'); } catch { return []; } }
export function useWorkbench() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [tabs, setTabs] = useState<string[]>(savedTabs);
  const [activeId, setActiveId] = useState<string | null>(() => localStorage.getItem('linea.active'));
  const [details, setDetails] = useState<Record<string, SessionDetail>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [online, setOnline] = useState(true);
  const [mode, setMode] = useState('demo');
  const mounted = useRef(true);
  const refreshSessions = useCallback(async () => { const list = await api.sessions(); if (mounted.current) { setSessions(list); setOnline(true); } return list; }, []);
  const refresh = useCallback(async (id: string) => { const detail = await api.detail(id); if (mounted.current) { setDetails(previous => ({ ...previous, [id]: detail })); setSessions(previous => previous.map(session => session.id === id ? detail.session : session)); } return detail; }, []);
  const notify = useCallback((failure: unknown) => { setError(failure instanceof Error ? failure.message : String(failure)); }, []);
  useEffect(() => { mounted.current = true; Promise.all([refreshSessions(), api.health()]).then(([list, health]) => { setMode(health.mode || 'demo'); setTabs(previous => previous.filter(id => list.some(session => session.id === id))); setActiveId(previous => list.some(session => session.id === previous) ? previous : null); }).catch(failure => { setOnline(false); notify(failure); }).finally(() => setLoading(false)); return () => { mounted.current = false; }; }, [refreshSessions, notify]);
  useEffect(() => { localStorage.setItem('linea.tabs', JSON.stringify(tabs)); }, [tabs]);
  useEffect(() => { if (activeId) { localStorage.setItem('linea.active', activeId); refresh(activeId).catch(notify); } else localStorage.removeItem('linea.active'); }, [activeId, refresh, notify]);
  useEffect(() => { const interval = window.setInterval(() => { refreshSessions().catch(() => setOnline(false)); }, 6500); return () => clearInterval(interval); }, [refreshSessions]);
  useEffect(() => {
    const timers = new Map<string, number>();
    const streams = tabs.map(id => { const stream = new EventSource(api.streamUrl(id)); const update = () => { if (timers.has(id)) return; timers.set(id, window.setTimeout(() => { timers.delete(id); refresh(id).catch(() => {}); }, 180)); }; stream.addEventListener('update', update); stream.onopen = () => { setOnline(true); refresh(id).catch(() => {}); }; return stream; });
    return () => { streams.forEach(stream => stream.close()); timers.forEach(timer => clearTimeout(timer)); };
  }, [tabs, refresh]);
  const open = useCallback((id: string) => { setTabs(previous => previous.includes(id) ? previous : [...previous, id]); setActiveId(id); }, []);
  const close = useCallback((id: string) => { setTabs(previous => { const remaining = previous.filter(tab => tab !== id); setActiveId(current => current === id ? remaining.at(-1) || null : current); return remaining; }); }, []);
  const create = useCallback(async () => { const session = await api.createSession(); setSessions(previous => [session, ...previous]); open(session.id); return session; }, [open]);
  const patch = useCallback(async (id: string, changes: Partial<Session>) => { const session = await api.patchSession(id, changes); setSessions(previous => previous.map(item => item.id === id ? session : item)); setDetails(previous => previous[id] ? { ...previous, [id]: { ...previous[id], session } } : previous); }, []);
  const act = useCallback(async (action: () => Promise<unknown>, id?: string) => { try { await action(); if (id) await refresh(id); await refreshSessions(); } catch (failure) { notify(failure); throw failure; } }, [refresh, refreshSessions, notify]);
  return { sessions, tabs, activeId, detail: activeId ? details[activeId] : undefined, loading, error, online, mode, setMode, setError, open, close, create, patch, act, notify, refreshSessions };
}
