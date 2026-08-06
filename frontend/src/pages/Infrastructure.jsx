import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Inbox, MoveRight, Plus, RefreshCw, Search } from 'lucide-react';
import GlassPanel from '../ui/GlassPanel';
import GlassButton from '../ui/GlassButton';
import CustomSelect from '../ui/CustomSelect';
import apiClient from '../api/client';
import { CollapsiblePanel, CompactNumberInput, DataTable, ErrorBanner, Field, MetricCard, OutputBox, StatusBadge, Toggle } from '../ui/ConsolePrimitives';
import useNavigationSub from '../hooks/useNavigationSub';

const queueValue = (payload) => payload?.emailQueue || payload || {};

export default function Infrastructure() {
  const { activeSub: activeTab, activeItem, redirecting } = useNavigationSub('/infrastructure');
  const [queue, setQueue] = useState({ emails: [] });
  const [queueText, setQueueText] = useState('');
  const [targetMailAddress, setTargetMailAddress] = useState('');
  const [latestMail, setLatestMail] = useState(null);
  const [platformUsage, setPlatformUsage] = useState(null);
  const [genPlatform, setGenPlatform] = useState('openai');
  const [genDomain, setGenDomain] = useState('');
  const [genCount, setGenCount] = useState(1);
  const [genPrefix, setGenPrefix] = useState('');
  const [preferInventory, setPreferInventory] = useState(true);
  const [inventory, setInventory] = useState(null);
  const [accounts, setAccounts] = useState([]);
  const [sourceGroup, setSourceGroup] = useState('默认分组');
  const [selectedAccounts, setSelectedAccounts] = useState([]);
  const [moveTarget, setMoveTarget] = useState('pending');
  const [phonePool, setPhonePool] = useState({ items: [] });
  const [countries, setCountries] = useState([]);
  const [countryQuery, setCountryQuery] = useState('');
  const [serviceCode, setServiceCode] = useState('ot');
  const [countryCode, setCountryCode] = useState('');
  const [operators, setOperators] = useState([]);
  const [catalogOutput, setCatalogOutput] = useState(null);
  const [output, setOutput] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const applyQueue = useCallback((payload) => {
    const next = queueValue(payload);
    const emails = Array.isArray(next.emails) ? next.emails : [];
    setQueue(next);
    setQueueText(emails.join('\n'));
    if (!targetMailAddress) setTargetMailAddress(next.activeEmail || emails[next.cursor || 0] || '');
  }, [targetMailAddress]);

  const refresh = useCallback(async () => {
    setError(null);
    const query = sourceGroup ? `?sourceGroupName=${encodeURIComponent(sourceGroup)}` : '';
    const results = await Promise.allSettled([
      apiClient.get('/email-queue'),
      apiClient.get(`/outlook-email/inventory${query}`),
      apiClient.get('/outlook-email/accounts'),
      apiClient.get('/email-queue/platform-usage'),
      apiClient.get('/phones/pool?limit=200'),
      apiClient.get(`/purchase-catalog/countries?limit=500${countryQuery ? `&query=${encodeURIComponent(countryQuery)}` : ''}`),
    ]);
    if (results[0].status === 'fulfilled') applyQueue(results[0].value);
    if (results[1].status === 'fulfilled') setInventory(results[1].value);
    if (results[2].status === 'fulfilled') setAccounts(results[2].value?.accounts || []);
    if (results[3].status === 'fulfilled') setPlatformUsage(results[3].value);
    if (results[4].status === 'fulfilled') setPhonePool(results[4].value || { items: [] });
    if (results[5].status === 'fulfilled') setCountries(results[5].value?.items || results[5].value?.countries || []);
    const rejected = results.find((item) => item.status === 'rejected');
    if (rejected) setError(rejected.reason);
  }, [applyQueue, countryQuery, sourceGroup]);

  useEffect(() => {
    if (!redirecting) refresh();
  }, [redirecting, refresh]);

  const act = async (task) => {
    setBusy(true); setError(null);
    try {
      const result = await task();
      setOutput(result);
      await refresh();
      return result;
    } catch (reason) {
      setError(reason);
      return null;
    } finally { setBusy(false); }
  };

  const inventoryGroups = useMemo(() => {
    const values = [
      inventory?.sourceGroup?.name,
      inventory?.pendingGroup?.name,
      inventory?.successGroup?.name,
      inventory?.badGroup?.name,
      ...(inventory?.groups || []).map((item) => item.name),
      ...accounts.map((item) => item.groupName || item.group),
    ].filter(Boolean);
    return [...new Set(['默认分组', ...values])];
  }, [accounts, inventory]);

  const visibleAccounts = inventory?.sourceAccountsAll || accounts.filter((item) => !sourceGroup || (item.groupName || item.group) === sourceGroup);
  const toggleAccount = (account) => {
    const key = String(account.id || account.email);
    setSelectedAccounts((current) => current.includes(key) ? current.filter((item) => item !== key) : [...current, key]);
  };
  const identifiersText = visibleAccounts.filter((item) => selectedAccounts.includes(String(item.id || item.email))).map((item) => item.email || item.id).join('\n');

  const accountColumns = [
    { key: 'select', label: '', render: (item) => <input type="checkbox" checked={selectedAccounts.includes(String(item.id || item.email))} onChange={() => toggleAccount(item)} /> },
    { key: 'email', label: '邮箱', render: (item) => <span className="console-code">{item.email || item.id}</span> },
    { key: 'group', label: '分组', render: (item) => item.groupName || item.group || sourceGroup },
    { key: 'status', label: '状态', render: (item) => <StatusBadge ok={!!item.queueEligible}>{item.queueStatusLabel || item.status || item.queueSkipReason || '—'}</StatusBadge> },
    { key: 'reason', label: '队列判断', render: (item) => item.queueEligible ? '可导入' : (item.retryAfter ? `冷却到 ${item.retryAfter}` : item.queueSkipReason || '跳过') },
    { key: 'updated', label: '更新', render: (item) => item.updatedAt || item.updated_at || '—' },
  ];

  const phoneColumns = [
    { key: 'phone', label: '号码', render: (item) => <span className="console-code">{item.phoneNumber || item.phoneKey || item.phone || '—'}</span> },
    { key: 'pool', label: '分层', render: (item) => item.poolScope === 'active_registration' ? (item.inventoryClass === 'used_many' ? '注册池 / 已接多次' : item.inventoryClass === 'unused' ? '注册池 / 未接码' : '注册池 / 已接码') : '历史留存' },
    { key: 'status', label: '状态', render: (item) => <StatusBadge ok={!!item.reusable}>{item.statusLabel || item.status || '—'}</StatusBadge> },
    { key: 'lifecycle', label: '号码状态', render: (item) => item.lifecycleLabel || item.lifecycleStatus || '—' },
    { key: 'codes', label: '成功接码', render: (item) => `${item.successCount || 0}/${item.quota?.maxTotal || 3}` },
    { key: 'cooldown', label: '冷却到期', render: (item) => item.cooldownUntil || '—' },
    { key: 'source', label: '来源', render: (item) => item.source || '—' },
    { key: 'quota', label: '配额', render: (item) => `${item.quota?.total || 0}/${item.quota?.maxTotal || 0}；窗口 ${item.quota?.windowCount || 0}/${item.quota?.maxPerWindow || 0}` },
    { key: 'binding', label: '绑定', render: (item) => [item.binding?.email, item.binding?.proxyName || item.binding?.proxyUrl].filter(Boolean).join(' / ') || '—' },
    { key: 'updated', label: '更新', render: (item) => item.updatedAt || item.purchasedAt || '—' },
  ];

  if (redirecting) return null;

  return (
    <div className="page-container operations-page">
      <div className="page-header">
        <div className="page-title-group"><h1>{activeItem?.label || '邮箱与手机基础设施'}</h1></div>
        <GlassButton variant="glass" onClick={refresh} icon={RefreshCw}>刷新数据</GlassButton>
      </div>
      <ErrorBanner error={error} onRetry={refresh} />
      <div className="engine-view">

      {activeTab === 'email_queue' && <div className="operations-stack">
        <GlassPanel style={{ padding: '1.25rem' }}>
          <h3 style={{ marginBottom: '1rem' }}>生成与分配邮箱</h3>
          <div className="console-grid">
            <Field label="平台"><CustomSelect value={genPlatform} onChange={setGenPlatform} options={[{ value: 'openai', label: 'OpenAI' }, { value: 'grok', label: 'Grok' }]} ariaLabel="生成邮箱平台" /></Field>
            <Field label="域名"><input className="input-glass" value={genDomain} onChange={(e) => setGenDomain(e.target.value)} placeholder="留空使用默认域名" /></Field>
            <Field label="数量"><CompactNumberInput min={1} max={1000} value={genCount} onChange={setGenCount} ariaLabel="邮箱生成数量" /></Field>
            <Field label="前缀"><input className="input-glass" value={genPrefix} onChange={(e) => setGenPrefix(e.target.value)} placeholder="留空随机" /></Field>
          </div>
          <div style={{ marginTop: '.8rem' }}><Toggle checked={preferInventory} onChange={setPreferInventory} label="优先使用已有库存" hint="库存不足时再走生成或外部来源" /></div>
          <div className="console-actions" style={{ marginTop: '1rem' }}>
            <GlassButton variant="primary" disabled={busy} icon={Plus} onClick={() => act(() => apiClient.post('/email-queue/allocate', { platform: genPlatform, preferInventory }))}>从主库分配</GlassButton>
            <GlassButton variant="glass" disabled={busy} onClick={() => act(() => apiClient.post('/email-queue/generate-domain', { domain: genDomain, count: genCount, prefix: genPrefix, preferSubdomain: true }))}>生成域名邮箱</GlassButton>
            <GlassButton variant="glass" disabled={busy} onClick={() => act(() => apiClient.post('/email-queue/import-outlook-source', { sourceGroupName: sourceGroup }))}>从 Mail Admin 导入</GlassButton>
          </div>
        </GlassPanel>
        <div className="console-grid-wide">
          <GlassPanel style={{ padding: '1.25rem' }}>
            <div className="console-toolbar"><h3>邮箱队列</h3><StatusBadge>{(queue.emails || []).length} 个</StatusBadge></div>
            <textarea className="input-glass" rows="12" value={queueText} onChange={(e) => setQueueText(e.target.value)} style={{ marginTop: '.8rem' }} />
            <div className="console-actions" style={{ marginTop: '.8rem' }}>
              <GlassButton variant="primary" disabled={busy} onClick={() => act(async () => { const result = await apiClient.post('/email-queue', { emailsText: queueText }); applyQueue(result); return result; })}>保存队列</GlassButton>
              <GlassButton variant="glass" disabled={busy} onClick={() => act(() => apiClient.post('/email-queue/generate', { platform: genPlatform, count: genCount }))}>生成队列</GlassButton>
            </div>
            <div className="console-metrics" style={{ marginTop: '.8rem' }}><MetricCard label="当前邮箱" value={queue.activeEmail || queue.emails?.[queue.cursor || 0] || '—'} /><MetricCard label="队列位置" value={`${(queue.cursor || 0) + 1}/${queue.emails?.length || 0}`} /></div>
          </GlassPanel>
          <GlassPanel style={{ padding: '1.25rem' }}>
            <h3 style={{ marginBottom: '.8rem' }}><Inbox size={18} style={{ verticalAlign: 'middle' }} /> 最新邮件与验证码</h3>
            <div className="console-actions"><input className="input-glass" value={targetMailAddress} onChange={(e) => setTargetMailAddress(e.target.value)} placeholder="邮箱地址" /><GlassButton variant="primary" icon={Search} disabled={!targetMailAddress.trim()} onClick={() => act(async () => { const result = await apiClient.get(`/email-queue/mail/latest?address=${encodeURIComponent(targetMailAddress)}`); setLatestMail(result); if (result.emailQueue) applyQueue(result); return result; })}>查询</GlassButton></div>
            <OutputBox value={latestMail || '暂无查询结果'} title="邮件结果" filename="latest-mail.json" />
          </GlassPanel>
        </div>
        <CollapsiblePanel title="邮箱平台使用情况" summary="原始统计与来源分布；需要排查时再展开"><OutputBox value={platformUsage || '暂无平台使用统计'} title="邮箱平台使用情况" filename="mail-platform-usage.json" /></CollapsiblePanel>
      </div>}

      {activeTab === 'outlook_groups' && <div className="operations-stack">
        <GlassPanel style={{ padding: '1.25rem' }}>
          <div className="console-toolbar"><h3>Mail Admin 库存和分组</h3><div className="console-actions"><GlassButton variant="glass" disabled={busy} onClick={() => act(() => apiClient.post('/outlook-email/groups/ensure', {}))}>确保分组</GlassButton><GlassButton variant="glass" disabled={busy} onClick={() => act(() => apiClient.post('/outlook-email/groups/replan', {}))}>重新规划</GlassButton></div></div>
          <div className="console-metrics" style={{ marginTop: '1rem' }}><MetricCard label="总账号" value={`${inventory?.usable || 0}/${inventory?.total || 0}`} /><MetricCard label="来源组" value={`${inventory?.sourceGroup?.usable || 0}/${inventory?.sourceGroup?.total || 0}`} /><MetricCard label="待授权" value={inventory?.pendingGroup?.total || 0} /><MetricCard label="成功" value={inventory?.successGroup?.total || 0} /><MetricCard label="坏邮箱" value={inventory?.badGroup?.total || 0} /></div>
          <div className="console-grid" style={{ marginTop: '1rem' }}><Field label="来源分组"><CustomSelect value={sourceGroup} onChange={(next) => { setSourceGroup(next); setSelectedAccounts([]); }} options={inventoryGroups.map((name) => ({ value: name, label: name }))} ariaLabel="来源分组" /></Field><Field label="移动目标"><CustomSelect value={moveTarget} onChange={setMoveTarget} options={[{ value: 'pending', label: '待授权' }, { value: 'success', label: '成功' }, { value: 'bad', label: '坏邮箱' }]} ariaLabel="移动目标" /></Field></div>
        </GlassPanel>
        <GlassPanel style={{ padding: '1.25rem' }}>
          <div className="console-toolbar"><span>已选择 {selectedAccounts.length} 个账号</span><div className="console-actions"><GlassButton variant="glass" onClick={() => setSelectedAccounts(visibleAccounts.map((item) => String(item.id || item.email)))}>全选当前页</GlassButton><GlassButton variant="primary" icon={MoveRight} disabled={busy || !identifiersText} onClick={() => act(() => apiClient.post('/outlook-email/accounts/move', { target: moveTarget, identifiersText }))}>移动所选账号</GlassButton></div></div>
          <DataTable columns={accountColumns} rows={visibleAccounts} rowKey={(item, index) => item.id || item.email || index} empty="该分组暂无账号" />
        </GlassPanel>
      </div>}

      {activeTab === 'phone_pool' && <div className="operations-stack">
        <GlassPanel style={{ padding: '1.25rem' }}><div className="console-toolbar"><h3>号码池</h3><StatusBadge>{phonePool.total || phonePool.items?.length || 0} 个号码</StatusBadge></div><DataTable columns={phoneColumns} rows={phonePool.items || []} rowKey={(item, index) => item.phoneKey || item.phoneNumber || index} empty="暂无号码记录" /></GlassPanel>
        <CollapsiblePanel title="购买目录与查询工具" summary="号码地区只用于购买检索，不参与注册代理匹配" actions={<GlassButton variant="glass" disabled={busy} onClick={() => act(() => apiClient.post('/purchase-catalog/countries/refresh', {}))}>刷新国家目录</GlassButton>}>
          <div className="console-grid"><Field label="国家搜索"><input className="input-glass" value={countryQuery} onChange={(e) => setCountryQuery(e.target.value)} placeholder="国家名或代码" /></Field><Field label="国家代码"><input className="input-glass" value={countryCode} onChange={(e) => setCountryCode(e.target.value)} /></Field><Field label="服务代码"><input className="input-glass" value={serviceCode} onChange={(e) => setServiceCode(e.target.value)} /></Field></div>
          <div className="console-actions" style={{ marginTop: '1rem' }}><GlassButton variant="glass" disabled={busy} onClick={() => act(async () => { const result = await apiClient.get(`/purchase-catalog/operators?serviceCode=${encodeURIComponent(serviceCode)}&countryCode=${encodeURIComponent(countryCode)}&refresh=false`); setOperators(result.items || result.operators || []); return result; })}>查询运营商</GlassButton><GlassButton variant="glass" disabled={busy} onClick={() => act(async () => { const result = await apiClient.get('/balance'); setCatalogOutput(result); return result; })}>查询余额</GlassButton><GlassButton variant="glass" disabled={busy} onClick={() => act(async () => { const result = await apiClient.get(`/pricing?service=${encodeURIComponent(serviceCode)}&country=${encodeURIComponent(countryCode)}`); setCatalogOutput(result); return result; })}>查询价格</GlassButton><GlassButton variant="glass" disabled={busy} onClick={() => act(async () => { const result = await apiClient.get('/catalog'); setCatalogOutput(result); return result; })}>读取完整目录</GlassButton></div>
          <div className="console-grid-wide" style={{ marginTop: '1rem' }}><OutputBox value={countries} title={`国家目录 (${countries.length})`} filename="sms-countries.json" /><OutputBox value={operators.length ? operators : catalogOutput || '暂无查询结果'} title="运营商/价格/余额结果" filename="sms-catalog-result.json" /></div>
        </CollapsiblePanel>
      </div>}

      {output ? <CollapsiblePanel title="最近操作结果" summary="刚才调用后端接口返回的原始数据" defaultOpen><OutputBox value={output} title="最近操作结果" filename="infrastructure-output.json" onClear={() => setOutput(null)} /></CollapsiblePanel> : null}
      </div>
    </div>
  );
}
