import React, { useState, useEffect, useRef, useCallback } from 'react';
import { RefreshCw, ExternalLink, SlidersHorizontal } from 'lucide-react';
import GlassPanel from '../ui/GlassPanel';
import GlassButton from '../ui/GlassButton';
import OpenAI1ControlPanel from '../components/openai/OpenAI1ControlPanel';
import OpenAI1Operations from '../components/openai/OpenAI1Operations';
import OpenAI3Workbench from '../components/openai/OpenAI3Workbench';
import OpenAI5Supervisor from '../components/openai/OpenAI5Supervisor';
import OpenAI6Workbench from '../components/openai/OpenAI6Workbench';
import OpenAI7Registration from '../components/openai/OpenAI7Registration';
import apiClient from '../api/client';
import { useToast } from '../contexts/ToastContext';
import { MetricCard } from '../ui/ConsolePrimitives';
import useNavigationSub from '../hooks/useNavigationSub';

const OPENAI4_PROXY_DRAFT_KEY = 'automyai.openai4.custom_proxy_url';

function readOpenAI4ProxyDraft() {
  try {
    return window.localStorage.getItem(OPENAI4_PROXY_DRAFT_KEY) || '';
  } catch (_) {
    return '';
  }
}

function toAccountId(value) {
  if (value === '' || value == null) return 0;
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

function normalizeOpenAI4StartPayload(config = {}) {
  const {
    concurrency,
    outlook_api_url,
    outlook_api_key,
    outlook_admin_password,
    resolved_proxy,
    resolved_proxy_mode,
    resolved_proxy_name,
    proxy_configured,
    engine,
    concurrency_fixed,
    ...rest
  } = config;
  return {
    ...rest,
    total: Math.max(1, Number(config.total) || 1),
    selected_account_id: toAccountId(config.selected_account_id),
    selected_account_email: config.selected_account_email || '',
    traffic_meter: !!config.traffic_meter,
    fingerprint_enabled: config.fingerprint_enabled !== false,
    fingerprint_strict: config.fingerprint_strict !== false,
    sub2api_import_use_signup_proxy: !!config.sub2api_import_use_signup_proxy,
    get_refresh_token: config.get_refresh_token !== false,
    auth_only: !!config.auth_only,
    manual_mode: false,
    keep_browser_on_failure: false,
  };
}

function normalizeOpenAI3StartPayload(config = {}) {
  const {
    concurrency,
    ...rest
  } = config;
  return {
    ...rest,
    total: Math.max(1, Number(config.total) || 1),
    selected_account_id: toAccountId(config.selected_account_id),
    selected_account_email: config.selected_account_email || '',
    selected_account_group: config.selected_account_group || '',
    traffic_meter: !!config.traffic_meter,
    fingerprint_enabled: config.fingerprint_enabled !== false,
    fingerprint_strict: config.fingerprint_strict !== false,
  };
}

export default function OpenAIAutomation() {
  const { notify } = useToast();
  const { activeSub: activeTab, activeItem } = useNavigationSub('/openai');

  const [loading, setLoading] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);

  // --- OpenAI 4 (UC Signup) State ---
  const [o4Status, setO4Status] = useState(null);
  const [o4Logs, setO4Logs] = useState([]);
  const [o4Traffic, setO4Traffic] = useState(null);
  const [o4Accounts, setO4Accounts] = useState([]);
  const [o4Groups, setO4Groups] = useState([]);
  const [o4MailGroups, setO4MailGroups] = useState([]);
  const [o4Sub2apiGroups, setO4Sub2apiGroups] = useState([]);
  const [o4Preflight, setO4Preflight] = useState(null);

  const [o4Config, setO4Config] = useState({
    concurrency: 1,
    total: 1,
    custom_proxy_url: readOpenAI4ProxyDraft(),
    fingerprint_enabled: true,
    fingerprint_source: 'local',
    fingerprint_seed: '',
    fingerprint_strict: true,
    mail_source_group: '默认分组',
    mail_pending_group: 'oai_pending',
    mail_success_group: 'oai_success',
    mail_bad_group: 'badmail',
    sub2api_group: 'auto',
    sub2api_import_use_signup_proxy: false,
    get_refresh_token: true,
    auth_only: false,
    manual_mode: false,
    keep_browser_on_failure: false,
    traffic_meter: false,
    forced_phone: '',
    selected_account_id: '',
    selected_account_email: ''
  });

  // --- OpenAI 3 State ---
  const [o3Status, setO3Status] = useState(null);
  const [o3Logs, setO3Logs] = useState([]);
  const [o3Traffic, setO3Traffic] = useState(null);
  const [o3Accounts, setO3Accounts] = useState([]);
  const [o3Groups, setO3Groups] = useState([]);
  const [o3Preflight, setO3Preflight] = useState(null);

  const [o3Config, setO3Config] = useState({
    concurrency: 1,
    total: 1,
    proxy: '',
    traffic_meter: false,
    mail_pass: '',
    sub2api_group: 'auto',
    fingerprint_enabled: true,
    fingerprint_source: 'local',
    fingerprint_seed: '',
    fingerprint_strict: true,
    mail_source_group: '默认分组',
    mail_pending_group: 'oai_pending',
    mail_success_group: 'oai_success',
    mail_bad_group: 'badmail',
    selected_account_id: '',
    selected_account_email: '',
    selected_account_group: ''
  });

  // --- OpenAI 2 State ---
  const [o2Health, setO2Health] = useState(null);

  // Prevent 4s status polling from wiping unsaved form edits.
  const o4ConfigDirtyRef = useRef(false);
  const o3ConfigDirtyRef = useRef(false);
  const o4ConfigHydratedRef = useRef(false);
  const o3ConfigHydratedRef = useRef(false);
  const o4LastSavedProxyRef = useRef('');
  const o4PreflightPayloadRef = useRef('');

  const updateO4Config = useCallback((patchOrUpdater) => {
    o4ConfigDirtyRef.current = true;
    setO4Preflight(null);
    o4PreflightPayloadRef.current = '';
    setO4Config((prev) => {
      const next = typeof patchOrUpdater === 'function' ? patchOrUpdater(prev) : { ...prev, ...patchOrUpdater };
      if (next.custom_proxy_url !== prev.custom_proxy_url) {
        try {
          window.localStorage.setItem(OPENAI4_PROXY_DRAFT_KEY, next.custom_proxy_url || '');
        } catch (_) {}
      }
      return next;
    });
  }, []);

  const updateO3Config = useCallback((patchOrUpdater) => {
    o3ConfigDirtyRef.current = true;
    setO3Config((prev) => (
      typeof patchOrUpdater === 'function' ? patchOrUpdater(prev) : { ...prev, ...patchOrUpdater }
    ));
  }, []);


  const applyStatusPayload = ({
    o4StatRes, o4LogRes, o4AccRes, o4CfgRes, o4TrafRes, o4Sub2GroupsRes,
    o3StatRes, o3LogRes, o3AccRes, o3CfgRes, o3TrafRes,
    o2Res,
  }) => {
    if (o4StatRes?.status === 'fulfilled') setO4Status(o4StatRes.value?.state || o4StatRes.value);
    if (o4LogRes?.status === 'fulfilled') setO4Logs(o4LogRes.value?.logs || []);
    if (o4AccRes?.status === 'fulfilled') {
      setO4Accounts(o4AccRes.value?.accounts || []);
      const g = o4AccRes.value?.groups || [];
      if (!g.some(x => x.name === '默认分组')) g.unshift({name: '默认分组'});
      setO4Groups(g);
      setO4MailGroups(o4AccRes.value?.mailGroups || g.filter((item) => item.name !== 'Mail Opus 待注册'));
    }
    if (o4Sub2GroupsRes?.status === 'fulfilled') setO4Sub2apiGroups(o4Sub2GroupsRes.value?.groups || []);
    if (o4CfgRes?.status === 'fulfilled' && o4CfgRes.value?.config) {
      if (!o4ConfigDirtyRef.current || !o4ConfigHydratedRef.current) {
        setO4Config(prev => {
          const serverConfig = o4CfgRes.value.config;
          const rememberedProxy = readOpenAI4ProxyDraft();
          const next = {
            ...prev,
            ...serverConfig,
            custom_proxy_url: serverConfig.custom_proxy_url || rememberedProxy || prev.custom_proxy_url || '',
          };
          if (next.custom_proxy_url) {
            try { window.localStorage.setItem(OPENAI4_PROXY_DRAFT_KEY, next.custom_proxy_url); } catch (_) {}
          }
          o4LastSavedProxyRef.current = next.custom_proxy_url || '';
          return next;
        });
        o4ConfigHydratedRef.current = true;
      }
    }
    if (o4TrafRes?.status === 'fulfilled') setO4Traffic(o4TrafRes.value?.current || null);
    if (o3StatRes?.status === 'fulfilled') setO3Status(o3StatRes.value?.state || o3StatRes.value);
    if (o3LogRes?.status === 'fulfilled') setO3Logs(o3LogRes.value?.logs || []);
    if (o3AccRes?.status === 'fulfilled') {
      setO3Accounts(o3AccRes.value?.accounts || []);
      const g = (o3AccRes.value?.groups || []).filter(x => !x.badmail && x.selectable > 0);
      if (!g.some(x => x.name === '默认分组')) g.unshift({name: '默认分组'});
      setO3Groups(g);
    }
    if (o3CfgRes?.status === 'fulfilled' && o3CfgRes.value?.config) {
      if (!o3ConfigDirtyRef.current || !o3ConfigHydratedRef.current) {
        setO3Config(prev => ({ ...prev, ...o3CfgRes.value.config }));
        o3ConfigHydratedRef.current = true;
      }
    }
    if (o3TrafRes?.status === 'fulfilled') setO3Traffic(o3TrafRes.value?.current || null);
    if (o2Res?.status === 'fulfilled') setO2Health(o2Res.value);
  };

  // Light poll keeps the live run view fresh without hammering Mail Admin / Sub2 inventory.
  const fetchLightStatus = async () => {
    try {
      const [o4StatRes, o4LogRes, o4TrafRes, o3StatRes, o3LogRes, o3TrafRes, o2Res] = await Promise.allSettled([
        apiClient.openai4.get('/status').catch(() => ({})),
        apiClient.openai4.get('/logs?tail=100').catch(() => ({})),
        apiClient.openai4.get('/traffic?tail=30').catch(() => ({})),
        apiClient.openai3.get('/status').catch(() => ({})),
        apiClient.openai3.get('/logs?tail=50').catch(() => ({})),
        apiClient.openai3.get('/traffic?tail=30').catch(() => ({})),
        apiClient.openai2.get('/health').catch(() => ({})),
      ]);
      applyStatusPayload({ o4StatRes, o4LogRes, o4TrafRes, o3StatRes, o3LogRes, o3TrafRes, o2Res });
    } catch (err) {
      console.error(err);
    } finally {
      setInitialLoading(false);
    }
  };

  // Heavy poll is for account selectors / config hydration only.
  const fetchHeavyStatus = async () => {
    try {
      const [o4AccRes, o4CfgRes, o4Sub2GroupsRes, o3AccRes, o3CfgRes] = await Promise.allSettled([
        apiClient.openai4.get('/accounts').catch(() => ({})),
        apiClient.openai4.get('/config').catch(() => ({})),
        apiClient.openai4.get('/sub2api-groups').catch(() => ({})),
        apiClient.get('/outlook-email/accounts').catch(() => ({})),
        apiClient.openai3.get('/config').catch(() => ({})),
      ]);
      applyStatusPayload({ o4AccRes, o4CfgRes, o4Sub2GroupsRes, o3AccRes, o3CfgRes });
    } catch (err) {
      console.error(err);
    }
  };

  const fetchAllStatus = async () => {
    await Promise.all([fetchLightStatus(), fetchHeavyStatus()]);
  };

  useEffect(() => {
    fetchAllStatus();
    const lightTimer = setInterval(fetchLightStatus, 4000);
    const heavyTimer = setInterval(fetchHeavyStatus, 30000);
    return () => {
      clearInterval(lightTimer);
      clearInterval(heavyTimer);
    };
  }, []);

  // Persist the registration proxy shortly after editing. The local draft
  // handles an immediate refresh; this backend save handles other browsers and
  // later sessions without requiring the user to remember the Save button.
  useEffect(() => {
    const proxy = o4Config.custom_proxy_url || '';
    if (!o4ConfigHydratedRef.current || !o4ConfigDirtyRef.current || !proxy.trim()) return undefined;
    if (proxy === o4LastSavedProxyRef.current) return undefined;
    const timer = window.setTimeout(async () => {
      try {
        await apiClient.openai4.post('/config', normalizeOpenAI4StartPayload(o4Config));
        o4LastSavedProxyRef.current = proxy;
      } catch (error) {
        console.error('OpenAI4 proxy autosave failed:', error);
      }
    }, 900);
    return () => window.clearTimeout(timer);
  }, [o4Config.custom_proxy_url]);

  // Handlers for OpenAI 4
  const handleSaveO4 = async () => {
    try {
      const res = await apiClient.openai4.post('/config', normalizeOpenAI4StartPayload(o4Config));
      if (res?.config) {
        setO4Config(prev => ({ ...prev, ...res.config }));
        try { window.localStorage.setItem(OPENAI4_PROXY_DRAFT_KEY, res.config.custom_proxy_url || o4Config.custom_proxy_url || ''); } catch (_) {}
      }
      o4ConfigDirtyRef.current = false;
      o4ConfigHydratedRef.current = true;
      notify('OpenAI 注册配置已保存', 'success');
    } catch(e) { notify(e.message, 'error', { title: '保存失败' }); }
  };
  const handlePreflightO4 = async () => {
    try {
      setLoading(true);
      const payload = normalizeOpenAI4StartPayload(o4Config);
      await apiClient.openai4.post('/config', payload);
      const res = await apiClient.openai4.post('/preflight', payload);
      setO4Preflight(res);
      o4PreflightPayloadRef.current = JSON.stringify(payload);
      notify(res.ok ? '启动前检查通过' : '请查看检查结果后修正配置', res.ok ? 'success' : 'warning', { title: res.ok ? '预检通过' : '预检未通过' });
    } catch (e) {
      notify(e.message, 'error', { title: 'Preflight 失败' });
    } finally { setLoading(false); }
  };
  const handleStartO4 = async () => {
    try {
      setLoading(true);
      const payload = normalizeOpenAI4StartPayload(o4Config);
      await apiClient.openai4.post('/config', payload);
      const payloadSignature = JSON.stringify(payload);
      const checked = o4Preflight?.ok && o4PreflightPayloadRef.current === payloadSignature
        ? o4Preflight
        : await apiClient.openai4.post('/preflight', payload);
      if (checked !== o4Preflight) setO4Preflight(checked);
      o4PreflightPayloadRef.current = payloadSignature;
      const selected = checked.mail?.accounts || [];
      const emailSummary = selected.map((item) => item.email).filter(Boolean).join('\n') || '自动分配';
      const proxySummary = checked.proxy?.proxy || checked.proxy?.name || checked.proxy?.mode || '未识别';
      const regionSummary = checked.proxy?.region || '自动识别';
      const fingerprintSummary = payload.fingerprint_enabled ? `${payload.fingerprint_source || 'local'} / 严格=${payload.fingerprint_strict ? '是' : '否'}` : '关闭';
      const getRt = payload.get_refresh_token !== false;
      const phoneSummary = checked.phone?.mode === 'forced'
        ? `指定号码 ${payload.forced_phone || '—'}（已在号码池确认）`
        : `自动取号（池内 ${checked.phone?.poolCount ?? 0} 条，HeroSMS=${checked.phone?.heroSmsConfigured ? '可用' : '否'}，TeleAuto=${checked.phone?.teleAutoConfigured ? '可用' : '否'}）`;
      const confirmed = window.confirm([
        '启动前最终确认',
        '',
        `邮箱：${emailSummary}`,
        `来源池：${payload.mail_source_group || '—'}`,
        `浏览器注册代理：${proxySummary}`,
        `Sub2API/OAuth 导入代理：${getRt ? `${checked.sub2apiProxy?.proxy || '—'}（${checked.sub2apiProxy?.followSignupProxy ? '跟随注册代理' : '独立 JP 兜底'}）` : '跳过'}`,
        `地区：${regionSummary}`,
        `指纹：${fingerprintSummary}`,
        `手机：${phoneSummary}`,
        `浏览器：${checked.display?.display || ':1'} / 有头 noVNC`,
        `获取 RT：${getRt ? '是' : '否（跳过 OAuth 与 Sub2API）'}`,
        `Sub2API：${getRt ? (payload.sub2api_group || 'auto') : '跳过'}`,
        '',
        '以上预检刚刚通过，确认立即启动吗？',
      ].join('\n'));
      if (!confirmed) {
        notify('已取消启动，预检结果已保留', 'info');
        return;
      }
      const res = await apiClient.openai4.post('/start', payload);
      if (res?.config) setO4Config(prev => ({ ...prev, ...res.config }));
      o4ConfigDirtyRef.current = false;
      o4ConfigHydratedRef.current = true;
      notify('OpenAI 注册任务已启动', 'success');
      fetchAllStatus();
    } catch(e) { notify(e.message, 'error', { title: '启动失败' }); } finally { setLoading(false); }
  };
  const handleStopO4 = async () => {
    try {
      await apiClient.openai4.post('/stop', {});
      notify('已请求停止，状态会自动刷新', 'info');
    } catch(e) { notify(e.message, 'error', { title: '停止失败' }); }
  };

  // Handlers for OpenAI 3
  const handleSaveO3 = async () => {
    try {
      const res = await apiClient.openai3.post('/config', normalizeOpenAI3StartPayload(o3Config));
      if (res?.config) setO3Config(prev => ({ ...prev, ...res.config }));
      o3ConfigDirtyRef.current = false;
      o3ConfigHydratedRef.current = true;
      notify('OpenAI 3 配置已保存', 'success');
    } catch(e) { notify(e.message, 'error', { title: '保存失败' }); }
  };
  const handlePreflightO3 = async () => {
    try {
      setLoading(true);
      await apiClient.openai3.post('/config', normalizeOpenAI3StartPayload(o3Config));
      const res = await apiClient.openai3.post('/preflight', normalizeOpenAI3StartPayload(o3Config));
      setO3Preflight(res);
      notify('启动前检查已完成，请查看检查结果', 'success');
    } catch (e) {
      notify(e.message, 'error', { title: 'Preflight 失败' });
    } finally { setLoading(false); }
  };
  const handleStartO3 = async () => {
    try {
      setLoading(true);
      const res = await apiClient.openai3.post('/start', normalizeOpenAI3StartPayload(o3Config));
      if (res?.config) setO3Config(prev => ({ ...prev, ...res.config }));
      o3ConfigDirtyRef.current = false;
      o3ConfigHydratedRef.current = true;
      notify('OpenAI 3 已启动', 'success');
      fetchAllStatus();
    } catch(e) { notify(e.message, 'error', { title: '启动失败' }); } finally { setLoading(false); }
  };
  const handleStopO3 = async () => {
    try {
      await apiClient.openai3.post('/stop', {});
      notify('已请求停止，状态会自动刷新', 'info');
    } catch(e) { notify(e.message, 'error', { title: '停止失败' }); }
  };

  const o4FilteredAccs = o4Accounts.filter(a => !o4Config.mail_source_group || a.group === o4Config.mail_source_group);
  const o3FilteredAccs = o3Accounts.filter(a => !o3Config.mail_source_group || a.groupName === o3Config.mail_source_group);

  const openai2UiBase = (window.AUTOMYAI_RUNTIME_CONFIG || window.__RUNTIME_CONFIG__ || {}).openai2UiBase || '/openai2/';

  return (
    <div className={`page-container ${activeTab === 'openai4' ? 'openai1-page' : ''}`}>
      <div className="page-header">
        <div className="page-title-group">
          <h1>{activeItem?.label || 'OpenAI 流程'}</h1>
        </div>
        <GlassButton variant="glass" onClick={fetchAllStatus} icon={RefreshCw}>
          刷新引擎状态
        </GlassButton>
      </div>

      <div className="openai-engine-content">
        <div className="engine-view">

        {/* OpenAI 4 (UC Signup) */}
        {activeTab === 'openai4' && (
          <div className="openai1-workbench">
            <div className="openai-mobile-action-dock">
              <GlassButton variant="glass" icon={SlidersHorizontal} onClick={() => document.getElementById('openai1-control-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>配置</GlassButton>
              <GlassButton variant="glass" onClick={handlePreflightO4} loading={loading}>预检</GlassButton>
              <GlassButton variant="glass" onClick={handleSaveO4}>保存</GlassButton>
              {!o4Status?.running ? <GlassButton variant="primary" onClick={handleStartO4} loading={loading}>开始</GlassButton> : <GlassButton variant="danger" onClick={handleStopO4}>停止</GlassButton>}
            </div>
            <OpenAI1ControlPanel
              config={o4Config}
              setConfig={updateO4Config}
              status={o4Status || {}}
              groups={o4Groups}
              mailGroups={o4MailGroups}
              sub2apiGroups={o4Sub2apiGroups}
              accounts={o4FilteredAccs}
              loading={loading}
              initialLoading={initialLoading}
              preflight={o4Preflight}
              onPreflight={handlePreflightO4}
              onSave={handleSaveO4}
              onStart={handleStartO4}
              onStop={handleStopO4}
            />
            <OpenAI1Operations status={o4Status || {}} config={o4Config} logs={o4Logs} traffic={o4Traffic} onRefresh={fetchAllStatus} />
          </div>
        )}

        {activeTab === 'openai5' && <OpenAI5Supervisor notify={notify} />}

        {/* OpenAI 7 (native registration-only UI) */}
        {activeTab === 'openai7' && <OpenAI7Registration notify={notify} />}

        {/* OpenAI 6 (AT Maker) */}
        {activeTab === 'openai6' && <OpenAI6Workbench notify={notify} />}

        {/* OpenAI 3 */}
        {activeTab === 'openai3' && (
          <OpenAI3Workbench
            config={o3Config}
            setConfig={updateO3Config}
            status={o3Status || {}}
            groups={o3Groups}
            accounts={o3FilteredAccs}
            preflight={o3Preflight}
            logs={o3Logs}
            traffic={o3Traffic}
            loading={loading}
            onPreflight={handlePreflightO3}
            onSave={handleSaveO3}
            onStart={handleStartO3}
            onStop={handleStopO3}
            onRefresh={fetchAllStatus}
          />
        )}

        {/* OpenAI 2 */}
        {activeTab === 'openai2' && (
          <div className="operations-stack external-console-stack">
            <GlassPanel className="operation-action-panel"><div><h3>OpenAI 2 服务池统计</h3><small>状态摘要在前，完整控制台保持原样嵌入</small></div><a href={openai2UiBase} target="_blank" rel="noreferrer"><GlassButton variant="primary" icon={ExternalLink}>全屏打开控制台</GlassButton></a></GlassPanel>
            <div className="console-metrics"><MetricCard label="服务健康" value={o2Health?.ok ? 'Healthy' : 'Offline'} tone={o2Health?.ok ? 'success' : 'danger'} /><MetricCard label="可用账号" value={o2Health?.stats?.available ?? 0} /><MetricCard label="已完成" value={o2Health?.stats?.done ?? 0} tone="success" /><MetricCard label="失败" value={o2Health?.stats?.failed ?? 0} tone="danger" /></div>
            <GlassPanel className="external-console-frame"><iframe src={openai2UiBase} title="OpenAI 2 Panel" /></GlassPanel>
          </div>
        )}
        </div>
      </div>
    </div>
  );
}
