import React, { useEffect, useRef, useState } from 'react';
import { Check, Palette } from 'lucide-react';
import GlassButton from './GlassButton';
import { useTheme } from '../contexts/ThemeContext';

function ThemeCards({ compact = false, onSelect }) {
  const { theme, setTheme, saving, options } = useTheme();
  const choose = async (value) => {
    if (value === theme || saving) return;
    try {
      await setTheme(value);
      onSelect?.();
    } catch (_) {}
  };

  return (
    <div className={compact ? 'theme-picker-list' : 'theme-picker-grid'}>
      {options.map((option) => (
        <button
          type="button"
          key={option.value}
          className={`theme-option ${theme === option.value ? 'active' : ''}`}
          onClick={() => choose(option.value)}
          disabled={saving}
        >
          <span className="theme-swatch" aria-hidden="true">
            {option.colors.map((color) => <i key={color} style={{ background: color }} />)}
          </span>
          <span className="theme-option-copy"><b>{option.label}</b>{!compact ? <small>{option.description}</small> : null}</span>
          {theme === option.value ? <Check size={16} /> : null}
        </button>
      ))}
    </div>
  );
}

export default function ThemePicker({ compact = false }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const { theme, options } = useTheme();
  const selected = options.find((option) => option.value === theme) || options[0];

  useEffect(() => {
    if (!open) return undefined;
    const close = (event) => { if (!rootRef.current?.contains(event.target)) setOpen(false); };
    const escape = (event) => { if (event.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', escape); };
  }, [open]);

  if (!compact) return <ThemeCards />;

  return (
    <div className="theme-picker-compact" ref={rootRef}>
      <GlassButton variant="icon" onClick={() => setOpen((value) => !value)} title={`界面主题：${selected.label}`} aria-expanded={open}>
        <Palette size={18} />
      </GlassButton>
      {open ? <div className="theme-picker-popover glass-panel-opaque"><ThemeCards compact onSelect={() => setOpen(false)} /></div> : null}
    </div>
  );
}
