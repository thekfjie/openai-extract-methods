import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Copy, Mail, RefreshCw, Save, Search } from 'lucide-react';
import AutomationFlowRunner from '../components/workflow/AutomationFlowRunner';
import apiClient from '../api/client';
import { CollapsiblePanel, CompactNumberInput, ErrorBanner, Field, MetricCard, OutputBox, StatusBadge } from '../ui/ConsolePrimitives';
import GlassButton from '../ui/GlassButton';
import GlassPanel from '../ui/GlassPanel';
import CustomSelect from '../ui/CustomSelect';

const readLocal = (key, fallback = '') => { try { return localStorage.getItem(key) || fallback; } catch (_) { return fallback; } };
const writeLocal = (key, value) => { try { localStorage.setItem(key, String(value ?? '')); } catch (_) {} };

export default function AppleMail() {
  const [config, setConfig] = useState({
    mailBase: readLocal('AppleMail.mailBase', 'https://apimail.kfjie.me'),
    adminAuth: readLocal('AppleMail.adminAuth'),
    importBase: readLocal('AppleMail.importBase', 'https://cloud.opus.sryze.cc'),
    importApiKey: readLocal('AppleMail.importApiKey'),
    proxyUrl: readLocal('AppleMail.proxyUrl', 'http://172.19.0.1:7905'),
    defaultPassword: readLocal('AppleMail.password'),
    startIndex: Number(readLocal('AppleMail.cursor', '0')) || 0,
    batchCount: 1,
    fpPolicy: readLocal('AppleMail.fpPolicy', 'safari'),
  });
  const [emails, setEmails] = useState([]);
  const [names, setNames] = useState([]);
  const [consoleScript, setConsoleScript] = useState('');
  const [status, setStatus] = useState(null);
  const [localLog, setLocalLog] = useState([]);
  const [testOutput, setTestOutput] = useState(null);
  const [error, setError] = useState(null);

  const update = (patch) => setConfig((current) => ({ ...current, ...patch }));
  const appendLog = (message) => setLocalLog((current) => [...current.slice(-99), `[${new Date().toLocaleTimeString()}] ${message}`]);
  const load = useCallback(async () => {
    setError(null);
    const results = await Promise.allSettled([
      fetch('/ui/js/apple_mail_emails.json', { cache: 'no-store' }).then((response) => response.json()),
      fetch('/ui/js/apple_mail_names.json', { cache: 'no-store' }).then((response) => response.json()),
      fetch('/ui/js/apple_mail_console.js', { cache: 'no-store' }).then((response) => response.text()),
      apiClient.get('/apple-mail/status?tail=250'),
    ]);
    if (results[0].status === 'fulfilled') setEmails(Array.isArray(results[0].value) ? results[0].value : []);
    if (results[1].status === 'fulfilled') setNames(Array.isArray(results[1].value) ? results[1].value : []);
    if (results[2].status === 'fulfilled') setConsoleScript(results[2].value || '');
    if (results[3].status === 'fulfilled') setStatus(results[3].value);
    const rejected = results.find((item) => item.status === 'rejected');
    if (rejected) setError(rejected.reason);
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(() => apiClient.get('/apple-mail/status?tail=250').then(setStatus).catch(() => {}), 2000);
    return () => clearInterval(timer);
  }, [load]);

  const boot = useMemo(() => {
    const publicConfig = { mailBase: config.mailBase, importBase: config.importBase, proxyUrl: config.proxyUrl, requireProxy: true, dryRunDefault: true, fingerprintBrowser: config.fpPolicy === 'firefox' ? 'firefox' : config.fpPolicy, fingerprintVersion: config.fpPolicy === 'firefox' ? '147' : '' };
    return `/* Apple Mail bootstrap - paste in ChatGPT page console */
window.AppleMailConfig = ${JSON.stringify(publicConfig, null, 2)};
window.AppleMailEmails = ${JSON.stringify(emails, null, 2)};
window.AppleMailNames = ${JSON.stringify(names, null, 2)};
localStorage.setItem('AppleMail.mailBase', ${JSON.stringify(config.mailBase)});
localStorage.setItem('AppleMail.importBase', ${JSON.stringify(config.importBase)});
localStorage.setItem('AppleMail.adminAuth', ${JSON.stringify(config.adminAuth)});
localStorage.setItem('AppleMail.importApiKey', ${JSON.stringify(config.importApiKey)});
localStorage.setItem('AppleMail.password', ${JSON.stringify(config.defaultPassword)});
localStorage.setItem('AppleMail.cursor', ${JSON.stringify(String(config.startIndex))});
console.log('Apple Mail ready | emails=' + window.AppleMailEmails.length);
console.log('代理门禁: ' + window.AppleMailConfig.proxyUrl + '（禁止真实 IP 直连）');
console.log('Next: paste /ui/js/apple_mail_console.js, then await AppleMail.auto() or await AppleMail.autoBatch(${config.batchCount})');`;
  }, [config, emails, names]);

  const save = () => {
    writeLocal('AppleMail.mailBase', config.mailBase);
    writeLocal('AppleMail.adminAuth', config.adminAuth);
    writeLocal('AppleMail.importBase', config.importBase);
    writeLocal('AppleMail.importApiKey', config.importApiKey);
    writeLocal('AppleMail.proxyUrl', config.proxyUrl);
    writeLocal('AppleMail.password', config.defaultPassword);
    writeLocal('AppleMail.cursor', config.startIndex);
    writeLocal('AppleMail.fpPolicy', config.fpPolicy);
    try { localStorage.setItem('AppleMail.emails', JSON.stringify(emails)); localStorage.setItem('AppleMail.names', JSON.stringify(names)); } catch (_) {}
    appendLog('本机配置已保存');
  };

  const copy = async (text, label) => {
    await navigator.clipboard.writeText(text);
    appendLog(`${label}已复制`);
  };

  const testMail = async () => {
    if (!config.proxyUrl) { setError(new Error('未填写项目代理，已阻止测试')); return; }
    if (!config.mailBase || !config.adminAuth) { setError(new Error('请填写 Mail API Base 和 Admin Auth')); return; }
    const email = emails[config.startIndex] || emails[0];
    if (!email) { setError(new Error('Apple Mail 号池为空')); return; }
    try {
      const response = await fetch(`${config.mailBase.replace(/\/+$/, '')}/admin/mails?limit=5&offset=0&address=${encodeURIComponent(email)}`, { headers: { Accept: 'application/json', 'x-admin-auth': config.adminAuth } });
      const text = await response.text();
      if (!response.ok) throw new Error(`HTTP ${response.status}: ${text.slice(0, 180)}`);
      const result = JSON.parse(text);
      setTestOutput(result);
      appendLog(`拉信接口可用：${email}`);
    } catch (reason) {
      setError(reason);
      appendLog(`拉信失败：${reason.message}`);
    }
  };

  const combinedLogs = [...(status?.logs || []), ...localLog];
  return (
    <div className="page-container operations-page">
      <div className="page-header"><div className="page-title-group"><h1>Apple Mail 控制台</h1></div><GlassButton variant="glass" icon={RefreshCw} onClick={load}>刷新号池和状态</GlassButton></div>
      <ErrorBanner error={error} onRetry={load} />

      <div className="operations-priority-layout">
        <GlassPanel variant="strong" className="quick-control-panel">
          <div className="quick-control-heading"><div><h2><Mail size={17} />Apple Mail 快速控制</h2><small>代理、游标、批量数量和指纹策略保持在首屏</small></div><StatusBadge ok={!!status?.running}>{status?.running ? '运行中' : '待机'}</StatusBadge></div>
          <div className="quick-control-body">
            <Field label="项目代理（必填）" hint="未填写时测试会被阻止，避免真实 IP 直连"><input className="input-glass" value={config.proxyUrl} onChange={(event) => update({ proxyUrl: event.target.value })} /></Field>
            <div className="quick-control-row"><Field label="起始下标"><CompactNumberInput min={0} max={Math.max(emails.length - 1, 0)} value={config.startIndex} onChange={(value) => update({ startIndex: value })} ariaLabel="Apple Mail 起始下标" /></Field><Field label="批量数量"><CompactNumberInput min={1} max={1000} value={config.batchCount} onChange={(value) => update({ batchCount: value })} ariaLabel="Apple Mail 批量数量" /></Field></div>
            <Field label="指纹策略"><CustomSelect value={config.fpPolicy} onChange={(fpPolicy) => update({ fpPolicy })} options={[{ value: 'safari', label: 'Safari 随机' }, { value: 'firefox', label: 'Firefox 147' }, { value: 'chrome', label: 'Chrome 随机' }, { value: 'edge', label: 'Edge 随机' }]} ariaLabel="指纹策略" /></Field>
            <div className="console-metrics apple-mail-mini-metrics"><MetricCard label="邮箱池" value={emails.length} /><MetricCard label="姓名池" value={names.length} /><MetricCard label="当前步骤" value={status?.currentStepLabel || status?.currentStep || '空闲'} /></div>
          </div>
          <div className="quick-control-actions"><GlassButton variant="glass" icon={Save} onClick={save}>保存本机配置</GlassButton><GlassButton variant="glass" icon={Search} onClick={testMail}>测试拉信</GlassButton><GlassButton className="primary-action" variant="primary" icon={Copy} onClick={() => copy(boot, '启动脚本')}>复制启动脚本</GlassButton></div>
        </GlassPanel>

        <AutomationFlowRunner title="Apple Mail 实时状态与日志" running={!!status?.running} progress={{ current: status?.completed || status?.currentIndex || 0, total: status?.total || config.batchCount, step: status?.currentStepLabel || status?.currentStep || 'Idle' }} logs={combinedLogs} onRefresh={load} />
      </div>

      <CollapsiblePanel title="通道与鉴权配置" summary="Mail API、导入地址、密钥和默认密码；配置完整保留">
        <div className="console-grid-wide"><Field label="Mail API Base"><input className="input-glass" value={config.mailBase} onChange={(event) => update({ mailBase: event.target.value })} /></Field><Field label="Mail Admin Auth"><input type="password" className="input-glass" value={config.adminAuth} onChange={(event) => update({ adminAuth: event.target.value })} /></Field><Field label="Import Base"><input className="input-glass" value={config.importBase} onChange={(event) => update({ importBase: event.target.value })} /></Field><Field label="Import API Key"><input type="password" className="input-glass" value={config.importApiKey} onChange={(event) => update({ importApiKey: event.target.value })} /></Field><Field label="默认密码"><input type="password" className="input-glass" value={config.defaultPassword} onChange={(event) => update({ defaultPassword: event.target.value })} /></Field></div>
      </CollapsiblePanel>

      <CollapsiblePanel title={`号池预览（${emails.length}）`} summary="前 30 个邮箱与姓名映射"><pre className="log-viewer apple-mail-pool-preview">{emails.slice(0, 30).map((email, index) => `${String(index).padStart(3, '0')}  ${email}  |  ${names[index % Math.max(names.length, 1)] || ''}`).join('\n') || '号池为空'}</pre></CollapsiblePanel>

      <CollapsiblePanel title="完整注入脚本" summary="复制完整控制台脚本或检查启动参数" actions={<GlassButton variant="glass" icon={Copy} disabled={!consoleScript} onClick={() => copy(consoleScript, '完整控制台脚本')}>复制完整脚本</GlassButton>}><textarea readOnly className="input-glass console-code apple-mail-script" value={boot} /></CollapsiblePanel>

      {testOutput ? <CollapsiblePanel title="拉信测试结果" summary="最近一次 Mail API 测试响应" defaultOpen><OutputBox value={testOutput} title="拉信测试结果" filename="apple-mail-test.json" onClear={() => setTestOutput(null)} /></CollapsiblePanel> : null}
    </div>
  );
}
