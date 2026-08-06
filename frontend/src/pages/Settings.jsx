import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Palette, Plus, RefreshCw, Save, Trash2 } from 'lucide-react';
import GlassPanel from '../ui/GlassPanel';
import GlassButton from '../ui/GlassButton';
import CustomSelect from '../ui/CustomSelect';
import apiClient from '../api/client';
import { CollapsiblePanel, ErrorBanner, Field, MetricCard, OutputBox, StatusBadge, Toggle } from '../ui/ConsolePrimitives';
import ThemePicker from '../ui/ThemePicker';
import { useTheme } from '../contexts/ThemeContext';
import useNavigationSub from '../hooks/useNavigationSub';

const regions = ['US', 'HK', 'JP', 'SG', 'TW', 'UK', 'KR', 'MY', 'NL', 'DE'];
const boolValue = (value) => ['1', 'true', 'yes', 'on'].includes(String(value ?? '').toLowerCase());
const publicFields = [
  'CPA_ENABLED', 'CPA_AUTH_DIR', 'CPA_REMOTE_URL', 'GROK2API_BASE_URL', 'GROK2API_POOL',
  'DOMAIN_MAIL_ROOT', 'DOMAIN_MAIL_PREFER_SUBDOMAIN', 'DOMAIN_MAIL_SUBDOMAINS', 'DOMAIN_MAIL_NAME_STYLE', 'DOMAIN_MAIL_NAME_DIGITS', 'MAIL_PREFER_INVENTORY',
  'MAIL_SOURCE_GROUP_NAME', 'MAIL_PENDING_GROUP_NAME', 'MAIL_SUCCESS_GROUP_NAME', 'MAIL_BAD_GROUP_NAME', 'GROK_MAIL_PENDING_GROUP_NAME', 'GROK_MAIL_SUCCESS_GROUP_NAME',
  'SUB2API_API_URL', 'SUB2API_IMPORT_GROUP_NAMES', 'SIGNUP_PROXY_MODE', 'SIGNUP_PROXY_REGION', 'SIGNUP_PROXY_CUSTOM_URL', 'CLIPROXY_PROXY_URL',
  'SUB2API_PROXY_REGION', 'SUB2API_PROXY_URL', 'SUB2API_PROXY_NAME', 'SUB2API_IMPORT_USE_SIGNUP_PROXY', 'TRAFFIC_METER_ENABLED',
  'OAI_FINGERPRINT_CLOUD_ENABLED', 'OAI_FINGERPRINT_CLOUD_API_BASE_URL', 'OAI_FINGERPRINT_CLOUD_HEADERS_FILE', 'OAI_FINGERPRINT_CLOUD_INCLUDE_MAC',
  'ROXY_OPENAPI_ENABLED', 'ROXY_OPENAPI_URL', 'ROXY_OPENAPI_KEY_FILE', 'ROXY_OPENAPI_TIMEOUT_SECONDS',
];
const secretFields = ['CPA_MANAGEMENT_KEY', 'CPA_API_KEY', 'GROK2API_ADMIN_KEY'];
const labels = {
  SIGNUP_PROXY_MODE: '注册代理模式', SIGNUP_PROXY_REGION: '本地代理地区', SIGNUP_PROXY_CUSTOM_URL: '自定义注册代理完整链接', CLIPROXY_PROXY_URL: 'Cliproxy 完整动态代理链接',
  SUB2API_PROXY_REGION: 'Sub2API 默认代理地区', SUB2API_PROXY_URL: 'Sub2API 自定义代理 URL', SUB2API_PROXY_NAME: 'Sub2API 代理名称', SUB2API_IMPORT_USE_SIGNUP_PROXY: '导入 Sub2API 时跟随注册代理',
  MAIL_SOURCE_GROUP_NAME: '邮箱来源分组', MAIL_PENDING_GROUP_NAME: '待授权分组', MAIL_SUCCESS_GROUP_NAME: '成功分组', MAIL_BAD_GROUP_NAME: '坏邮箱分组', SUB2API_IMPORT_GROUP_NAMES: 'Sub2API 导入目标分组',
  DOMAIN_MAIL_ROOT: '域名邮箱根域名', DOMAIN_MAIL_SUBDOMAINS: '可用子域名', DOMAIN_MAIL_NAME_STYLE: '邮箱名称风格', DOMAIN_MAIL_NAME_DIGITS: '邮箱数字位数', DOMAIN_MAIL_PREFER_SUBDOMAIN: '优先使用子域名', MAIL_PREFER_INVENTORY: '优先使用库存邮箱',
  OAI_FINGERPRINT_CLOUD_ENABLED: '使用云端基础指纹 API', OAI_FINGERPRINT_CLOUD_API_BASE_URL: '云端指纹 API 地址', OAI_FINGERPRINT_CLOUD_HEADERS_FILE: '云端指纹授权 Headers 文件', OAI_FINGERPRINT_CLOUD_INCLUDE_MAC: '云端同时申请 MAC 记录',
  ROXY_OPENAPI_ENABLED: '启用 Roxy 本地 OpenAPI', ROXY_OPENAPI_URL: 'Roxy OpenAPI 地址', ROXY_OPENAPI_KEY_FILE: 'Roxy Key 文件', ROXY_OPENAPI_TIMEOUT_SECONDS: 'Roxy 超时秒数',
  CPA_ENABLED: '启用 CPA', CPA_AUTH_DIR: 'CPA 授权目录', CPA_REMOTE_URL: 'CPA 远端地址', GROK2API_BASE_URL: 'Grok2API 地址', GROK2API_POOL: 'Grok2API 池', TRAFFIC_METER_ENABLED: '默认启用流量统计',
};
const booleanFields = new Set(['CPA_ENABLED', 'DOMAIN_MAIL_PREFER_SUBDOMAIN', 'MAIL_PREFER_INVENTORY', 'SUB2API_IMPORT_USE_SIGNUP_PROXY', 'TRAFFIC_METER_ENABLED', 'OAI_FINGERPRINT_CLOUD_ENABLED', 'OAI_FINGERPRINT_CLOUD_INCLUDE_MAC', 'ROXY_OPENAPI_ENABLED']);

