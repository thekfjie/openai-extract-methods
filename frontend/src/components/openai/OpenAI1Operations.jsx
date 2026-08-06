import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Activity, ArrowDown, BarChart3, ChevronDown, ExternalLink, Inbox, ListChecks, RefreshCw, Search, ShieldCheck, Smartphone } from 'lucide-react';
import GlassPanel from '../../ui/GlassPanel';
import GlassButton from '../../ui/GlassButton';
import Skeleton from '../../ui/Skeleton';
import CustomSelect from '../../ui/CustomSelect';
import apiClient from '../../api/client';
import { DataTable, ErrorBanner, Field, OutputBox, StatusBadge } from '../../ui/ConsolePrimitives';

const stageGroups = [
  { label: '准备', steps: ['starting', 'preparing_proxy', 'preparing_email', 'preparing_browser', 'restoring_session'] },
  { label: '注册页', steps: ['opening_signup', 'accepting_cookie', 'filling_email', 'filling_email_code'] },
  { label: '账号资料', steps: ['filling_password', 'filling_account_details', 'signup_done'] },
  { label: '电话验证', steps: ['buying_phone', 'filling_phone', 'waiting_sms', 'filling_sms_code'] },
  { label: 'OAuth 邮箱', steps: ['oauth', 'authorizing', 'waiting_oauth_callback'] },
  { label: 'Sub2API', steps: ['sub2api_import', 'completed'] },
];

const stepLabels = {
  starting: '启动脚本',
  preparing_proxy: '准备注册代理',
  preparing_email: '准备邮箱/密码',
  preparing_browser: '启动浏览器/指纹',
  restoring_session: '恢复浏览器断点',
  opening_signup: '打开注册页',
  accepting_cookie: '处理 Cookie',
  filling_email: '填写注册邮箱',
  filling_email_code: '填写邮箱验证码',
  filling_password: '填写注册密码',
  filling_account_details: '填写姓名年龄',
  signup_done: '邮箱注册完成',
  buying_phone: '获取手机号',
  filling_phone: '填写手机号',
  waiting_sms: '等待短信验证码',
  filling_sms_code: '填写短信验证码',
  oauth: 'OAuth 授权流程',
  authorizing: '确认授权',
  waiting_oauth_callback: '等待 OAuth 回调',
  sub2api_import: '导入 Sub2API',
  completed: '完成',
};

const queueFrom = (payload) => payload?.emailQueue || payload || {};
const stringify = (value) => typeof value === 'string' ? value : JSON.stringify(value ?? {}, null, 2);
const displayLogTime = (value) => {
  const raw = String(value || '');
  const matched = raw.match(/(?:T|\s)(\d{2}:\d{2}:\d{2})/) || raw.match(/^(\d{2}:\d{2}:\d{2})/);
  return matched?.[1] || raw || '—';
};

function quotaText(quota = {}) {
  if (!Object.keys(quota).length) return '—';
  const total = `${quota.total || 0}/${quota.maxTotal || 0}`;
  const windowCount = `${quota.windowCount || 0}/${quota.maxPerWindow || 0}`;
  if (!quota.allowed && quota.reason === 'window_limit') return `窗口 ${windowCount}，冷却 ${quota.retryAfterSeconds || 0}s`;
  if (!quota.allowed && quota.reason === 'total_limit') return `累计 ${total}，已达上限`;
  return `累计 ${total}，窗口 ${windowCount}`;
}

function phoneLink(item = {}) {
  if (item.publicUrl || item.smsUrl) return item.publicUrl || item.smsUrl;
  const found = (item.links || []).find((entry) => entry?.publicUrl || entry?.smsUrl);
  return found?.publicUrl || found?.smsUrl || '';
}

function DenseMetric({ label, value, tone = '' }) {
  return <div className={`dense-metric ${tone ? `metric-${tone}` : ''}`}><span>{label}</span><b>{value ?? '—'}</b></div>;
}

function WorkbenchSkeleton() {
  return (
    <div className="openai-monitor-workspace" aria-label="正在加载 OpenAI1 工作台">
      <GlassPanel variant="strong" className="openai-monitor-panel">
        <div className="openai-status-strip">{Array.from({ length: 6 }, (_, index) => <Skeleton key={index} height="46px" borderRadius="8px" />)}</div>
        <Skeleton height="42px" borderRadius="10px" />
        <div className="openai-live-grid"><Skeleton height="100%" borderRadius="12px" /><Skeleton height="100%" borderRadius="12px" /></div>
      </GlassPanel>
      <Skeleton height="100%" borderRadius="16px" />
    </div>
  );
}

