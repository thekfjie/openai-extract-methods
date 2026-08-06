import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { LoaderCircle, RefreshCw, Search, UsersRound, X } from 'lucide-react';
import apiClient from '../../api/client';
import GlassButton from '../../ui/GlassButton';
import CustomSelect from '../../ui/CustomSelect';

const COLOR_OPTIONS = [
  { value: '', label: '全部颜色' },
  { value: 'red', label: '红' },
  { value: 'yellow', label: '黄' },
  { value: 'orange', label: '橙' },
  { value: 'green', label: '绿' },
  { value: 'blue', label: '蓝' },
  { value: 'purple', label: '紫' },
  { value: 'gray', label: '灰' },
  { value: 'none', label: '无颜色' },
];

function colorDotClass(color) {
  return color ? `mail-admin-color-dot color-${color}` : 'mail-admin-color-dot color-none';
}

export default function MailAdminAccountPicker({
  open,
  onClose,
  onImport,
  importing = false,
}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [accounts, setAccounts] = useState([]);
  const [summary, setSummary] = useState(null);
  const [selected, setSelected] = useState(() => new Set());
  const [query, setQuery] = useState('');
  const [markColor, setMarkColor] = useState('');
  const [markedOnly, setMarkedOnly] = useState(false);
  const [includeSold, setIncludeSold] = useState(false);

  const loadAccounts = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const params = new URLSearchParams();
      if (query.trim()) params.set('q', query.trim());
      if (markedOnly) params.set('markedOnly', '1');
      if (!includeSold) params.set('includeSold', '0');
      if (markColor && markColor !== 'none') params.set('markColor', markColor);
      params.set('limit', '300');
      const payload = await apiClient.get(`/mail-admin/free-accounts?${params.toString()}`);
      let rows = Array.isArray(payload?.accounts) ? payload.accounts : [];
      if (markColor === 'none') rows = rows.filter((item) => !item.markColor);
      setAccounts(rows);
      setSummary(payload || null);
      setSelected((current) => {
        const valid = new Set(rows.map((item) => item.id));
        return new Set([...current].filter((id) => valid.has(id)));
      });
    } catch (err) {
      setAccounts([]);
      setSummary(null);
      setError(err?.message || '读取 Mail Admin 失败');
    } finally {
      setLoading(false);
    }
  }, [includeSold, markColor, markedOnly, query]);

  useEffect(() => {
    if (!open) return undefined;
    loadAccounts();
    return undefined;
  }, [open, loadAccounts]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event) => {
      if (event.key === 'Escape' && !importing) onClose?.();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [importing, onClose, open]);

  const selectedAccounts = useMemo(
    () => accounts.filter((item) => selected.has(item.id)),
    [accounts, selected],
  );

  const toggle = (id) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectAllVisible = () => {
    setSelected(new Set(accounts.map((item) => item.id)));
  };

  const clearSelected = () => setSelected(new Set());

  const handleImport = async () => {
    if (!selectedAccounts.length || importing) return;
    await onImport?.(selectedAccounts.map((item) => item.id), selectedAccounts);
  };

  if (!open) return null;

  return (
    <div className="mail-admin-picker-backdrop" role="presentation" onClick={() => !importing && onClose?.()}>
      <div
        className="mail-admin-picker-panel glass-panel glass-panel-strong"
        role="dialog"
        aria-modal="true"
        aria-label="从 Mail Admin 选择 Free 未开通账号"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="mail-admin-picker-head">
          <div>
            <b><UsersRound size={16} /> Mail Admin Free 未开通</b>
            <small>
              {summary
                ? `共 ${summary.total || 0} · 有 session ${summary.withCredential || 0} · 有颜色 ${summary.marked || 0}`
                : '读取 cloud.opus 未开通 Free 账号；优先展示有颜色标记（如红/c选）'}
            </small>
          </div>
          <button type="button" className="btn-icon" onClick={onClose} disabled={importing} aria-label="关闭">
            <X size={16} />
          </button>
        </div>

        <div className="mail-admin-picker-toolbar">
          <label className="mail-admin-picker-search">
            <Search size={14} />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索邮箱 / 颜色"
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault();
                  loadAccounts();
                }
              }}
            />
          </label>
          <div className="mail-admin-picker-color-select">
            <CustomSelect
              value={markColor}
              onChange={setMarkColor}
              options={COLOR_OPTIONS}
              ariaLabel="颜色筛选"
            />
          </div>
          <label className="mail-admin-picker-check">
            <input type="checkbox" checked={markedOnly} onChange={(event) => setMarkedOnly(event.target.checked)} />
            只看有颜色
          </label>
          <label className="mail-admin-picker-check">
            <input type="checkbox" checked={includeSold} onChange={(event) => setIncludeSold(event.target.checked)} />
            含已售
          </label>
          <GlassButton variant="glass" icon={loading ? LoaderCircle : RefreshCw} loading={loading} onClick={loadAccounts}>
            刷新
          </GlassButton>
        </div>

        <div className="mail-admin-picker-actions-row">
          <button type="button" onClick={selectAllVisible} disabled={!accounts.length}>全选当前</button>
          <button type="button" onClick={clearSelected} disabled={!selected.size}>清空选择</button>
          <span>已选 {selected.size} 个</span>
        </div>

        <div className="mail-admin-picker-list" role="list">
          {loading ? (
            <div className="mail-admin-picker-empty"><LoaderCircle size={16} className="spin" /> 正在读取 Mail Admin…</div>
          ) : error ? (
            <div className="mail-admin-picker-empty error">{error}</div>
          ) : accounts.length ? accounts.map((account) => {
            const checked = selected.has(account.id);
            return (
              <label
                key={account.id}
                className={`mail-admin-picker-item${checked ? ' active' : ''}${account.hasCredential ? '' : ' missing'}`}
                role="listitem"
              >
                <input type="checkbox" checked={checked} onChange={() => toggle(account.id)} />
                <span className={colorDotClass(account.markColor)} title={account.markColorLabel || '无颜色'} />
                <span className="mail-admin-picker-meta">
                  <b>{account.email || account.id}</b>
                  <small>
                    {account.freeLabel || 'Free 未开通'}
                    {account.markColorLabel ? ` · ${account.markColorLabel}` : ' · 无颜色'}
                    {account.sold ? ' · 已售' : ''}
                    {account.hasCredential ? ' · 有 session' : ' · 无 session'}
                  </small>
                </span>
              </label>
            );
          }) : (
            <div className="mail-admin-picker-empty">没有符合条件的 Free 未开通账号</div>
          )}
        </div>

        <div className="mail-admin-picker-foot">
          <small>导入后写入当前标签页“账号凭证”，不会改 Mail Admin，也不会写入任务历史。</small>
          <div className="mail-admin-picker-foot-actions">
            <GlassButton variant="glass" onClick={onClose} disabled={importing}>取消</GlassButton>
            <GlassButton
              variant="primary"
              icon={UsersRound}
              loading={importing}
              disabled={!selected.size || importing}
              onClick={handleImport}
            >
              导入已选 session
            </GlassButton>
          </div>
        </div>
      </div>
    </div>
  );
}