function SettingsField({ name, value, onChange }) {
  if (booleanFields.has(name)) return <Toggle checked={boolValue(value)} onChange={(checked) => onChange(name, checked ? 'true' : 'false')} label={labels[name] || name} />;
  if (name === 'SIGNUP_PROXY_MODE') return <Field label={labels[name]}><CustomSelect value={value || 'regional'} onChange={(next) => onChange(name, next)} options={[{ value: 'regional', label: '本地地区代理（省钱默认）' }, { value: 'cliproxy', label: 'Cliproxy 动态家宽' }, { value: 'custom', label: '自定义代理' }]} ariaLabel={labels[name]} /></Field>;
  if (name === 'SIGNUP_PROXY_REGION' || name === 'SUB2API_PROXY_REGION') return <Field label={labels[name]}><CustomSelect value={value || 'JP'} onChange={(next) => onChange(name, next)} options={regions.map((region) => ({ value: region, label: region }))} ariaLabel={labels[name]} /></Field>;
  return <Field label={labels[name] || name}><input className="input-glass" value={value || ''} onChange={(e) => onChange(name, e.target.value)} /></Field>;
}

export default function Settings() {
  const { activeSub: activeTab, activeItem } = useNavigationSub('/settings');
  const [settings, setSettings] = useState({});
  const [secretsConfigured, setSecretsConfigured] = useState({});
  const [secretUpdates, setSecretUpdates] = useState({});
  const [appSettings, setAppSettings] = useState({});
  const [appSettingsText, setAppSettingsText] = useState('{}');
  const [purchaseSettings, setPurchaseSettings] = useState({ serviceName: 'OpenAI', serviceCode: 'dr', purchaseGroups: [] });
  const [purchaseText, setPurchaseText] = useState('{}');
  const [traffic, setTraffic] = useState(null);
  const [health, setHealth] = useState(null);
  const [proxyResult, setProxyResult] = useState(null);
  const [output, setOutput] = useState(null);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const { theme, error: themeError } = useTheme();

  const load = useCallback(async () => {
    setError(null);
    const results = await Promise.allSettled([apiClient.get('/settings'), apiClient.get('/app-settings'), apiClient.get('/purchase-settings'), apiClient.get('/traffic?tail=50'), apiClient.get('/health')]);
    if (results[0].status === 'fulfilled') { setSettings(results[0].value.settings || {}); setSecretsConfigured(results[0].value.secretsConfigured || {}); }
    if (results[1].status === 'fulfilled') { const value = results[1].value.settings || results[1].value || {}; setAppSettings(value); setAppSettingsText(JSON.stringify(value, null, 2)); }
    if (results[2].status === 'fulfilled') { const value = results[2].value.purchaseSettings || results[2].value || {}; setPurchaseSettings(value); setPurchaseText(JSON.stringify(value, null, 2)); }
    if (results[3].status === 'fulfilled') setTraffic(results[3].value);
    if (results[4].status === 'fulfilled') setHealth(results[4].value);
    const rejected = results.find((item) => item.status === 'rejected'); if (rejected) setError(rejected.reason);
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { setSettings((current) => ({ ...current, UI_THEME: theme })); }, [theme]);

  const changeSetting = (name, value) => setSettings((current) => ({ ...current, [name]: value }));
  const saveMain = async () => {
    setSaving(true); setError(null);
    try { const result = await apiClient.post('/settings', { ...settings, ...Object.fromEntries(Object.entries(secretUpdates).filter(([, value]) => value)) }); setOutput(result); setSecretUpdates({}); await load(); }
    catch (reason) { setError(reason); } finally { setSaving(false); }
  };
  const selectedProxy = () => settings.SIGNUP_PROXY_MODE === 'cliproxy' ? settings.CLIPROXY_PROXY_URL : settings.SIGNUP_PROXY_MODE === 'custom' ? settings.SIGNUP_PROXY_CUSTOM_URL : `http://172.19.0.1:${7901 + Math.max(0, regions.indexOf(settings.SIGNUP_PROXY_REGION || 'JP'))}`;
  const checkProxy = async (proxyUrl) => {
    setSaving(true); setError(null);
    try { const result = await apiClient.post('/proxy/check', { proxyUrl }); setProxyResult(result.result || result); }
    catch (reason) { setError(reason); } finally { setSaving(false); }
  };
  const saveApp = async () => {
    setSaving(true); setError(null);
    try { const parsed = JSON.parse(appSettingsText); const result = await apiClient.post('/app-settings', { settings: parsed }); setOutput(result); await load(); }
    catch (reason) { setError(reason); } finally { setSaving(false); }
  };
  const savePurchase = async () => {
    setSaving(true); setError(null);
    try { const parsed = JSON.parse(purchaseText); const result = await apiClient.post('/purchase-settings', parsed); setOutput(result); await load(); }
    catch (reason) { setError(reason); } finally { setSaving(false); }
  };
  const groups = purchaseSettings.purchaseGroups || [];
  const updateGroups = (next) => { const value = { ...purchaseSettings, purchaseGroups: next }; setPurchaseSettings(value); setPurchaseText(JSON.stringify(value, null, 2)); };

  const proxyFields = ['SIGNUP_PROXY_MODE', 'SIGNUP_PROXY_REGION', 'SIGNUP_PROXY_CUSTOM_URL', 'CLIPROXY_PROXY_URL', 'SUB2API_PROXY_REGION', 'SUB2API_PROXY_URL', 'SUB2API_PROXY_NAME', 'SUB2API_IMPORT_USE_SIGNUP_PROXY'];
  const mailFields = ['MAIL_SOURCE_GROUP_NAME', 'MAIL_PENDING_GROUP_NAME', 'MAIL_SUCCESS_GROUP_NAME', 'MAIL_BAD_GROUP_NAME', 'GROK_MAIL_PENDING_GROUP_NAME', 'GROK_MAIL_SUCCESS_GROUP_NAME', 'SUB2API_API_URL', 'SUB2API_IMPORT_GROUP_NAMES'];
  const domainFields = ['DOMAIN_MAIL_ROOT', 'DOMAIN_MAIL_PREFER_SUBDOMAIN', 'DOMAIN_MAIL_SUBDOMAINS', 'DOMAIN_MAIL_NAME_STYLE', 'DOMAIN_MAIL_NAME_DIGITS', 'MAIL_PREFER_INVENTORY'];
  const fingerprintFields = ['OAI_FINGERPRINT_CLOUD_ENABLED', 'OAI_FINGERPRINT_CLOUD_API_BASE_URL', 'OAI_FINGERPRINT_CLOUD_HEADERS_FILE', 'OAI_FINGERPRINT_CLOUD_INCLUDE_MAC', 'ROXY_OPENAPI_ENABLED', 'ROXY_OPENAPI_URL', 'ROXY_OPENAPI_KEY_FILE', 'ROXY_OPENAPI_TIMEOUT_SECONDS'];
  const serviceFields = publicFields.filter((name) => ![...proxyFields, ...mailFields, ...domainFields, ...fingerprintFields].includes(name));

  return <div className="page-container operations-page">
    <div className="page-header"><div className="page-title-group"><h1>{activeItem?.label || '系统设置'}</h1></div><GlassButton variant="glass" onClick={load} icon={RefreshCw}>重新加载</GlassButton></div>
    <ErrorBanner error={error} onRetry={load} />
    <ErrorBanner error={themeError} />
    <div className="engine-view">

    {activeTab === 'appearance' && <div className="settings-appearance">
      <GlassPanel variant="strong" style={{ padding: '1.25rem' }}>
        <div className="console-toolbar" style={{ marginBottom: '1rem' }}><div><h3 style={{ display: 'flex', alignItems: 'center', gap: '.55rem' }}><Palette size={18} />界面主题</h3><small style={{ color: 'var(--text-muted)' }}>当前主题：{theme}；保存后在所有浏览器登录时同步</small></div></div>
        <ThemePicker />
      </GlassPanel>
      <GlassPanel style={{ padding: '1.25rem' }}>
        <h3 style={{ marginBottom: '.8rem' }}>布局规则</h3>
        <div className="console-metrics"><MetricCard label="桌面侧栏" value="业务页自动收起" /><MetricCard label="工作台密度" value="高密度" /><MetricCard label="移动端" value="遮罩抽屉" /><MetricCard label="动效" value="跟随系统减弱设置" /></div>
      </GlassPanel>
    </div>}

    {activeTab === 'main_settings' && <div className="operations-stack">
      <GlassPanel style={{ padding: '1.25rem' }}><div className="console-metrics"><MetricCard label="主 API" value={health?.ok ? '运行中' : '异常'} tone={health?.ok ? 'success' : 'danger'} /><MetricCard label="Tele Auto" value={health?.teleAutoConfigured ? '已配置' : (health?.teleAutoEnabled ? '未配置' : '已禁用')} /><MetricCard label="Mail Admin" value={health?.outlookEmailAdminConfigured ? '已配置' : '未配置'} /><MetricCard label="Sub2API" value={health?.sub2apiConfigured ? '已配置' : '未配置'} /></div></GlassPanel>
      <CollapsiblePanel title="注册与 Sub2API 代理" summary="本地地区代理 / Cliproxy / 自定义代理一键切换" defaultOpen><div className="console-grid-wide">{proxyFields.map((name) => <SettingsField key={name} name={name} value={settings[name]} onChange={changeSetting} />)}</div><div className="console-actions" style={{ marginTop: '1rem' }}><GlassButton variant="primary" disabled={saving} onClick={() => checkProxy(selectedProxy())}>检测当前注册代理</GlassButton><GlassButton variant="glass" disabled={saving || !settings.CLIPROXY_PROXY_URL} onClick={() => checkProxy(settings.CLIPROXY_PROXY_URL)}>检测 Cliproxy</GlassButton></div>{proxyResult ? <OutputBox value={proxyResult} title="代理出口检测" filename="proxy-check.json" /> : null}</CollapsiblePanel>
      <CollapsiblePanel title="邮箱和 Sub2API 分组" summary="来源、待授权、成功、坏邮箱和导入目标"><div className="console-grid-wide">{mailFields.map((name) => <SettingsField key={name} name={name} value={settings[name]} onChange={changeSetting} />)}</div></CollapsiblePanel>
      <CollapsiblePanel title="域名邮箱策略"><div className="console-grid-wide">{domainFields.map((name) => <SettingsField key={name} name={name} value={settings[name]} onChange={changeSetting} />)}</div></CollapsiblePanel>
      <CollapsiblePanel title="指纹与 Roxy 集成"><div className="console-grid-wide">{fingerprintFields.map((name) => <SettingsField key={name} name={name} value={settings[name]} onChange={changeSetting} />)}</div></CollapsiblePanel>
      <CollapsiblePanel title="CPA、Grok2API 与流量"><div className="console-grid-wide">{serviceFields.map((name) => <SettingsField key={name} name={name} value={settings[name]} onChange={changeSetting} />)}</div><div className="console-grid-wide" style={{ marginTop: '1rem' }}>{secretFields.map((name) => <Field key={name} label={`${name}（${secretsConfigured[name] ? '已配置；留空不变' : '未配置'}）`}><input type="password" className="input-glass" value={secretUpdates[name] || ''} onChange={(e) => setSecretUpdates((current) => ({ ...current, [name]: e.target.value }))} /></Field>)}</div></CollapsiblePanel>
      <div className="console-actions settings-save-bar"><span>修改只会在点击保存后写入后端配置</span><GlassButton variant="primary" loading={saving} icon={Save} onClick={saveMain}>保存主配置</GlassButton></div>
    </div>}

    {activeTab === 'app_settings' && <GlassPanel style={{ padding: '1.25rem' }}><div className="console-toolbar"><div><h3>完整 App Console 配置</h3><small style={{ color: 'var(--text-muted)' }}>保留旧控制台全部高级字段；JSON 可直接编辑</small></div><GlassButton variant="primary" loading={saving} icon={Save} onClick={saveApp}>保存</GlassButton></div><textarea className="input-glass" rows="28" value={appSettingsText} onChange={(e) => setAppSettingsText(e.target.value)} style={{ marginTop: '1rem', fontFamily: 'var(--font-mono)' }} /></GlassPanel>}

    {activeTab === 'purchase_settings' && <div className="operations-stack">
      <GlassPanel style={{ padding: '1.25rem' }}><div className="console-toolbar"><h3>号码购买设置组</h3><GlassButton variant="primary" icon={Plus} onClick={() => updateGroups([...groups, { label: `设置组 ${groups.length + 1}`, enabled: true, countryName: '', countryCode: '', operator: 'any', fixedPrice: false, maxPrice: '' }])}>新增设置组</GlassButton></div><div style={{ display: 'flex', flexDirection: 'column', gap: '.8rem', marginTop: '1rem' }}>{groups.length ? groups.map((group, index) => <div key={index} className="console-grid" style={{ border: '1px solid var(--glass-border)', borderRadius: 'var(--radius-md)', padding: '1rem' }}><Field label="名称"><input className="input-glass" value={group.label || ''} onChange={(e) => updateGroups(groups.map((item, i) => i === index ? { ...item, label: e.target.value } : item))} /></Field><Field label="国家代码"><input className="input-glass" value={group.countryCode || ''} onChange={(e) => updateGroups(groups.map((item, i) => i === index ? { ...item, countryCode: e.target.value } : item))} /></Field><Field label="运营商"><input className="input-glass" value={group.operator || 'any'} onChange={(e) => updateGroups(groups.map((item, i) => i === index ? { ...item, operator: e.target.value } : item))} /></Field><Field label="最高价格"><input className="input-glass" value={group.maxPrice || ''} onChange={(e) => updateGroups(groups.map((item, i) => i === index ? { ...item, maxPrice: e.target.value } : item))} /></Field><Toggle checked={group.enabled !== false} onChange={(checked) => updateGroups(groups.map((item, i) => i === index ? { ...item, enabled: checked } : item))} label="启用此组" /><GlassButton variant="danger" icon={Trash2} onClick={() => updateGroups(groups.filter((_, i) => i !== index))}>删除</GlassButton></div>) : <span style={{ color: 'var(--text-muted)' }}>暂无购买设置组</span>}</div></GlassPanel>
      <GlassPanel style={{ padding: '1.25rem' }}><div className="console-toolbar"><h3>完整购买 JSON</h3><GlassButton variant="primary" loading={saving} icon={Save} onClick={savePurchase}>保存购买配置</GlassButton></div><textarea className="input-glass" rows="18" value={purchaseText} onChange={(e) => setPurchaseText(e.target.value)} style={{ marginTop: '1rem', fontFamily: 'var(--font-mono)' }} /></GlassPanel>
    </div>}

    {activeTab === 'traffic' && <GlassPanel style={{ padding: '1.25rem' }}><div className="console-toolbar"><h3>流量与任务历史</h3><StatusBadge ok={!!traffic?.enabled}>{traffic?.enabled ? '已启用' : '未启用'}</StatusBadge></div><OutputBox value={traffic || '暂无流量记录'} title="流量记录" filename="traffic-history.json" /></GlassPanel>}
    {output ? <CollapsiblePanel title="最近保存结果" summary="后端返回的原始配置写入结果" defaultOpen><OutputBox value={output} title="保存结果" filename="settings-output.json" onClear={() => setOutput(null)} /></CollapsiblePanel> : null}
    </div>
  </div>;
}
