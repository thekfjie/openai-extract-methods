import React, { useEffect, useState } from 'react';
import { ExternalLink, Play, RefreshCw, Square, Zap } from 'lucide-react';
import GrokTTKWorkbench from '../components/grok/GrokTTKWorkbench';
import AutomationFlowRunner from '../components/workflow/AutomationFlowRunner';
import ConverterPipelineNode from '../components/workflow/ConverterPipelineNode';
import { useToast } from '../contexts/ToastContext';
import apiClient from '../api/client';
import GlassButton from '../ui/GlassButton';
import GlassPanel from '../ui/GlassPanel';
import useNavigationSub from '../hooks/useNavigationSub';

export default function GrokAutomation() {
  const { notify } = useToast();
  const { activeSub: activeTab, activeItem } = useNavigationSub('/grok');
  const [ttkState, setTtkState] = useState(null);
  const [ttkResults, setTtkResults] = useState([]);
  const [ttkTraffic, setTtkTraffic] = useState(null);
  const [ttkLogs, setTtkLogs] = useState([]);
  const [ttkConfig, setTtkConfig] = useState({
    emailProvider: 'duckmail',
    registerCount: 1,
    registerThreads: 1,
    threadStartInterval: 10,
    proxy: '',
    proxyPreset: '',
    trafficMeter: false,
    enableNsfw: false,
    grok2apiAutoAddRemote: true,
    cpaAutoAdd: true,
    grokAutoNsfw: false,
    duckmailApiKey: '',
    yydsApiKey: '',
    yydsJwt: '',
    cloudflareApiBase: '',
    cloudflareApiKey: '',
    cloudflareAuthMode: 'none',
    cloudflareCustomAuth: '',
    cloudflarePaths: '',
    defaultDomains: '',
    grok2apiPoolName: 'grok2',
    grok2apiRemoteBase: 'http://127.0.0.1:8000',
    grok2apiRemoteAppKey: '',
    cpaAuthDir: '/opt/cliproxyapi/auths',
    cpaRemoteUrl: 'http://127.0.0.1:8317',
    cpaManagementKey: '',
    ssoText: '',
  });
  const [signupLogs, setSignupLogs] = useState([]);
  const [cpaStatus, setCpaStatus] = useState(null);
  const [exportName, setExportName] = useState('grok_accounts.json');
  const [loading, setLoading] = useState(false);

  const fetchGrokStatus = async () => {
    try {
      const [ttkStatusRes, ttkResultsRes, ttkTrafficRes, ttkLogsRes, ttkConfigRes, signupLogsRes, cpaRes] = await Promise.allSettled([
        apiClient.get('/grok/ttk/status').catch(() => ({})),
        apiClient.get('/grok/ttk/results').catch(() => ({})),
        apiClient.get('/grok/ttk/traffic?tail=30').catch(() => ({})),
        apiClient.get('/grok/ttk/logs?tail=100').catch(() => ({})),
        apiClient.get('/grok/ttk/config').catch(() => ({})),
        apiClient.get('/grok/registration/logs?tail=50').catch(() => ({})),
        apiClient.get('/cpa/monitor/status').catch(() => ({})),
      ]);
      if (ttkStatusRes.status === 'fulfilled') setTtkState(ttkStatusRes.value);
      if (ttkResultsRes.status === 'fulfilled') setTtkResults(ttkResultsRes.value?.results || ttkResultsRes.value || []);
      if (ttkTrafficRes.status === 'fulfilled') setTtkTraffic(ttkTrafficRes.value);
      if (ttkLogsRes.status === 'fulfilled') setTtkLogs(ttkLogsRes.value?.logs || []);
      if (ttkConfigRes.status === 'fulfilled' && ttkConfigRes.value?.config) setTtkConfig((current) => ({ ...current, ...ttkConfigRes.value.config }));
      if (signupLogsRes.status === 'fulfilled') setSignupLogs(signupLogsRes.value?.logs || []);
      if (cpaRes.status === 'fulfilled') setCpaStatus(cpaRes.value);
    } catch (error) {
      console.error(error);
    }
  };

  useEffect(() => {
    fetchGrokStatus();
    const timer = setInterval(fetchGrokStatus, 3000);
    return () => clearInterval(timer);
  }, []);

  const handleSaveConfig = async () => {
    try {
      setLoading(true);
      await apiClient.post('/grok/ttk/config', ttkConfig);
      notify('Grok TTK 及其生态配置已保存', 'success');
    } catch (error) {
      notify(error.message, 'error', { title: '保存失败' });
    } finally {
      setLoading(false);
    }
  };

  const handleStartTTK = async () => {
    try {
      setLoading(true);
      await apiClient.post('/grok/ttk/config', ttkConfig);
      await apiClient.post('/grok/ttk/start', {});
      notify('Grok TTK 批量任务已启动', 'success');
      fetchGrokStatus();
    } catch (error) {
      notify(error.message, 'error', { title: '启动 Grok TTK 失败' });
    } finally {
      setLoading(false);
    }
  };

  const handleStopTTK = async () => {
    try {
      setLoading(true);
      await apiClient.post('/grok/ttk/stop', {});
      notify('已请求停止 TTK，状态会自动刷新', 'info');
      fetchGrokStatus();
    } catch (error) {
      notify(error.message, 'error', { title: '停止失败' });
    } finally {
      setLoading(false);
    }
  };

  const handleSyncTTK = async () => {
    try {
      await apiClient.post('/grok/ttk/sync', {});
      notify('TTK 产出数据已同步至 Grok2API 与 CPA', 'success');
      fetchGrokStatus();
    } catch (error) {
      notify(error.message, 'error', { title: '同步失败' });
    }
  };

  const handleImportSso = async () => {
    try {
      await apiClient.post('/grok/import/sso', { ssoText: ttkConfig.ssoText });
      notify('SSO 凭证导入成功', 'success');
      setTtkConfig((current) => ({ ...current, ssoText: '' }));
    } catch (error) {
      notify(error.message, 'error', { title: '导入失败' });
    }
  };

  const handleExportTTK = () => window.open(`/api/grok/ttk/export?name=${encodeURIComponent(exportName)}`, '_blank');

  const handleStartSignup = async () => {
    try {
      await apiClient.post('/grok/signup/start', {});
      notify('Grok Signup 已启动', 'success');
      fetchGrokStatus();
    } catch (error) {
      notify(error.message, 'error', { title: '启动 Grok Signup 失败' });
    }
  };

  const handleStopSignup = async () => {
    try {
      await apiClient.post('/grok/signup/stop', {});
      notify('已请求停止 Grok Signup', 'info');
      fetchGrokStatus();
    } catch (error) {
      notify(error.message, 'error', { title: '停止失败' });
    }
  };

  const handleImportGrok2API = async () => {
    try {
      await apiClient.post('/grok/import/grok2api', {});
      notify('Grok 凭证已成功导入 Grok2API', 'success');
    } catch (error) {
      notify(error.message, 'error', { title: '导入 Grok2API 失败' });
    }
  };

  const grok2Base = (window.AUTOMYAI_RUNTIME_CONFIG || window.__RUNTIME_CONFIG__ || {}).grok2Base || '/grok2/';

  return (
    <div className="page-container operations-page">
      <div className="page-header">
        <div className="page-title-group"><h1>{activeItem?.label || 'Grok 流程'}</h1></div>
        <GlassButton variant="glass" onClick={fetchGrokStatus} icon={RefreshCw}>刷新状态</GlassButton>
      </div>

      <div className="engine-view">
        {activeTab === 'ttk' ? (
          <GrokTTKWorkbench
            config={ttkConfig}
            setConfig={setTtkConfig}
            state={ttkState || {}}
            results={ttkResults}
            logs={ttkLogs}
            cpaStatus={cpaStatus || {}}
            traffic={ttkTraffic}
            exportName={exportName}
            setExportName={setExportName}
            loading={loading}
            onSave={handleSaveConfig}
            onStart={handleStartTTK}
            onStop={handleStopTTK}
            onSync={handleSyncTTK}
            onExport={handleExportTTK}
            onImportSso={handleImportSso}
            onRefresh={fetchGrokStatus}
          />
        ) : null}

        {activeTab === 'cpa_convert' ? (
          <div className="operations-stack">
            <ConverterPipelineNode title="Grok 凭证 → CPA Device-Flow / Grok2API 转换" defaultType="grok" />
            <GlassPanel className="operation-action-panel">
              <div><h3>直接导入凭证至 Grok2API</h3><small>使用当前已产出的 Grok 凭证执行一次手工导入</small></div>
              <GlassButton variant="primary" onClick={handleImportGrok2API} icon={Zap}>一键导入 Grok2API</GlassButton>
            </GlassPanel>
          </div>
        ) : null}

        {activeTab === 'grok2_panel' ? (
          <div className="operations-stack external-console-stack">
            <GlassPanel className="operation-action-panel">
              <div><h3>Grok 2 外部控制面板</h3><small>{grok2Base}</small></div>
              <a href={grok2Base} target="_blank" rel="noreferrer"><GlassButton variant="primary" icon={ExternalLink}>新标签页全屏打开</GlassButton></a>
            </GlassPanel>
            <GlassPanel className="external-console-frame"><iframe src={grok2Base} title="Grok 2 External Panel" /></GlassPanel>
          </div>
        ) : null}

        {activeTab === 'signup' ? (
          <div className="operations-stack">
            <GlassPanel className="operation-action-panel">
              <div><h3>Grok 历史注册模块</h3><small>保留 grok_signup.py 的原有启动入口与日志</small></div>
              <div className="operation-inline-actions">
                <GlassButton variant="primary" onClick={handleStartSignup} icon={Play}>启动 Signup</GlassButton>
                <GlassButton variant="danger" onClick={handleStopSignup} icon={Square}>停止 Signup</GlassButton>
              </div>
            </GlassPanel>
            <AutomationFlowRunner title="Grok Signup 实时流程" running={false} progress={{ current: 0, total: 100, step: 'Legacy' }} logs={signupLogs} onStart={handleStartSignup} onStop={handleStopSignup} onRefresh={fetchGrokStatus} />
          </div>
        ) : null}
      </div>
    </div>
  );
}
