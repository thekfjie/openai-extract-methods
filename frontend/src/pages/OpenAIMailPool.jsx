import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ExternalLink, Inbox, Plus, RefreshCw } from 'lucide-react';
import apiClient from '../api/client';
import GlassButton from '../ui/GlassButton';
import GlassPanel from '../ui/GlassPanel';
import { DataTable, ErrorBanner, MetricCard, StatusBadge } from '../ui/ConsolePrimitives';

const initialEmails = '';

export default function OpenAIMailPool() {
  const [emailsText, setEmailsText] = useState(initialEmails);
  const [pool, setPool] = useState({ accounts: [], total: 0 });
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      setPool(await apiClient.get('/mail-admin/openai-signup-pool?limit=1000'));
    } catch (reason) {
      setError(reason);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const inputCount = useMemo(() => new Set(
    emailsText.split(/[\s,;]+/).map((item) => item.trim().toLowerCase()).filter(Boolean),
  ).size, [emailsText]);

  const importEmails = async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const payload = await apiClient.post('/mail-admin/openai-signup-pool/import', { emailsText });
      setResult(payload);
      setEmailsText('');
      await refresh();
    } catch (reason) {
      setError(reason);
    } finally {
      setBusy(false);
    }
  };

  const columns = [
    { key: 'email', label: '邮箱', render: (item) => <span className="console-code">{item.email}</span> },
    { key: 'status', label: '状态', render: () => <StatusBadge ok>待注册</StatusBadge> },
    { key: 'mail', label: '接码', render: (item) => <StatusBadge ok={item.mailReadable}>{item.mailReadable ? 'Mail Opus 可拉信' : '不可拉信'}</StatusBadge> },
    { key: 'token', label: 'OAuth', render: (item) => <StatusBadge ok={item.hasAccessToken}>{item.hasAccessToken ? '已有 AT' : '暂无 AT'}</StatusBadge> },
    { key: 'updated', label: '更新', render: (item) => item.updatedAt || item.createdAt || '—' },
  ];

  return (
    <div className="page-container operations-page">
      <div className="page-header">
        <div className="page-title-group">
          <h1>OpenAI 邮箱池</h1>
          <p>录入暂无 AT 的 iCloud 邮箱；注册成功后同一 Mail Opus 记录会补齐 OAuth Token，并自动离开待注册池。</p>
        </div>
        <div className="console-actions">
          <GlassButton variant="glass" icon={RefreshCw} disabled={busy} onClick={refresh}>刷新</GlassButton>
          <GlassButton variant="glass" icon={ExternalLink} onClick={() => { window.location.href = '/ui/openai?sub=openai4'; }}>打开 OpenAI4</GlassButton>
        </div>
      </div>

      <ErrorBanner error={error} onRetry={refresh} />

      <div className="operations-stack">
        <div className="console-metrics">
          <MetricCard label="待注册邮箱" value={pool.total || pool.accounts?.length || 0} />
          <MetricCard label="本次待导入" value={inputCount} />
          <MetricCard label="来源" value="Mail Opus" />
        </div>

        <GlassPanel style={{ padding: '1.25rem' }}>
          <div className="console-toolbar">
            <div><h3><Inbox size={18} style={{ verticalAlign: 'middle' }} /> 批量录入暂无 AT 邮箱</h3><small>每行一个，也支持空格、逗号或分号分隔；重复邮箱会自动跳过。</small></div>
            <GlassButton variant="primary" icon={Plus} disabled={busy || inputCount === 0} onClick={importEmails}>导入待注册池</GlassButton>
          </div>
          <textarea
            className="input-glass"
            rows="12"
            value={emailsText}
            onChange={(event) => setEmailsText(event.target.value)}
            placeholder="example@icloud.com"
            style={{ marginTop: '1rem', width: '100%' }}
          />
          {result ? <p style={{ marginTop: '.8rem' }}>导入 {result.importedCount || 0} 个，跳过 {result.skippedCount || 0} 个，失败 {result.failedCount || 0} 个。</p> : null}
        </GlassPanel>

        <GlassPanel style={{ padding: '1.25rem' }}>
          <div className="console-toolbar"><h3>Mail Opus 待注册</h3><StatusBadge>{pool.total || 0} 个</StatusBadge></div>
          <DataTable columns={columns} rows={pool.accounts || []} rowKey={(item, index) => item.id || item.email || index} empty="当前没有待注册邮箱" />
        </GlassPanel>
      </div>
    </div>
  );
}
