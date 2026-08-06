import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { CheckCircle2, CircleDot, Copy, CreditCard, ExternalLink, KeyRound, Layers3, Play, RefreshCw, ShieldCheck, Square, Trash2 } from 'lucide-react';
import cardPaymentPortalApi from '../../api/cardPaymentPortal';
import GlassButton from '../../ui/GlassButton';
import GlassPanel from '../../ui/GlassPanel';
import CustomSelect from '../../ui/CustomSelect';
import { CompactNumberInput, DataTable, ErrorBanner, Field, MetricCard, OutputBox, StatusBadge, Toggle } from '../../ui/ConsolePrimitives';

const TERMINAL = new Set(['done', 'ready', 'error', 'failed', 'cancelled']);
const splitLines = (value) => String(value || '').replace(/\r/g, '').split('\n').map((item) => item.trim()).filter(Boolean);
const jsonCards = (value) => {
  const text = String(value || '').trim();
  if (!text) return [];
  try { return JSON.parse(text); } catch (_) { return splitLines(text); }
};

function usePolling(fetcher, active, interval = 1500) {
  const [value, setValue] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    if (!active) return undefined;
    let disposed = false;
    let timer = null;
    const tick = async () => {
      try {
        const next = await fetcher();
        if (disposed) return;
        setValue(next); setError(null);
        const status = next?.job?.status || next?.status;
        if (!TERMINAL.has(status)) timer = setTimeout(tick, interval);
      } catch (reason) { if (!disposed) { setError(reason); timer = setTimeout(tick, interval * 2); } }
    };
    tick();
    return () => { disposed = true; clearTimeout(timer); };
  }, [active, fetcher, interval]);
  return { value, error, setValue };
}

function DashboardTab({ health, cdk, jobs, protocolJobs, refresh }) {
  const active = jobs.filter((item) => !TERMINAL.has(item.status)).length + protocolJobs.filter((item) => !TERMINAL.has(item.status)).length;
  return <>
    <GlassPanel variant="strong" className="payment-center-hero">
      <div className="payment-center-hero-copy"><span><ShieldCheck size={16} />直卡协议 · 一条龙后半段</span><h2>Checkout、卡绑定与协议确认</h2><p>短链来源、卡绑定、Checkout、批量确认和任务状态统一在当前直卡协议中处理。</p></div>
      <div className="payment-center-hero-state"><StatusBadge ok={health?.ok}>{health?.ok ? '服务在线' : '等待服务'}</StatusBadge><strong>{health?.mode || 'RECOVERED'}</strong></div>
    </GlassPanel>
    <div className="console-metrics payment-center-metrics">
      <MetricCard label="运行任务" value={active} hint="短链与协议支付合计" tone={active ? 'warning' : undefined} />
      <MetricCard label="短链任务" value={jobs.length} hint="直卡任务状态机" />
      <MetricCard label="CDK 剩余" value={cdk?.session?.remaining_uses ?? 0} hint={cdk?.valid ? '当前 CDK 有效' : '管理员模式 / 未激活'} tone={cdk?.valid ? 'success' : undefined} />
      <MetricCard label="临时浏览器" value={health?.temporary_browser ? 'ON' : 'OFF'} hint="当前备份固定为纯后端状态机" />
    </div>
    <GlassPanel className="portal-operation-panel"><div><h3>直卡接口状态</h3><small>Checkout、历史短链、卡绑定、协议确认与用量管理均已接入当前页面。</small></div><GlassButton variant="glass" icon={RefreshCw} onClick={refresh}>刷新全部</GlassButton></GlassPanel>
  </>;
}