export default function OpenAI1Operations({ status = {}, config = {}, logs = [], onRefresh }) {
  const [queue, setQueue] = useState({ emails: [] });
  const [queueText, setQueueText] = useState('');
  const [currentEmail, setCurrentEmail] = useState('');
  const [inventory, setInventory] = useState(null);
  const [moveText, setMoveText] = useState('');
  const [moveTarget, setMoveTarget] = useState('pending');
  const [phones, setPhones] = useState({ items: [] });
  const [sub2Groups, setSub2Groups] = useState([]);
  const [sub2Monitor, setSub2Monitor] = useState(null);
  const [sub2Compliance, setSub2Compliance] = useState(null);
  const [health, setHealth] = useState(null);
  const [browser, setBrowser] = useState(null);
  const [trafficHistory, setTrafficHistory] = useState([]);
  const [output, setOutput] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const [supportTab, setSupportTab] = useState('results');
  const [supportOpen, setSupportOpen] = useState(false);
  const [followLogs, setFollowLogs] = useState(true);
  const polling = useRef(false);
  const logRef = useRef(null);
  const wasRunning = useRef(!!status.running);
  const supportTabRef = useRef(supportTab);
  const lastHeavySupportAt = useRef(0);

  const sourceGroup = config.mail_source_group || '默认分组';
  const heavySupportTabs = new Set(['mail', 'phones', 'sub2', 'traffic']);

  const applyQueue = useCallback((payload) => {
    const next = queueFrom(payload);
    const emails = Array.isArray(next.emails) ? next.emails : [];
    setQueue(next);
    setQueueText(emails.join('\n'));
    setCurrentEmail(next.activeEmail || emails[next.cursor || 0] || '');
  }, []);

  const applyInventory = useCallback((payload) => {
    setInventory(payload || null);
    const eligible = payload?.queueEligibleAccounts || payload?.sourceAccounts || [];
    setMoveText(eligible.map((account) => account.email || account.id).filter(Boolean).join('\n'));
  }, []);

  const refreshBrowser = useCallback(async () => {
    const info = await apiClient.get('/browser-live/status');
    setBrowser(info);
  }, []);

  const refreshSupport = useCallback(async ({ forceHeavy = false, includeBrowser = true } = {}) => {
    if (polling.current) return;
    polling.current = true;
    setError(null);
    try {
      const activeTab = supportTabRef.current || supportTab;
      const now = Date.now();
      // Sub2/Mail Admin heavy endpoints are on-demand or slow-polled only.
      // The old React port polled them every 3s while running and hammered Sub2API.
      const wantHeavy = forceHeavy || heavySupportTabs.has(activeTab);
      const heavyDue = wantHeavy && (forceHeavy || now - lastHeavySupportAt.current >= 30000);
      const tasks = [
        apiClient.get('/email-queue'),
        apiClient.get('/phones/pool?limit=200'),
        apiClient.get('/health'),
      ];
      const taskMeta = ['queue', 'phones', 'health'];
      if (heavyDue) {
        const encodedGroup = encodeURIComponent(sourceGroup);
        tasks.push(
          apiClient.get(`/outlook-email/inventory?sourceGroupName=${encodedGroup}`),
          apiClient.get('/sub2api/groups'),
          apiClient.get('/sub2api/monitor/status'),
          apiClient.get('/sub2api/compliance'),
          apiClient.openai4.get('/traffic?tail=30'),
        );
        taskMeta.push('inventory', 'sub2Groups', 'sub2Monitor', 'sub2Compliance', 'traffic');
      }
      if (includeBrowser) {
        tasks.push(refreshBrowser());
        taskMeta.push('browser');
      }
      const results = await Promise.allSettled(tasks);
      const byName = Object.fromEntries(taskMeta.map((name, index) => [name, results[index]]));
      if (byName.queue?.status === 'fulfilled') applyQueue(byName.queue.value);
      if (byName.inventory?.status === 'fulfilled') applyInventory(byName.inventory.value);
      if (byName.phones?.status === 'fulfilled') setPhones(byName.phones.value || { items: [] });
      if (byName.sub2Groups?.status === 'fulfilled') setSub2Groups(byName.sub2Groups.value?.groups || byName.sub2Groups.value?.items || []);
      if (byName.sub2Monitor?.status === 'fulfilled') setSub2Monitor(byName.sub2Monitor.value);
      if (byName.sub2Compliance?.status === 'fulfilled') setSub2Compliance(byName.sub2Compliance.value);
      if (byName.health?.status === 'fulfilled') setHealth(byName.health.value);
      if (byName.traffic?.status === 'fulfilled') setTrafficHistory(byName.traffic.value?.history || byName.traffic.value?.items || []);
      if (heavyDue) lastHeavySupportAt.current = now;
      const rejected = results.find((item) => item.status === 'rejected');
      if (rejected) setError(rejected.reason);
    } finally {
      polling.current = false;
      setInitialLoading(false);
    }
  }, [applyInventory, applyQueue, refreshBrowser, sourceGroup, supportTab]);

  useEffect(() => {
    supportTabRef.current = supportTab;
  }, [supportTab]);

  useEffect(() => {
    refreshSupport({ forceHeavy: true, includeBrowser: true }).catch(() => {});
    const browserTimer = setInterval(() => {
      refreshBrowser().catch(() => {});
    }, 3000);
    const lightTimer = setInterval(() => {
      if (!status.running) return;
      refreshSupport({ forceHeavy: false, includeBrowser: false }).catch(() => {});
    }, 8000);
    const heavyTimer = setInterval(() => {
      if (!status.running) return;
      if (!heavySupportTabs.has(supportTabRef.current || 'results')) return;
      refreshSupport({ forceHeavy: true, includeBrowser: false }).catch(() => {});
    }, 30000);
    return () => {
      clearInterval(browserTimer);
      clearInterval(lightTimer);
      clearInterval(heavyTimer);
    };
  }, [refreshBrowser, refreshSupport, status.running]);

  useEffect(() => {
    if (!heavySupportTabs.has(supportTab)) return;
    refreshSupport({ forceHeavy: true, includeBrowser: false }).catch(() => {});
  }, [refreshSupport, supportTab]);

  const runAction = async (name, task) => {
    setBusy(name);
    setError(null);
    try {
      const result = await task();
      if (result !== undefined) setOutput(result);
      await refreshSupport({ forceHeavy: true, includeBrowser: true });
      if (onRefresh) await onRefresh();
      return result;
    } catch (reason) {
      setError(reason);
      return null;
    } finally {
      setBusy('');
    }
  };

  const inventoryAccounts = inventory?.sourceAccountsAll || [];
  const eligibleCount = inventory?.sourceGroup?.queueEligible || inventory?.queueEligibleAccounts?.length || 0;
  const phoneItems = phones?.items || [];
  const results = Array.isArray(status.results) ? status.results : [];
  const browserTarget = browser?.target || {};
  const browserPage = browser?.page || {};
  const novncUrl = config.novnc_path || '/novnc/vnc.html?autoconnect=1&resize=scale&path=novnc/websockify';
  const monitorHealth = sub2Monitor?.groupHealth || sub2Monitor?.monitor?.groupHealth || {};
  const currentStep = status.current_step || status.currentStep || '';
  const activeStage = stageGroups.findIndex((stage) => stage.steps.includes(currentStep));
  const completeRun = currentStep === 'completed' || status.phase === 'completed';

  useEffect(() => {
    if (!followLogs || !logRef.current) return;
    logRef.current.scrollTo({ top: logRef.current.scrollHeight, behavior: status.running ? 'smooth' : 'auto' });
  }, [followLogs, logs.length, status.running]);

  useEffect(() => {
    if (wasRunning.current && !status.running) {
      setSupportTab('results');
      setSupportOpen(true);
    }
    wasRunning.current = !!status.running;
  }, [status.running]);

  const handleLogScroll = () => {
    const element = logRef.current;
    if (!element) return;
    setFollowLogs(element.scrollHeight - element.scrollTop - element.clientHeight < 36);
  };

  const accountColumns = useMemo(() => [
    { key: 'email', label: '邮箱', render: (item) => <span className="console-code">{item.email || item.id || '—'}</span> },
    { key: 'group', label: '分组', render: (item) => item.groupName || sourceGroup },
    { key: 'status', label: '状态', render: (item) => <StatusBadge ok={!!item.queueEligible}>{item.queueStatusLabel || item.queueSkipReason || '—'}</StatusBadge> },
    { key: 'decision', label: '队列判断', render: (item) => item.queueEligible ? '会导入队列' : (item.retryAfter ? `冷却到 ${item.retryAfter}` : '跳过') },
    { key: 'updated', label: '最近更新', render: (item) => item.updatedAt || '—' },
  ], [sourceGroup]);

  const phoneColumns = useMemo(() => [
    { key: 'phone', label: '号码', render: (item) => <span className="console-code">{item.phoneNumber || item.phoneKey || '—'}</span> },
    { key: 'status', label: '状态', render: (item) => <StatusBadge ok={!!item.reusable}>{item.statusLabel || item.status || (item.reusable ? '可复用' : '—')}</StatusBadge> },
    { key: 'lifecycle', label: '号码状态', render: (item) => item.lifecycleLabel || item.lifecycleStatus || '—' },
    { key: 'quota', label: '配额', render: (item) => quotaText(item.quota) },
    { key: 'cooldown', label: '冷却到期', render: (item) => item.cooldownUntil || '—' },
    { key: 'binding', label: '绑定', render: (item) => [item.binding?.email, item.binding?.region, item.binding?.proxyName || item.binding?.proxyUrl].filter(Boolean).join(' / ') || '—' },
    { key: 'link', label: '链接', render: (item) => phoneLink(item) ? <a href={phoneLink(item)} target="_blank" rel="noreferrer">打开</a> : '—' },
    { key: 'updated', label: '最近更新', render: (item) => item.updatedAt || item.purchasedAt || '—' },
  ], []);

  if (initialLoading) return <WorkbenchSkeleton />;

  const tabs = [
    { id: 'results', label: '本批结果', icon: ListChecks, badge: results.length },
    { id: 'mail', label: '邮箱与 Mail Admin', icon: Inbox, badge: eligibleCount },
    { id: 'phones', label: '号码池', icon: Smartphone, badge: phones?.total || phoneItems.length },
    { id: 'sub2', label: 'Sub2API', icon: ShieldCheck, badge: sub2Groups.length },
    { id: 'traffic', label: '流量历史', icon: BarChart3, badge: trafficHistory.length },
  ];

  return (
    <div className="openai-monitor-workspace">
      <ErrorBanner error={error} onRetry={() => refreshSupport({ forceHeavy: true, includeBrowser: true })} />

      <GlassPanel variant="strong" className="openai-monitor-panel">
        <div className="openai-monitor-heading">
          <div><h2>实时运行</h2><small>关键步骤、日志与浏览器画面保持在首屏</small></div>
          <div className="openai-monitor-actions">
            <span className={`openai-live-indicator ${status.running ? 'active' : ''}`}><i />{status.running ? '实时更新' : '待机'}</span>
            <GlassButton variant="icon" icon={RefreshCw} onClick={() => refreshSupport({ forceHeavy: true, includeBrowser: true })} title="刷新运行数据" />
          </div>
        </div>

        <div className={`openai-run-overview ${status.running ? 'active' : ''}`}>
          <div className="openai-current-step">
            <span><Activity size={14} />当前步骤</span>
            <strong>{stepLabels[currentStep] || currentStep || '等待任务启动'}</strong>
            <small>{status.running
              ? [status.current_email || status.currentEmail, status.current_phone || status.currentPhone].filter(Boolean).join(' · ') || '任务已启动，等待下一条状态'
              : '在左侧确认代理与账号，预检通过后即可开始'}</small>
          </div>
          <div className="openai-status-strip">
            <DenseMetric label="任务状态" value={status.running ? '运行中' : (status.phase || '空闲')} tone={status.running ? 'success' : ''} />
            <DenseMetric label="完成进度" value={`${status.completed || 0}/${status.total || 0}`} />
            <DenseMetric label="成功 / 失败" value={`${status.success || 0} / ${status.failed || 0}`} />
            <DenseMetric label="当前出口" value={status.current_proxy || status.currentProxy || config.resolved_proxy || '—'} />
          </div>
        </div>

        <div className="openai-stage-rail" aria-label="注册阶段">
          {stageGroups.map((stage, index) => {
            const state = completeRun || (activeStage >= 0 && index < activeStage) ? 'done' : index === activeStage ? 'active' : '';
            return <div key={stage.label} className={`openai-stage ${state}`}><i>{index + 1}</i><span>{stage.label}</span></div>;
          })}
        </div>

        <div className="openai-live-grid">
          <GlassPanel variant="inset" className="openai-live-card openai-log-card">
            <div className="openai-card-heading">
              <div><b>任务日志</b><small>自动跟随最新进度 · 最近 {logs.length || 0} 条</small></div>
              {!followLogs ? <GlassButton variant="glass" icon={ArrowDown} onClick={() => setFollowLogs(true)}>回到最新</GlassButton> : null}
            </div>
            <div ref={logRef} className="openai-live-log" role="log" aria-live="polite" onScroll={handleLogScroll}>
              {(logs || []).length ? (logs || []).map((entry, index) => {
                const level = String(entry?.level || 'info').toLowerCase();
                const message = typeof entry === 'string' ? entry : (entry?.message || stringify(entry));
                return <div className={`openai-log-line level-${level}`} key={`${entry?.time || ''}-${index}-${message.slice(0, 18)}`}><time title={entry?.time || ''}>{displayLogTime(entry?.time)}</time><em>{level.toUpperCase()}</em><span>{message}</span></div>;
              }) : <div className="openai-log-empty">暂无任务日志。启动任务后，这里会自动显示当前流程和每一步结果。</div>}
            </div>
          </GlassPanel>

          <GlassPanel variant="inset" className="openai-live-card openai-browser-card">
            <div className="openai-card-heading"><div><b>真实 noVNC</b><small>可直接点击与键盘操作 · 非截图预览</small></div><a href={novncUrl} target="_blank" rel="noreferrer"><GlassButton variant="glass" icon={ExternalLink}>新窗口打开</GlassButton></a></div>
            <div className="console-browser openai-browser-frame">
              <iframe src={novncUrl} title="OpenAI 注册真实 noVNC 桌面" allow="clipboard-read; clipboard-write" referrerPolicy="same-origin" />
            </div>
            <a className="openai-novnc-link console-code" href={novncUrl} target="_blank" rel="noreferrer">{novncUrl}</a>
            <div className="openai-browser-meta"><span><b>进程</b>{browser?.running ? browserTarget.pid || '—' : '未运行'}</span><span><b>页面</b>{browserPage.title || '—'}</span><span><b>Profile</b>{browserTarget.profile || '—'}</span><span><b>URL</b>{browserPage.url || '—'}</span></div>
          </GlassPanel>
        </div>
      </GlassPanel>

      <GlassPanel className={`openai-support-dock ${supportOpen ? 'open' : ''}`}>
        <button type="button" className="openai-support-toggle" aria-expanded={supportOpen} onClick={() => setSupportOpen((value) => !value)}>
          <span className="openai-support-title"><ListChecks size={16} /><span><b>运行数据与维护工具</b><small>结果、邮箱、号码池、Sub2API 与流量按需展开</small></span></span>
          <span className="openai-support-summary">{results.length} 结果 · {eligibleCount} 邮箱 · {phones?.total || phoneItems.length} 号码</span>
          <ChevronDown className="openai-support-chevron" size={18} />
        </button>
        <div className="openai-support-content" aria-hidden={!supportOpen}>
          <div className="openai-support-content-inner">
            <div className="openai-support-tabs" role="tablist">
              {tabs.map((tab) => { const Icon = tab.icon; return <button type="button" role="tab" aria-selected={supportTab === tab.id} key={tab.id} className={supportTab === tab.id ? 'active' : ''} onClick={() => setSupportTab(tab.id)}><Icon size={15} /><span>{tab.label}</span><em>{tab.badge}</em></button>; })}
            </div>
            <div className="openai-support-body" key={supportTab}>
          {supportTab === 'results' && <div className="openai-results-grid"><OutputBox value={results.length ? results : '暂无本批结果'} title="本批结果" filename="openai1-results.json" />{output ? <OutputBox value={stringify(output)} title="最近一次工具输出" filename="openai1-tool-output.json" onClear={() => setOutput('')} /> : <div className="openai-empty-note">工具操作结果会显示在这里</div>}</div>}

          {supportTab === 'mail' && <div className="openai-mail-tab">
            <div className="openai-mini-metrics"><DenseMetric label="总账号" value={`${inventory?.usable || 0}/${inventory?.total || 0}`} /><DenseMetric label="来源库存" value={`${inventory?.sourceGroup?.usable || 0}/${inventory?.sourceGroup?.total || 0}`} /><DenseMetric label="已取用" value={inventory?.sourceGroup?.claimed || 0} /><DenseMetric label="待授权" value={inventory?.sourceGroup?.registered || 0} /><DenseMetric label="冷却" value={inventory?.sourceGroup?.cooldown || 0} /></div>
            <div className="console-actions"><GlassButton variant="primary" disabled={!!busy} onClick={() => runAction('import', async () => { const result = await apiClient.post('/email-queue/import-outlook-source', { sourceGroupName: sourceGroup }); applyQueue(result); if (result.inventory) applyInventory(result.inventory); return result; })}>来源组导入队列</GlassButton><GlassButton variant="glass" disabled={!!busy} onClick={() => runAction('ensure', () => apiClient.post('/outlook-email/groups/ensure', {}))}>确保分组</GlassButton><GlassButton variant="glass" disabled={!!busy} onClick={() => runAction('mail', () => apiClient.get(`/email-queue/mail/latest${currentEmail ? `?address=${encodeURIComponent(currentEmail)}` : ''}`))} icon={Search}>查询验证码</GlassButton></div>
            <DataTable columns={accountColumns} rows={inventoryAccounts} rowKey={(item, index) => item.id || item.email || index} empty="来源组暂无邮箱" />
            <div className="openai-mail-editors"><Field label="待处理邮箱队列" hint={`当前 ${currentEmail || '—'} · 游标 ${(queue.cursor || 0) + 1}/${(queue.emails || []).length || 0}`}><textarea className="input-glass" rows="5" value={queueText} onChange={(event) => setQueueText(event.target.value)} /></Field><Field label="账号 ID 或邮箱" hint="用于批量移动 Mail Admin 分组"><textarea className="input-glass" rows="5" value={moveText} onChange={(event) => setMoveText(event.target.value)} /></Field></div>
            <div className="console-actions"><GlassButton variant="primary" disabled={!!busy} onClick={() => runAction('queue-save', async () => { const result = await apiClient.post('/email-queue', { emailsText: queueText }); applyQueue(result); return result; })}>保存队列</GlassButton><CustomSelect compact value={moveTarget} onChange={setMoveTarget} options={[{ value: 'pending', label: '待授权分组' }, { value: 'success', label: '成功分组' }, { value: 'bad', label: '坏邮箱分组' }]} ariaLabel="移动目标分组" /><GlassButton variant="glass" disabled={!!busy || !moveText.trim()} onClick={() => runAction('move', () => apiClient.post('/outlook-email/accounts/move', { target: moveTarget, identifiersText: moveText }))}>移动账号</GlassButton></div>
          </div>}

          {supportTab === 'phones' && <DataTable columns={phoneColumns} rows={phoneItems} rowKey={(item, index) => item.phoneKey || item.phoneNumber || index} empty="暂无号码记录" />}

          {supportTab === 'sub2' && <div className="openai-sub2-tab"><div className="openai-mini-metrics"><DenseMetric label="配置" value={health?.sub2apiConfigured ? '已配置' : '未配置'} tone={health?.sub2apiConfigured ? 'success' : 'warning'} /><DenseMetric label="监控状态" value={monitorHealth.status || sub2Monitor?.status || '—'} /><DenseMetric label="健康账号" value={`${monitorHealth.okAccounts ?? '—'}/${monitorHealth.minOkAccounts ?? '—'}`} /><DenseMetric label="合规检查" value={sub2Compliance?.ok ? '通过' : (sub2Compliance?.status || '待检查')} /></div><div className="console-checkbox-list">{sub2Groups.length ? sub2Groups.map((group, index) => <label key={group.id || group.name || index}><span>{group.name || group.groupName || group.id}</span><StatusBadge ok={(group.usable || group.okAccounts || 0) > 0}>{group.usable ?? group.account_count ?? group.total ?? '—'}</StatusBadge></label>) : <span style={{ color: 'var(--text-muted)' }}>暂无分组数据</span>}</div><div className="console-actions"><GlassButton variant="primary" icon={ShieldCheck} disabled={!!busy} onClick={() => runAction('sub2-check', () => apiClient.post('/sub2api/monitor/check', {}))}>检查监控分组</GlassButton><GlassButton variant="glass" disabled={!!busy} onClick={() => runAction('sub2-auth', () => apiClient.get('/sub2api/openai-auth-url'))}>生成授权链接</GlassButton><GlassButton variant="glass" disabled={!!busy} onClick={() => runAction('sub2-groups', () => apiClient.get('/sub2api/groups'))}>读取完整分组</GlassButton><GlassButton variant="glass" disabled={!!busy} onClick={() => runAction('sub2-compliance', () => apiClient.get('/sub2api/compliance'))}>检查授权状态</GlassButton></div></div>}

          {supportTab === 'traffic' && <OutputBox value={trafficHistory.length ? trafficHistory : '暂无流量记录'} title="OpenAI1 流量记录" filename="openai1-traffic.json" />}
            </div>
          </div>
        </div>
      </GlassPanel>
    </div>
  );
}
