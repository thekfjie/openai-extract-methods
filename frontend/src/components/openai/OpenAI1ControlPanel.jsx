import React from 'react';
import { Bot, Play, Save, ShieldCheck, Square } from 'lucide-react';
import GlassButton from '../../ui/GlassButton';
import GlassPanel from '../../ui/GlassPanel';
import Skeleton from '../../ui/Skeleton';
import CustomSelect from '../../ui/CustomSelect';
import { CollapsiblePanel, CompactNumberInput, StatusBadge, Toggle } from '../../ui/ConsolePrimitives';

export default function OpenAI1ControlPanel({
  config,
  setConfig,
  status,
  groups,
  mailGroups,
  sub2apiGroups,
  accounts,
  loading,
  initialLoading,
  preflight,
  onPreflight,
  onSave,
  onStart,
  onStop,
}) {
  const change = (patch) => setConfig((current) => ({ ...current, ...patch }));
  const mailGroupOptions = (mailGroups || []).map((group) => ({ value: group.name, label: group.name }));
  const sub2apiGroupOptions = (sub2apiGroups || []).map((group) => ({
    value: group.name,
    label: [group.name, group.platform, group.status].filter(Boolean).join(' · '),
  }));

  if (initialLoading) {
    return <GlassPanel variant="strong" className="openai-control-panel openai-control-skeleton"><Skeleton height="54px" borderRadius="0" /><div>{[42, 34, 34, 42, 42, 86].map((height, index) => <Skeleton key={index} height={`${height}px`} borderRadius="8px" />)}</div><Skeleton height="58px" borderRadius="0" /></GlassPanel>;
  }

  return (
    <GlassPanel id="openai1-control-panel" variant="strong" className="openai-control-panel">
      <div className="openai-panel-heading">
        <div><h2><Bot size={18} />注册控制</h2><small>OpenAI1 · 有头 Chromium</small></div>
        <StatusBadge ok={!!status?.running}>{status?.running ? '运行中' : (status?.phase || '空闲')}</StatusBadge>
      </div>

      <div className="openai-control-scroll">
        <label className="console-field console-field-wide">
          <span>注册代理</span>
          <input className="input-glass" value={config.custom_proxy_url || ''} onChange={(event) => change({ custom_proxy_url: event.target.value })} onBlur={onSave} placeholder="host:port:user:pass 或 http://user:pass@host:port" />
          <small>输入后会自动保存；刷新页面会从服务器和本机草稿恢复。</small>
        </label>

        <div className="openai-compact-fields">
          <label className="console-field"><span>并发</span><CompactNumberInput value={1} min={1} max={1} disabled ariaLabel="并发" /></label>
          <label className="console-field"><span>本批数量</span><CompactNumberInput value={config.total || 1} min={1} max={50} onChange={(total) => change({ total })} ariaLabel="本批数量" /></label>
        </div>

        <label className="console-field console-field-wide">
          <span>账号来源分组</span>
          <CustomSelect value={config.mail_source_group || ''} onChange={(mail_source_group) => change({ mail_source_group })} options={groups.map((group) => ({ value: group.name, label: group.name }))} ariaLabel="账号来源分组" />
        </label>

        <label className="console-field console-field-wide">
          <span>账号分配</span>
          <CustomSelect value={config.selected_account_id || ''} onChange={(id) => {
            const account = accounts.find((item) => String(item.id) === id);
            change({ selected_account_id: id, selected_account_email: account?.email || '', total: id ? 1 : config.total });
          }} options={[{ value: '', label: '自动分配（推荐）' }, ...accounts.map((account) => ({ value: String(account.id), label: account.email || String(account.id) }))]} ariaLabel="账号分配" />
        </label>

        <CollapsiblePanel title="高级注册配置" summary="分组流转、指纹、指定号码与诊断开关" storageKey="openai4-advanced" defaultOpen>
          <div className="console-form-grid">
            <label className="console-field"><span>执行中分组</span><CustomSelect value={config.mail_pending_group || ''} onChange={(mail_pending_group) => change({ mail_pending_group })} options={mailGroupOptions} ariaLabel="执行中分组" /></label>
            <label className="console-field"><span>成功分组</span><CustomSelect value={config.mail_success_group || ''} onChange={(mail_success_group) => change({ mail_success_group })} options={mailGroupOptions} ariaLabel="成功分组" /></label>
            <label className="console-field"><span>坏邮箱分组</span><CustomSelect value={config.mail_bad_group || ''} onChange={(mail_bad_group) => change({ mail_bad_group })} options={mailGroupOptions} ariaLabel="坏邮箱分组" /></label>
            <label className="console-field"><span>Sub2API 分组</span><CustomSelect value={config.sub2api_group || ''} onChange={(sub2api_group) => change({ sub2api_group })} options={sub2apiGroupOptions} ariaLabel="Sub2API 分组" /></label>
            <label className="console-field"><span>指纹模块</span><CustomSelect value={config.fingerprint_enabled !== false ? 'true' : 'false'} onChange={(next) => change({ fingerprint_enabled: next === 'true' })} options={[{ value: 'true', label: '启用' }, { value: 'false', label: '关闭' }]} ariaLabel="指纹模块" /></label>
            <label className="console-field"><span>指纹来源</span><CustomSelect value={config.fingerprint_source || 'local'} onChange={(fingerprint_source) => change({ fingerprint_source })} options={[{ value: 'local', label: '本地 / API' }, { value: 'cloud', label: '云端' }]} ariaLabel="指纹来源" /></label>
            <label className="console-field console-field-wide"><span>指纹 Seed</span><input className="input-glass" value={config.fingerprint_seed || ''} onChange={(event) => change({ fingerprint_seed: event.target.value })} placeholder="留空随机" /></label>
            <label className="console-field console-field-wide"><span>指定手机号</span><input className="input-glass" value={config.forced_phone || ''} onChange={(event) => change({ forced_phone: event.target.value })} placeholder="一般留空自动取号" /></label>
          </div>
          <div className="console-toggle-grid">
            <Toggle checked={config.fingerprint_strict !== false} onChange={(value) => change({ fingerprint_strict: value })} label="严格指纹模式" hint="指纹 API 失败时阻止启动" />
            <Toggle checked={!!config.sub2api_import_use_signup_proxy} onChange={(value) => change({ sub2api_import_use_signup_proxy: value })} label="Sub2API 跟随注册代理" hint="关闭时固定使用独立 JP 兜底 http://172.19.0.1:7903" />
            <Toggle checked={config.get_refresh_token !== false} onChange={(value) => {
              if (!value && !window.confirm('关闭后，本次只完成邮箱注册，不执行 OAuth、不会获取 RT，也不会导入 Sub2API。确定关闭吗？')) return;
              change({ get_refresh_token: value });
            }} label="获取 RT 并导入" hint="开启：OAuth 获取 RT、写回 Mail Opus 并导入 Sub2API；关闭：注册完成后立即停止" />
            <Toggle checked={!!config.auth_only} onChange={(value) => change({ auth_only: value })} label="仅重新授权" />
            <Toggle checked={!!config.traffic_meter} onChange={(value) => change({ traffic_meter: value })} label="启用流量统计" />
          </div>
        </CollapsiblePanel>

        {preflight ? <div className={`openai-preflight ${preflight.ok ? 'ok' : 'failed'}`}><b>启动前检查：{preflight.ok ? '通过' : '未通过'}</b><span>注册代理 {preflight.proxy?.reachable ? '可达' : '不可达'} · 导入代理 {preflight.sub2apiProxy?.skipped ? '跳过' : (preflight.sub2apiProxy?.reachable ? `${preflight.sub2apiProxy?.proxy || '可达'}（${preflight.sub2apiProxy?.followSignupProxy ? '跟随' : '独立'}）` : '不可达')} · 邮箱 {preflight.mail?.checked ?? 0} · 手机 {preflight.phone?.reachable ? (preflight.phone?.mode === 'forced' ? '指定号码可用' : '自动取号已配置') : '不可用'} · RT {config.get_refresh_token !== false ? '获取' : '跳过'} · Sub2API {preflight.sub2api?.skipped ? '跳过' : (preflight.sub2api?.reachable ? '可达' : '未达')} · 指纹 {config.fingerprint_enabled ? (preflight.fingerprint?.reachable ? '可达' : '不可达') : '关闭'} · VNC {preflight.display?.headed ? '有头可观察' : '异常'}</span></div> : null}
      </div>

      <div className="openai-control-actions">
        <GlassButton variant="glass" onClick={onPreflight} loading={loading} icon={ShieldCheck}>预检</GlassButton>
        <GlassButton variant="glass" onClick={onSave} icon={Save}>保存</GlassButton>
        {!status?.running ? <GlassButton variant="primary" onClick={onStart} loading={loading} icon={Play}>开始</GlassButton> : <GlassButton variant="danger" onClick={onStop} icon={Square}>停止</GlassButton>}
      </div>
    </GlassPanel>
  );
}
