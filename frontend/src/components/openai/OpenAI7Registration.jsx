import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Download, Play, RefreshCw, Save, ShieldCheck, Square, TerminalSquare } from 'lucide-react';
import GlassButton from '../../ui/GlassButton';
import GlassPanel from '../../ui/GlassPanel';
import CustomSelect from '../../ui/CustomSelect';
import apiClient from '../../api/client';
import { CompactNumberInput, ErrorBanner, Field, MetricCard, StatusBadge, Toggle } from '../../ui/ConsolePrimitives';

const providers = [
  { value: 'outlookmail', label: 'OutlookMail · 默认分组' },
  { value: 'applemail', label: 'Apple Mail · iCloud 邮箱池' },
];

function describeEvent(event) {
  if (event.type === 'success') return '成功 · ' + (event.email || '账号') + ' · ' + (event.hasRefreshToken ? 'AT + RT' : 'AT');
  if (event.type === 'fail') return '失败 · ' + (event.error || event.failureCode || '未知错误');
  if (event.type === 'done') return '任务结束 · 成功 ' + (event.success || 0) + ' · 失败 ' + (event.fail || 0);
  return event.message || [event.stage, event.state].filter(Boolean).join(' · ') || event.type || '状态更新';
}

function downloadBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  URL.revokeObjectURL(url);
}