function QuickCheckoutTab({ onOutput }) {
  const [form, setForm] = useState({ accessToken: '', entry: '', exit: '' });
  const [taskID, setTaskID] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const fetchTask = useCallback(() => cardPaymentPortalApi.getQuickCheckout(taskID), [taskID]);
  const polled = usePolling(fetchTask, !!taskID);
  useEffect(() => { if (polled.value) onOutput(polled.value); }, [onOutput, polled.value]);
  const start = async () => {
    setBusy(true); setError(null);
    try {
      const result = await cardPaymentPortalApi.createQuickCheckout({ access_token: form.accessToken, entry_proxy_pool: splitLines(form.entry), exit_proxy_pool: splitLines(form.exit) });
      setTaskID(result.task_id); onOutput(result);
    } catch (reason) { setError(reason); } finally { setBusy(false); }
  };
  const cancel = async () => { if (taskID) onOutput(await cardPaymentPortalApi.cancelQuickCheckout(taskID)); };
  return <>
    <ErrorBanner error={error || polled.error} />
    <div className="payment-center-workspace"><GlassPanel className="payment-center-config">
      <div className="payment-center-panel-head"><span><CreditCard size={17} />A · 快速 Checkout</span><StatusBadge>{taskID ? 'TRACKING' : 'READY'}</StatusBadge></div>
      <div className="console-grid">
        <Field label="AT / Session JSON" wide><textarea className="input-glass console-code portal-large-input" value={form.accessToken} onChange={(event) => setForm({ ...form, accessToken: event.target.value })} /></Field>
        <Field label="代理池 1 · US" hint={`${splitLines(form.entry).length} 条`}><textarea className="input-glass console-code" value={form.entry} onChange={(event) => setForm({ ...form, entry: event.target.value })} /></Field>
        <Field label="代理池 2 · TR" hint={`${splitLines(form.exit).length} 条`}><textarea className="input-glass console-code" value={form.exit} onChange={(event) => setForm({ ...form, exit: event.target.value })} /></Field>
      </div>
      <div className="payment-center-actions"><GlassButton variant="primary" icon={Play} loading={busy} onClick={start}>创建任务</GlassButton><GlassButton variant="danger" icon={Square} disabled={!taskID || TERMINAL.has(polled.value?.status)} onClick={cancel}>停止</GlassButton><GlassButton variant="glass" onClick={async () => onOutput(await cardPaymentPortalApi.clearQuickCheckouts())}>清理任务槽</GlassButton></div>
    </GlassPanel><GlassPanel className="payment-center-runtime"><div className="payment-center-panel-head"><span>任务状态</span><StatusBadge ok={polled.value?.status === 'done'}>{polled.value?.status || 'IDLE'}</StatusBadge></div>
      <div className="portal-progress"><i style={{ width: `${polled.value?.progress || 0}%` }} /></div><b>{polled.value?.progress || 0}% · {polled.value?.message || '等待创建'}</b>
      {polled.value?.result?.checkout_url || polled.value?.result?.short_link ? <GlassButton variant="primary" icon={ExternalLink} onClick={() => window.open(polled.value.result.checkout_url || polled.value.result.short_link, '_blank', 'noopener,noreferrer')}>打开 Checkout</GlassButton> : null}
    </GlassPanel></div>
  </>;
}

function ProtocolTab({ onOutput, onJob }) {
  const [form, setForm] = useState({ accessToken: '', checkoutUrl: '', proxies: '', cards: '', deferConfirm: false });
  const [jobID, setJobID] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const fetchJob = useCallback(() => cardPaymentPortalApi.getProtocolJob(jobID), [jobID]);
  const polled = usePolling(fetchJob, !!jobID);
  useEffect(() => { if (polled.value) { onOutput(polled.value); onJob(polled.value.job); } }, [onJob, onOutput, polled.value]);
  const start = async () => {
    setBusy(true); setError(null);
    try {
      const result = await cardPaymentPortalApi.createProtocolJob({ access_token: form.accessToken, checkout_url: form.checkoutUrl, proxy_pool: splitLines(form.proxies), cards: jsonCards(form.cards), defer_confirm: form.deferConfirm });
      setJobID(result.job?.id || ''); onOutput(result); onJob(result.job);
    } catch (reason) { setError(reason); } finally { setBusy(false); }
  };
  return <>
    <ErrorBanner error={error || polled.error} />
    <div className="payment-center-workspace"><GlassPanel className="payment-center-config">
      <div className="payment-center-panel-head"><span><Layers3 size={17} />A · 独立协议支付</span><StatusBadge>{form.deferConfirm ? 'PREPARE' : 'DIRECT'}</StatusBadge></div>
      <div className="console-grid">
        <Field label="AT / Session JSON" wide><textarea className="input-glass console-code" value={form.accessToken} onChange={(event) => setForm({ ...form, accessToken: event.target.value })} /></Field>
        <Field label="Checkout URL" wide><input className="input-glass console-code" value={form.checkoutUrl} onChange={(event) => setForm({ ...form, checkoutUrl: event.target.value })} /></Field>
        <Field label="代理池"><textarea className="input-glass console-code" value={form.proxies} onChange={(event) => setForm({ ...form, proxies: event.target.value })} /></Field>
        <Field label="卡片池 JSON / 每行一条"><textarea className="input-glass console-code" value={form.cards} onChange={(event) => setForm({ ...form, cards: event.target.value })} /></Field>
      </div>
      <Toggle checked={form.deferConfirm} onChange={(value) => setForm({ ...form, deferConfirm: value })} label="先准备，后统一确认" hint="任务到 prepared 后可与其他任务批量同步确认" />
      <div className="payment-center-actions"><GlassButton variant="primary" icon={Play} loading={busy} onClick={start}>提交协议任务</GlassButton>{polled.value?.job?.status === 'prepared' ? <GlassButton variant="primary" onClick={async () => onOutput(await cardPaymentPortalApi.confirmProtocolBatch([jobID]))}>确认当前任务</GlassButton> : null}</div>
    </GlassPanel><GlassPanel className="payment-center-runtime"><div className="payment-center-panel-head"><span>协议状态</span><StatusBadge ok={polled.value?.job?.status === 'ready'}>{polled.value?.job?.status || 'IDLE'}</StatusBadge></div><div className="portal-progress"><i style={{ width: `${polled.value?.job?.progress || 0}%` }} /></div><b>{polled.value?.job?.stage || '等待提交'}</b><small>{polled.value?.job?.message || ''}</small></GlassPanel></div>
  </>;
}

