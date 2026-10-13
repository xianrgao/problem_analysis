import { useEffect, useRef, useState } from 'react';

export type Theme = 'light' | 'dark';
const STORAGE_KEY = 'linea.theme';
const SYSTEM_QUERY = '(prefers-color-scheme: dark)';

export function readThemePreference(): Theme | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === 'light' || value === 'dark' ? value : null;
  } catch {
    return null; // Theme switching still works when storage is unavailable.
  }
}

export function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#0c0e13' : '#f5f6f8');
}

export function useTheme() {
  const preference = useRef(readThemePreference());
  const [theme, setTheme] = useState<Theme>(() => preference.current ?? (window.matchMedia(SYSTEM_QUERY).matches ? 'dark' : 'light'));

  useEffect(() => { applyTheme(theme); }, [theme]);
  useEffect(() => {
    const media = window.matchMedia(SYSTEM_QUERY);
    const followSystem = () => { if (!preference.current) setTheme(media.matches ? 'dark' : 'light'); };
    const syncPreference = (event: StorageEvent) => {
      if (event.key !== STORAGE_KEY && event.key !== null) return;
      preference.current = readThemePreference();
      setTheme(preference.current ?? (media.matches ? 'dark' : 'light'));
    };
    media.addEventListener('change', followSystem);
    window.addEventListener('storage', syncPreference);
    followSystem();
    return () => {
      media.removeEventListener('change', followSystem);
      window.removeEventListener('storage', syncPreference);
    };
  }, []);

  const toggleTheme = () => {
    const next = theme === 'light' ? 'dark' : 'light';
    preference.current = next;
    setTheme(next);
    try { localStorage.setItem(STORAGE_KEY, next); } catch { /* Keep this tab's explicit choice in memory. */ }
  };
  return { theme, toggleTheme };
}