export default function OpenAI7Registration({ notify }) {
  const [config, setConfig] = useState({
    proxyUrl: '', threads: 1, totalRounds: 1, emailProvider: 'outlookmail', atOnly: true,
    outlookAccountMode: 'specific', outlookAccountEmail: 'EricGonzales91681r+gh@outlook.com',
    outlookAliasMode: 'random', outlookAliasSuffix: '',
    appleAccountMode: 'auto', appleAccountEmail: '', appleAliasMode: 'preserve', appleAliasSuffix: '',
  });
  const [runtime, setRuntime] = useState({ running: false, tasks: [] });
  const [mail, setMail] = useState({ configured: false, groupName: '默认分组', selectable: 0, appleMail: { configured: false, selectable: 0, accounts: [] } });
  const [stats, setStats] = useState({ success: 0, fail: 0, round: 0, total: 0 });
  const [logs, setLogs] = useState([]);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState(null);
  const [proxyCheck, setProxyCheck] = useState(null);
  const logRef = useRef(null);
  const hydratedRef = useRef(false);

  const appendLog = useCallback((text, tone) => {
    const time = new Date().toLocaleTimeString('zh-CN', { hour12: false });
    setLogs((rows) => rows.slice(-499).concat([{ time, text: String(text || ''), tone: tone || 'info' }]));
  }, []);

  const refresh = useCallback(async () => {
    try {
      const results = await Promise.all([apiClient.openai7.get('/config'), apiClient.openai7.get('/status')]);
      const cfg = results[0] || {};
      const status = results[1] || {};
      if (!hydratedRef.current) {
        const saved = cfg.config || {};
        const selection = cfg.outlookSelection || {};
        setConfig((current) => ({
          ...current, proxyUrl: saved.defaultProxyUrl || '',
          emailProvider: ['outlookmail', 'applemail'].includes(cfg.emailProvider || saved.emailProvider) ? (cfg.emailProvider || saved.emailProvider) : 'outlookmail',
          outlookAccountMode: selection.accountMode || saved.outlookAccountMode || 'specific',
          outlookAccountEmail: selection.accountEmail || saved.outlookAccountEmail || 'EricGonzales91681r+gh@outlook.com',
          outlookAliasMode: selection.aliasMode || saved.outlookAliasMode || 'random',
          outlookAliasSuffix: selection.aliasSuffix || saved.outlookAliasSuffix || '',
          appleAccountMode: (cfg.appleSelection && cfg.appleSelection.accountMode) || saved.appleAccountMode || 'auto',
          appleAccountEmail: (cfg.appleSelection && cfg.appleSelection.accountEmail) || saved.appleAccountEmail || '',
          appleAliasMode: (cfg.appleSelection && cfg.appleSelection.aliasMode) || saved.appleAliasMode || 'preserve',
          appleAliasSuffix: (cfg.appleSelection && cfg.appleSelection.aliasSuffix) || saved.appleAliasSuffix || '',
        }));
        hydratedRef.current = true;
      }
      setMail({ ...(cfg.outlookMail || { configured: false, groupName: '默认分组', selectable: 0 }), appleMail: cfg.appleMail || { configured: false, selectable: 0, accounts: [] } });
      setRuntime(status);
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 5000);
    const stream = new EventSource((window.AUTOMYAI_RUNTIME_CONFIG?.openai7ApiBase || '/openai6/api') + '/events');
    const receive = (raw) => {
      try {
        const event = JSON.parse(raw.data || '{}');
        setStats({ success: event.success || 0, fail: event.fail || 0, round: event.round || 0, total: event.total || 0 });
        if (event.type === 'done' || event.type === 'stopped') setRuntime({ running: false, tasks: [] });
        if (event.type === 'progress' && event.round) setRuntime((value) => ({ ...value, running: true }));
        appendLog(describeEvent(event), event.type === 'fail' ? 'error' : event.type === 'success' ? 'success' : 'info');
      } catch (_) {}
    };
    stream.addEventListener('progress', receive);
    return () => { window.clearInterval(timer); stream.close(); };
  }, [appendLog, refresh]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [logs]);

  const save = async (quiet) => {
    const result = await apiClient.openai7.post('/config', {
      proxyUrl: config.proxyUrl.trim(), emailProvider: config.emailProvider, loopDelayMs: 1000,
      outlookAccountMode: config.outlookAccountMode, outlookAccountEmail: config.outlookAccountEmail,
      outlookAliasMode: config.outlookAliasMode, outlookAliasSuffix: config.outlookAliasSuffix,
      appleAccountMode: config.appleAccountMode, appleAccountEmail: config.appleAccountEmail,
      appleAliasMode: config.appleAliasMode, appleAliasSuffix: config.appleAliasSuffix,
    });
    if (!result || !result.ok) throw new Error((result && result.error) || '保存失败');
    if (!quiet) notify('OpenAI 7 配置已保存', 'success');
  };

  const handleSave = async () => {
    setBusy('save');
    try {
      await save(false);
      setError(null);
    } catch (nextError) {
      setError(nextError);
      notify(nextError.message, 'error', { title: '保存失败' });
    } finally {
      setBusy('');
    }
  };

  const testProxy = async () => {
    if (!config.proxyUrl.trim()) return notify('请填写注册代理', 'warning');
    setBusy('proxy');
    try {
      const result = await apiClient.openai7.post('/proxy/test', { proxyUrl: config.proxyUrl.trim() });
      setProxyCheck(result);
      notify(result.ok ? '代理检测通过' : (result.error || '代理检测失败'), result.ok ? 'success' : 'error');
    } catch (nextError) { setError(nextError); } finally { setBusy(''); }
  };

  const start = async () => {
    if (!config.proxyUrl.trim()) return notify('请填写注册代理', 'warning');
    if (config.emailProvider === 'outlookmail' && !mail.selectable) return notify('OutlookMail 默认分组没有可用邮箱', 'warning');
    if (config.emailProvider === 'applemail' && !(mail.appleMail && mail.appleMail.selectable)) return notify('Apple Mail 号池没有可用邮箱或未配置 adminAuth', 'warning');
    if (config.emailProvider === 'outlookmail' && config.outlookAccountMode === 'specific' && !config.outlookAccountEmail) return notify('请选择 Outlook 账号', 'warning');
    setBusy('start');
    try {
      await save(true);
      const result = await apiClient.openai7.post('/start', {
        proxyUrl: config.proxyUrl.trim(), threads: config.threads, totalRounds: config.totalRounds,
        loopDelayMs: 1000, getRefreshToken: !config.atOnly,
      });
      if (!result || !result.ok) throw new Error((result && result.error) || '启动失败');
      setStats({ success: 0, fail: 0, round: 0, total: config.totalRounds });
      setLogs([]);
      setRuntime({ running: true, tasks: [] });
      appendLog('任务启动 · ' + config.threads + ' 线程 × ' + config.totalRounds + ' 个 · ' + (config.atOnly ? 'AT-only' : 'AT + RT'));
      notify('OpenAI 7 任务已启动', 'success');
    } catch (nextError) {
      setError(nextError);
      notify(nextError.message, 'error', { title: '启动失败' });
    } finally { setBusy(''); }
  };

  const stop = async () => {
    setBusy('stop');
    try {
      await apiClient.openai7.post('/stop', {});
      setRuntime({ running: false, tasks: [] });
      notify('已请求停止', 'info');
    } catch (nextError) { setError(nextError); } finally { setBusy(''); }
  };

  const download = async (kind) => {
    try {
      const blob = await apiClient.openai7.blob(kind === 'rt' ? '/download-rt' : '/download');
      downloadBlob(blob, kind === 'rt' ? 'openai7-refresh-tokens.zip' : 'openai7-access-tokens.txt');
    } catch (nextError) { notify(nextError.message, 'error', { title: '下载失败' }); }
  };

  const activeTask = runtime.tasks && runtime.tasks[0];
  const progress = stats.total ? stats.round + '/' + stats.total : (runtime.running ? '运行中' : '0/0');
  const outlookAccounts = (mail.accounts || []).filter((account) => account.selectable);
  const accountOptions = [
    { value: '', label: '自动随机（默认分组全部账号）' },
    ...outlookAccounts.map((account) => ({ value: String(account.email || ''), label: String(account.email || account.id) })),
  ];
  const aliasOptions = [
    { value: 'random', label: '随机 +3位（如 +asa）' },
    { value: 'preserve', label: '保留账号现有后缀' },
    { value: 'fixed', label: '固定自定义后缀' },
  ];
  const setOutlookAccount = (outlookAccountEmail) => setConfig((value) => ({
    ...value, outlookAccountEmail, outlookAccountMode: outlookAccountEmail ? 'specific' : 'auto',
    threads: outlookAccountEmail ? 1 : value.threads, totalRounds: outlookAccountEmail ? 1 : value.totalRounds,
  }));

  return (
    <div className="openai6-workbench openai7-workbench">
      <div className="openai-mobile-action-dock">
        <GlassButton variant="glass" onClick={testProxy} loading={busy === 'proxy'}>检测代理</GlassButton>
        <GlassButton variant="glass" onClick={handleSave} loading={busy === 'save'} icon={Save}>保存</GlassButton>
        {!runtime.running ? <GlassButton variant="primary" onClick={start} loading={busy === 'start'}>开始</GlassButton> : <GlassButton variant="danger" onClick={stop}>停止</GlassButton>}
      </div>

      <GlassPanel variant="strong" className="openai-control-panel openai6-control-panel">
        <div className="openai-panel-heading">
          <div><h2><TerminalSquare size={18} />OpenAI 7 · GPT 注册机</h2><small>原生 OpenAI 模块 · OutlookMail / Apple Mail · 注册任务</small></div>
          <StatusBadge ok={!!runtime.running}>{runtime.running ? '运行中' : '空闲'}</StatusBadge>
        </div>
        <div className="openai-control-scroll">
          <Field label="注册代理" hint="与 UC 有头一致，支持 host:port:user:pass 或标准代理 URL" wide>
            <input className="input-glass" value={config.proxyUrl} onChange={(event) => setConfig((value) => ({ ...value, proxyUrl: event.target.value }))} placeholder="host:port:user:pass" />
          </Field>
          <div className="openai-compact-fields">
            <Field label="线程数"><CompactNumberInput value={config.threads} min={1} max={20} disabled={(config.emailProvider === 'outlookmail' && config.outlookAccountMode === 'specific') || (config.emailProvider === 'applemail' && config.appleAccountMode === 'specific')} onChange={(threads) => setConfig((value) => ({ ...value, threads }))} ariaLabel="线程数" /></Field>
            <Field label="本批数量"><CompactNumberInput value={config.totalRounds} min={1} max={1000} disabled={(config.emailProvider === 'outlookmail' && config.outlookAccountMode === 'specific') || (config.emailProvider === 'applemail' && config.appleAccountMode === 'specific')} onChange={(totalRounds) => setConfig((value) => ({ ...value, totalRounds }))} ariaLabel="本批数量" /></Field>
          </div>
          <Field label="邮箱来源" hint={config.emailProvider === 'outlookmail' ? '默认分组现有 ' + (mail.selectable || 0) + ' 个可用账号' : config.emailProvider === 'applemail' ? 'Apple Mail 号池现有 ' + ((mail.appleMail && mail.appleMail.selectable) || 0) + ' 个可用账号' : '注册邮箱池'} wide>
            <CustomSelect value={config.emailProvider} onChange={(emailProvider) => setConfig((value) => ({ ...value, emailProvider }))} options={providers} ariaLabel="邮箱来源" />
          </Field>
          {config.emailProvider === 'outlookmail' ? (
            <>
              <Field label="Outlook 账号" hint={config.outlookAccountMode === 'specific' ? '已锁定指定账号；本批固定运行 1 次' : '每个任务从默认分组随机领取'} wide>
                <CustomSelect value={config.outlookAccountMode === 'specific' ? config.outlookAccountEmail : ''} onChange={setOutlookAccount} options={accountOptions} ariaLabel="Outlook 账号" />
              </Field>
              <Field label="注册邮箱后缀" hint="随机模式会把账号地址转换成同一基础邮箱的 +三位随机后缀" wide>
                <CustomSelect value={config.outlookAliasMode} onChange={(outlookAliasMode) => setConfig((value) => ({ ...value, outlookAliasMode }))} options={aliasOptions} ariaLabel="注册邮箱后缀" />
              </Field>
              {config.outlookAliasMode === 'fixed' ? <Field label="固定后缀" hint="填写 asa 后注册地址即为 基础邮箱+asa@outlook.com" wide><input className="input-glass" value={config.outlookAliasSuffix} onChange={(event) => setConfig((value) => ({ ...value, outlookAliasSuffix: event.target.value.replace(/^\++/, '') }))} placeholder="asa" /></Field> : null}
            </>
          ) : null}
          {config.emailProvider === 'applemail' ? (
            <>
              <Field label="Apple / iCloud 账号" hint={config.appleAccountMode === 'specific' ? '已锁定指定 iCloud 账号；本批固定运行 1 次' : '从 Apple Mail 号池顺序选择'} wide>
                <CustomSelect value={config.appleAccountMode === 'specific' ? config.appleAccountEmail : ''} onChange={(value) => setConfig((current) => ({ ...current, appleAccountEmail: value, appleAccountMode: value ? 'specific' : 'auto', threads: value ? 1 : current.threads, totalRounds: value ? 1 : current.totalRounds }))} options={[{ value: '', label: '自动顺序选择（Apple Mail 号池）' }, ...((mail.appleMail && mail.appleMail.accounts) || []).filter((account) => account.selectable !== false).map((account) => ({ value: String(account.email || ''), label: String(account.email || '') }))]} ariaLabel="Apple Mail 账号" />
              </Field>
              <Field label="iCloud 地址模式" hint="Apple Hide My Email 默认保留原地址；需要时可启用 +后缀" wide>
                <CustomSelect value={config.appleAliasMode} onChange={(appleAliasMode) => setConfig((value) => ({ ...value, appleAliasMode }))} options={[{ value: 'preserve', label: '保留 iCloud 地址' }, { value: 'random', label: '随机 +oaiXXXXXX 后缀' }, { value: 'fixed', label: '固定自定义后缀' }]} ariaLabel="iCloud 地址模式" />
              </Field>
              {config.appleAliasMode === 'fixed' ? <Field label="固定后缀" hint="例如 test，注册地址为 base+test@icloud.com" wide><input className="input-glass" value={config.appleAliasSuffix} onChange={(event) => setConfig((value) => ({ ...value, appleAliasSuffix: event.target.value.replace(/^\++/, '') }))} placeholder="test" /></Field> : null}
            </>
          ) : null}
          <Toggle checked={config.atOnly} onChange={(atOnly) => setConfig((value) => ({ ...value, atOnly }))} label="仅获取 AT" hint="默认开启，跳过 Codex OAuth / RT" />
          <div className="console-grid openai6-detail-grid">
            <MetricCard label="邮箱账号" value={config.emailProvider === 'applemail' ? (config.appleAccountMode === 'specific' ? config.appleAccountEmail : 'Apple Mail 号池顺序') : (config.outlookAccountMode === 'specific' ? config.outlookAccountEmail : '默认分组随机')} />
            <MetricCard label="可用邮箱" value={config.emailProvider === 'applemail' ? ((mail.appleMail && mail.appleMail.selectable) || 0) : (mail.selectable || 0)} tone={(config.emailProvider === 'applemail' ? (mail.appleMail && mail.appleMail.selectable) : mail.selectable) ? 'success' : 'danger'} />
            <MetricCard label="代理状态" value={proxyCheck && proxyCheck.ok ? '可用' : '待检测'} tone={proxyCheck && proxyCheck.ok ? 'success' : undefined} />
            <MetricCard label="当前阶段" value={(activeTask && activeTask.state) || (runtime.running ? '准备中' : '空闲')} />
          </div>
          <ErrorBanner error={error} onRetry={refresh} />
        </div>
        <div className="openai-control-actions">
          <GlassButton variant="glass" onClick={testProxy} loading={busy === 'proxy'} icon={ShieldCheck}>检测代理</GlassButton>
          <GlassButton variant="glass" onClick={handleSave} loading={busy === 'save'} icon={Save}>保存</GlassButton>
          {!runtime.running ? <GlassButton variant="primary" onClick={start} loading={busy === 'start'} icon={Play}>开始</GlassButton> : <GlassButton variant="danger" onClick={stop} loading={busy === 'stop'} icon={Square}>停止</GlassButton>}
        </div>
      </GlassPanel>

      <GlassPanel variant="strong" className="openai6-monitor-panel">
        <div className="console-toolbar">
          <div><h3>实时任务</h3><small>状态机事件与运行输出</small></div>
          <div className="console-actions">
            <GlassButton variant="glass" onClick={refresh} icon={RefreshCw}>刷新</GlassButton>
            <GlassButton variant="glass" onClick={() => download('at')} icon={Download}>下载 AT</GlassButton>
            <GlassButton variant="glass" onClick={() => download('rt')} icon={Download}>下载 RT</GlassButton>
          </div>
        </div>
        <div className="console-metrics openai6-metrics">
          <MetricCard label="成功" value={stats.success} tone="success" />
          <MetricCard label="失败" value={stats.fail} tone={stats.fail ? 'danger' : undefined} />
          <MetricCard label="进度" value={progress} />
          <MetricCard label="任务数" value={(runtime.tasks && runtime.tasks.length) || 0} />
        </div>
        <div ref={logRef} className="log-viewer openai6-log-viewer">
          {logs.length ? logs.map((item, index) => <div key={item.time + '-' + index} className={'openai6-log-line ' + item.tone}><time>{item.time}</time><span>{item.text}</span></div>) : <div className="console-browser-empty">暂无运行日志，点击“开始”后自动显示</div>}
        </div>
      </GlassPanel>
    </div>
  );
}