function SourceJobsTab({ sourceTasks, jobs, refresh, onOutput }) {
  const [form, setForm] = useState({ taskID: '', accessToken: '', proxies: '', cards: '', savedPaymentMethodID: '', confirmationToken: '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const selected = form.taskID || sourceTasks[0]?.task_id || '';
  const act = async (operation) => { setBusy(true); setError(null); try { const result = await operation(); onOutput(result); await refresh(); } catch (reason) { setError(reason); } finally { setBusy(false); } };
  const columns = [
    { key: 'account', label: '账号 / 任务', render: (row) => <span><b>{row.account_email || row.task_id}</b><small>{row.id}</small></span> },
    { key: 'status', label: '状态', render: (row) => <StatusBadge ok={row.status === 'ready'}>{row.status}</StatusBadge> },
    { key: 'progress', label: '进度', render: (row) => `${row.progress || 0}% · ${row.stage || row.message || ''}` },
    { key: 'actions', label: '操作', render: (row) => <div className="console-actions">{row.status === 'verification_required' ? <GlassButton variant="glass" onClick={() => act(() => cardPaymentPortalApi.resumeJob(row.id))}>续跑</GlassButton> : null}{!TERMINAL.has(row.status) ? <GlassButton variant="danger" onClick={() => act(() => cardPaymentPortalApi.cancelJob(row.id))}>停止</GlassButton> : <GlassButton variant="icon" onClick={() => act(() => cardPaymentPortalApi.deleteJob(row.id))}><Trash2 size={14} /></GlassButton>}{row.result?.short_url ? <GlassButton variant="icon" onClick={() => window.open(cardPaymentPortalApi.openJobUrl(row.id), '_blank')}><ExternalLink size={14} /></GlassButton> : null}</div> },
  ];
  return <>
    <ErrorBanner error={error} />
    <GlassPanel className="payment-center-config"><div className="payment-center-panel-head"><span><Layers3 size={17} />来源短链任务</span><StatusBadge>{sourceTasks.length} SOURCE</StatusBadge></div>
      <div className="console-grid"><Field label="来源任务 ID"><CustomSelect value={selected} onChange={(value) => setForm({ ...form, taskID: value })} options={sourceTasks.map((item) => ({ value: item.task_id, label: `${item.email || item.task_id} · ${item.has_context ? '本地上下文' : '需补 AT'}` }))} ariaLabel="来源任务 ID" /></Field><Field label="AT / 历史凭证缺失时补充" wide><textarea className="input-glass console-code" value={form.accessToken} onChange={(event) => setForm({ ...form, accessToken: event.target.value })} /></Field><Field label="代理池"><textarea className="input-glass console-code" value={form.proxies} onChange={(event) => setForm({ ...form, proxies: event.target.value })} /></Field><Field label="卡片池"><textarea className="input-glass console-code" value={form.cards} onChange={(event) => setForm({ ...form, cards: event.target.value })} /></Field><Field label="已保存 PaymentMethod"><input className="input-glass console-code" value={form.savedPaymentMethodID} onChange={(event) => setForm({ ...form, savedPaymentMethodID: event.target.value })} /></Field><Field label="ConfirmationToken"><input className="input-glass console-code" value={form.confirmationToken} onChange={(event) => setForm({ ...form, confirmationToken: event.target.value })} /></Field></div>
      <div className="payment-center-actions"><GlassButton variant="primary" loading={busy} onClick={() => act(() => cardPaymentPortalApi.createJob({ task_id: selected, access_token: form.accessToken, proxies: splitLines(form.proxies), cards: jsonCards(form.cards), saved_payment_method_id: form.savedPaymentMethodID, confirmation_token: form.confirmationToken }))}>创建直卡任务</GlassButton><GlassButton variant="glass" onClick={() => act(() => cardPaymentPortalApi.inspectSourceContext(selected, form.accessToken))}>读取上下文</GlassButton><GlassButton variant="glass" onClick={() => act(() => cardPaymentPortalApi.createServerToken(selected, form.accessToken))}>生成服务端 Token</GlassButton></div>
    </GlassPanel><GlassPanel><DataTable columns={columns} rows={jobs} rowKey={(row) => row.id} empty="暂无直卡任务" /></GlassPanel>
  </>;
}

function loadStripe() {
  if (window.Stripe) return Promise.resolve(window.Stripe);
  return new Promise((resolve, reject) => { const script = document.createElement('script'); script.src = 'https://js.stripe.com/v3/'; script.async = true; script.onload = () => resolve(window.Stripe); script.onerror = () => reject(new Error('Stripe.js 加载失败')); document.head.appendChild(script); });
}

function CardBindTab({ onOutput }) {
  const [form, setForm] = useState({ accessToken: '', proxies: '', paymentMethodID: '' });
  const [session, setSession] = useState(null); const [busy, setBusy] = useState(false); const [error, setError] = useState(null);
  const numberRef = useRef(null); const expiryRef = useRef(null); const cvcRef = useRef(null); const stripeRef = useRef(null); const cardRef = useRef(null);
  const load = async () => {
    setBusy(true); setError(null);
    try {
      let result = await cardPaymentPortalApi.createCardBindSession({ access_token: form.accessToken, proxy_pool: splitLines(form.proxies), proxy: splitLines(form.proxies)[0] });
      if (result.pending && result.key_probe_id) { for (let i = 0; i < 180; i += 1) { await new Promise((resolve) => setTimeout(resolve, 1000)); const probe = await cardPaymentPortalApi.getCardKeyProbe(result.key_probe_id); if (probe.probe?.status === 'done') { result = { ...result, ...probe.probe.session, publishable_key: probe.probe.publishable_key || probe.probe.session?.publishable_key }; break; } if (probe.probe?.status === 'error') throw new Error(probe.probe.error || '公钥探针失败'); } }
      setSession(result); onOutput(result);
      const Stripe = await loadStripe(); const stripe = Stripe(result.publishable_key); const elements = stripe.elements(); const style = { base: { color: '#f4f8ff', fontSize: '16px', '::placeholder': { color: '#708198' } } }; const number = elements.create('cardNumber', { style, showIcon: true }); const expiry = elements.create('cardExpiry', { style }); const cvc = elements.create('cardCvc', { style }); number.mount(numberRef.current); expiry.mount(expiryRef.current); cvc.mount(cvcRef.current); stripeRef.current = stripe; cardRef.current = number;
    } catch (reason) { setError(reason); } finally { setBusy(false); }
  };
  const confirm = async () => {
    setBusy(true); setError(null);
    try { const confirmed = await stripeRef.current.confirmCardSetup(session.client_secret, { payment_method: { card: cardRef.current, billing_details: session.billing_details } }); if (confirmed.error) throw confirmed.error; const paymentMethodID = typeof confirmed.setupIntent.payment_method === 'string' ? confirmed.setupIntent.payment_method : confirmed.setupIntent.payment_method?.id; setForm({ ...form, paymentMethodID }); onOutput(confirmed); } catch (reason) { setError(reason); } finally { setBusy(false); }
  };
  const setDefault = async () => { setBusy(true); try { onOutput(await cardPaymentPortalApi.setDefaultCard({ access_token: form.accessToken, payment_method_id: form.paymentMethodID, proxy: splitLines(form.proxies)[0], record_id: session?.record_id })); } catch (reason) { setError(reason); } finally { setBusy(false); } };
  return <><ErrorBanner error={error} /><div className="payment-center-workspace"><GlassPanel className="payment-center-config"><div className="payment-center-panel-head"><span><CreditCard size={17} />安全卡绑定</span><StatusBadge>{session ? 'SESSION READY' : 'WAITING'}</StatusBadge></div><div className="console-grid"><Field label="AT" wide><textarea className="input-glass console-code" value={form.accessToken} onChange={(event) => setForm({ ...form, accessToken: event.target.value })} /></Field><Field label="US 代理池" wide><textarea className="input-glass console-code" value={form.proxies} onChange={(event) => setForm({ ...form, proxies: event.target.value })} /></Field></div><GlassButton variant="primary" loading={busy} onClick={load}>加载托管卡片字段</GlassButton></GlassPanel><GlassPanel className="portal-card-elements"><Field label="卡号"><div ref={numberRef} className="portal-stripe-field" /></Field><div className="portal-element-row"><Field label="有效期"><div ref={expiryRef} className="portal-stripe-field" /></Field><Field label="CVC"><div ref={cvcRef} className="portal-stripe-field" /></Field></div><div className="payment-center-actions"><GlassButton variant="primary" disabled={!session} loading={busy} onClick={confirm}>确认 SetupIntent</GlassButton><GlassButton variant="glass" disabled={!form.paymentMethodID} onClick={setDefault}>设为默认卡</GlassButton></div>{form.paymentMethodID ? <code>{form.paymentMethodID}</code> : null}</GlassPanel></div></>;
}

function CdkTab({ cdk, refresh, onOutput }) {
  const [codes, setCodes] = useState([]); const [input, setInput] = useState(''); const [form, setForm] = useState({ quantity: 1, validDays: 30, maxActivations: 10, note: '' }); const [error, setError] = useState(null);
  const load = useCallback(async () => { try { const result = await cardPaymentPortalApi.listCdkCodes(); setCodes(result.items || []); } catch (reason) { setError(reason); } }, []);
  useEffect(() => { load(); }, [load]);
  const act = async (operation) => { setError(null); try { const result = await operation(); onOutput(result); await Promise.all([load(), refresh()]); } catch (reason) { setError(reason); } };
  const columns = [{ key: 'id', label: 'ID' }, { key: 'code_hint', label: 'CDK' }, { key: 'uses', label: '用量', render: (row) => `${row.activation_count ?? row.usage_count ?? 0} / ${row.max_activations ?? row.max_uses ?? 0}` }, { key: 'enabled', label: '状态', render: (row) => <StatusBadge ok={!!row.enabled}>{row.enabled ? '启用' : '停用'}</StatusBadge> }, { key: 'actions', label: '操作', render: (row) => <div className="console-actions"><GlassButton variant="glass" onClick={() => act(() => cardPaymentPortalApi.setCdkEnabled(row.id, !row.enabled))}>{row.enabled ? '停用' : '启用'}</GlassButton><GlassButton variant="icon" onClick={() => act(() => cardPaymentPortalApi.deleteCdk(row.id))}><Trash2 size={14} /></GlassButton></div> }];
  return <><ErrorBanner error={error} /><div className="console-metrics payment-center-metrics"><MetricCard label="当前会话" value={cdk?.valid ? 'VALID' : 'NONE'} tone={cdk?.valid ? 'success' : undefined} /><MetricCard label="剩余次数" value={cdk?.session?.remaining_uses ?? 0} /><MetricCard label="CDK 总数" value={codes.length} /></div><div className="payment-center-workspace"><GlassPanel className="payment-center-config"><div className="payment-center-panel-head"><span><KeyRound size={17} />公开 CDK 操作</span></div><Field label="CDK / 每行一个" wide><textarea className="input-glass console-code" value={input} onChange={(event) => setInput(event.target.value)} /></Field><div className="payment-center-actions"><GlassButton variant="primary" onClick={() => act(() => cardPaymentPortalApi.activateCdk(splitLines(input)[0]))}>激活</GlassButton><GlassButton variant="glass" onClick={() => act(() => cardPaymentPortalApi.mergeCdks(splitLines(input)))}>合并并激活</GlassButton><GlassButton variant="glass" onClick={() => act(() => cardPaymentPortalApi.queryCdkTasks(splitLines(input)[0]))}>查询任务</GlassButton><GlassButton variant="glass" onClick={() => act(() => cardPaymentPortalApi.lookupCdkMerge(splitLines(input)[0]))}>合并链</GlassButton></div></GlassPanel><GlassPanel className="payment-center-config"><div className="payment-center-panel-head"><span>管理员创建</span></div><div className="portal-number-grid"><Field label="数量"><CompactNumberInput value={form.quantity} min={1} max={100} onChange={(value) => setForm({ ...form, quantity: value })} /></Field><Field label="有效天数"><CompactNumberInput value={form.validDays} min={1} max={3650} onChange={(value) => setForm({ ...form, validDays: value })} /></Field><Field label="可用次数"><CompactNumberInput value={form.maxActivations} min={1} max={10000} onChange={(value) => setForm({ ...form, maxActivations: value })} /></Field></div><Field label="备注"><input className="input-glass" value={form.note} onChange={(event) => setForm({ ...form, note: event.target.value })} /></Field><div className="payment-center-actions"><GlassButton variant="primary" onClick={() => act(() => cardPaymentPortalApi.createCdkCodes({ quantity: form.quantity, valid_days: form.validDays, max_activations: form.maxActivations, note: form.note }))}>生成 CDK</GlassButton><GlassButton variant="glass" onClick={() => act(() => cardPaymentPortalApi.adminMergeCdks(splitLines(input)))}>管理员合并</GlassButton></div></GlassPanel></div><GlassPanel><DataTable columns={columns} rows={codes} rowKey={(row) => row.id} empty="暂无 CDK" /></GlassPanel></>;
}

export default function RecoveredCardPortal() {
  const [tab, setTab] = useState('dashboard'); const [health, setHealth] = useState(null); const [cdk, setCdk] = useState(null); const [sourceTasks, setSourceTasks] = useState([]); const [jobs, setJobs] = useState([]); const [protocolJobs, setProtocolJobs] = useState([]); const [output, setOutput] = useState(null); const [error, setError] = useState(null); const [loading, setLoading] = useState(false);
  const rememberProtocolJob = useCallback((job) => { if (job?.id) setProtocolJobs((current) => [job, ...current.filter((item) => item.id !== job.id)].slice(0, 50)); }, []);
  const refresh = useCallback(async () => { setLoading(true); setError(null); const results = await Promise.allSettled([cardPaymentPortalApi.health(), cardPaymentPortalApi.cdkStatus(), cardPaymentPortalApi.sourceTasks(), cardPaymentPortalApi.listJobs()]); if (results[0].status === 'fulfilled') setHealth(results[0].value); if (results[1].status === 'fulfilled') setCdk(results[1].value); if (results[2].status === 'fulfilled') setSourceTasks(results[2].value.items || []); if (results[3].status === 'fulfilled') setJobs(results[3].value.items || []); const rejected = results.find((item) => item.status === 'rejected'); if (rejected) setError(rejected.reason); setLoading(false); }, []);
  useEffect(() => { refresh(); const timer = setInterval(refresh, 5000); return () => clearInterval(timer); }, [refresh]);
  const tabs = [['dashboard', '一条龙总览'], ['checkout', 'Checkout 任务'], ['protocol', '协议确认'], ['source', '历史短链'], ['bind', '安全卡绑定'], ['cdk', '用量管理']];
  return <section className="portal-recovered-workspace" aria-label="直卡协议后半段"><ErrorBanner error={error} onRetry={refresh} /><div className="segmented-tabs portal-tabs">{tabs.map(([value, label]) => <button type="button" className={`segmented-tab ${tab === value ? 'active' : ''}`} key={value} onClick={() => setTab(value)}>{label}</button>)}</div>{tab === 'dashboard' ? <DashboardTab health={health} cdk={cdk} jobs={jobs} protocolJobs={protocolJobs} refresh={refresh} /> : null}{tab === 'checkout' ? <QuickCheckoutTab onOutput={setOutput} /> : null}{tab === 'protocol' ? <ProtocolTab onOutput={setOutput} onJob={rememberProtocolJob} /> : null}{tab === 'source' ? <SourceJobsTab sourceTasks={sourceTasks} jobs={jobs} refresh={refresh} onOutput={setOutput} /> : null}{tab === 'bind' ? <CardBindTab onOutput={setOutput} /> : null}{tab === 'cdk' ? <CdkTab cdk={cdk} refresh={refresh} onOutput={setOutput} /> : null}{output ? <OutputBox value={output} title="直卡协议 API 输出" filename="direct-card-protocol-result.json" onClear={() => setOutput(null)} /> : null}{loading ? <span className="portal-refresh-note"><RefreshCw size={13} className="spin" />正在同步直卡任务</span> : null}</section>;
}
