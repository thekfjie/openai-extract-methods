import React from 'react';
import { Download, Play, RefreshCw, Settings, Square, Zap } from 'lucide-react';
import AutomationFlowRunner from '../workflow/AutomationFlowRunner';
import BatchExecutionMonitor from '../workflow/BatchExecutionMonitor';
import GlassButton from '../../ui/GlassButton';
import GlassPanel from '../../ui/GlassPanel';
import CustomSelect from '../../ui/CustomSelect';
import { CollapsiblePanel, CompactNumberInput, Field, Toggle } from '../../ui/ConsolePrimitives';

export default function GrokTTKWorkbench({
  config,
  setConfig,
  state = {},
  results = [],
  logs = [],
  cpaStatus = {},
  traffic,
  exportName,
  setExportName,
  loading,
  onSave,
  onStart,
  onStop,
  onSync,
  onExport,
  onImportSso,
  onRefresh,
}) {
  const running = !!state.running;
  const update = (patch) => setConfig((current) => ({ ...current, ...patch }));

  return (
    <div className="operations-stack grok-ttk-workbench">
      <div className="operations-priority-layout">
        <GlassPanel variant="strong" className="quick-control-panel">
          <div className="quick-control-heading">
            <div>
              <h2><Zap size={17} />TTK 快速控制</h2>
              <small>首屏只保留本批任务真正会反复调整的参数</small>
            </div>
            <span className={`openai-live-indicator ${running ? 'active' : ''}`}><i />{running ? '运行中' : '待机'}</span>
          </div>

          <div className="quick-control-body">
            <Field label="邮箱服务商">
              <CustomSelect value={config.emailProvider || 'duckmail'} onChange={(emailProvider) => update({ emailProvider })} options={[{ value: 'duckmail', label: 'DuckMail' }, { value: 'yyds', label: 'YYDS（接码）' }, { value: 'cloudflare', label: 'Cloudflare 随机路由' }]} ariaLabel="邮箱服务商" />
            </Field>

            <div className="quick-control-row">
              <Field label="注册总数"><CompactNumberInput value={config.registerCount || 1} min={1} max={1000} ariaLabel="TTK 注册总数" onChange={(value) => update({ registerCount: value })} /></Field>
              <Field label="并发线程"><CompactNumberInput value={config.registerThreads || 1} min={1} max={50} ariaLabel="TTK 并发线程" onChange={(value) => update({ registerThreads: value })} /></Field>
            </div>

            <Field label="线程启动间隔（秒）"><CompactNumberInput value={config.threadStartInterval || 10} min={0} max={600} ariaLabel="线程启动间隔" onChange={(value) => update({ threadStartInterval: value })} /></Field>
            <Field label="代理地址" hint="本批所有工作线程使用此代理配置">
              <input className="input-glass" value={config.proxy || ''} onChange={(event) => update({ proxy: event.target.value })} placeholder="http://user:pass@host:port" />
            </Field>

            <div className="operation-status-note">
              <b>{running ? `${state.runningCount || config.registerThreads || 0} 个 Worker 正在执行` : '任务尚未启动'}</b>
              <span>成功 {state.success || 0} · 失败 {state.failed || 0} · 产出 {results.length}</span>
            </div>
          </div>

          <div className="quick-control-actions">
            <GlassButton variant="glass" onClick={onSave} loading={loading} icon={Settings}>保存配置</GlassButton>
            <GlassButton variant="glass" onClick={onRefresh} icon={RefreshCw}>刷新状态</GlassButton>
            {!running ? (
              <GlassButton className="primary-action" variant="primary" onClick={onStart} loading={loading} icon={Play}>启动 TTK 批量任务</GlassButton>
            ) : (
              <GlassButton className="primary-action" variant="danger" onClick={onStop} loading={loading} icon={Square}>停止 TTK</GlassButton>
            )}
          </div>
        </GlassPanel>

        <AutomationFlowRunner
          title="Grok TTK 实时流程"
          running={running}
          progress={{
            current: state.success || 0,
            total: state.total || config.registerCount,
            step: state.phase || (running ? 'TTK Batch Workers Running' : 'Idle'),
          }}
          logs={logs}
          onStart={onStart}
          onStop={onStop}
          onRefresh={onRefresh}
        />
      </div>

      <BatchExecutionMonitor
        concurrency={config.registerThreads}
        onConcurrencyChange={(value) => update({ registerThreads: value })}
        stats={{
          total: state.total || config.registerCount || 0,
          success: state.success || 0,
          failed: state.failed || 0,
          running: state.runningCount || (running ? config.registerThreads : 0),
        }}
        proxyStats={{ total: cpaStatus?.proxyCount || 0, active: cpaStatus?.activeProxies || 0 }}
      />

      <CollapsiblePanel title="TTK 完整生态配置" summary="接码平台、Cloudflare、CPA、Grok2API 与自动分发开关；默认收起">
        <div className="console-toggle-grid ttk-toggle-grid">
          <Toggle checked={!!config.trafficMeter} onChange={(value) => update({ trafficMeter: value })} label="流量统计" hint="记录本批网络消耗" />
          <Toggle checked={!!config.enableNsfw} onChange={(value) => update({ enableNsfw: value })} label="注册即开启 NSFW" />
          <Toggle checked={!!config.grok2apiAutoAddRemote} onChange={(value) => update({ grok2apiAutoAddRemote: value })} label="自动导入 Grok2API" />
          <Toggle checked={!!config.cpaAutoAdd} onChange={(value) => update({ cpaAutoAdd: value })} label="自动导入 CPA" />
          <Toggle checked={!!config.grokAutoNsfw} onChange={(value) => update({ grokAutoNsfw: value })} label="Grok 自动 NSFW 流程" />
        </div>

        <div className="operation-detail-grid ttk-detail-grid">
          <div className="operation-subpanel">
            <h4>DuckMail 与 YYDS</h4>
            <Field label="DuckMail API Key"><input className="input-glass" value={config.duckmailApiKey || ''} onChange={(event) => update({ duckmailApiKey: event.target.value })} /></Field>
            <Field label="YYDS API Key"><input className="input-glass" value={config.yydsApiKey || ''} onChange={(event) => update({ yydsApiKey: event.target.value })} /></Field>
            <Field label="YYDS JWT Token"><input className="input-glass" value={config.yydsJwt || ''} onChange={(event) => update({ yydsJwt: event.target.value })} /></Field>
          </div>

          <div className="operation-subpanel">
            <h4>Cloudflare 邮箱路由</h4>
            <Field label="Worker API Base"><input className="input-glass" value={config.cloudflareApiBase || ''} onChange={(event) => update({ cloudflareApiBase: event.target.value })} placeholder="https://your-worker.example" /></Field>
            <div className="quick-control-row">
              <Field label="Auth Mode">
                <CustomSelect value={config.cloudflareAuthMode || 'none'} onChange={(cloudflareAuthMode) => update({ cloudflareAuthMode })} options={[{ value: 'none', label: 'None' }, { value: 'header', label: 'Header Auth' }]} ariaLabel="Cloudflare Auth Mode" />
              </Field>
              <Field label="API Key"><input type="password" className="input-glass" value={config.cloudflareApiKey || ''} onChange={(event) => update({ cloudflareApiKey: event.target.value })} /></Field>
            </div>
            <Field label="Custom Auth"><input className="input-glass" value={config.cloudflareCustomAuth || ''} onChange={(event) => update({ cloudflareCustomAuth: event.target.value })} /></Field>
            <Field label="Paths"><input className="input-glass" value={config.cloudflarePaths || ''} onChange={(event) => update({ cloudflarePaths: event.target.value })} /></Field>
            <Field label="默认域名组" hint="多个域名使用逗号分隔"><input className="input-glass" value={config.defaultDomains || ''} onChange={(event) => update({ defaultDomains: event.target.value })} /></Field>
          </div>

          <div className="operation-subpanel">
            <h4>Grok2API 分发</h4>
            <Field label="Pool"><input className="input-glass" value={config.grok2apiPoolName || ''} onChange={(event) => update({ grok2apiPoolName: event.target.value })} /></Field>
            <Field label="Remote Base"><input className="input-glass" value={config.grok2apiRemoteBase || ''} onChange={(event) => update({ grok2apiRemoteBase: event.target.value })} /></Field>
            <Field label="Remote App Key"><input type="password" className="input-glass" value={config.grok2apiRemoteAppKey || ''} onChange={(event) => update({ grok2apiRemoteAppKey: event.target.value })} /></Field>
          </div>

          <div className="operation-subpanel">
            <h4>CPA 分发</h4>
            <Field label="CPA Auth 目录"><input className="input-glass" value={config.cpaAuthDir || ''} onChange={(event) => update({ cpaAuthDir: event.target.value })} /></Field>
            <Field label="CPA Remote URL"><input className="input-glass" value={config.cpaRemoteUrl || ''} onChange={(event) => update({ cpaRemoteUrl: event.target.value })} /></Field>
            <Field label="CPA 管理密钥"><input type="password" className="input-glass" value={config.cpaManagementKey || ''} onChange={(event) => update({ cpaManagementKey: event.target.value })} /></Field>
            <Field label="代理预设"><input className="input-glass" value={config.proxyPreset || ''} onChange={(event) => update({ proxyPreset: event.target.value })} /></Field>
          </div>
        </div>

        <div className="operation-subpanel ttk-sso-panel">
          <h4>SSO 授权凭证导入</h4>
          <textarea className="input-glass" placeholder="粘贴已有授权凭证" value={config.ssoText || ''} onChange={(event) => update({ ssoText: event.target.value })} />
          <div className="operation-inline-actions">
            <GlassButton variant="primary" onClick={onImportSso}>导入凭证</GlassButton>
            <GlassButton variant="glass" onClick={() => update({ ssoText: '' })}>清空内容</GlassButton>
          </div>
        </div>

        <div className="operation-inline-actions ttk-save-row">
          <GlassButton variant="primary" onClick={onSave} loading={loading} icon={Settings}>保存完整生态配置</GlassButton>
        </div>
      </CollapsiblePanel>

      <CollapsiblePanel title={`产出、同步与导出（${results.length}）`} summary="低频维护操作默认收起，运行日志不会因此被推到页面底部">
        <div className="operation-support-grid">
          <div className="operation-subpanel">
            <h4>手工同步与导出</h4>
            <Field label="导出文件名">
              <div className="operation-inline-actions operation-file-row">
                <input className="input-glass" value={exportName} onChange={(event) => setExportName(event.target.value)} />
                <GlassButton variant="glass" onClick={onExport} icon={Download}>下载</GlassButton>
              </div>
            </Field>
            <GlassButton variant="primary" onClick={onSync}>强制同步至 CPA / Grok2API</GlassButton>
            {traffic ? <div className="operation-status-note"><b>最近流量记录</b><span>{typeof traffic === 'string' ? traffic : JSON.stringify(traffic)}</span></div> : null}
          </div>

          <div className="operation-subpanel ttk-results-panel">
            <div className="console-toolbar">
              <h4>TTK 产出结果库</h4>
              <GlassButton variant="icon" onClick={onRefresh} title="刷新产出"><RefreshCw size={15} /></GlassButton>
            </div>
            <div className="runner-log-stream ttk-results-stream">
              {results.length ? results.map((result, index) => (
                <div className="runner-log-line level-success" key={`${result.email || 'result'}-${index}`}>
                  <time>{result.time || 'SUCCESS'}</time>
                  <em>OK</em>
                  <span>{result.email || '—'} · {result.token ? `token ${result.token.slice(0, 30)}…` : '已完成'}</span>
                </div>
              )) : <div className="runner-log-empty">暂无 TTK 注册产出凭证</div>}
            </div>
          </div>
        </div>
      </CollapsiblePanel>
    </div>
  );
}
