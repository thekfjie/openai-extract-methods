import React, { useRef, useState } from 'react';
import { ArrowRightLeft, Copy, FileJson, Key, ShieldCheck } from 'lucide-react';
import apiClient from '../api/client';
import { useToast } from '../contexts/ToastContext';
import { Field, Toggle } from '../ui/ConsolePrimitives';
import GlassButton from '../ui/GlassButton';
import GlassPanel from '../ui/GlassPanel';
import CustomSelect from '../ui/CustomSelect';
import useNavigationSub from '../hooks/useNavigationSub';
import { extractEyJTokens } from '../utils/extractEyJ';

const OPENAI_OUTPUT_TARGETS = [
  ['sub2api', 'Sub2API'],
  ['cpa', 'CPA'],
  ['codex', 'Codex'],
  ['cockpit', 'Cockpit'],
  ['9router', '9Router'],
  ['axonhub', 'AxonHub'],
  ['codexmanager', 'Codex Manager'],
];

function jsonText(value) {
  return typeof value === 'string' ? value : JSON.stringify(value ?? '', null, 2);
}

export default function Converters() {
  const { notify } = useToast();
  const { activeSub: activeTool, activeItem } = useNavigationSub('/converters');
  const [busy, setBusy] = useState('');
  const [oaiIn, setOaiIn] = useState('');
  const [oaiTarget, setOaiTarget] = useState('sub2api');
  const [namePrefix, setNamePrefix] = useState('');
  const [planType, setPlanType] = useState('');
  const [oaiOut, setOaiOut] = useState('');
  const [tokenExtractIn, setTokenExtractIn] = useState('');
  const [tokenExtractOut, setTokenExtractOut] = useState('');
  const [tokenExtractCount, setTokenExtractCount] = useState(0);
  const [promoInput, setPromoInput] = useState('');
  const [promoToken, setPromoToken] = useState('');
  const [promoAccountId, setPromoAccountId] = useState('');
  const [promoDeviceId, setPromoDeviceId] = useState('');
  const [promoProxy, setPromoProxy] = useState('');
  const [promoDirect, setPromoDirect] = useState(false);
  const [promoOut, setPromoOut] = useState('');
  const [promoResult, setPromoResult] = useState(null);
  const oaiFileInput = useRef(null);

  const copyValue = async (value, label = '内容') => {
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      notify(`${label}已复制`, 'success');
    } catch (error) {
      notify(error.message || '复制失败', 'error');
    }
  };

  const handleOaiConvert = async () => {
    if (!oaiIn.trim()) return notify('请先输入凭证', 'warning');
    setBusy('convert');
    try {
      const result = await apiClient.post('/convert/openai', { input: oaiIn, target: oaiTarget, namePrefix, planType });
      setOaiOut(jsonText(result.output ?? result));
      const targetLabel = OPENAI_OUTPUT_TARGETS.find(([value]) => value === oaiTarget)?.[1] || oaiTarget;
      notify(`已转换 ${result.count || 0} 条凭证为 ${targetLabel}`, 'success');
    } catch (error) {
      setOaiOut(jsonText(error.data || { error: error.message }));
      notify(error.message || '转换失败', 'error');
    } finally {
      setBusy('');
    }
  };

  const handleOaiFileImport = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    try {
      setOaiIn(await file.text());
      notify(`已载入 ${file.name}，点击“转换”生成 Sub2API 格式`, 'success');
    } catch (error) {
      notify(error.message || '文件读取失败', 'error');
    }
  };

  const handleOaiExport = () => {
    if (!oaiOut.trim()) return notify('请先转换出结果', 'warning');
    const blob = new Blob([oaiOut], { type: 'application/json;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `sub2api-data-${new Date().toISOString().replace(/[:.]/g, '-')}.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    notify('Sub2API JSON 已导出到本机', 'success');
  };

  const handleTokenExtract = () => {
    const tokens = extractEyJTokens(tokenExtractIn);
    if (!tokens.length) {
      setTokenExtractOut('');
      setTokenExtractCount(0);
      return notify('没有找到 eyJ 开头的 accessToken', 'warning');
    }
    setTokenExtractOut(tokens.join('\n'));
    setTokenExtractCount(tokens.length);
    notify(`已提取 ${tokens.length} 条 eyJ accessToken`, 'success');
  };

  const clearTokenExtract = () => {
    setTokenExtractIn('');
    setTokenExtractOut('');
    setTokenExtractCount(0);
  };

  const handlePromoFill = () => {
    try {
      const data = JSON.parse(promoInput);
      const account = Array.isArray(data.accounts)
        ? (data.accounts.find((item) => item?.credentials?.access_token || item?.credentials?.accessToken) || data.accounts[0] || {})
        : (data.account || data);
      const credentials = account.credentials || data.credentials || {};
      const tokens = account.tokens || data.tokens || {};
      const refreshToken = data.refreshToken || data.refresh_token || credentials.refresh_token || credentials.refreshToken || tokens.refresh_token;
      if (data.accessToken || data.access_token || tokens.access_token || credentials.access_token || credentials.accessToken) {
        setPromoToken(data.accessToken || data.access_token || tokens.access_token || credentials.access_token || credentials.accessToken);
      }
      if (data.account?.id || data.accountId || data.account_id || data.chatgpt_account_id || tokens.account_id || credentials.chatgpt_account_id || credentials.account_id) {
        setPromoAccountId(data.account?.id || data.accountId || data.account_id || data.chatgpt_account_id || tokens.account_id || credentials.chatgpt_account_id || credentials.account_id);
      }
      if (data.deviceId || data.device_id || credentials.device_id || credentials.deviceId) {
        setPromoDeviceId(data.deviceId || data.device_id || credentials.device_id || credentials.deviceId);
      }
      setPromoOut(refreshToken && !(data.accessToken || data.access_token || tokens.access_token || credentials.access_token || credentials.accessToken)
        ? '当前内容只有 refresh_token/RT；月优惠检测需要 access_token，请粘贴完整 session/auth JSON。'
        : '提取成功，可以开始检测。');
    } catch {
      setPromoOut('Error: 无法解析 JSON');
    }
  };

  const handlePromoCheck = async () => {
    if (!promoInput.trim() && !promoToken.trim()) return notify('请先输入凭证 JSON 或 Access Token', 'warning');
    setBusy('promo');
    try {
      const result = await apiClient.post('/tools/chatgpt-promo-check', {
        input: promoInput,
        accessToken: promoToken,
        accountId: promoAccountId,
        deviceId: promoDeviceId,
        proxy: promoProxy,
        direct: promoDirect,
      });
      setPromoResult(result);
      setPromoOut(jsonText(result));
      notify('月优惠检测完成', result?.ok === false ? 'warning' : 'success');
    } catch (error) {
      const failure = error.data || { ok: false, error: error.message };
      setPromoResult(failure);
      setPromoOut(jsonText(failure));
      notify(error.message || '检测失败', 'error');
    } finally {
      setBusy('');
    }
  };

  const clearPromo = () => {
    setPromoInput('');
    setPromoToken('');
    setPromoAccountId('');
    setPromoDeviceId('');
    setPromoProxy('');
    setPromoDirect(false);
    setPromoOut('');
    setPromoResult(null);
  };

  const promoSummary = promoResult && typeof promoResult === 'object' ? (() => {
    // The checker response is { ok, status, summary: { ... }, data }.
    // Keep the mapping tied to that contract instead of looking for summary
    // fields at the response root (which made every card display “—”).
    const response = promoResult.result && typeof promoResult.result === 'object' ? promoResult.result : promoResult;
    const summary = response.summary && typeof response.summary === 'object' ? response.summary : {};
    const nodes = Array.isArray(summary.promoNodes) ? summary.promoNodes : (Array.isArray(summary.promo_nodes) ? summary.promo_nodes : []);
    const flags = summary.flags && typeof summary.flags === 'object' ? summary.flags : {};
    const status = response.status ?? response.httpStatus ?? response.statusCode ?? (response.ok ? 200 : '—');
    const success = response.ok === true || response.success === true || (Number(status) >= 200 && Number(status) < 300);
    const errorText = response.error || response.message || response.upstreamError || '';
    return {
      success, status, email: response.email || '—', accountId: response.accountId || summary.accountId || '—',
      plan: summary.planType || '未返回',
      monthly: summary.monthlyPromoGuess,
      campaign: summary.monthlyPromoCampaign && typeof summary.monthlyPromoCampaign === 'object' ? summary.monthlyPromoCampaign : {},
      evidence: summary.monthlyPromoEvidence || '未返回判断依据',
      proxy: response.proxyUsed === true ? '已使用' : (response.proxyUsed === false ? '直连' : (promoProxy ? '已使用' : '直连')),
      backend: response.backend || '—', error: errorText, nodes, flags,
      accountKeys: Array.isArray(summary.accountKeys) ? summary.accountKeys : [],
      topKeys: Array.isArray(summary.topKeys) ? summary.topKeys : [],
    };
  })() : null;

  return (
    <div className="page-container operations-page converter-page">
      <div className="page-header">
        <div className="page-title-group"><h1>{activeItem?.label || '凭证与 Token 转换'}</h1></div>
      </div>

      <div className="converter-workspace">
        {activeTool === 'convert' ? (
          <GlassPanel variant="strong" className="converter-panel">
            <div className="converter-heading"><div><h2><ArrowRightLeft size={18} />OpenAI 凭证格式转换</h2></div></div>
            <div className="converter-form">
              <Field label="输入凭证 / RT"><textarea className="input-glass console-code" rows={6} placeholder={'ChatGPT session / Sub2API JSON / Codex auth.json\n也支持 OpenAI Refresh Token（每行一个）'} value={oaiIn} onChange={(event) => setOaiIn(event.target.value)} /></Field>
              <div className="quick-control-row converter-option-grid"><Field label="输出格式"><CustomSelect value={oaiTarget} onChange={setOaiTarget} options={OPENAI_OUTPUT_TARGETS.map(([value, label]) => ({ value, label }))} ariaLabel="输出格式" /></Field><Field label="Name Prefix"><input className="input-glass" value={namePrefix} onChange={(event) => setNamePrefix(event.target.value)} placeholder="[testplus]" /></Field><Field label="Plan Type"><input className="input-glass" value={planType} onChange={(event) => setPlanType(event.target.value)} placeholder="plus" /></Field></div>
              <input ref={oaiFileInput} type="file" accept=".json,.txt,application/json,text/plain" onChange={handleOaiFileImport} hidden />
              <div className="converter-actions"><GlassButton variant="glass" onClick={() => oaiFileInput.current?.click()}>导入文件</GlassButton><GlassButton variant="primary" loading={busy === 'convert'} onClick={handleOaiConvert}>转换</GlassButton><GlassButton variant="glass" onClick={handleOaiExport} disabled={!oaiOut.trim()}>导出 Sub2API JSON</GlassButton><GlassButton variant="glass" onClick={() => copyValue(oaiOut, '转换结果')} disabled={!oaiOut.trim()}><Copy size={15} />复制</GlassButton></div>
              <Field label={`转换结果（${OPENAI_OUTPUT_TARGETS.find(([value]) => value === oaiTarget)?.[1] || oaiTarget}）`}><textarea className="input-glass console-code" rows={8} readOnly placeholder="转换结果..." value={oaiOut} /></Field>
            </div>
          </GlassPanel>
        ) : null}

        {activeTool === 'token' ? (
          <GlassPanel variant="strong" className="converter-panel">
            <div className="converter-heading"><div><h2><Key size={18} />提取 eyJ accessToken</h2></div></div>
            <div className="converter-form">
              <Field label="凭证内容"><textarea className="input-glass console-code" rows={9} aria-label="eyJ 提取输入" placeholder={'粘贴 auth/session JSON、Bearer 文本或日志\n支持数组、嵌套 tokens.access_token 和多条 eyJ token'} value={tokenExtractIn} onChange={(event) => setTokenExtractIn(event.target.value)} /></Field>
              <div className="converter-actions"><GlassButton variant="primary" onClick={handleTokenExtract}>提取 eyJ</GlassButton><GlassButton variant="glass" onClick={() => copyValue(tokenExtractOut, tokenExtractCount > 1 ? '全部 Token' : 'Token')} disabled={!tokenExtractOut}><Copy size={15} />复制{tokenExtractCount > 1 ? `全部 ${tokenExtractCount} 条` : ' Token'}</GlassButton><GlassButton variant="danger" onClick={clearTokenExtract}>清空</GlassButton></div>
              <Field label={tokenExtractCount ? `提取结果 · ${tokenExtractCount} 条` : '提取结果'}><textarea className="input-glass console-code converter-success-output" rows={8} aria-label="eyJ 提取结果" readOnly placeholder="提取后的 eyJ... accessToken 会逐行显示在这里" value={tokenExtractOut} /></Field>
            </div>
          </GlassPanel>
        ) : null}

        {activeTool === 'promo' ? (
          <GlassPanel variant="strong" className="converter-panel">
            <div className="converter-heading"><div><h2><ShieldCheck size={18} />检测月优惠资格</h2><small>从 JSON 提取参数后通过真实后端检查 Token、Account ID、Device ID 与代理</small></div></div>
            <div className="converter-promo-grid">
              <div className="converter-form">
                <Field label="从 JSON 响应提取参数"><textarea className="input-glass console-code" rows={8} placeholder='{"accessToken":"eyJ...","account":{"id":"..."},"deviceId":"..."}' value={promoInput} onChange={(event) => setPromoInput(event.target.value)} /></Field>
                <GlassButton variant="glass" onClick={handlePromoFill} icon={FileJson}>从 JSON 提取字段</GlassButton>
              </div>
              <div className="operation-subpanel converter-promo-fields">
                <Field label="Access Token"><input className="input-glass" value={promoToken} onChange={(event) => setPromoToken(event.target.value)} placeholder="Bearer access token / eyJ..." /></Field>
                <Field label="Account ID"><input className="input-glass" value={promoAccountId} onChange={(event) => setPromoAccountId(event.target.value)} placeholder="ChatGPT-Account-ID" /></Field>
                <Field label="Device ID"><input className="input-glass" value={promoDeviceId} onChange={(event) => setPromoDeviceId(event.target.value)} placeholder="OAI-Device-Id，可空自动生成" /></Field>
                <Field label="代理"><input className="input-glass" value={promoProxy} onChange={(event) => setPromoProxy(event.target.value)} placeholder="http://user:pass@host:port" /></Field>
                <Toggle checked={promoDirect} onChange={setPromoDirect} label="直连（不用代理）" />
                <div className="converter-actions"><GlassButton variant="primary" loading={busy === 'promo'} onClick={handlePromoCheck}>检测月优惠</GlassButton><GlassButton variant="danger" onClick={clearPromo}>清空</GlassButton></div>
              </div>
            </div>
            {promoSummary ? (
              <div className="operation-subpanel converter-promo-result" aria-live="polite">
                <div className="converter-promo-result-head"><b>{promoSummary.success ? '检测成功' : '检测完成，但需要处理'}</b><span className={`status-badge ${promoSummary.success ? 'bg-success' : 'bg-error'}`}>{String(promoSummary.status)}</span></div>
                <div className="converter-promo-result-grid">
                  <div><span>账号 / 邮箱</span><strong>{promoSummary.email}<small>{promoSummary.accountId}</small></strong></div>
                  <div><span>套餐类型</span><strong>{String(promoSummary.plan)}</strong></div>
                  <div><span>月优惠判断</span><strong>{promoSummary.monthly === true ? '命中：可用' : promoSummary.monthly === false ? '命中：不可用' : '未返回明确 eligible 布尔值'}</strong></div>
                  <div><span>代理 / HTTP 客户端</span><strong>{String(promoSummary.proxy)}<small>{String(promoSummary.backend)}</small></strong></div>
                </div>
                <div className="converter-promo-match"><span>本次实际匹配依据</span><code>{promoSummary.evidence}{promoSummary.campaign?.id ? `；优惠=${promoSummary.campaign.id}` : ''}{Object.keys(promoSummary.flags).length ? `；flags: ${Object.entries(promoSummary.flags).map(([key, value]) => `${key}=${JSON.stringify(value)}`).join(' · ')}` : ''}{promoSummary.nodes.length ? `；promoNodes: ${promoSummary.nodes.length} 个` : '；promoNodes: 0 个'}</code></div>
                {promoSummary.error ? <div className="converter-promo-error"><span>上游错误</span><code>{String(promoSummary.error)}</code></div> : null}
                {promoSummary.nodes.length ? <div className="converter-promo-nodes"><span>优惠节点摘要（实际响应路径）</span>{promoSummary.nodes.slice(0, 6).map((node, index) => <code key={`${node.path || index}`}>{node.path || '$'}: {JSON.stringify(node.value ?? node).slice(0, 360)}</code>)}</div> : null}
              </div>
            ) : null}
            <Field label="完整响应"><textarea className="input-glass console-code" rows={8} readOnly placeholder="完整 JSON 响应" value={promoOut} /></Field>
          </GlassPanel>
        ) : null}
      </div>
    </div>
  );
}
