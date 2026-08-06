import React, { useEffect, useState } from 'react';
import { ChevronRight, Copy, Download, Minus, Plus, X } from 'lucide-react';
import GlassPanel from './GlassPanel';
import GlassButton from './GlassButton';

export function StatusBadge({ ok, tone, children }) {
  const cls = tone || (ok === true ? 'bg-success' : ok === false ? 'bg-error' : 'bg-glass');
  return <span className={`status-badge ${cls}`}>{children}</span>;
}

export function MetricCard({ label, value, tone, hint }) {
  return (
    <div className="console-metric">
      <span>{label}</span>
      <strong className={tone ? `metric-${tone}` : ''}>{value ?? '—'}</strong>
      {hint ? <small>{hint}</small> : null}
    </div>
  );
}

export function Field({ label, hint, children, wide = false }) {
  return (
    <label className={`console-field ${wide ? 'console-field-wide' : ''}`}>
      <span>{label}</span>
      {children}
      {hint ? <small>{hint}</small> : null}
    </label>
  );
}

export function Toggle({ checked, onChange, label, hint, disabled = false }) {
  return (
    <label className={`console-toggle ${disabled ? 'disabled' : ''}`}>
      <input type="checkbox" checked={!!checked} onChange={(event) => onChange(event.target.checked)} disabled={disabled} />
      <span>
        <b>{label}</b>
        {hint ? <small>{hint}</small> : null}
      </span>
    </label>
  );
}

export function CompactNumberInput({ value, onChange, min = 0, max, disabled = false, ariaLabel }) {
  const number = Number(value || 0);
  const change = (next) => {
    const bounded = Math.max(min, max === undefined ? next : Math.min(max, next));
    onChange(bounded);
  };
  return (
    <div className={`compact-number ${disabled ? 'disabled' : ''}`}>
      <button type="button" onClick={() => change(number - 1)} disabled={disabled || number <= min} aria-label={`${ariaLabel || '数值'}减一`}><Minus size={13} /></button>
      <input type="number" value={value} min={min} max={max} disabled={disabled} onChange={(event) => change(Number(event.target.value))} aria-label={ariaLabel} />
      <button type="button" onClick={() => change(number + 1)} disabled={disabled || (max !== undefined && number >= max)} aria-label={`${ariaLabel || '数值'}加一`}><Plus size={13} /></button>
    </div>
  );
}

export function CollapsiblePanel({ title, summary, children, defaultOpen = false, actions = null, storageKey = '' }) {
  const [open, setOpen] = useState(() => {
    if (!storageKey || typeof window === 'undefined') return defaultOpen;
    try {
      const stored = window.localStorage.getItem(`automyai.panel.${storageKey}`);
      return stored === null ? defaultOpen : stored === 'open';
    } catch (_) {
      return defaultOpen;
    }
  });
  useEffect(() => {
    if (!storageKey) return;
    try { window.localStorage.setItem(`automyai.panel.${storageKey}`, open ? 'open' : 'closed'); } catch (_) {}
  }, [open, storageKey]);
  return (
    <GlassPanel className="console-section">
      <div className="console-section-header">
        <button type="button" className="console-section-toggle" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
          <ChevronRight className={`console-section-chevron ${open ? 'open' : ''}`} size={18} />
          <span><b>{title}</b>{summary ? <small>{summary}</small> : null}</span>
        </button>
        {actions ? <div className="console-actions">{actions}</div> : null}
      </div>
      <div className={`console-section-content ${open ? 'open' : ''}`} aria-hidden={!open}>
        <div className="console-section-body">{children}</div>
      </div>
    </GlassPanel>
  );
}

export function DataTable({ columns, rows, rowKey, empty = '暂无数据' }) {
  const mobileBatchSize = 12;
  const [mobile, setMobile] = useState(() => typeof window !== 'undefined' && window.matchMedia('(max-width: 760px)').matches);
  const [visibleCount, setVisibleCount] = useState(mobileBatchSize);

  useEffect(() => {
    const media = window.matchMedia('(max-width: 760px)');
    const sync = () => setMobile(media.matches);
    sync();
    media.addEventListener?.('change', sync);
    return () => media.removeEventListener?.('change', sync);
  }, []);

  useEffect(() => setVisibleCount(mobileBatchSize), [rows.length]);
  const visibleRows = mobile ? rows.slice(0, visibleCount) : rows;

  return (
    <div className="console-table-shell">
      <div className="console-table-wrap">
        <table className="console-table">
          <thead><tr>{columns.map((column) => <th data-column={column.key} key={column.key}>{column.label}</th>)}</tr></thead>
          <tbody>
            {visibleRows.length ? visibleRows.map((row, index) => (
              <tr key={rowKey ? rowKey(row, index) : index}>
                {columns.map((column) => <td data-column={column.key} key={column.key}>{column.render ? column.render(row, index) : (row[column.key] ?? '—')}</td>)}
              </tr>
            )) : <tr><td colSpan={columns.length} className="console-empty">{empty}</td></tr>}
          </tbody>
        </table>
      </div>
      {mobile && rows.length > mobileBatchSize ? (
        <div className="console-table-more">
          <span>已显示 {Math.min(visibleCount, rows.length)} / {rows.length}</span>
          <div className="console-actions">
            {visibleCount > mobileBatchSize ? <GlassButton variant="glass" onClick={() => setVisibleCount(mobileBatchSize)}>收起</GlassButton> : null}
            {visibleCount < rows.length ? <GlassButton variant="glass" onClick={() => setVisibleCount((count) => Math.min(rows.length, count + mobileBatchSize))}>再显示 {Math.min(mobileBatchSize, rows.length - visibleCount)} 条</GlassButton> : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function OutputBox({ value, title = '输出', filename = 'automyai-output.json', onClear }) {
  const text = typeof value === 'string' ? value : JSON.stringify(value ?? '', null, 2);
  const copy = async () => navigator.clipboard.writeText(text || '');
  const download = () => {
    const blob = new Blob([text || ''], { type: 'application/json;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(url);
  };
  return (
    <div className="console-output">
      <div className="console-output-head">
        <b>{title}</b>
        <div className="console-actions">
          <GlassButton variant="icon" onClick={copy} title="复制"><Copy size={16} /></GlassButton>
          <GlassButton variant="icon" onClick={download} title="下载"><Download size={16} /></GlassButton>
          {onClear ? <GlassButton variant="icon" onClick={onClear} title="清空"><X size={16} /></GlassButton> : null}
        </div>
      </div>
      <pre className="log-viewer console-output-body">{text || '暂无输出'}</pre>
    </div>
  );
}

export function ErrorBanner({ error, onRetry }) {
  if (!error) return null;
  return (
    <div className="console-error">
      <span>{String(error.message || error)}</span>
      {onRetry ? <GlassButton variant="glass" onClick={onRetry}>重试</GlassButton> : null}
    </div>
  );
}
