import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import apiClient from '../api/client';
import { useAuth } from './AuthContext';

export const THEME_STORAGE_KEY = 'automyai-ui-theme';
export const DEFAULT_THEME = 'dark-purple';
export const THEME_OPTIONS = [
  { value: 'dark-purple', label: '默认紫黑', description: '深色紫蓝工作台', colors: ['#030712', '#6366f1', '#a5b4fc'] },
  { value: 'dark-cyberpunk', label: '深空蓝', description: '蓝色霓虹边框', colors: ['#020617', '#3b82f6', '#60a5fa'] },
  { value: 'dark-matrix', label: '骇客绿', description: '黑客帝国绿光', colors: ['#022c22', '#059669', '#34d399'] },
  { value: 'dark-obsidian', label: '纯黑高对比', description: '黑白高对比界面', colors: ['#000000', '#737373', '#e5e5e5'] },
  { value: 'light', label: '浅色', description: '明亮灰白界面', colors: ['#f8fafc', '#4f46e5', '#818cf8'] },
];

const VALID_THEMES = new Set(THEME_OPTIONS.map((option) => option.value));
const THEME_CLASSES = ['dark', 'dark-purple', 'dark-cyberpunk', 'dark-matrix', 'dark-obsidian'];
const ThemeContext = createContext(null);

export function normalizeTheme(value) {
  return VALID_THEMES.has(value) ? value : DEFAULT_THEME;
}

export function applyTheme(theme) {
  const next = normalizeTheme(theme);
  const root = document.documentElement;
  root.classList.remove(...THEME_CLASSES);
  if (next !== 'light') root.classList.add('dark', next);
  root.dataset.theme = next;
  root.style.colorScheme = next === 'light' ? 'light' : 'dark';
  try { localStorage.setItem(THEME_STORAGE_KEY, next); } catch (_) {}
  return next;
}

function cachedTheme() {
  try { return normalizeTheme(localStorage.getItem(THEME_STORAGE_KEY)); } catch (_) { return DEFAULT_THEME; }
}

export function ThemeProvider({ children }) {
  const { authenticated } = useAuth();
  const [theme, setThemeState] = useState(cachedTheme);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => { applyTheme(theme); }, [theme]);

  useEffect(() => {
    if (!authenticated) return;
    let active = true;
    apiClient.get('/settings').then((response) => {
      if (!active) return;
      const serverTheme = normalizeTheme(response?.settings?.UI_THEME);
      setThemeState(serverTheme);
    }).catch(() => {});
    return () => { active = false; };
  }, [authenticated]);

  const setTheme = useCallback(async (value) => {
    const next = normalizeTheme(value);
    const previous = theme;
    setThemeState(next);
    setSaving(true);
    setError(null);
    try {
      await apiClient.post('/settings', { UI_THEME: next });
      return next;
    } catch (reason) {
      setThemeState(previous);
      setError(reason);
      throw reason;
    } finally {
      setSaving(false);
    }
  }, [theme]);

  const value = useMemo(() => ({ theme, setTheme, saving, error, options: THEME_OPTIONS }), [error, saving, setTheme, theme]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const value = useContext(ThemeContext);
  if (!value) throw new Error('useTheme must be used inside ThemeProvider');
  return value;
}
