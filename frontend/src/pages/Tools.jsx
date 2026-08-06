import React, { useCallback, useEffect, useState } from 'react';
import { RefreshCw, ShieldCheck } from 'lucide-react';
import GlassPanel from '../ui/GlassPanel';
import GlassButton from '../ui/GlassButton';
import apiClient from '../api/client';
import { CollapsiblePanel, DataTable, ErrorBanner, MetricCard, OutputBox, StatusBadge } from '../ui/ConsolePrimitives';
import useNavigationSub from '../hooks/useNavigationSub';
import TestProfileGenerator from '../components/tools/TestProfileGenerator';
import OutlookRegisterConsole from '../components/tools/OutlookRegisterConsole';

export default function Tools() {
  const { activeSub: activeTab, activeItem } = useNavigationSub('/tools');
  const [extensions, setExtensions] = useState(null);
  const [grokResults, setGrokResults] = useState([]);
  const [cpa, setCpa] = useState(null);
  const [sub2, setSub2] = useState(null);
  const [sub2Groups, setSub2Groups] = useState([]);
  const [compliance, setCompliance] = useState(null);
  const [output, setOutput] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const localProfileTab = activeTab === 'test_profiles' || activeTab === 'outlook_register';

  const refresh = useCallback(async () => {
    setError(null);
    // Tab-scoped fetches only. Loading Tools used to hit Sub2API groups/compliance
    // on every visit even when the user stayed on CPA/Grok tabs.
    const tasks = [apiClient.get('/extensions/status')];
    const names = ['extensions'];
    if (activeTab === 'grok_results') {
      tasks.push(apiClient.get('/grok/results'));
      names.push('grok');
    }
    if (activeTab === 'cpa_monitor') {
      tasks.push(apiClient.get('/cpa/monitor/status'));
      names.push('cpa');
    }
    if (activeTab === 'sub2api_monitor') {
      tasks.push(
        apiClient.get('/sub2api/monitor/status'),
        apiClient.get('/sub2api/groups'),
        apiClient.get('/sub2api/compliance'),
      );
      names.push('sub2', 'sub2Groups', 'compliance');
    }
    const results = await Promise.allSettled(tasks);
    const byName = Object.fromEntries(names.map((name, index) => [name, results[index]]));
    if (byName.extensions?.status === 'fulfilled') setExtensions(byName.extensions.value);
    if (byName.grok?.status === 'fulfilled') setGrokResults(byName.grok.value?.results || byName.grok.value?.items || []);
    if (byName.cpa?.status === 'fulfilled') setCpa(byName.cpa.value);
    if (byName.sub2?.status === 'fulfilled') setSub2(byName.sub2.value);
    if (byName.sub2Groups?.status === 'fulfilled') setSub2Groups(byName.sub2Groups.value?.groups || byName.sub2Groups.value?.items || []);
    if (byName.compliance?.status === 'fulfilled') setCompliance(byName.compliance.value);
    const rejected = results.find((item) => item.status === 'rejected');
    if (rejected) setError(rejected.reason);
  }, [activeTab]);
  useEffect(() => {
    if (localProfileTab) {
      setError(null);
      return;
    }
    refresh();
  }, [localProfileTab, refresh]);

  const act = async (task) => {
    setBusy(true); setError(null);
    try { const result = await task(); setOutput(result); await refresh(); }
    catch (reason) { setError(reason); } finally { setBusy(false); }
  };

  const groupColumns = [
    { key: 'name', label: '分组', render: (item) => item.name || item.groupName || item.id },
    { key: 'total', label: '账号数', render: (item) => item.total ?? item.account_count ?? '—' },
    { key: 'usable', label: '可用', render: (item) => item.usable ?? item.okAccounts ?? '—' },
    { key: 'status', label: '状态', render: (item) => <StatusBadge ok={(item.usable || item.okAccounts || 0) > 0}>{item.status || '—'}</StatusBadge> },
  ];

  const cpaHealth = cpa?.groupHealth || cpa?.monitor?.groupHealth || cpa || {};
  const sub2Health = sub2?.groupHealth || sub2?.monitor?.groupHealth || sub2 || {};

  return <div className="page-container operations-page">
    <div className="page-header"><div className="page-title-group"><h1>{activeItem?.label || '工具与探针'}</h1></div>{!localProfileTab ? <GlassButton variant="glass" icon={RefreshCw} onClick={refresh}>刷新</GlassButton> : null}</div>
    {!localProfileTab ? <ErrorBanner error={error} onRetry={refresh} /> : null}
    <div className="engine-view">

    {activeTab === 'test_profiles' && <TestProfileGenerator />}

    {activeTab === 'outlook_register' && <OutlookRegisterConsole />}

    {activeTab === 'cpa_monitor' && <div className="operations-stack">
      <GlassPanel style={{ padding: '1.25rem' }}><div className="console-toolbar"><h3>CLIProxyAPI（CPA）状态</h3><GlassButton variant="primary" disabled={busy} onClick={() => act(() => apiClient.post('/cpa/monitor/check', { trigger: true }))}>执行真实探针</GlassButton></div><div className="console-metrics" style={{ marginTop: '1rem' }}><MetricCard label="状态" value={cpaHealth.status || (cpa?.ok ? '正常' : '待检查')} tone={cpa?.ok ? 'success' : ''} /><MetricCard label="账号数" value={cpa?.accountCount ?? cpaHealth.totalAccounts ?? '—'} /><MetricCard label="活跃代理" value={cpa?.activeProxies ?? '—'} /><MetricCard label="本地服务" value={extensions?.cpa?.localReady ? '就绪' : '未就绪'} /></div></GlassPanel>
      <CollapsiblePanel title="CPA 完整状态" summary="原始监控响应；排查异常时展开"><OutputBox value={cpa || '暂无 CPA 状态'} title="CPA 完整状态" filename="cpa-status.json" /></CollapsiblePanel>
    </div>}

    {activeTab === 'sub2api_monitor' && <div className="operations-stack">
      <GlassPanel style={{ padding: '1.25rem' }}><div className="console-toolbar"><h3>Sub2API 状态、分组与授权工具</h3><div className="console-actions"><GlassButton variant="primary" icon={ShieldCheck} disabled={busy} onClick={() => act(() => apiClient.post('/sub2api/monitor/check', {}))}>检查监控分组</GlassButton><GlassButton variant="glass" disabled={busy} onClick={() => act(() => apiClient.get('/sub2api/openai-auth-url'))}>生成 OpenAI 授权链接</GlassButton></div></div><div className="console-metrics" style={{ marginTop: '1rem' }}><MetricCard label="监控状态" value={sub2Health.status || (sub2?.ok ? '正常' : '待检查')} /><MetricCard label="健康账号" value={`${sub2Health.okAccounts ?? '—'}/${sub2Health.minOkAccounts ?? '—'}`} /><MetricCard label="合规" value={compliance?.ok ? '通过' : (compliance?.status || '待检查')} /><MetricCard label="分组数" value={sub2Groups.length} /></div></GlassPanel>
      <GlassPanel style={{ padding: '1.25rem' }}><DataTable columns={groupColumns} rows={sub2Groups} rowKey={(item, index) => item.id || item.name || index} empty="暂无 Sub2API 分组" /></GlassPanel>
      <CollapsiblePanel title="监控与合规原始详情" summary="保留完整后端响应，默认不占用首屏"><div className="console-grid-wide"><OutputBox value={sub2 || '暂无监控数据'} title="监控详情" filename="sub2api-monitor.json" /><OutputBox value={compliance || '暂无合规数据'} title="合规详情" filename="sub2api-compliance.json" /></div></CollapsiblePanel>
    </div>}

    {activeTab === 'grok_results' && <GlassPanel style={{ padding: '1.25rem' }}><OutputBox value={grokResults.length ? grokResults : '暂无 Grok 结果'} title="Grok 注册与转换历史" filename="grok-results.json" /></GlassPanel>}

    {!localProfileTab && output ? <CollapsiblePanel title="最近操作结果" summary="刚才执行探针返回的原始数据" defaultOpen><OutputBox value={output} title="最近操作结果" filename="tools-output.json" onClear={() => setOutput(null)} /></CollapsiblePanel> : null}
    </div>
  </div>;
}
