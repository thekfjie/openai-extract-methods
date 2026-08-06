import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Activity, CloudCog, Play, RefreshCw, Save, ShieldCheck, Square } from 'lucide-react';
import apiClient from '../../api/client';
import GlassButton from '../../ui/GlassButton';
import GlassPanel from '../../ui/GlassPanel';
import { ErrorBanner, Field, StatusBadge } from '../../ui/ConsolePrimitives';

const nodes = [
  ['fingerprint_api_health', 'API 健康'],
  ['fingerprint_api_auth', 'API 鉴权'],
  ['authorized_cloud_source', '授权云来源'],
  ['desktop_presets', '桌面预设'],
  ['target_connectivity', '目标连通'],
];

const timeText = (value) => {
  const match = String(value || '').match(/T(\d{2}:\d{2}:\d{2})/);
  return match?.[1] || value || '—';
};

export default function OpenAI5Supervisor({ notify }) {
  const [status, setStatus] = useState({});
  const [logs, setLogs] = useState([]);
  const [config, setConfig] = useState({
    proxy_url: '',
    targets: ['https://auth.openai.com/', 'https://chatgpt.com/'],
    attempts: 3,
    timeout_seconds: 12,
  });
  const [preflight, setPreflight] = useState(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState(null);
  const dirty = useRef(false);

  const payload = useMemo(() => ({
    proxy_url: config.proxy_url || '',
    targets: config.targets?.length ? config.targets : ['https://auth.openai.com/', 'https://chatgpt.com/'],
    attempts: Math.max(1, Math.min(3, Number(config.attempts) || 3)),
    timeout_seconds: Math.max(3, Math.min(30, Number(config.timeout_seconds) || 12)),
  }), [config]);

  const change = (patch) => {
    dirty.current = true;
    setConfig((current) => ({ ...current, ...patch }));
  };

  const refresh = async ({ hydrate = false } = {}) => {
    const [statusResult, logsResult, configResult] = await Promise.allSettled([
      apiClient.openai5.get('/status'),
      apiClient.openai5.get('/logs?tail=200'),
      hydrate ? apiClient.openai5.get('/config') : Promise.resolve(null),
    ]);
    if (statusResult.status === 'fulfilled') setStatus(statusResult.value?.state || {});
    if (logsResult.status === 'fulfilled') setLogs(logsResult.value?.logs || []);
    if (hydrate && configResult.status === 'fulfilled' && configResult.value?.config && !dirty.current) {
      setConfig((current) => ({ ...current, ...configResult.value.config }));
    }
    const rejected = [statusResult, logsResult, configResult].find((item) => item.status === 'rejected');
    if (rejected) setError(rejected.reason);
  };

  useEffect(() => {
    refresh({ hydrate: true }).catch(setError);
    const timer = setInterval(() => refresh().catch(setError), 3000);
    return () => clearInterval(timer);
  }, []);

  const action = async (name, task, success) => {
    setBusy(name);
    setError(null);
    try {
      const result = await task();
      if (success) notify(success, 'success');
      await refresh({ hydrate: name === 'save' });
      return result;
    } catch (reason) {
      setError(reason);
      notify(reason.message || String(reason), 'error', { title: 'OpenAI5' });
      return null;
    } finally {
      setBusy('');
    }
  };

  const save = async () => {
    const result = await action('save', () => apiClient.openai5.post('/config', payload), 'OpenAI5 配置已保存');
    if (result) dirty.current = false;
  };
  const runPreflight = async () => {
    const result = await action('preflight', () => apiClient.openai5.post('/preflight', payload));
    if (result) {
      setPreflight(result);
      notify(result.ok ? 'API-only 预检通过' : result.reason || 'API-only 预检未通过', result.ok ? 'success' : 'warning');
    }
  };
  const start = () => action('start', () => apiClient.openai5.post('/start', payload), 'OpenAI5 诊断已启动');
  const stop = () => action('stop', () => apiClient.openai5.post('/stop', {}), '已请求停止诊断');

  const source = preflight?.source || status.summary?.fingerprint_source || {};
  const targetText = (config.targets || []).join('\n');

  return (
    <div className="operations-stack openai5-supervisor">
      <ErrorBanner error={error} onRetry={() => refresh({ hydrate: true })} />

      <GlassPanel variant="strong" className="operation-action-panel">
        <div>
          <h3><CloudCog size={18} /> OpenAI5 API-only 桌面环境监督器</h3>
          <small>只做环境与连通性诊断；必须经过指纹 API，不执行注册或验证码/风控绕过。</small>
        </div>
        <StatusBadge ok={status.phase === 'completed'}>{status.running ? '运行中' : (status.phase || '空闲')}</StatusBadge>
      </GlassPanel>

      <div className="openai5-desktop-grid">
        <GlassPanel variant="strong" className="console-section">
          <div className="console-section-header"><div><b>诊断配置</b><small>桌面优先 · API 鉴权 · local-api / authorized-cloud</small></div></div>
          <div className="console-section-body">
            <div className="console-form-grid">
              <Field label="诊断代理" hint="留空为直连；不会自动换地区或轮换出口。" wide>
                <input className="input-glass" value={config.proxy_url || ''} onChange={(event) => change({ proxy_url: event.target.value })} placeholder="http://user:pass@host:port" />
              </Field>
              <Field label="目标站点" hint="一行一个，仅允许 OpenAI 官方 HTTPS 主机。" wide>
                <textarea className="input-glass" rows="4" value={targetText} onChange={(event) => change({ targets: event.target.value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean) })} />
              </Field>
              <Field label="每节点尝试次数">
                <input className="input-glass" type="number" min="1" max="3" value={config.attempts || 3} onChange={(event) => change({ attempts: Number(event.target.value) })} />
              </Field>
              <Field label="请求超时（秒）">
                <input className="input-glass" type="number" min="3" max="30" value={config.timeout_seconds || 12} onChange={(event) => change({ timeout_seconds: Number(event.target.value) })} />
              </Field>
            </div>
            <div className="console-actions">
              <GlassButton variant="glass" icon={ShieldCheck} loading={busy === 'preflight'} onClick={runPreflight}>预检</GlassButton>
              <GlassButton variant="glass" icon={Save} loading={busy === 'save'} onClick={save}>保存</GlassButton>
              {!status.running
                ? <GlassButton variant="primary" icon={Play} loading={busy === 'start'} onClick={start}>开始诊断</GlassButton>
                : <GlassButton variant="danger" icon={Square} loading={busy === 'stop'} onClick={stop}>停止</GlassButton>}
            </div>
            {preflight ? <div className={`openai-preflight ${preflight.ok ? 'ok' : 'failed'}`}><b>{preflight.ok ? '预检通过' : '预检未通过'}</b><span>{preflight.ok ? 'API 鉴权、实际 API 指纹生成与桌面约束均满足' : preflight.reason}</span></div> : null}
          </div>
        </GlassPanel>

        <GlassPanel variant="strong" className="console-section">
          <div className="console-section-header"><div><b>执行节点</b><small>有限重试，失败按节点停止</small></div><GlassButton variant="icon" icon={RefreshCw} onClick={() => refresh()} title="刷新" /></div>
          <div className="console-section-body openai5-node-list">
            {nodes.map(([id, label]) => {
              const state = status.node_statuses?.[id] || 'pending';
              return <div key={id} className={`openai5-node state-${state}`}><Activity size={15} /><span>{label}</span><StatusBadge ok={state === 'completed'}>{state}</StatusBadge></div>;
            })}
            <div className="openai5-source-summary">
              <span><b>模式</b>API-only</span>
              <span><b>回退</b>disabled</span>
              <span><b>来源</b>{source.mode || '待检查'}</span>
              <span><b>桌面预设</b>{status.summary?.desktop_presets?.join(', ') || '待检查'}</span>
            </div>
            {status.error ? <div className="openai5-run-error">{status.error}</div> : null}
          </div>
        </GlassPanel>
      </div>

      <GlassPanel variant="strong" className="console-section">
        <div className="console-section-header"><div><b>监督日志</b><small>网络、鉴权、来源和连通性错误会单独分类</small></div></div>
        <div className="openai-live-log openai5-log" role="log">
          {logs.length ? logs.map((entry, index) => <div className={`openai-log-line level-${entry.level || 'info'}`} key={`${entry.time}-${index}`}><time>{timeText(entry.time)}</time><em>{String(entry.level || 'info').toUpperCase()}</em><span>{entry.message}</span></div>) : <div className="openai-log-empty">暂无日志</div>}
        </div>
      </GlassPanel>
    </div>
  );
}
