import React from 'react';
import { Bot, Play, Settings, ShieldCheck, Square } from 'lucide-react';
import AutomationFlowRunner from '../workflow/AutomationFlowRunner';
import GlassButton from '../../ui/GlassButton';
import GlassPanel from '../../ui/GlassPanel';
import CustomSelect from '../../ui/CustomSelect';
import {
  CollapsiblePanel,
  CompactNumberInput,
  Field,
  MetricCard,
  Toggle,
} from '../../ui/ConsolePrimitives';

export default function OpenAI3Workbench({
  config,
  setConfig,
  status = {},
  groups = [],
  accounts = [],
  preflight,
  logs = [],
  traffic,
  loading,
  onPreflight,
  onSave,
  onStart,
  onStop,
  onRefresh,
}) {
  const update = (patch) => setConfig((current) => ({ ...current, ...patch }));
  const selectAccount = (id) => {
    const account = accounts.find((item) => String(item.id) === id);
    update({
      selected_account_id: id,
      selected_account_email: account?.email || '',
      selected_account_group: account?.groupName || '',
      total: id ? 1 : config.total,
    });
  };

  return (
    <div className="operations-stack openai3-workbench">
      <div className="operations-priority-layout">
        <GlassPanel variant="strong" className="quick-control-panel">
          <div className="quick-control-heading">
            <div>
              <h2><Bot size={17} />OpenAI 3 快速控制</h2>
              <small>常用任务参数和启动操作，不再被高级配置挤到页面后面</small>
            </div>
            <span className={`openai-live-indicator ${status.running ? 'active' : ''}`}><i />{status.running ? '运行中' : '待机'}</span>
          </div>

          <div className="quick-control-body">
            <Field label="注册代理" hint="支持直接粘贴完整认证代理链接">
              <input className="input-glass" value={config.proxy || ''} onChange={(event) => update({ proxy: event.target.value })} placeholder="http://user:pass@host:port" />
            </Field>

            <div className="quick-control-row">
              <Field label="并发">
                <CompactNumberInput value={config.concurrency || 1} min={1} max={20} ariaLabel="注册并发" onChange={(value) => update({ concurrency: value })} />
              </Field>
              <Field label="任务总数">
                <CompactNumberInput value={config.total || 1} min={1} max={200} disabled={!!config.selected_account_id} ariaLabel="注册总数" onChange={(value) => update({ total: value })} />
              </Field>
            </div>

            <Field label="账号来源分组">
              <CustomSelect value={config.mail_source_group || ''} onChange={(mail_source_group) => update({ mail_source_group, selected_account_id: '', selected_account_email: '', selected_account_group: '' })} options={groups.map((group) => ({ value: group.name, label: group.name }))} ariaLabel="账号来源分组" />
            </Field>

            <Field label="账号分配" hint={config.selected_account_id ? '已锁定单账号，本批总数自动设为 1' : '默认由后端自动分配可用邮箱'}>
              <CustomSelect value={config.selected_account_id || ''} onChange={selectAccount} options={[{ value: '', label: '自动分配（推荐）' }, ...accounts.map((account) => ({ value: String(account.id), label: account.email || String(account.id) }))]} ariaLabel="账号分配" />
            </Field>

            {preflight ? (
              <div className={`operation-status-note ${preflight.proxy?.configured ? 'ok' : 'warning'}`}>
                <b>最近预检：{preflight.proxy?.configured ? `代理可达（${preflight.proxy?.status || 'OK'}）` : '代理未配置'}</b>
                <span>邮箱已查 {preflight.mail?.checked ?? 0} 个 · Sub2API {preflight.sub2api?.status || '待检查'}</span>
              </div>
            ) : (
              <div className="operation-status-note"><b>尚未执行启动前检查</b><span>建议变更代理、邮箱来源或指纹后先运行预检。</span></div>
            )}
          </div>

          <div className="quick-control-actions">
            <GlassButton variant="glass" onClick={onPreflight} loading={loading} icon={ShieldCheck}>启动前检查</GlassButton>
            <GlassButton variant="glass" onClick={onSave} icon={Settings}>保存配置</GlassButton>
            {!status.running ? (
              <GlassButton className="primary-action" variant="primary" onClick={onStart} loading={loading} icon={Play}>开始注册</GlassButton>
            ) : (
              <GlassButton className="primary-action" variant="danger" onClick={onStop} icon={Square}>停止当前任务</GlassButton>
            )}
          </div>
        </GlassPanel>

        <AutomationFlowRunner
          title="OpenAI 3 实时流程"
          running={!!status.running}
          progress={{
            current: status.completed || 0,
            total: status.total || config.total,
            step: status.phase || 'Idle',
          }}
          logs={logs}
          onStart={onStart}
          onStop={onStop}
          onRefresh={onRefresh}
        />
      </div>

      <CollapsiblePanel title="高级注册配置" summary="分组映射、邮箱密码、指纹与流量统计；默认收起，字段完整保留">
        <div className="operation-detail-grid">
          <div className="operation-subpanel">
            <h4>邮箱与下游分组</h4>
            <Field label="执行中分组（mail_pending）"><input className="input-glass" value={config.mail_pending_group || ''} onChange={(event) => update({ mail_pending_group: event.target.value })} /></Field>
            <Field label="成功分组（mail_success）"><input className="input-glass" value={config.mail_success_group || ''} onChange={(event) => update({ mail_success_group: event.target.value })} /></Field>
            <Field label="坏邮箱分组（mail_bad）"><input className="input-glass" value={config.mail_bad_group || ''} onChange={(event) => update({ mail_bad_group: event.target.value })} /></Field>
            <Field label="Sub2API 目标分组"><input className="input-glass" value={config.sub2api_group || ''} onChange={(event) => update({ sub2api_group: event.target.value })} /></Field>
            <Field label="MAIL_PASS（邮箱密码）" hint={config.mail_pass === '***' ? '已保存；保持原值即可不变更' : '仅在 OpenAI 3 邮箱桥接需要时填写'}>
              <input type="password" className="input-glass" value={config.mail_pass || ''} onChange={(event) => update({ mail_pass: event.target.value })} placeholder={config.mail_pass === '***' ? '已保存（留空不变）' : '未设置'} />
            </Field>
          </div>

          <div className="operation-subpanel">
            <h4>浏览器指纹与运行诊断</h4>
            <Field label="指纹模块">
              <CustomSelect value={config.fingerprint_enabled !== false ? 'true' : 'false'} onChange={(next) => update({ fingerprint_enabled: next === 'true' })} options={[{ value: 'true', label: '启用 Go 指纹' }, { value: 'false', label: '关闭，使用原程序默认值' }]} ariaLabel="指纹模块" />
            </Field>
            <Field label="指纹来源">
              <CustomSelect value={config.fingerprint_source || 'local'} onChange={(fingerprint_source) => update({ fingerprint_source })} options={[{ value: 'local', label: '本地生成' }, { value: 'cloud', label: 'Roxy 云端' }]} ariaLabel="指纹来源" />
            </Field>
            <Field label="指纹 Seed" hint="留空时每个任务随机生成"><input className="input-glass" value={config.fingerprint_seed || ''} onChange={(event) => update({ fingerprint_seed: event.target.value })} /></Field>
            <Toggle checked={config.fingerprint_strict !== false} onChange={(value) => update({ fingerprint_strict: value })} label="严格指纹模式" hint="指纹失败时阻止启动，不静默退回默认值" />
            <Toggle checked={!!config.traffic_meter} onChange={(value) => update({ traffic_meter: value })} label="启用流量统计" hint="仍使用上游代理，本地仅做计数" />
          </div>
        </div>

        {preflight ? (
          <div className="console-metrics operation-preflight-metrics">
            <MetricCard label="代理" value={preflight.proxy?.configured ? `可达 ${preflight.proxy?.status || ''}` : '未配置'} tone={preflight.proxy?.configured ? 'success' : 'warning'} />
            <MetricCard label="邮箱检查" value={`${preflight.mail?.sourceGroup || '—'} · ${preflight.mail?.checked ?? 0}`} />
            <MetricCard label="Sub2API" value={`${preflight.sub2api?.status || '可用'} · ${preflight.sub2api?.groupName || '—'}`} />
            <MetricCard label="Mail Bridge" value={preflight.mailBridge?.port || '—'} />
          </div>
        ) : null}

        {traffic ? (
          <div className="operation-status-note"><b>最近流量记录</b><span>{typeof traffic === 'string' ? traffic : JSON.stringify(traffic)}</span></div>
        ) : null}
      </CollapsiblePanel>
    </div>
  );
}
