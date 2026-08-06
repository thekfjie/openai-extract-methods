import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { CheckCircle2, CircleDot, Copy, CreditCard, ExternalLink, Globe2, Phone, RefreshCw, Search, ShieldCheck, Square, TerminalSquare, Wifi } from 'lucide-react';
import apiClient from '../api/client';
import paypalProtocolApi from '../api/paypalProtocol';
import cardPaymentPortalApi from '../api/cardPaymentPortal';
import GlassButton from '../ui/GlassButton';
import GlassPanel from '../ui/GlassPanel';
import CustomSelect from '../ui/CustomSelect';
import { CollapsiblePanel, CompactNumberInput, ErrorBanner, Field, MetricCard, StatusBadge, Toggle } from '../ui/ConsolePrimitives';

const initialForm = {
  paypalUrl: '', country: 'GB', phone: '', proxies: '', maxCardAttempts: 5,
};

const PAYPAL_FORM_STORAGE_KEY = 'automyai.paypal.form.v1';

const browserStorageKeys = {
  extractionForm: 'automyai.card.unified.extraction-form.v1',
  protocolForm: 'automyai.card.unified.protocol-form.v1',
  protocolPairs: 'automyai.card.unified.protocol-pairs.v1',
  bulkPairs: 'automyai.card.unified.bulk-pairs.v1',
  cardFlowState: 'automyai.card.unified.card-flow-state.v2',
};

function loadStoredObject(key, defaults) {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(key) || '{}');
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return defaults;
    const normalized = { ...defaults };
    Object.entries(parsed).forEach(([field, value]) => {
      if (!Object.prototype.hasOwnProperty.call(defaults, field)) {
        normalized[field] = value;
        return;
      }
      const defaultValue = defaults[field];
      if (typeof defaultValue === 'string') {
        normalized[field] = Array.isArray(value)
          ? value.map((item) => String(item || '').trim()).filter(Boolean).join('\n')
          : String(value ?? defaultValue);
      } else if (typeof defaultValue === 'number') {
        const numberValue = Number(value);
        normalized[field] = Number.isFinite(numberValue) ? numberValue : defaultValue;
      } else if (typeof defaultValue === 'boolean') {
        normalized[field] = typeof value === 'boolean' ? value : value === 'true' ? true : value === 'false' ? false : defaultValue;
      } else {
        normalized[field] = value;
      }
    });
    return normalized;
  } catch (_) { return defaults; }
}

function loadStoredText(key) {
  try { return window.localStorage.getItem(key) || ''; } catch (_) { return ''; }
}

function storeBrowserValue(key, value) {
  try { window.localStorage.setItem(key, typeof value === 'string' ? value : JSON.stringify(value)); } catch (_) {}
}

function loadPayPalForm() {
  const stored = loadStoredObject(PAYPAL_FORM_STORAGE_KEY, {});
  const parsedAttempts = Number.parseInt(stored.maxCardAttempts, 10);
  const proxies = Array.isArray(stored.proxies)
    ? stored.proxies.map((item) => String(item || '').trim()).filter(Boolean).join('\n')
    : String(stored.proxies || '');
  return {
    ...initialForm,
    paypalUrl: String(stored.paypalUrl || stored.baToken || ''),
    country: String(stored.country || initialForm.country).trim().toUpperCase() || initialForm.country,
    phone: String(stored.phone || ''),
    proxies,
    maxCardAttempts: Number.isFinite(parsedAttempts) ? Math.max(1, Math.min(5, parsedAttempts)) : initialForm.maxCardAttempts,
  };
}

const supportTone = {
  real_ok: 'bg-success',
  theoretical_ok: 'bg-warning',
  unsupported: 'bg-error',
};

const cardInitialForm = {
  accessToken: '', proxyPool1: '', proxyPool2: '', proxyProtocol: 'socks5h', promoCampaign: 'plus-1-month-free',
  amountGate: 'strict_zero', amountThreshold: 0, allowUnknownAmount: false, maxAttempts: 10,
  batchConcurrency: 3, timeout: 90, diagnoseCoupon: true, accountId: '', deviceId: '',
  sessionTraceId: '', userAgent: '', sessionCookies: '',
};

const amountGateOptions = [
  { value: 'strict_zero', label: '严格等于 0' },
  { value: 'at_most', label: '不高于指定金额' },
  { value: 'at_least', label: '不低于指定金额' },
  { value: 'any_known', label: '任意已识别金额' },
];

function extractCardTokens(raw) {
  const found = [];
  const seen = new Set();
  const add = (value) => {
    const token = String(value || '').trim().replace(/^Bearer\s+/i, '');
    if (token.split('.').length === 3 && !seen.has(token)) { seen.add(token); found.push(token); }
  };
  const walk = (value) => {
    if (Array.isArray(value)) { value.forEach(walk); return; }
    if (value && typeof value === 'object') {
      add(value.accessToken || value.access_token || value.token);
      Object.values(value).forEach(walk);
    }
  };
  const text = String(raw || '').trim();
  if (text.startsWith('{') || text.startsWith('[')) { try { walk(JSON.parse(text)); } catch (_) {} }
  for (const match of text.match(/[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g) || []) add(match);
  return found.slice(0, 100);
}

function cardTokenIdentity(token) {
  try {
    const segment = String(token || '').split('.')[1] || '';
    const bytes = atob(segment.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(segment.length / 4) * 4, '='));
    const claims = JSON.parse(decodeURIComponent(Array.from(bytes).map((char) => '%' + char.charCodeAt(0).toString(16).padStart(2, '0')).join('')));
    const profile = claims['https://api.openai.com/profile'] || {};
    const email = String(profile.email || claims.email || '').trim();
    const rawName = String(profile.name || claims.name || '').trim();
    const fallback = email.split('@')[0].replace(/[._-]+/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase());
    return { email, name: rawName || fallback };
  } catch (_) { return { email: '', name: '' }; }
}

function friendlyCardError(reason) {
  let raw = String(reason?.data?.error || reason?.message || reason || '').trim();
  raw = raw.replace(/^RuntimeError:\s*/i, '');
  if (raw.includes('ACCOUNT_ALREADY_RUNNING')) {
    const detail = raw.replace(/^ACCOUNT_ALREADY_RUNNING:\s*/i, '').trim();
    return `该账号当前正在其他支付流程中运行：${detail || '请等待当前流程结束后再加载卡片框。'}`;
  }
  if (raw.includes('ACCOUNT_API_BASE_MISSING')) return '卡片账户接口尚未配置，安全卡片输入框暂时不可加载。';
  if (raw.includes('AT_INVALIDATED_OR_EXPIRED') || /HTTP\s*401/i.test(raw)) return '当前 AT 已失效，请更换 AT 后重新加载。';
  if (raw.includes('UPSTREAM_ROUTE_BLOCKED') || /HTTP\s*403/i.test(raw)) return '当前 US 代理无法访问卡片服务，请更换 US 代理。';
  if (raw.includes('UPSTREAM_RATE_LIMITED') || /HTTP\s*429/i.test(raw)) return '请求过于频繁，请稍后再试。';
  if (/wrong_version_number/i.test(raw)) return '代理 TLS 协议类型不匹配；常见原因是 HTTP 代理被写成 https://，请改成 http:// 后重试。';
  if (/curl:\s*\(35\)|connection reset by peer|recv failure/i.test(raw)) return 'US 代理或上游连接被重置，请更换 US 代理后重试。';
  if (raw.includes('BIND_SESSION_REFETCH_EXHAUSTED') || raw.includes('Stripe initialization/refetch failed')) return '卡片会话尚未传播完成，请稍后重新加载安全卡片输入框。';
  if (raw.includes('OPENAI_CONFIRM_BLOCKED')) return '官方 Checkout 拒绝了当前确认请求；卡片与提链结果已保留，可重新生成 Checkout 后再试。';
  const preflight = raw.match(/PROXY_PREFLIGHT_FAILED:\s*([A-Z]{2})\s*(\d+)\/(\d+),\s*([A-Z]{2})\s*(\d+)\/(\d+)/i);
  if (preflight) return `正式提链代理预检未通过：${preflight[1].toUpperCase()} ${preflight[2]}/${preflight[3]}，${preflight[4].toUpperCase()} ${preflight[5]}/${preflight[6]}。绑卡结果已保留；补充对应地区可用代理后点击“重试提链”。`;
  const paymentPreflight = raw.match(/US_PROXY_PREFLIGHT_FAILED:\s*(\d+)\/(\d+)(?:;\s*protocol=([^;\s]+))?(?:;\s*attempts=(\d+))?/i);
  if (paymentPreflight) return `最终支付阶段 US 代理预检 ${paymentPreflight[1]}/${paymentPreflight[2]}；协议 ${String(paymentPreflight[3] || '当前选择').toUpperCase()}，已检查 ${paymentPreflight[4] || 3} 次。绑卡与提链结果均已保留，请重试最终支付或切换该代理实际支持的协议。`;
  if (raw.includes('PROXY_')) return '代理格式或代理连接不正确，请检查对应代理池。';
  if (raw.includes('No permissions to access this checkout session')) return '当前账号无法访问该 Checkout，请为这个账号重新提链。';
  const stripeMeta = [reason?.type, reason?.code, reason?.decline_code].map((value) => String(value || '').trim()).filter(Boolean);
  const detail = raw || '操作失败，请检查当前步骤后重试。';
  return stripeMeta.length ? detail + '（Stripe: ' + [...new Set(stripeMeta)].join(' / ') + '）' : detail;
}

const protocolInitialForm = {
  accessToken: '', proxyPool: '', proxyPool2: '', proxyProtocol: 'socks5h', entryProxyCountry: 'US', exitProxyCountry: 'TR', batchConcurrency: 3, timeout: 90, accountId: '', deviceId: '',
  sessionTraceId: '', userAgent: '', sessionCookies: '',
  billingName: '', billingEmail: '', billingPhone: '', billingLine1: '', billingLine2: '',
  billingCity: '', billingState: '', billingPostalCode: '', billingCountry: 'PH',
  checkoutCountry: 'PH', checkoutCurrency: 'PHP',
  protocolMode: 'auto', paymentMethodType: 'card', setupFutureUsage: 'off_session',
  returnUrl: '', finalConcurrency: 3, cardRetryCount: 2, cardRetryDelay: 1, linkOnlyMode: false, bindOnlyMode: false,
};

const protocolModeOptions = [
  { value: 'auto', label: '自动（0 元 Setup / 非 0 Subscription）' },
  { value: 'setup', label: 'Setup' },
  { value: 'subscription', label: 'Subscription' },
];

const proxyProtocolOptions = [
  { value: 'socks5h', label: 'SOCKS5H · 粘性 / DNS 走代理' },
  { value: 'socks5', label: 'SOCKS5 · 粘性' },
  { value: 'http', label: 'HTTP CONNECT' },
];

const setupFutureUsageOptions = [
  { value: 'off_session', label: 'off_session' },
  { value: 'on_session', label: 'on_session' },
  { value: 'none', label: '不指定' },
];

const TAX_FREE_ADDRESS_STORAGE_KEY = 'automyai.payment.us-tax-free-address.v1';
const taxFreeStateOptions = [
  { value: '', label: '随机免税州' },
  { value: 'AK', label: 'AK · Alaska' },
  { value: 'DE', label: 'DE · Delaware' },
  { value: 'MT', label: 'MT · Montana' },
  { value: 'NH', label: 'NH · New Hampshire' },
  { value: 'OR', label: 'OR · Oregon' },
];

function readTaxFreeAddress() {
  try {
    const item = JSON.parse(window.localStorage.getItem(TAX_FREE_ADDRESS_STORAGE_KEY) || 'null');
    return item?.schema === 'automyai.us-tax-free-address.v1' ? item : null;
  } catch (_) { return null; }
}

function BillingAddressApiPanel() {
  const [item, setItem] = useState(readTaxFreeAddress);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const address = item?.address || null;
  const profile = item?.profile || null;

  const loadAddress = useCallback(async () => {
    setBusy(true);
    setError('');
    try {
      const result = await cardPaymentPortalApi.billingAddress('');
      if (!result?.item?.address || !result?.item?.profile) throw new Error('地址 API 没有返回完整姓名、邮箱与地址');
      setItem(result.item);
      try { window.localStorage.setItem(TAX_FREE_ADDRESS_STORAGE_KEY, JSON.stringify(result.item)); } catch (_) {}
      window.dispatchEvent(new CustomEvent('automyai-apply-us-tax-free-address', { detail: { ...result.item.address, ...result.item.profile } }));
    } catch (reason) {
      setError(reason?.message || String(reason));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    try {
      const cached = readTaxFreeAddress();
      if (cached?.billing) {
        const { billing: _discarded, ...clean } = cached;
        window.localStorage.setItem(TAX_FREE_ADDRESS_STORAGE_KEY, JSON.stringify(clean));
        setItem(clean);
      }
      if (cached?.address && cached?.profile) return;
    } catch (_) {}
    loadAddress();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return <div className="classic-billing-refresh payment-tax-free-inline">
    <span>{profile && address ? '账单资料已在后台随机生成' : busy ? '正在生成账单资料…' : error || '账单资料待生成'}</span>
    <button type="button" onClick={loadAddress} disabled={busy} title="随机刷新姓名、邮箱、电话与 US 免税州地址"><RefreshCw size={14} className={busy ? 'spin' : ''} />刷新账单资料</button>
  </div>;
}

const newProtocolPair = (id) => ({
  id, accessToken: '', checkoutUrl: '', selected: true, status: 'idle', result: null, error: '',
});

function loadStoredProtocolPairs() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(browserStorageKeys.protocolPairs) || '[]');
    if (!Array.isArray(parsed) || !parsed.length) return [newProtocolPair('protocol-pair-1')];
    return parsed.slice(0, 50).map((item, index) => ({
      ...newProtocolPair(`protocol-pair-${index + 1}`),
      accessToken: String(item?.accessToken || ''),
      checkoutUrl: String(item?.checkoutUrl || ''),
      selected: item?.selected !== false,
    }));
  } catch (_) { return [newProtocolPair('protocol-pair-1')]; }
}

function loadStripeJs() {
  if (typeof window.Stripe === 'function') return Promise.resolve(window.Stripe);
  return new Promise((resolve, reject) => {
    const existing = document.querySelector('script[data-automyai-stripe]');
    const script = existing || document.createElement('script');
    const done = () => typeof window.Stripe === 'function' ? resolve(window.Stripe) : reject(new Error('Stripe.js 未正确加载'));
    script.addEventListener('load', done, { once: true });
    script.addEventListener('error', () => reject(new Error('Stripe 安全组件加载失败')), { once: true });
    if (!existing) {
      script.src = 'https://js.stripe.com/v3/';
      script.async = true;
      script.dataset.automyaiStripe = '1';
      document.head.appendChild(script);
    }
  });
}

function CardCheckoutProtocolWorkspace({ status, incomingPairs = [] }) {
  const restoredPairsRef = useRef(null);
  if (!restoredPairsRef.current) restoredPairsRef.current = loadStoredProtocolPairs();
  const pairSequence = useRef(restoredPairsRef.current.length + 1);
  const importedPairKeys = useRef(new Set());
  const stopRef = useRef(false);
  const stripeRef = useRef(null);
  const stripeElementsRef = useRef([]);
  const cardNumberHostRef = useRef(null);
  const cardExpiryHostRef = useRef(null);
  const cardCvcHostRef = useRef(null);
  const [form, setForm] = useState(() => loadStoredObject(browserStorageKeys.protocolForm, protocolInitialForm));
  const [pairs, setPairs] = useState(restoredPairsRef.current);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);
  const [bulkPairs, setBulkPairs] = useState(() => loadStoredText(browserStorageKeys.bulkPairs));
  const [cardLoading, setCardLoading] = useState(false);
  const [cardMounted, setCardMounted] = useState(false);
  const [cardMessage, setCardMessage] = useState('填写首组 AT、Checkout 和代理后加载安全卡片输入框');
  const [cardComplete, setCardComplete] = useState({ number: false, expiry: false, cvc: false });
  const [cardBrand, setCardBrand] = useState('CARD');
  const updateForm = (key, value) => setForm((current) => ({ ...current, [key]: value }));
  const updatePair = (id, patch) => setPairs((current) => current.map((pair) => pair.id === id ? { ...pair, ...patch } : pair));

  useEffect(() => { storeBrowserValue(browserStorageKeys.protocolForm, form); }, [form]);
  useEffect(() => {
    storeBrowserValue(browserStorageKeys.protocolPairs, pairs.map((pair) => ({
      accessToken: pair.accessToken,
      checkoutUrl: pair.checkoutUrl,
      selected: pair.selected,
    })));
  }, [pairs]);
  useEffect(() => { storeBrowserValue(browserStorageKeys.bulkPairs, bulkPairs); }, [bulkPairs]);
  useEffect(() => {
    const applyAddress = (event) => {
      const address = event?.detail || {};
      setForm((current) => ({
        ...current,
        billingLine1: String(address.line1 || ''),
        billingCity: String(address.city || ''),
        billingState: String(address.state || ''),
        billingPostalCode: String(address.postalCode || ''),
        billingCountry: String(address.country || 'US').toUpperCase(),
      }));
    };
    window.addEventListener('automyai-apply-us-tax-free-address', applyAddress);
    return () => window.removeEventListener('automyai-apply-us-tax-free-address', applyAddress);
  }, []);
  const prepared = pairs.filter((pair) => pair.status === 'prepared');
  const selectedPrepared = prepared.filter((pair) => pair.selected);
  const proxyCount = form.proxyPool.split(/\r?\n/).filter((line) => line.trim()).length;
  const cardReady = cardMounted && cardComplete.number && cardComplete.expiry && cardComplete.cvc;
  const billingReady = Boolean(form.billingName.trim() && form.billingEmail.trim()
    && form.billingLine1.trim() && form.billingCity.trim() && form.billingPostalCode.trim()
    && /^[A-Z]{2}$/.test(form.billingCountry.trim().toUpperCase()));
  const readyForFinal = prepared.filter((pair) => pair.result?.protocol?.materialsReady && cardReady && billingReady);

  const addPair = () => {
    if (pairs.length >= 50) return;
    const id = `protocol-pair-${pairSequence.current++}`;
    setPairs((current) => [...current, newProtocolPair(id)]);
  };
  const removePair = (id) => setPairs((current) => current.length === 1 ? current : current.filter((pair) => pair.id !== id));

  useEffect(() => {
    const fresh = incomingPairs.filter((item) => {
      const key = String(item?.checkoutUrl || '').trim();
      if (!key || importedPairKeys.current.has(key)) return false;
      importedPairKeys.current.add(key);
      return true;
    });
    if (!fresh.length) return;
    setPairs((current) => {
      const next = [...current];
      fresh.forEach((item) => {
        const value = {
          accessToken: String(item.accessToken || '').trim(),
          checkoutUrl: String(item.checkoutUrl || '').trim(),
        };
        const emptyIndex = next.findIndex((pair) => !pair.accessToken.trim() && !pair.checkoutUrl.trim());
        if (emptyIndex >= 0) next[emptyIndex] = { ...next[emptyIndex], ...value };
        else if (next.length < 50) next.push({ ...newProtocolPair(`protocol-pair-${pairSequence.current++}`), ...value });
      });
      return next;
    });
    const proxyPool = fresh.find((item) => String(item?.proxyPool || '').trim())?.proxyPool;
    if (proxyPool) setForm((current) => current.proxyPool.trim() ? current : { ...current, proxyPool });
  }, [incomingPairs]);

  const destroyCardElements = useCallback(() => {
    stripeElementsRef.current.forEach((element) => { try { element.destroy(); } catch (_) {} });
    stripeElementsRef.current = [];
    stripeRef.current = null;
  }, []);

  useEffect(() => () => destroyCardElements(), [destroyCardElements]);

  const importPairs = () => {
    setError(null);
    const text = bulkPairs.trim();
    if (!text) return;
    let imported = [];
    if (text.startsWith('[')) {
      try {
        const parsed = JSON.parse(text);
        imported = (Array.isArray(parsed) ? parsed : []).map((item) => ({
          accessToken: String(item?.accessToken || item?.access_token || item?.at || '').trim(),
          checkoutUrl: String(item?.checkoutUrl || item?.checkout_url || item?.url || '').trim(),
        }));
      } catch (reason) {
        setError(new Error(`批量 JSON 无法解析：${reason?.message || reason}`));
        return;
      }
    } else {
      imported = text.split(/\r?\n/).map((line) => {
        const url = line.match(/https:\/\/chatgpt\.com\/checkout\/[^\s|,，]+/i)?.[0] || '';
        const token = line.match(/[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/)?.[0] || '';
        return { accessToken: token, checkoutUrl: url };
      });
    }
    imported = imported.filter((item) => item.accessToken && item.checkoutUrl).slice(0, 50);
    if (!imported.length) {
      setError(new Error('未识别到有效配对；每行需同时包含 AT 与 Checkout 链接，或粘贴 JSON 数组'));
      return;
    }
    setPairs(imported.map((item) => newProtocolPair(`protocol-pair-${pairSequence.current++}`)).map((pair, index) => ({ ...pair, ...imported[index] })));
    setBulkPairs('');
  };

  const payloadFor = (pair) => ({
    accessToken: pair.accessToken,
    checkoutUrl: pair.checkoutUrl,
    proxyPool: form.proxyPool,
    timeout: form.timeout,
    accountId: form.accountId,
    deviceId: form.deviceId,
    sessionTraceId: form.sessionTraceId,
    userAgent: form.userAgent,
    sessionCookies: form.sessionCookies,
    cardSummary: {
      hostedElements: true, brand: cardBrand, numberComplete: cardComplete.number,
      expiryComplete: cardComplete.expiry, cvcComplete: cardComplete.cvc,
    },
    protocolOptions: {
      mode: form.protocolMode, paymentMethodType: form.paymentMethodType,
      setupFutureUsage: form.setupFutureUsage, returnUrl: form.returnUrl,
      finalConcurrency: form.finalConcurrency, cardRetryCount: form.cardRetryCount,
      cardRetryDelay: form.cardRetryDelay,
    },
    billingDetails: {
      name: form.billingName, email: form.billingEmail, phone: form.billingPhone,
      line1: form.billingLine1, line2: form.billingLine2, city: form.billingCity,
      state: form.billingState, postalCode: form.billingPostalCode, country: form.billingCountry,
    },
  });

  const loadCardFields = async () => {
    setError(null);
    const first = pairs.find((pair) => pair.accessToken.trim() && pair.checkoutUrl.trim());
    if (!first) { setError(new Error('请先填写首组 AT / Session JSON 与 Checkout 链接')); return; }
    if (!proxyCount) { setError(new Error('请先填写协议上下文代理池')); return; }
    setCardLoading(true);
    setCardMessage('正在读取 Checkout 并创建安全卡片输入会话…');
    try {
      const context = await paypalProtocolApi.loadCardElements(payloadFor(first));
      const publishableKey = context?.elements?.publishableKey || '';
      if (!publishableKey.startsWith('pk_')) throw new Error('Checkout 未返回 Stripe 公钥');
      const Stripe = await loadStripeJs();
      destroyCardElements();
      setCardComplete({ number: false, expiry: false, cvc: false });
      setCardBrand('CARD');
      const stripe = Stripe(publishableKey);
      const elements = stripe.elements();
      const computed = getComputedStyle(document.documentElement);
      const style = {
        base: {
          fontSize: '16px', fontFamily: 'Inter, system-ui, sans-serif',
          color: computed.getPropertyValue('--text-primary').trim() || '#f8fafc',
          iconColor: computed.getPropertyValue('--accent-color').trim() || '#818cf8',
          '::placeholder': { color: computed.getPropertyValue('--text-muted').trim() || '#94a3b8' },
        },
        invalid: { color: computed.getPropertyValue('--danger-color').trim() || '#f87171' },
      };
      const entries = [
        ['number', elements.create('cardNumber', { showIcon: true, style }), cardNumberHostRef.current],
        ['expiry', elements.create('cardExpiry', { style }), cardExpiryHostRef.current],
        ['cvc', elements.create('cardCvc', { style }), cardCvcHostRef.current],
      ];
      let readyCount = 0;
      entries.forEach(([name, element, host]) => {
        if (!host) throw new Error('安全卡片输入容器未就绪');
        host.replaceChildren();
        element.on('ready', () => {
          readyCount += 1;
          if (readyCount === 3) setCardMessage('安全卡片输入框已就绪，请填写卡号、有效期和 CVC');
        });
        element.on('change', (event) => {
          setCardComplete((current) => ({ ...current, [name]: Boolean(event.complete) }));
          if (name === 'number' && event.brand) setCardBrand(String(event.brand).toUpperCase());
          host.classList.toggle('field-complete', Boolean(event.complete));
          host.classList.toggle('field-invalid', Boolean(event.error));
          if (event.error) setCardMessage(event.error.message || '卡片信息无效');
        });
        element.mount(host);
      });
      stripeRef.current = stripe;
      stripeElementsRef.current = entries.map((entry) => entry[1]);
      setCardMounted(true);
    } catch (reason) {
      destroyCardElements();
      setCardMounted(false);
      setCardMessage(`加载失败：${reason?.message || reason}`);
      setError(reason instanceof Error ? reason : new Error(String(reason)));
    } finally {
      setCardLoading(false);
    }
  };

  const preparePair = async (pair) => {
    updatePair(pair.id, { status: 'preparing', result: null, error: '', selected: true });
    try {
      const result = await paypalProtocolApi.inspectCardCheckout(payloadFor(pair));
      updatePair(pair.id, { status: 'prepared', result, error: '', selected: true });
    } catch (reason) {
      updatePair(pair.id, { status: 'failed', result: null, error: reason?.message || String(reason), selected: false });
    }
  };

  const prepareAll = async (event) => {
    event.preventDefault();
    setError(null);
    stopRef.current = false;
    const runnable = pairs.filter((pair) => pair.accessToken.trim() && pair.checkoutUrl.trim());
    if (!runnable.length) { setError(new Error('请至少填写一组 AT / Session JSON 与 Checkout 链接')); return; }
    if (!proxyCount) { setError(new Error('请填写协议上下文代理池')); return; }
    setRunning(true);
    let cursor = 0;
    const workers = Array.from({ length: Math.min(runnable.length, Math.max(1, Number(form.batchConcurrency) || 1)) }, async () => {
      while (!stopRef.current && cursor < runnable.length) {
        const pair = runnable[cursor]; cursor += 1;
        await preparePair(pair);
      }
    });
    await Promise.all(workers);
    setRunning(false);
  };

  const selectPrepared = (selected) => setPairs((current) => current.map((pair) => pair.status === 'prepared' ? { ...pair, selected } : pair));
  const copySelected = async () => {
    const links = selectedPrepared.map((pair) => pair.result?.checkoutUrl).filter(Boolean);
    if (links.length) await navigator.clipboard.writeText(links.join('\n'));
  };
  const openSelected = () => selectedPrepared.forEach((pair) => window.open(pair.result.checkoutUrl, '_blank', 'noopener,noreferrer'));

  return <>
    <ErrorBanner error={error} />
    <div className="console-metrics payment-center-metrics">
      <MetricCard label="协议任务" value={pairs.length} hint="最多 50 组 AT + Checkout" />
      <MetricCard label="已准备" value={prepared.length} hint="官方上下文已复核" tone="success" />
      <MetricCard label="最终就绪" value={readyForFinal.length} hint="卡片、账单与协议材料完整" tone="success" />
      <MetricCard label="协议代理" value={proxyCount} hint="每项解析时独立使用" />
      <MetricCard label="并发" value={form.batchConcurrency} hint="上下文准备并发" />
    </div>

    <form className="protocol-workspace" onSubmit={prepareAll}>
      <GlassPanel className="protocol-shared-panel">
        <div className="payment-center-panel-head"><span><CreditCard size={17} />02 · 安全卡片与支付资料</span><StatusBadge ok={cardReady}>{cardReady ? '卡片已填写' : '请填写卡片'}</StatusBadge></div>
        <section className={`protocol-card-entry ${cardReady ? 'ready' : ''}`}>
          <div className="protocol-card-fields">
            <div className="protocol-card-fields-head"><span><CreditCard size={16} /><b>Stripe 安全卡片输入</b></span><StatusBadge ok={cardReady}>{cardReady ? '输入完整' : cardMounted ? '等待填写' : '未加载'}</StatusBadge></div>
            <Field label="卡号" wide><div ref={cardNumberHostRef} className="input-glass protocol-stripe-field" /></Field>
            <div className="protocol-card-small-fields">
              <Field label="有效期"><div ref={cardExpiryHostRef} className="input-glass protocol-stripe-field" /></Field>
              <Field label="CVC"><div ref={cardCvcHostRef} className="input-glass protocol-stripe-field" /></Field>
            </div>
            <div className="protocol-card-load-row"><GlassButton variant="primary" type="button" loading={cardLoading} onClick={loadCardFields} disabled={cardLoading || running}>加载安全卡片输入框</GlassButton><small className={cardMessage.startsWith('加载失败') ? 'error' : cardReady ? 'ok' : ''}>{cardMessage}</small></div>
          </div>
          <div className="protocol-card-visual">
            <div><span>CHECKOUT / PROTOCOL</span><b>{cardBrand}</b></div>
            <strong>•••• •••• •••• ••••</strong>
            <footer><span><small>CARD HOLDER</small><b>{form.billingName || 'CHECKOUT USER'}</b></span><span><small>SECURE INPUT</small><b>{cardReady ? 'COMPLETE' : cardMounted ? 'READY' : 'NOT LOADED'}</b></span></footer>
          </div>
        </section>
        <BillingAddressApiPanel />
        <div className="console-grid">
          <Field label="协议上下文代理池" wide hint={`每行一条，当前 ${proxyCount} 条；用于读取已有 Checkout`}>
            <textarea className="input-glass console-code payment-center-proxies" value={form.proxyPool} onChange={(event) => updateForm('proxyPool', event.target.value)} required placeholder="US proxy&#10;host:port:username:password" />
          </Field>
          <Field label="持卡人姓名"><input className="input-glass" value={form.billingName} onChange={(event) => updateForm('billingName', event.target.value)} placeholder="Card holder" /></Field>
          <Field label="账单邮箱"><input className="input-glass" type="email" value={form.billingEmail} onChange={(event) => updateForm('billingEmail', event.target.value)} placeholder="name@example.com" /></Field>
          <Field label="账单电话"><input className="input-glass" type="tel" value={form.billingPhone} onChange={(event) => updateForm('billingPhone', event.target.value)} placeholder="+63 ..." /></Field>
          <Field label="国家 / 地区代码"><input className="input-glass console-code" maxLength={2} value={form.billingCountry} onChange={(event) => updateForm('billingCountry', event.target.value.toUpperCase())} placeholder="PH" /></Field>
          <Field label="账单地址 1" wide><input className="input-glass" value={form.billingLine1} onChange={(event) => updateForm('billingLine1', event.target.value)} placeholder="Street address" /></Field>
          <Field label="账单地址 2" wide><input className="input-glass" value={form.billingLine2} onChange={(event) => updateForm('billingLine2', event.target.value)} placeholder="Apartment / Suite（选填）" /></Field>
          <Field label="城市"><input className="input-glass" value={form.billingCity} onChange={(event) => updateForm('billingCity', event.target.value)} placeholder="City" /></Field>
          <Field label="省 / 州"><input className="input-glass" value={form.billingState} onChange={(event) => updateForm('billingState', event.target.value)} placeholder="State / Province" /></Field>
          <Field label="邮编"><input className="input-glass console-code" value={form.billingPostalCode} onChange={(event) => updateForm('billingPostalCode', event.target.value)} placeholder="Postal code" /></Field>
          <Field label="准备并发"><CompactNumberInput value={form.batchConcurrency} onChange={(value) => updateForm('batchConcurrency', value)} min={1} max={10} ariaLabel="协议准备并发" /></Field>
          <Field label="协议模式"><CustomSelect value={form.protocolMode} onChange={(value) => updateForm('protocolMode', value)} options={protocolModeOptions} ariaLabel="Checkout 协议模式" /></Field>
          <Field label="支付方式"><input className="input-glass console-code" value={form.paymentMethodType} onChange={(event) => updateForm('paymentMethodType', event.target.value.toLowerCase())} placeholder="card" /></Field>
          <Field label="后续使用"><CustomSelect value={form.setupFutureUsage} onChange={(value) => updateForm('setupFutureUsage', value)} options={setupFutureUsageOptions} ariaLabel="Setup Future Usage" /></Field>
          <Field label="最终任务并发"><CompactNumberInput value={form.finalConcurrency} onChange={(value) => updateForm('finalConcurrency', value)} min={1} max={10} ariaLabel="最终任务并发" /></Field>
          <Field label="单卡重试次数"><CompactNumberInput value={form.cardRetryCount} onChange={(value) => updateForm('cardRetryCount', value)} min={0} max={10} ariaLabel="单卡重试次数" /></Field>
          <Field label="重试间隔（秒）"><CompactNumberInput value={form.cardRetryDelay} onChange={(value) => updateForm('cardRetryDelay', value)} min={0} max={30} ariaLabel="单卡重试间隔" /></Field>
          <Field label="返回地址" wide><input className="input-glass console-code" type="url" value={form.returnUrl} onChange={(event) => updateForm('returnUrl', event.target.value)} placeholder="留空跟随当前官方 Checkout" /></Field>
        </div>
        <CollapsiblePanel title="协议身份高级输入" summary="Account、Device、Session、Cookie、UA 与超时" storageKey="card-checkout-protocol-advanced">
          <div className="console-grid">
            <Field label="ChatGPT Account ID"><input className="input-glass console-code" value={form.accountId} onChange={(event) => updateForm('accountId', event.target.value)} autoComplete="off" /></Field>
            <Field label="Checkout Device ID"><input className="input-glass console-code" value={form.deviceId} onChange={(event) => updateForm('deviceId', event.target.value)} autoComplete="off" /></Field>
            <Field label="Checkout Session Trace ID"><input className="input-glass console-code" value={form.sessionTraceId} onChange={(event) => updateForm('sessionTraceId', event.target.value)} autoComplete="off" /></Field>
            <Field label="单请求超时（秒）"><CompactNumberInput value={form.timeout} onChange={(value) => updateForm('timeout', value)} min={20} max={180} ariaLabel="协议请求超时" /></Field>
            <Field label="User-Agent" wide><input className="input-glass console-code" value={form.userAgent} onChange={(event) => updateForm('userAgent', event.target.value)} autoComplete="off" placeholder="Mozilla/5.0 ..." /></Field>
            <Field label="Session Cookies" wide><textarea className="input-glass console-code payment-center-token" value={form.sessionCookies} onChange={(event) => updateForm('sessionCookies', event.target.value)} autoComplete="off" placeholder={'{"oai-did":"..."} 或 name=value; ...'} /></Field>
          </div>
        </CollapsiblePanel>
      </GlassPanel>

      <GlassPanel className="protocol-pairs-panel">
        <div className="payment-center-panel-head"><span><CreditCard size={17} />03 · AT + Checkout 任务</span><StatusBadge>{pairs.length} / 50</StatusBadge></div>
        <CollapsiblePanel title="批量导入配对" summary="每行 AT + Checkout，或 JSON 数组" storageKey="card-checkout-protocol-bulk-import">
          <div className="protocol-bulk-import">
            <textarea className="input-glass console-code payment-center-token" value={bulkPairs} onChange={(event) => setBulkPairs(event.target.value)} autoComplete="off" placeholder={'每行同时放一条 AT 和 Checkout 链接\n或 [{"accessToken":"...","checkoutUrl":"https://chatgpt.com/checkout/..."}]'} />
            <GlassButton variant="glass" type="button" onClick={importPairs} disabled={!bulkPairs.trim() || running}>识别并导入</GlassButton>
          </div>
        </CollapsiblePanel>
        <div className="protocol-pair-list">
          {pairs.map((pair, index) => <article className={`protocol-pair status-${pair.status}`} key={pair.id}>
            <div className="protocol-pair-head">
              <span><i>{String(index + 1).padStart(2, '0')}</i><b>协议任务</b><StatusBadge ok={pair.status === 'prepared'}>{pair.status === 'idle' ? '待填写' : pair.status === 'preparing' ? '准备中' : pair.status === 'prepared' ? '已准备' : '失败'}</StatusBadge></span>
              <button type="button" onClick={() => removePair(pair.id)} disabled={pairs.length === 1 || running}>删除</button>
            </div>
            <div className="protocol-pair-fields">
              <Field label="AT / Session JSON" wide><textarea className="input-glass console-code payment-center-token" value={pair.accessToken} onChange={(event) => updatePair(pair.id, { accessToken: event.target.value, status: 'idle', result: null })} required autoComplete="off" placeholder="粘贴 AT 或包含 accessToken 的 Session JSON" /></Field>
              <Field label="已有 Checkout 链接" wide><input className="input-glass console-code" type="url" value={pair.checkoutUrl} onChange={(event) => updatePair(pair.id, { checkoutUrl: event.target.value, status: 'idle', result: null })} required placeholder="https://chatgpt.com/checkout/openai_ie/oaics_..." /></Field>
            </div>
            {pair.error ? <div className="protocol-pair-error">{pair.error}</div> : null}
            {pair.result ? <div className="protocol-context-result">
              <label><input type="checkbox" checked={pair.selected} onChange={(event) => updatePair(pair.id, { selected: event.target.checked })} /><span>选择进入最终 Checkout</span></label>
              <dl>
                <div><dt>地区 / 币种</dt><dd>{pair.result.country} / {pair.result.currency}</dd></div>
                <div><dt>最终金额</dt><dd>{pair.result.amountDisplay}</dd></div>
                <div><dt>支付方式</dt><dd>{pair.result.paymentMethodTypes?.join(' · ')}</dd></div>
                <div><dt>卡协议</dt><dd>{pair.result.cardSupported ? '可用' : '不可用'}</dd></div>
                <div><dt>Stripe 上下文</dt><dd>{pair.result.publishableKeyReady && pair.result.customerSessionReady ? '完整' : '部分可用'}</dd></div>
                <div><dt>卡片输入</dt><dd>{cardReady ? `${cardBrand} · 完整` : '待填写'}</dd></div>
                <div><dt>支付资料</dt><dd>{pair.result.billing?.ready ? '完整' : `${pair.result.billing?.completed || 0} / ${pair.result.billing?.required || 6}`}</dd></div>
                <div><dt>后续使用</dt><dd>{pair.result.setupFutureUsage}</dd></div>
                <div><dt>协议模式</dt><dd>{pair.result.protocol?.mode || '—'}</dd></div>
                <div><dt>确认能力</dt><dd>{pair.result.protocol?.canConfirm ? 'Checkout 可继续' : '等待官方 Checkout 状态'}</dd></div>
                <div><dt>返回路径</dt><dd>{pair.result.protocol?.returnUrlReady ? '已准备' : '待补充'}</dd></div>
                <div><dt>协议材料</dt><dd>{pair.result.protocol?.materialsReady ? '完整' : (pair.result.protocol?.missing || []).join(' · ') || '待补充'}</dd></div>
                <div><dt>Session</dt><dd><code>{pair.result.checkoutId}</code></dd></div>
              </dl>
              <div className="payment-center-result-actions"><GlassButton variant="primary" type="button" icon={ExternalLink} onClick={() => window.open(pair.result.checkoutUrl, '_blank', 'noopener,noreferrer')}>前往官方 Checkout</GlassButton><GlassButton variant="glass" type="button" icon={Copy} onClick={() => navigator.clipboard.writeText(pair.result.checkoutUrl)}>复制链接</GlassButton></div>
            </div> : null}
          </article>)}
        </div>
        <div className="protocol-pair-tools"><GlassButton variant="glass" type="button" onClick={addPair} disabled={pairs.length >= 50 || running}>＋ 添加一组</GlassButton><span>每组独立读取并复核现有 Checkout。</span></div>
      </GlassPanel>

      <GlassPanel className="protocol-final-panel">
        <div className="payment-center-panel-head"><span><CheckCircle2 size={17} />04 · 协议准备与最终流程</span><StatusBadge ok={prepared.length > 0}>{running ? 'PREPARING' : `${prepared.length} READY`}</StatusBadge></div>
        <div className="payment-center-phase-list">
          {[['AT 与 Checkout 配对', '逐组校验账号凭证与官方链接'], ['官方上下文读取', '读取金额、币种、支付方式与 Stripe 环境'], ['安全卡片与账单资料', cardReady && billingReady ? '卡片与账单资料完整' : '填写卡号、有效期、CVC 与完整账单资料'], ['确认材料准备', readyForFinal.length ? `${readyForFinal.length} 组协议材料完整` : '汇总协议模式、CustomerSession、后续使用方式与返回路径'], ['最终 Checkout', '将已选择任务交接至对应官方页面']].map((phase, index) => <div className="payment-center-phase" key={phase[0]}><i>{index + 1}</i><span><b>{phase[0]}</b><small>{phase[1]}</small></span>{(index < 2 ? prepared.length : index === 2 ? cardReady && billingReady : index === 3 ? readyForFinal.length : selectedPrepared.length && readyForFinal.length) ? <CheckCircle2 size={16} /> : <CircleDot size={16} />}</div>)}
        </div>
        <div className="protocol-selection-actions">
          <GlassButton variant="primary" type="submit" loading={running} disabled={!status?.protocolWorkspaceAvailable}>准备全部协议任务</GlassButton>
          <GlassButton variant="glass" type="button" onClick={() => { stopRef.current = true; }} disabled={!running}>停止派发</GlassButton>
          <GlassButton variant="glass" type="button" onClick={() => selectPrepared(true)} disabled={!prepared.length}>全选已准备</GlassButton>
          <GlassButton variant="glass" type="button" onClick={() => selectPrepared(false)} disabled={!prepared.length}>取消选择</GlassButton>
          <GlassButton variant="glass" type="button" icon={Copy} onClick={copySelected} disabled={!selectedPrepared.length}>复制已选链接</GlassButton>
          <GlassButton variant="primary" type="button" icon={ExternalLink} onClick={openSelected} disabled={!selectedPrepared.length}>打开已选 Checkout（{selectedPrepared.length}）</GlassButton>
        </div>
      </GlassPanel>
    </form>
  </>;
}

function CardExtractionWorkspace({ status, refresh, loading, onCheckoutReady }) {
  const [form, setForm] = useState(() => {
    const saved = loadStoredObject(browserStorageKeys.extractionForm, cardInitialForm);
    return { ...cardInitialForm, ...saved, proxyProtocol: saved.proxyProtocol || cardInitialForm.proxyProtocol };
  });
  const [tasks, setTasks] = useState([]);
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [preflighting, setPreflighting] = useState(false);
  const [preflight, setPreflight] = useState(null);
  const stoppedRef = useRef(false);
  const active = tasks.some((item) => ['queued', 'running'].includes(item.status));
  const phases = status?.phases || [];
  const tokens = useMemo(() => extractCardTokens(form.accessToken), [form.accessToken]);
  const proxy1Count = useMemo(() => form.proxyPool1.split(/\r?\n/).filter((line) => line.trim()).length, [form.proxyPool1]);
  const proxy2Count = useMemo(() => form.proxyPool2.split(/\r?\n/).filter((line) => line.trim()).length, [form.proxyPool2]);
  const update = (key, value) => setForm((current) => ({ ...current, [key]: value }));
  useEffect(() => { storeBrowserValue(browserStorageKeys.extractionForm, form); }, [form]);

  const mergeTask = (next) => setTasks((current) => {
    const index = current.findIndex((item) => item.id === next.id);
    if (index < 0) return [...current, next];
    const copy = [...current]; copy[index] = next; return copy;
  });

  const runToken = async (accessToken, index) => {
    try {
      const created = await paypalProtocolApi.createCardJob({ ...form, accessToken, proxyProtocol: form.proxyProtocol });
      mergeTask({ ...created, batchIndex: index });
      for (;;) {
        await new Promise((resolve) => window.setTimeout(resolve, 1400));
        const response = await paypalProtocolApi.getCardJob(created.id);
        const next = { ...response.task, batchIndex: index };
        mergeTask(next);
        if (['ready', 'failed'].includes(next.status)) {
          const checkoutUrl = next?.result?.result?.url;
          if (next.status === 'ready' && checkoutUrl) onCheckoutReady?.({
            accessToken, checkoutUrl, proxyPool: form.proxyPool1, proxyPool2: form.proxyPool2, proxyProtocol: form.proxyProtocol,
          });
          return next;
        }
      }
    } catch (reason) {
      const failed = {
        id: `local-failed-${index}-${Date.now()}`, batchIndex: index, status: 'failed',
        stage: '任务创建或状态查询失败', error: reason?.message || String(reason),
      };
      mergeTask(failed);
      return failed;
    }
  };

  const runPreflight = async () => {
    setPreflighting(true);
    setError(null);
    setPreflight(null);
    try {
      const result = await paypalProtocolApi.preflightCardProxies({
        proxyPool1: form.proxyPool1, proxyPool2: form.proxyPool2, proxyProtocol: form.proxyProtocol, timeout: Math.min(30, form.timeout),
      });
      setPreflight(result);
    } catch (reason) {
      setError(reason);
    } finally {
      setPreflighting(false);
    }
  };

  const useValidProxies = () => {
    if (!preflight) return;
    update('proxyPool1', (preflight.pool1?.reachableProxies || preflight.pool1?.validProxies || []).join('\n'));
    update('proxyPool2', (preflight.pool2?.reachableProxies || preflight.pool2?.validProxies || []).join('\n'));
  };

  const submit = async (event) => {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    setTasks([]);
    stoppedRef.current = false;
    try {
      if (!tokens.length) throw new Error('没有识别到有效 AT / Session JSON');
      let cursor = 0;
      const workerCount = Math.min(tokens.length, Math.max(1, Number(form.batchConcurrency) || 1));
      const workers = Array.from({ length: workerCount }, async () => {
        while (!stoppedRef.current && cursor < tokens.length) {
          const index = cursor; cursor += 1;
          await runToken(tokens[index], index);
        }
      });
      await Promise.all(workers);
    } catch (reason) {
      setError(reason);
    } finally {
      setSubmitting(false);
    }
  };

  const stopBatch = () => { stoppedRef.current = true; };
  const copyAll = async () => {
    const links = tasks.map((item) => item?.result?.result?.url).filter(Boolean);
    if (links.length) await navigator.clipboard.writeText(links.join('\n'));
  };

  return <>
    <ErrorBanner error={error} onRetry={refresh} />
    <div className="console-metrics payment-center-metrics">
      <MetricCard label="地区" value="PH" hint="菲律宾 Checkout" />
      <MetricCard label="币种" value="PHP" hint="按两位小数比较" />
      <MetricCard label="AT" value={tokens.length} hint="最多 50 条" />
      <MetricCard label="代理池 1" value={proxy1Count} hint="创建 Checkout" />
      <MetricCard label="代理池 2" value={proxy2Count} hint="应用优惠" />
      <MetricCard label="并发 / 重试" value={`${form.batchConcurrency} / ${form.maxAttempts}`} hint="每个 AT 独立完整链路" />
    </div>

    <div className="payment-center-workspace">
      <GlassPanel className="payment-center-config">
        <div className="payment-center-panel-head"><span><CreditCard size={17} />01 · Checkout 生成参数</span><StatusBadge>当前浏览器自动保存</StatusBadge></div>
        <form onSubmit={submit}>
          <div className="console-grid">
            <Field label="AT / Session JSON 批量输入" wide hint={`已识别 ${tokens.length} / 50 条；支持 JWT、多行文本、Session JSON 和账号导出 JSON`}>
              <textarea className="input-glass console-code payment-center-token" value={form.accessToken} onChange={(event) => update('accessToken', event.target.value)} required autoComplete="off" placeholder={'每行一条 AT\n或直接粘贴包含 accessToken 的 Session JSON'} />
            </Field>
            <Field label="代理池 1 · 创建 Checkout" wide hint={`每行一条，当前 ${proxy1Count} 条；推荐 US`}>
              <textarea className="input-glass console-code payment-center-proxies" value={form.proxyPool1} onChange={(event) => update('proxyPool1', event.target.value)} required placeholder="host:port:username:password（按下方协议解析）" />
            </Field>
            <Field label="代理池 2 · 应用优惠" wide hint={`每行一条，当前 ${proxy2Count} 条；按任务配置原样使用`}>
              <textarea className="input-glass console-code payment-center-proxies" value={form.proxyPool2} onChange={(event) => update('proxyPool2', event.target.value)} required placeholder="host:port:username:password（按下方协议解析）" />
            </Field>
            <Field label="代理协议" hint="US、TR、绑卡、提链和最终支付统一使用；选择后统一覆盖每条地址的协议前缀">
              <CustomSelect value={form.proxyProtocol} onChange={(value) => update('proxyProtocol', value)} options={proxyProtocolOptions} ariaLabel="代理协议" />
            </Field>
            <div className="payment-center-preflight console-field-wide">
              <div className="payment-center-preflight-actions">
                <GlassButton variant="glass" type="button" icon={Wifi} loading={preflighting} onClick={runPreflight} disabled={!proxy1Count || !proxy2Count}>预检 US / TR 出口</GlassButton>
                {preflight ? <GlassButton variant="glass" type="button" onClick={useValidProxies} disabled={!preflight.ok}>仅保留可连接代理</GlassButton> : null}
              </div>
              {preflight ? <div className={`payment-center-preflight-result ${preflight.ok ? (preflight.regionOk ? 'ok' : 'warning') : 'error'}`}>
                <span><b>代理池 1 · 可连接 / US</b><strong>{preflight.pool1?.reachable ?? preflight.pool1?.valid ?? 0} / {preflight.pool1?.total || 0} · 地区 {preflight.pool1?.regionMatched ?? 0}</strong></span>
                <span><b>代理池 2 · 可连接 / TR</b><strong>{preflight.pool2?.reachable ?? preflight.pool2?.valid ?? 0} / {preflight.pool2?.total || 0} · 地区 {preflight.pool2?.regionMatched ?? 0}</strong></span>
                <small>{!preflight.ok ? '存在无法连接的代理' : preflight.regionOk ? '双代理池可连接，实际出口地区符合推荐配置' : `代理可以连接，但实际出口地区未全部命中推荐值（池 2：${preflight.pool2?.results?.map((item) => item.country).join(' / ') || 'UNKNOWN'}）；不会阻止任务执行`}</small>
              </div> : <small>读取实际代理出口国家；代理输入会自动保存在当前浏览器。</small>}
            </div>
            <Field label="金额门禁">
              <CustomSelect value={form.amountGate} onChange={(value) => update('amountGate', value)} options={amountGateOptions} ariaLabel="金额门禁" />
            </Field>
            <Field label="金额阈值（PHP）" hint="上限/下限模式使用">
              <input className="input-glass console-code" type="number" min="0" step="0.01" value={form.amountThreshold} disabled={!['at_most', 'at_least'].includes(form.amountGate)} onChange={(event) => update('amountThreshold', event.target.value)} />
            </Field>
            <Field label="完整链路尝试次数">
              <CompactNumberInput value={form.maxAttempts} onChange={(value) => update('maxAttempts', value)} min={1} max={50} ariaLabel="完整链路尝试次数" />
            </Field>
            <Field label="提链并发" hint="对应本地文件的批量并发，1–10">
              <CompactNumberInput value={form.batchConcurrency} onChange={(value) => update('batchConcurrency', value)} min={1} max={10} ariaLabel="提链并发" />
            </Field>
            <Field label="Plus 优惠 Campaign">
              <input className="input-glass console-code" value={form.promoCampaign} onChange={(event) => update('promoCampaign', event.target.value)} placeholder="plus-1-month-free" />
            </Field>
            <Field label="金额未知处理" wide>
              <Toggle checked={form.allowUnknownAmount} onChange={(value) => update('allowUnknownAmount', value)} label="显式允许金额未知的 Checkout" hint="默认关闭；关闭时上游未返回金额会丢弃并完整重跑" />
            </Field>
          </div>
          <CollapsiblePanel title="本地协议高级输入" summary="身份上下文、Cookie、UA、超时与优惠诊断" storageKey="card-protocol-advanced">
            <div className="console-grid">
              <Field label="ChatGPT Account ID" hint="选填；留空时自动从 AT 解析">
                <input className="input-glass console-code" value={form.accountId} onChange={(event) => update('accountId', event.target.value)} autoComplete="off" placeholder="账号 ID" />
              </Field>
              <Field label="Checkout Device ID" hint="选填；留空时每个任务自动生成并全链路固定">
                <input className="input-glass console-code" value={form.deviceId} onChange={(event) => update('deviceId', event.target.value)} autoComplete="off" placeholder="UUID / oai-did" />
              </Field>
              <Field label="Checkout Session Trace ID" hint="选填；对应本地协议的 OAI-Session-Id">
                <input className="input-glass console-code" value={form.sessionTraceId} onChange={(event) => update('sessionTraceId', event.target.value)} autoComplete="off" placeholder="会话追踪 ID" />
              </Field>
              <Field label="单请求超时（秒）" hint="20–180">
                <CompactNumberInput value={form.timeout} onChange={(value) => update('timeout', value)} min={20} max={180} ariaLabel="单请求超时" />
              </Field>
              <Field label="User-Agent" wide hint="选填；留空使用协议指纹档案">
                <input className="input-glass console-code" value={form.userAgent} onChange={(event) => update('userAgent', event.target.value)} autoComplete="off" placeholder="Mozilla/5.0 ..." />
              </Field>
              <Field label="Session Cookies" wide hint="选填；支持 JSON 对象或 name=value; name2=value2，自动保存在当前浏览器">
                <textarea className="input-glass console-code payment-center-token" value={form.sessionCookies} onChange={(event) => update('sessionCookies', event.target.value)} autoComplete="off" placeholder={'{"oai-did":"..."}\n或 cookieName=cookieValue; ...'} />
              </Field>
              <Field label="优惠资格诊断" wide>
                <Toggle checked={form.diagnoseCoupon} onChange={(value) => update('diagnoseCoupon', value)} label="创建 Checkout 前检查 Campaign 资格" hint="诊断失败不直接放行金额，最终仍以官方 Checkout 上下文为准" />
              </Field>
            </div>
          </CollapsiblePanel>
          <div className="payment-center-actions">
            <GlassButton variant="primary" type="submit" loading={submitting || active} disabled={!status?.ok || !tokens.length}>开始批量直卡协议提炼</GlassButton>
            <GlassButton variant="glass" type="button" icon={Square} onClick={stopBatch} disabled={!submitting && !active}>停止派发新 AT</GlassButton>
            <GlassButton variant="glass" type="button" icon={Copy} onClick={copyAll} disabled={!tasks.some((item) => item?.result?.result?.url)}>复制全部链接</GlassButton>
            <span>{tokens.length} 个 AT · 并发 {form.batchConcurrency} · 成功后交接官方 Checkout</span>
          </div>
        </form>
      </GlassPanel>

      <div className="payment-center-side-stack">
        <GlassPanel className="payment-center-runtime">
          <div className="payment-center-panel-head"><span><TerminalSquare size={17} />B · 运行状态</span><StatusBadge ok={tasks.length > 0 && tasks.every((item) => item.status === 'ready')}>{active ? 'RUNNING' : (tasks.length ? 'DONE' : (loading ? 'LOADING' : 'WAITING'))}</StatusBadge></div>
          <div className="payment-center-phase-list">
            {phases.map((phase, index) => <div key={phase.id} className="payment-center-phase">
              <i>{index + 1}</i><span><b>{phase.label}</b><small>{phase.detail}</small></span>
              {tasks.length > 0 && tasks.every((item) => ['ready', 'failed'].includes(item.status)) ? <CheckCircle2 size={16} /> : <CircleDot size={16} />}
            </div>)}
          </div>
          {tasks.length ? <div className="payment-center-capture-note">完成 {tasks.filter((item) => ['ready', 'failed'].includes(item.status)).length} / {tokens.length} · 成功 {tasks.filter((item) => item.status === 'ready').length} · 失败 {tasks.filter((item) => item.status === 'failed').length}</div> : null}
        </GlassPanel>

        {[...tasks].sort((left, right) => Number(left.batchIndex || 0) - Number(right.batchIndex || 0)).map((item) => {
          const result = item?.result?.result;
          return <GlassPanel className="payment-center-result" key={item.id}>
            <div className="payment-center-panel-head"><span><CheckCircle2 size={17} />C · AT {Number(item.batchIndex || 0) + 1}</span><StatusBadge ok={item.status === 'ready'}>{item.status?.toUpperCase()}</StatusBadge></div>
            {result ? <><dl>
              <div><dt>地区 / 币种</dt><dd>{result.country} / {result.currency}</dd></div>
              <div><dt>金额</dt><dd>{result.amountDisplay || result.amount}</dd></div>
              <div><dt>金额依据</dt><dd>{result.amountSource || '未识别'}</dd></div>
              <div><dt>上下文复核</dt><dd>{result.contextVerified ? '官方 Checkout 已复核' : '未复核'}</dd></div>
              <div><dt>命中尝试</dt><dd>{item.result.attempt} / {item.result.maxAttempts}</dd></div>
              <div><dt>Session</dt><dd><code>{result.checkoutId}</code></dd></div>
              <div><dt>后续步骤</dt><dd>官方 Checkout</dd></div>
            </dl><div className="payment-center-result-actions">
              <GlassButton variant="primary" icon={ExternalLink} onClick={() => window.open(result.url, '_blank', 'noopener,noreferrer')}>打开 Checkout</GlassButton>
              <GlassButton variant="glass" icon={Copy} onClick={() => navigator.clipboard.writeText(result.url)}>复制链接</GlassButton>
            </div></> : <div className="payment-center-capture-note">{item.error || item.stage || '等待执行'}</div>}
          </GlassPanel>;
        })}
      </div>
    </div>

    {tasks.some((item) => item?.result?.logs?.length) ? <CollapsiblePanel title="完整任务日志" summary="按 AT 汇总，敏感字段已脱敏" defaultOpen storageKey="card-protocol-logs">
      <pre className="log-viewer">{tasks.flatMap((item) => [`===== AT ${Number(item.batchIndex || 0) + 1} · ${item.status} =====`, ...(item?.result?.logs || [])]).join('\n')}</pre>
    </CollapsiblePanel> : null}
  </>;
}

function CardBindLinkWorkspace() {
  const numberRef = useRef(null); const expiryRef = useRef(null); const cvcRef = useRef(null);
  const stripeRef = useRef(null); const elementsRef = useRef([]); const timerRef = useRef(null); const pauseRequestedRef = useRef(false);
  const restoredWorkspaceRef = useRef(null);
  if (!restoredWorkspaceRef.current) {
    const saved = loadStoredObject(browserStorageKeys.protocolForm, protocolInitialForm);
    const cached = readTaxFreeAddress();
    const address = cached?.address || {};
    const profile = cached?.profile || {};
    const restoredForm = {
      ...protocolInitialForm, ...saved, proxyProtocol: saved.proxyProtocol || protocolInitialForm.proxyProtocol,
      accessToken: saved.accessToken || '', proxyPool: saved.proxyPool || '', proxyPool2: saved.proxyPool2 || '',
      billingName: saved.billingName || profile.name || '', billingEmail: saved.billingEmail || profile.email || '', billingPhone: saved.billingPhone || profile.phone || '',
      billingLine1: saved.billingLine1 || address.line1 || '', billingLine2: saved.billingLine2 || address.line2 || '',
      billingCity: saved.billingCity || address.city || '', billingState: saved.billingState || address.state || '',
      billingPostalCode: saved.billingPostalCode || address.postalCode || address.postal_code || '', billingCountry: saved.billingCountry || address.country || 'US',
    };
    if (restoredForm.linkOnlyMode && restoredForm.bindOnlyMode) restoredForm.bindOnlyMode = false;
    const tokenSignature = extractCardTokens(restoredForm.accessToken).join('\n');
    const savedFlow = loadStoredObject(browserStorageKeys.cardFlowState, {});
    const matches = savedFlow.tokenSignature === tokenSignature;
    const restoredRows = matches && Array.isArray(savedFlow.taskResults) ? savedFlow.taskResults.map((row) => {
      if (row.status === 'extracting' && row.bindSucceeded) return { ...row, status: 'failed', retrying: false, detail: '刷新后已保留绑卡结果', error: '上次提链在刷新时中断，请点击重试提链。', failureStage: '生成 Checkout 提链' };
      if (['binding', 'extracting'].includes(row.status)) return { ...row, status: 'failed', retrying: false, detail: '刷新时当前步骤中断', error: '已保留此前结果，请重新绑卡。', failureStage: row.failureStage || '准备绑卡会话' };
      return { ...row, retrying: false };
    }) : [];
    restoredWorkspaceRef.current = {
      form: restoredForm,
      tokenSignature,
      session: matches && savedFlow.session?.publishable_key && savedFlow.session?.client_secret ? savedFlow.session : null,
      phase: matches && savedFlow.phase ? savedFlow.phase : 'input',
      message: matches && savedFlow.message ? savedFlow.message : '先填写 AT、代理池和账单地址，再加载安全卡片输入框。',
      taskResults: restoredRows,
    };
  }
  const restoredWorkspace = restoredWorkspaceRef.current;
  const [form, setForm] = useState(restoredWorkspace.form);
  const [session, setSession] = useState(restoredWorkspace.session); const [cardState, setCardState] = useState({ number: false, expiry: false, cvc: false });
  const [phase, setPhase] = useState(restoredWorkspace.phase); const [message, setMessage] = useState(restoredWorkspace.message);
  const [error, setError] = useState(null); const [busy, setBusy] = useState(false); const [paymentBusy, setPaymentBusy] = useState(false); const [paused, setPaused] = useState(false); const [taskResults, setTaskResults] = useState(restoredWorkspace.taskResults);
  const [bindConfig, setBindConfig] = useState(null);
  const sessionIdentityRef = useRef(restoredWorkspace.session ? restoredWorkspace.tokenSignature : '');
  const previousTokenSignatureRef = useRef(restoredWorkspace.tokenSignature);
  const restoredSessionMountedRef = useRef(false);
  const update = (key, value) => setForm((current) => ({ ...current, [key]: value }));
  const tokens = useMemo(() => extractCardTokens(form.accessToken), [form.accessToken]);
  const tokenSignature = useMemo(() => tokens.join('\n'), [tokens]);
  const proxies = useMemo(() => form.proxyPool.split(/\r?\n/).map((x) => x.trim()).filter(Boolean), [form.proxyPool]);
  const promoProxies = useMemo(() => form.proxyPool2.split(/\r?\n/).map((x) => x.trim()).filter(Boolean), [form.proxyPool2]);
  const cardReady = Boolean(session && cardState.number && cardState.expiry && cardState.cvc);
  const cardServiceReady = Boolean(bindConfig?.account_api_configured);
  const billingReady = Boolean(form.billingName.trim() && form.billingEmail.trim() && form.billingLine1.trim() && form.billingCity.trim() && form.billingPostalCode.trim() && /^[A-Z]{2}$/.test(form.billingCountry.trim().toUpperCase()));
  const checkoutTargetReady = /^[A-Z]{2}$/.test(String(form.checkoutCountry || '').trim().toUpperCase()) && /^[A-Z]{3}$/.test(String(form.checkoutCurrency || '').trim().toUpperCase());
  const proxyCountriesReady = /^[A-Z]{2}$/.test(String(form.entryProxyCountry || '').trim().toUpperCase()) && /^[A-Z]{2}$/.test(String(form.exitProxyCountry || '').trim().toUpperCase());
  useEffect(() => {
    let active = true;
    cardPaymentPortalApi.cardBindConfig().then((value) => { if (active) setBindConfig(value); }).catch(() => { if (active) setBindConfig({ account_api_configured: false }); });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    storeBrowserValue(browserStorageKeys.protocolForm, form);
  }, [form]);
  useEffect(() => {
    storeBrowserValue(browserStorageKeys.cardFlowState, { tokenSignature, session, phase, message, taskResults });
  }, [tokenSignature, session, phase, message, taskResults]);
  useEffect(() => {
    if (previousTokenSignatureRef.current === tokenSignature) return;
    previousTokenSignatureRef.current = tokenSignature;
    elementsRef.current.forEach((element) => { try { element.destroy(); } catch (_) {} });
    elementsRef.current = [];
    stripeRef.current = null;
    sessionIdentityRef.current = '';
    restoredSessionMountedRef.current = false;
    setSession(null);
    setCardState({ number: false, expiry: false, cvc: false });
    setTaskResults([]);
    setError(null);
    setPhase('input');
    setMessage('AT 列表已变化，旧批次状态已清空；代理或账单资料变化不会清空结果。');
  }, [tokenSignature]);
  useEffect(() => {
    const applyAddress = (event) => {
      const address = event?.detail || {};
      setForm((current) => ({ ...current, billingName: String(address.name || ''), billingEmail: String(address.email || ''), billingPhone: String(address.phone || ''), billingLine1: String(address.line1 || ''), billingCity: String(address.city || ''), billingState: String(address.state || ''), billingPostalCode: String(address.postalCode || ''), billingCountry: String(address.country || 'US').toUpperCase() }));
      setMessage('姓名、邮箱和免税州地址已写入当前协议任务。');
    };
    window.addEventListener('automyai-apply-us-tax-free-address', applyAddress);
    return () => window.removeEventListener('automyai-apply-us-tax-free-address', applyAddress);
  }, []);
  useEffect(() => () => { elementsRef.current.forEach((e) => { try { e.destroy(); } catch (_) {} }); if (timerRef.current) clearTimeout(timerRef.current); }, []);
  const billingPayload = () => ({ name: form.billingName, email: form.billingEmail, phone: form.billingPhone, address: { line1: form.billingLine1, line2: form.billingLine2, city: form.billingCity, state: form.billingState, postal_code: form.billingPostalCode, country: form.billingCountry.toUpperCase() } });
  // Keep the card workspace self-contained.  These helpers used to live in
  // an older build and a restored card session could call them before the
  // user clicked anything, leaving the whole payment page blank.
  const togglePause = () => {
    const next = !pauseRequestedRef.current;
    pauseRequestedRef.current = next;
    setPaused(next);
    setMessage(next ? '已请求暂停；当前步骤结束后停止进入下一步。' : '已继续执行当前批次。');
  };
  const waitForResume = async (pausedMessage) => {
    while (pauseRequestedRef.current) {
      setMessage('已暂停 · ' + (pausedMessage || '当前任务') + '；点击“继续”恢复。');
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  };
  const pollProbe = async (probeID) => {
    for (let i = 0; i < 180; i += 1) {
      const result = await cardPaymentPortalApi.getCardKeyProbe(probeID);
      const probe = result?.probe || {};
      setMessage('正在准备 Stripe 安全会话 ' + (probe.progress || 0) + '%');
      if (probe.status === 'done') return probe.session;
      if (probe.status === 'error') throw new Error(probe.error || probe.message || 'Stripe 会话准备失败');
      await new Promise((resolve) => { timerRef.current = setTimeout(resolve, 1000); });
    }
    throw new Error('Stripe 会话准备超时');
  };
  const mountElements = async (publishableKey) => {
    const Stripe = await loadStripeJs();
    if (!numberRef.current || !expiryRef.current || !cvcRef.current) throw new Error('卡片输入框尚未挂载，请刷新页面后重试');
    elementsRef.current.forEach((element) => { try { element.destroy(); } catch (_) {} });
    const stripe = Stripe(publishableKey);
    const elements = stripe.elements();
    const style = { base: { color: '#f8fafc', fontSize: '16px', fontFamily: 'Inter,system-ui,sans-serif', '::placeholder': { color: '#94a3b8' } }, invalid: { color: '#f87171' } };
    const entries = [
      ['number', elements.create('cardNumber', { showIcon: true, style }), numberRef.current],
      ['expiry', elements.create('cardExpiry', { style }), expiryRef.current],
      ['cvc', elements.create('cardCvc', { style }), cvcRef.current],
    ];
    entries.forEach(([name, element, host]) => {
      host.replaceChildren();
      element.on('change', (event) => setCardState((current) => ({ ...current, [name]: Boolean(event.complete) })));
      element.mount(host);
    });
    stripeRef.current = stripe;
    elementsRef.current = entries.map((entry) => entry[1]);
    setCardState({ number: false, expiry: false, cvc: false });
  };
  useEffect(() => {
    if (!session?.publishable_key || restoredSessionMountedRef.current || elementsRef.current.length) return;
    restoredSessionMountedRef.current = true;
    sessionIdentityRef.current = tokenSignature;
    mountElements(session.publishable_key).then(() => {
      setPhase((current) => current === 'input' || current === 'loading' ? 'card' : current);
      setMessage((current) => taskResults.length ? current : '安全卡片框已恢复；Stripe 卡号、有效期和 CVC 需要重新输入。');
    }).catch((reason) => {
      restoredSessionMountedRef.current = false;
      setError(new Error(friendlyCardError(reason)));
    });
  }, [session?.publishable_key, tokenSignature]); // eslint-disable-line react-hooks/exhaustive-deps
  const loadCard = async () => {
    setError(null);
    if (!tokens.length) { setError(new Error('请先粘贴至少一个 AT')); return; }
    if (!proxies.length) { setError(new Error('请填写代理池 1 · US')); return; }
    setBusy(true); setPhase('loading');
    setMessage('使用第 1 个 AT 准备安全卡片会话；如需初始化会自动排队等待。');
    try {
      let result = await cardPaymentPortalApi.createCardBindSession({ access_token: tokens[0], proxy_pool: proxies, proxy: proxies[0], proxy_protocol: form.proxyProtocol, billing_details: billingPayload() });
      if (result?.pending && result.key_probe_id) result = await pollProbe(result.key_probe_id);
      if (!result?.client_secret || !String(result.client_secret).startsWith('seti_')) throw new Error('第 1 个 AT 暂未返回 SetupIntent');
      if (!String(result.publishable_key || '').startsWith('pk_')) throw new Error('安全会话未返回 Stripe 公钥');
      const billing = result.billing_details || {};
      const address = billing.address || {};
      setForm((current) => ({ ...current, billingName: billing.name || current.billingName, billingEmail: billing.email || current.billingEmail, billingPhone: billing.phone || current.billingPhone, billingLine1: address.line1 || current.billingLine1, billingLine2: address.line2 || current.billingLine2, billingCity: address.city || current.billingCity, billingState: address.state || current.billingState, billingPostalCode: address.postal_code || current.billingPostalCode, billingCountry: String(address.country || current.billingCountry || 'US').toUpperCase() }));
      sessionIdentityRef.current = tokenSignature;
      setSession(result);
      await mountElements(result.publishable_key);
      setPhase('card');
      setMessage('安全卡片输入框已就绪，请在上方直接输入卡号、有效期和 CVC。');
    } catch (reason) {
      const detail = friendlyCardError(reason);
      setError(new Error(detail)); setPhase('input'); setMessage(detail);
    } finally { setBusy(false); }
  };
  const waitCheckout = async (taskID) => { for (let i = 0; i < 360; i += 1) { const result = await cardPaymentPortalApi.getQuickCheckout(taskID); const task = result?.task || result; setMessage('正在生成 Checkout 链接 ' + (task.progress || 0) + '%'); if (task.status === 'done') return task.result?.url || task.result?.checkout_url || task.url; if (['error', 'failed', 'cancelled'].includes(task.status)) throw new Error(task.error || task.message || 'Checkout 生成失败'); await new Promise((r) => { timerRef.current = setTimeout(r, 1500); }); } throw new Error('Checkout 生成超时'); };
  const finishSingleRetry = (succeeded, hadReady) => { setPhase(succeeded || hadReady ? 'done' : 'card'); setBusy(false); };
  const retryExtract = async (row) => {
    const canRegenerate = row?.status === 'done' && row?.link && !['支付完成', '需要额外验证'].includes(row?.paymentStatus);
    if (busy || paymentBusy || row?.retrying || !row?.bindSucceeded || (!canRegenerate && row?.failureStage !== '生成 Checkout 提链')) return;
    if (!proxies.length || !promoProxies.length) { setError(new Error('请先补充 US 与 TR 两个代理池')); return; }
    if (!checkoutTargetReady || !proxyCountriesReady) { setError(new Error('请填写有效的代理地区、Checkout 地区和币种')); return; }
    const hadReady = taskResults.some((item) => item.status === 'done' && item.link);
    let succeeded = false;
    setError(null); setBusy(true); setPhase('extracting');
    setTaskResults((rows) => rows.map((item) => item.index === row.index ? { ...item, status: 'extracting', retrying: true, detail: canRegenerate ? '正在重新生成最终链' : '仅重试最终提链', error: '', failureStage: '' } : item));
    setMessage('第 ' + (row.index + 1) + ' 个账号' + (canRegenerate ? '重新生成最终链' : '仅重试最终提链') + '；不会再次绑卡。');
    try {
      const created = await cardPaymentPortalApi.createQuickCheckout({ access_token: row.token, record_id: row.recordId, entry_proxy_pool: proxies, exit_proxy_pool: promoProxies, proxy_protocol: form.proxyProtocol, entry_proxy_country: form.entryProxyCountry, exit_proxy_country: form.exitProxyCountry, checkout_country: form.checkoutCountry, checkout_currency: form.checkoutCurrency });
      const link = await waitCheckout(created.task_id);
      succeeded = true;
      cardPaymentPortalApi.reportCardClientEvent({ stage: '生成 Checkout 提链', status: 'succeeded', account_index: row.index + 1, account_email: row.email, payment_status: '重试提链完成' }).catch(() => {});
      setTaskResults((rows) => rows.map((item) => item.index === row.index ? { ...item, status: 'done', retrying: false, detail: row.linkOnly ? '最终链已重新生成' : '绑卡已保留，最终链已重新生成', link, selectedForPayment: false, paymentStatus: '', paymentError: '', error: '', failureStage: '' } : item));
      setMessage('第 ' + (row.index + 1) + ' 个账号最终链已重新生成，可执行同步最后支付。');
    } catch (reason) {
      const detail = friendlyCardError(reason);
      cardPaymentPortalApi.reportCardClientEvent({ stage: '生成 Checkout 提链', status: 'failed', type: reason?.type || '', code: reason?.code || '', decline_code: reason?.decline_code || '', message: reason?.message || String(reason || ''), account_index: row.index + 1, account_email: row.email }).catch(() => {});
      setTaskResults((rows) => rows.map((item) => item.index === row.index ? { ...item, status: 'failed', retrying: false, detail: '最终提链重试失败', error: detail, failureStage: '生成 Checkout 提链' } : item));
      setMessage('第 ' + (row.index + 1) + ' 个账号重试提链失败；绑卡结果仍保留。');
    } finally { finishSingleRetry(succeeded, hadReady); }
  };
  const retryBind = async (row) => {
    if (busy || paymentBusy || row?.retrying || row?.failureStage === '生成 Checkout 提链') return;
    if (!cardReady || !billingReady) { setError(new Error('重新绑卡前请保持卡片与账单资料完整')); return; }
    if (!proxies.length || (!form.bindOnlyMode && !promoProxies.length)) { setError(new Error(form.bindOnlyMode ? '请填写 US 代理池' : '请填写 US 与 TR 两个代理池')); return; }
    const hadReady = taskResults.some((item) => item.status === 'done' && item.link);
    const selectedProxy = proxies[row.index % proxies.length];
    const canResumeDefault = row.failureStage === '设置默认卡' && row.paymentMethodId && row.recordId;
    let succeeded = false;
    let failureStep = canResumeDefault ? '设置默认卡' : '准备绑卡会话';
    let current = canResumeDefault ? { record_id: row.recordId } : null;
    let paymentMethodID = canResumeDefault ? row.paymentMethodId : '';
    setError(null); setBusy(true); setPhase('binding');
    setTaskResults((rows) => rows.map((item) => item.index === row.index ? { ...item, status: 'binding', retrying: true, detail: canResumeDefault ? '继续设置默认卡' : '正在重新绑卡', error: '', failureStage: '' } : item));
    setMessage('第 ' + (row.index + 1) + ' 个账号' + (canResumeDefault ? '从设置默认卡继续' : '重新创建绑卡会话') + '；本账号固定使用分配到的 US 节点。');
    try {
      if (!canResumeDefault) {
        current = await cardPaymentPortalApi.createCardBindSession({ access_token: row.token, proxy_pool: proxies, proxy: selectedProxy, proxy_protocol: form.proxyProtocol, billing_details: billingPayload() });
        if (current?.pending && current.key_probe_id) current = await pollProbe(current.key_probe_id);
        if (!current?.client_secret || !String(current.client_secret).startsWith('seti_')) throw new Error('SetupIntent 尚未就绪');
        failureStep = '确认卡片';
        const confirmation = await stripeRef.current.confirmCardSetup(current.client_secret, { payment_method: { card: elementsRef.current[0], billing_details: current.billing_details || billingPayload() } });
        if (confirmation.error) {
          const stripeError = new Error(confirmation.error.message || '绑卡失败');
          stripeError.type = confirmation.error.type || '';
          stripeError.code = confirmation.error.code || '';
          stripeError.decline_code = confirmation.error.decline_code || '';
          throw stripeError;
        }
        paymentMethodID = typeof confirmation.setupIntent.payment_method === 'string' ? confirmation.setupIntent.payment_method : confirmation.setupIntent.payment_method?.id;
        if (!paymentMethodID) throw new Error('Stripe 未返回 PaymentMethod');
        cardPaymentPortalApi.reportCardClientEvent({ stage: '确认卡片', status: 'succeeded', account_index: row.index + 1, account_email: row.email }).catch(() => {});
        setTaskResults((rows) => rows.map((item) => item.index === row.index ? { ...item, paymentMethodId: paymentMethodID, recordId: current.record_id || '' } : item));
      }
      failureStep = '设置默认卡';
      setTaskResults((rows) => rows.map((item) => item.index === row.index ? { ...item, detail: '正在设置默认卡' } : item));
      await cardPaymentPortalApi.setDefaultCard({ access_token: row.token, payment_method_id: paymentMethodID, record_id: current.record_id, proxy: selectedProxy, proxy_protocol: form.proxyProtocol });
      cardPaymentPortalApi.reportCardClientEvent({ stage: '设置默认卡', status: 'succeeded', account_index: row.index + 1, account_email: row.email }).catch(() => {});
      if (form.bindOnlyMode) {
        succeeded = true;
        setTaskResults((rows) => rows.map((item) => item.index === row.index ? { ...item, bindSucceeded: true, recordId: current.record_id || '', paymentMethodId: paymentMethodID, status: 'bound', retrying: false, detail: '绑卡完成，暂未提链', error: '', failureStage: '生成 Checkout 提链' } : item));
        setMessage('第 ' + (row.index + 1) + ' 个账号绑卡完成；可稍后单独开始提链。');
        return;
      }
      setTaskResults((rows) => rows.map((item) => item.index === row.index ? { ...item, bindSucceeded: true, recordId: current.record_id || '', paymentMethodId: paymentMethodID, status: 'extracting', detail: '重新绑卡完成，等待生成最终链' } : item));
      failureStep = '生成 Checkout 提链';
      const created = await cardPaymentPortalApi.createQuickCheckout({ access_token: row.token, record_id: current.record_id, entry_proxy_pool: proxies, exit_proxy_pool: promoProxies, proxy_protocol: form.proxyProtocol, entry_proxy_country: form.entryProxyCountry, exit_proxy_country: form.exitProxyCountry, checkout_country: form.checkoutCountry, checkout_currency: form.checkoutCurrency });
      const link = await waitCheckout(created.task_id);
      succeeded = true;
      cardPaymentPortalApi.reportCardClientEvent({ stage: '生成 Checkout 提链', status: 'succeeded', account_index: row.index + 1, account_email: row.email, payment_status: '重新绑卡后提链完成' }).catch(() => {});
      setTaskResults((rows) => rows.map((item) => item.index === row.index ? { ...item, status: 'done', retrying: false, bindSucceeded: true, detail: '重新绑卡与提链完成', link, error: '', failureStage: '' } : item));
      setMessage('第 ' + (row.index + 1) + ' 个账号重新绑卡与提链完成。');
    } catch (reason) {
      const detail = friendlyCardError(reason);
      const bound = failureStep === '生成 Checkout 提链';
      cardPaymentPortalApi.reportCardClientEvent({ stage: failureStep, status: 'failed', type: reason?.type || '', code: reason?.code || '', decline_code: reason?.decline_code || '', message: reason?.message || String(reason || ''), account_index: row.index + 1, account_email: row.email }).catch(() => {});
      setTaskResults((rows) => rows.map((item) => item.index === row.index ? { ...item, status: 'failed', retrying: false, bindSucceeded: bound, recordId: current?.record_id || item.recordId || '', paymentMethodId: paymentMethodID || item.paymentMethodId || '', detail: failureStep + '失败', error: detail, failureStage: failureStep } : item));
      setMessage('第 ' + (row.index + 1) + ' 个账号' + failureStep + '失败。');
    } finally {
      if (succeeded && form.bindOnlyMode) { setPhase('bound'); setBusy(false); }
      else finishSingleRetry(succeeded, hadReady);
    }
  };
  const runLinkOnlyBatch = async () => {
    setTaskResults(tokens.map((token, index) => ({ index, token, email: cardTokenIdentity(token).email || '账号 ' + (index + 1), status: 'waiting', detail: '等待提链', link: '', error: '', paymentStatus: '', paymentError: '', bindSucceeded: true, linkOnly: true, recordId: '', paymentMethodId: '', failureStage: '', retrying: false, selectedForPayment: false })));
    setPhase('extracting');
    setMessage('只提链并支付模式：跳过卡片加载和绑卡，正在批量生成 Checkout。');
    let cursor = 0; let succeeded = 0; let failed = 0;
    const workerCount = Math.min(tokens.length, Math.max(1, Number(form.batchConcurrency) || 1));
    const workers = Array.from({ length: workerCount }, async () => {
      while (cursor < tokens.length) {
        await waitForResume('只提链任务已暂停');
        const index = cursor; cursor += 1;
        const token = tokens[index]; const accountEmail = cardTokenIdentity(token).email || '账号 ' + (index + 1);
        setTaskResults((rows) => rows.map((row) => row.index === index ? { ...row, status: 'extracting', detail: '正在生成 Checkout' } : row));
        try {
          const created = await cardPaymentPortalApi.createQuickCheckout({ access_token: token, entry_proxy_pool: proxies, exit_proxy_pool: promoProxies, proxy_protocol: form.proxyProtocol, entry_proxy_country: form.entryProxyCountry, exit_proxy_country: form.exitProxyCountry, checkout_country: form.checkoutCountry, checkout_currency: form.checkoutCurrency });
          const link = await waitCheckout(created.task_id);
          succeeded += 1;
          setTaskResults((rows) => rows.map((row) => row.index === index ? { ...row, status: 'done', detail: '提链成功', link, selectedForPayment: false, error: '', failureStage: '' } : row));
        } catch (reason) {
          failed += 1; const detail = friendlyCardError(reason);
          setTaskResults((rows) => rows.map((row) => row.index === index ? { ...row, status: 'failed', detail: '提链失败', error: detail, failureStage: '生成 Checkout 提链' } : row));
        }
      }
    });
    await Promise.all(workers);
    setPhase(succeeded ? 'done' : 'input');
    setMessage('批量任务完成：' + succeeded + ' 个成功，' + failed + ' 个失败。');
  };
  const bindAndExtract = async () => {
    setError(null);
    if (!form.linkOnlyMode && (!cardReady || !billingReady)) { setError(new Error('请先完整填写卡片与账单资料')); return; }
    if (!proxies.length || (!form.bindOnlyMode && !promoProxies.length)) { setError(new Error(form.bindOnlyMode ? '请填写 US 代理池' : '请填写 US 与 TR 两个代理池')); return; }
    if (!proxyCountriesReady || (!form.bindOnlyMode && !checkoutTargetReady)) { setError(new Error('请填写有效的代理地区、Checkout 地区和币种')); return; }
    pauseRequestedRef.current = false; setPaused(false);
    setBusy(true); setPhase('binding');
    if (form.linkOnlyMode) {
      try { await runLinkOnlyBatch(); }
      catch (reason) { const detail = friendlyCardError(reason); setError(new Error(detail)); setPhase('input'); setMessage(detail); }
      finally { pauseRequestedRef.current = false; setPaused(false); setBusy(false); }
      return;
    }
    setTaskResults(tokens.map((token, index) => ({ index, token, email: cardTokenIdentity(token).email || '账号 ' + (index + 1), status: 'waiting', detail: '等待串行绑卡', link: '', error: '', paymentStatus: '', bindSucceeded: false, recordId: '', paymentMethodId: '', failureStage: '', retrying: false })));
    let succeeded = 0;
    let failed = 0;
    // SetupIntent/Stripe-context preparation is server-side and can overlap;
    // the actual browser confirmCardSetup below remains strictly serial.
    const prepDeferred = tokens.map(() => {
      let resolve;
      let reject;
      const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
      // The serial consumer may not await a later index immediately; attach a
      // sink so a preparation failure is surfaced by the row that consumes it
      // instead of becoming an unhandled browser rejection.
      promise.catch(() => {});
      return { promise, resolve, reject };
    });
    if (session) prepDeferred[0].resolve(session);
    let prepCursor = 1;
    const prepWorkerCount = Math.min(Math.max(1, Number(form.batchConcurrency) || 2), 3, Math.max(1, tokens.length - 1));
    const prepWorkers = Array.from({ length: prepWorkerCount }, async () => {
      while (true) {
        const index = prepCursor;
        prepCursor += 1;
        if (index >= tokens.length) return;
        try {
          await waitForResume('等待准备第 ' + (index + 1) + ' 个绑卡会话');
          const prepared = await cardPaymentPortalApi.createCardBindSession({ access_token: tokens[index], proxy_pool: proxies, proxy: proxies[index % proxies.length], proxy_protocol: form.proxyProtocol, billing_details: billingPayload() });
          const resolved = prepared?.pending && prepared.key_probe_id ? await pollProbe(prepared.key_probe_id) : prepared;
          if (!resolved?.client_secret || !String(resolved.client_secret).startsWith('seti_')) throw new Error('SetupIntent 尚未就绪');
          prepDeferred[index].resolve(resolved);
          setTaskResults((rows) => rows.map((row) => row.index === index ? { ...row, detail: '绑卡会话已预热，等待浏览器确认' } : row));
        } catch (reason) {
          prepDeferred[index].reject(reason);
        }
      }
    });
    try {
      for (let index = 0; index < tokens.length; index += 1) {
        await waitForResume('等待处理第 ' + (index + 1) + ' 个账号');
        const token = tokens[index];
        const accountEmail = cardTokenIdentity(token).email || '账号 ' + (index + 1);
        let failureStep = '准备绑卡会话';
        try {
          setMessage('串行绑卡 ' + (index + 1) + ' / ' + tokens.length + '；安全确认始终只走一个通道');
          setTaskResults((rows) => rows.map((row) => row.index === index ? { ...row, status: 'binding', detail: '正在串行确认卡片' } : row));
          const current = await prepDeferred[index].promise;
          await waitForResume('即将确认第 ' + (index + 1) + ' 个账号的卡片');
          failureStep = '确认卡片';
          const confirmation = await stripeRef.current.confirmCardSetup(current.client_secret, { payment_method: { card: elementsRef.current[0], billing_details: current.billing_details || billingPayload() } });
          if (confirmation.error) {
            const stripeError = new Error(confirmation.error.message || '绑卡失败');
            stripeError.type = confirmation.error.type || '';
            stripeError.code = confirmation.error.code || '';
            stripeError.decline_code = confirmation.error.decline_code || '';
            throw stripeError;
          }
          const pm = typeof confirmation.setupIntent.payment_method === 'string' ? confirmation.setupIntent.payment_method : confirmation.setupIntent.payment_method?.id;
          if (!pm) throw new Error('Stripe 未返回 PaymentMethod');
          cardPaymentPortalApi.reportCardClientEvent({ stage: '确认卡片', status: 'succeeded', account_index: index + 1, account_email: accountEmail }).catch(() => {});
          setTaskResults((rows) => rows.map((row) => row.index === index ? { ...row, paymentMethodId: pm, recordId: current.record_id || '' } : row));
          await waitForResume('卡片已确认，等待设置默认卡');
          failureStep = '设置默认卡';
          await cardPaymentPortalApi.setDefaultCard({ access_token: token, payment_method_id: pm, record_id: current.record_id, proxy: proxies[index % proxies.length], proxy_protocol: form.proxyProtocol });
          cardPaymentPortalApi.reportCardClientEvent({ stage: '设置默认卡', status: 'succeeded', account_index: index + 1, account_email: accountEmail }).catch(() => {});
          if (form.bindOnlyMode) {
            succeeded += 1;
            setTaskResults((rows) => rows.map((row) => row.index === index ? { ...row, bindSucceeded: true, recordId: current.record_id || '', paymentMethodId: pm, status: 'bound', detail: '绑卡完成，暂未提链', failureStage: '生成 Checkout 提链' } : row));
            continue;
          }
          setTaskResults((rows) => rows.map((row) => row.index === index ? { ...row, bindSucceeded: true, recordId: current.record_id || '', paymentMethodId: pm, status: 'extracting', detail: '绑卡完成，等待生成最终链', failureStage: '' } : row));
          await waitForResume('绑卡完成，等待生成最终 Checkout 链');
          failureStep = '生成 Checkout 提链';
          const created = await cardPaymentPortalApi.createQuickCheckout({ access_token: token, record_id: current.record_id, entry_proxy_pool: proxies, exit_proxy_pool: promoProxies, proxy_protocol: form.proxyProtocol, entry_proxy_country: form.entryProxyCountry, exit_proxy_country: form.exitProxyCountry, checkout_country: form.checkoutCountry, checkout_currency: form.checkoutCurrency });
          const link = await waitCheckout(created.task_id);
          cardPaymentPortalApi.reportCardClientEvent({ stage: '生成 Checkout 提链', status: 'succeeded', account_index: index + 1, account_email: accountEmail, payment_status: '提链完成' }).catch(() => {});
          succeeded += 1;
          setTaskResults((rows) => rows.map((row) => row.index === index ? { ...row, status: 'done', detail: '绑卡与提链完成', link } : row));
        } catch (reason) {
          failed += 1;
          const detail = friendlyCardError(reason);
          cardPaymentPortalApi.reportCardClientEvent({ stage: failureStep, status: 'failed', type: reason?.type || '', code: reason?.code || '', decline_code: reason?.decline_code || '', message: reason?.message || String(reason || ''), account_index: index + 1, account_email: accountEmail }).catch(() => {});
          setTaskResults((rows) => rows.map((row) => row.index === index ? { ...row, status: 'failed', detail: failureStep + '失败', error: detail, failureStage: failureStep } : row));
          setMessage('第 ' + (index + 1) + ' 个账号失败，继续处理下一个账号');
        }
      }
      setPhase(form.bindOnlyMode ? 'bound' : succeeded ? 'done' : 'card');
      setMessage('批次完成：成功 ' + succeeded + ' 个，失败 ' + failed + ' 个' + (succeeded ? form.bindOnlyMode ? '；卡片已保存，可按账号开始提链。' : '；可执行最后一步支付。' : '。'));
    } catch (reason) {
      const detail = friendlyCardError(reason);
      setError(new Error(detail)); setPhase('card'); setMessage('当前账号失败：' + detail);
      setTaskResults((rows) => rows.map((row) => ['binding', 'extracting'].includes(row.status) ? { ...row, status: 'failed', error: detail } : row));
    } finally {
      pauseRequestedRef.current = false;
      await Promise.allSettled(prepWorkers);
      setPaused(false); setBusy(false);
    }
  };
  const waitProtocol = async (jobID, allowPrepared = false) => { for (let i = 0; i < 600; i += 1) { const result = await cardPaymentPortalApi.getProtocolJob(jobID); const job = result?.job || {}; setMessage((job.stage || '协议处理中') + ' · ' + (job.progress || 0) + '%'); if ((allowPrepared && job.status === 'prepared') || ['ready', 'verification_required', 'error', 'cancelled'].includes(job.status)) return job; await new Promise((r) => { timerRef.current = setTimeout(r, 1200); }); } throw new Error('协议任务状态查询超时'); };
  const confirmProtocol = async (requestedRows = null) => {
    if (paymentBusy) return;
    const batchRunningAtStart = busy && ['binding', 'extracting'].includes(phase);
    const candidates = Array.isArray(requestedRows) ? requestedRows : taskResults.filter((row) => row.selectedForPayment);
    const ready = candidates.filter((row) => row.status === 'done' && row.link && !['支付完成', '需要额外验证'].includes(row.paymentStatus));
    if (!ready.length) { setError(new Error('没有待支付的提链结果')); return; }
    setError(null); setPaymentBusy(true);
    try {
      setTaskResults((rows) => rows.map((item) => ready.some((row) => row.index === item.index) ? { ...item, paymentStatus: '并发准备支付', paymentError: '' } : item));
      setMessage('正在并发准备 ' + ready.length + ' 个支付上下文；准备成功的账号将统一同时放行，个别失败不会阻断。');
      let prepareFailed = 0; let paid = 0; let finalFailed = 0; let verification = 0;
      const preparedResults = await Promise.all(ready.map(async (row) => {
      try {
        const created = await cardPaymentPortalApi.createProtocolJob({ access_token: row.token, checkout_url: row.link, proxy_pool: proxies, proxy_protocol: form.proxyProtocol, defer_confirm: true, billing_details: billingPayload() });
        const jobID = created.job?.id;
        if (!jobID) throw new Error('协议支付任务未返回 ID');
        const prepared = await waitProtocol(jobID, true);
        if (['error', 'cancelled'].includes(prepared.status)) throw new Error(prepared.error || prepared.message || '支付准备失败');
        setTaskResults((rows) => rows.map((item) => item.index === row.index ? { ...item, paymentStatus: '准备完成，等待统一放行', paymentError: '' } : item));
        return { row, jobID };
      } catch (reason) {
        prepareFailed += 1;
        const detail = friendlyCardError(reason);
        setTaskResults((rows) => rows.map((item) => item.index === row.index ? { ...item, paymentStatus: '支付准备失败', paymentError: detail } : item));
        return null;
      }
      }));
      const prepared = preparedResults.filter(Boolean);
      if (!prepared.length) {
        if (!batchRunningAtStart) setPhase('done');
        setMessage('支付准备全部失败：0/' + ready.length + '；没有可放行的最终支付任务。');
        return;
      }
      if (prepared.length) {
      setMessage(prepared.length + '/' + ready.length + ' 个账号准备完成' + (prepareFailed ? '，' + prepareFailed + ' 个准备失败但不阻断' : '') + '；正在统一同时放行最后支付。');
      setTaskResults((rows) => rows.map((item) => prepared.some(({ row }) => row.index === item.index) ? { ...item, paymentStatus: '已统一放行，正在同时支付', paymentError: '' } : item));
      try {
        await cardPaymentPortalApi.confirmProtocolBatch(prepared.map((item) => item.jobID));
        await Promise.all(prepared.map(async ({ row, jobID }) => {
          try {
            const final = await waitProtocol(jobID, false);
            if (['error', 'cancelled'].includes(final.status)) throw new Error(final.error || final.message || '协议支付失败');
            const label = final.status === 'verification_required' ? '需要额外验证' : '支付完成';
            if (final.status === 'verification_required') verification += 1; else paid += 1;
            setTaskResults((rows) => rows.map((item) => item.index === row.index ? { ...item, paymentStatus: label, paymentError: '' } : item));
          } catch (reason) {
            finalFailed += 1;
            const detail = friendlyCardError(reason);
            setTaskResults((rows) => rows.map((item) => item.index === row.index ? { ...item, paymentStatus: '支付失败', paymentError: detail } : item));
          }
        }));
      } catch (reason) {
        const detail = friendlyCardError(reason);
        finalFailed += prepared.length;
        setTaskResults((rows) => rows.map((item) => prepared.some(({ row }) => row.index === item.index) ? { ...item, paymentStatus: '统一放行失败', paymentError: detail } : item));
      }
      }
      const failed = prepareFailed + finalFailed;
      if (!batchRunningAtStart) setPhase(paid || verification ? 'paid' : 'done');
      setMessage('最后支付完成：成功 ' + paid + ' 个，需要验证 ' + verification + ' 个，失败 ' + failed + ' 个。');
    } catch (reason) {
      const detail = friendlyCardError(reason);
      setError(new Error(detail));
      setMessage('协议支付异常：' + detail);
    } finally {
      setPaymentBusy(false);
    }
  };
  const payableRows = taskResults.filter((row) => row.status === 'done' && row.link && !['支付完成', '需要额外验证'].includes(row.paymentStatus));
  const selectedPayRows = payableRows.filter((row) => row.selectedForPayment);
  const toggleAllPayments = () => {
    const shouldSelect = selectedPayRows.length !== payableRows.length;
    const indexes = new Set(payableRows.map((row) => row.index));
    setTaskResults((rows) => rows.map((row) => indexes.has(row.index) ? { ...row, selectedForPayment: shouldSelect } : row));
  };
  const copyAllCheckoutLinks = async () => {
    const links = taskResults.map((row) => row.link).filter(Boolean);
    if (links.length) await navigator.clipboard.writeText(links.join('\n'));
  };
  const exportCheckoutCsv = () => {
    const rows = [['account', 'status', 'checkout_url', 'payment_status'], ...taskResults.map((row) => [row.email || '', row.status || '', row.link || '', row.paymentStatus || ''])];
    const csv = rows.map((row) => row.map((value) => '"' + String(value).replace(/"/g, '""') + '"').join(',')).join('\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'card-checkout-results.csv'; anchor.click(); URL.revokeObjectURL(url);
  };
  const clearCheckoutResults = () => { setTaskResults([]); setPhase('input'); setMessage('结果已清空；AT、代理、账单资料与协议选择保持不变。'); };
  return <div className="card-unified-flow classic-one-stop">
    <ErrorBanner error={error} />
    <GlassPanel className="protocol-shared-panel classic-card-flow-panel">
      <div className="payment-center-panel-head">
        <span><CreditCard size={17} />直卡绑卡 · 提链 · 协议支付</span>
        <StatusBadge ok={(form.linkOnlyMode || cardServiceReady) && ['bound', 'done', 'paid'].includes(phase)}>{!form.linkOnlyMode && !cardServiceReady ? '服务待配置' : busy ? '运行中' : phase === 'paid' ? '支付完成' : phase === 'done' ? '提链完成' : phase === 'bound' ? '绑卡完成' : '等待操作'}</StatusBadge>
      </div>

      <div className="payment-center-phase-list classic-flow-progress">
        {[
          ['1', form.linkOnlyMode ? '已有支付方式' : '填写卡片', form.linkOnlyMode || cardReady, form.linkOnlyMode ? '跳过卡片加载和绑卡' : cardReady ? '卡片资料完整' : session ? '请输入卡片' : '先输入 AT 后加载'],
          ['2', '输入多个 AT', tokens.length > 0, tokens.length ? tokens.length + ' / 100 个账号' : '支持批量粘贴'],
          ['3', form.linkOnlyMode ? '跳过绑卡' : '串行绑卡', form.linkOnlyMode || ['bound', 'done', 'paid'].includes(phase), form.linkOnlyMode ? '使用账号已有支付方式' : taskResults.length ? taskResults.filter((row) => ['bound', 'extracting', 'done'].includes(row.status)).length + ' / ' + taskResults.length : '单安全通道'],
          ['4', '批量提链', taskResults.some((row) => row.status === 'done'), taskResults.length ? taskResults.filter((row) => row.status === 'done').length + ' / ' + taskResults.length : form.linkOnlyMode ? '直接生成 Checkout' : '绑卡后自动提链'],
        ].map(([n, label, ok, detail]) => <div className="payment-center-phase" key={n}><i>{n}</i><span><b>{label}</b><small>{detail}</small></span>{ok ? <CheckCircle2 size={16} /> : <CircleDot size={16} />}</div>)}
      </div>

      <div className="classic-prep-grid">
        <section className="classic-flow-section classic-at-section">
          <header><span><b>开始前准备 · AT 列表</b><small>每行一个，最多 100 个；首个 AT 用于加载安全卡框</small></span><em>{tokens.length} AT</em></header>
          <textarea className="input-glass console-code payment-center-token" value={form.accessToken} onChange={(event) => update('accessToken', event.target.value)} placeholder="每行粘贴一个 AT / Session" />
        </section>
        <section className="classic-flow-section classic-proxy-section">
          <header><span><b>开始前准备 · 双代理池</b><small>{form.entryProxyCountry} 按账号轮询并负责绑卡/建单；{form.exitProxyCountry} 负责优惠更新</small></span><em>{form.entryProxyCountry} + {form.exitProxyCountry}</em></header>
          <div className="classic-proxy-stack">
            <Field label="代理协议" hint="US、TR、绑卡、提链与最终支付统一使用">
              <CustomSelect value={form.proxyProtocol} onChange={(value) => update('proxyProtocol', value)} options={proxyProtocolOptions} ariaLabel="代理协议" />
            </Field>
            <Field label="代理池 1 地区" hint="绑卡与创建 Checkout"><input className="input-glass console-code" maxLength={2} value={form.entryProxyCountry} onChange={(event) => update('entryProxyCountry', event.target.value.toUpperCase().replace(/[^A-Z]/g, ''))} placeholder="US" /></Field>
            <Field label="代理池 2 地区" hint="优惠更新与提链"><input className="input-glass console-code" maxLength={2} value={form.exitProxyCountry} onChange={(event) => update('exitProxyCountry', event.target.value.toUpperCase().replace(/[^A-Z]/g, ''))} placeholder="TR" /></Field>
            <Field label={`代理 1 · ${form.entryProxyCountry || '??'} 绑卡 / Checkout`} hint={proxies.length + ' 条 · 多账号轮询'}><textarea className="input-glass console-code payment-center-proxies" value={form.proxyPool} onChange={(event) => update('proxyPool', event.target.value)} placeholder={`${form.entryProxyCountry || '地区'} proxy，每行一条`} /></Field>
            <Field label={`代理 2 · ${form.exitProxyCountry || '??'} 优惠 / 提链`} hint={promoProxies.length + ' 条'}><textarea className="input-glass console-code payment-center-proxies" value={form.proxyPool2} onChange={(event) => update('proxyPool2', event.target.value)} placeholder={`${form.exitProxyCountry || '地区'} proxy，每行一条`} /></Field>
          </div>
        </section>
      </div>

      <section className="classic-flow-section classic-link-only-section">
        <header><span><b>只提链并支付</b><small>跳过卡片加载和绑卡，使用账号已有支付方式</small></span><em>{form.linkOnlyMode ? 'ON' : 'OFF'}</em></header>
        <div className="classic-link-only-controls">
          <Toggle checked={Boolean(form.linkOnlyMode)} onChange={(checked) => setForm((current) => ({ ...current, linkOnlyMode: checked, bindOnlyMode: checked ? false : current.bindOnlyMode }))} label="只提链并支付" hint="开启后直接批量提链；成功账号可选择支付" />
          <Toggle checked={Boolean(form.bindOnlyMode)} onChange={(checked) => setForm((current) => ({ ...current, bindOnlyMode: checked, linkOnlyMode: checked ? false : current.linkOnlyMode }))} label="只绑卡，暂不提链" hint="先完成 Stripe 绑卡与设置默认卡；之后可在结果行单独开始提链" />
          <Field label="Checkout 地区" hint="与账单国家分开"><input className="input-glass console-code" maxLength={2} value={form.checkoutCountry} onChange={(event) => update('checkoutCountry', event.target.value.toUpperCase().replace(/[^A-Z]/g, ''))} placeholder="PH" /></Field>
          <Field label="Checkout 币种" hint="使用官方返回值复核"><input className="input-glass console-code" maxLength={3} value={form.checkoutCurrency} onChange={(event) => update('checkoutCurrency', event.target.value.toUpperCase().replace(/[^A-Z]/g, ''))} placeholder="PHP" /></Field>
          <Field label="提链并发" hint="1–10"><CompactNumberInput value={form.batchConcurrency} onChange={(value) => update('batchConcurrency', value)} min={1} max={10} ariaLabel="提链并发" /></Field>
        </div>
      </section>

      {!form.linkOnlyMode ? <section className="classic-flow-section classic-card-section">
        <header><span><b>安全卡片</b><small>加载后在这里填写卡号、有效期和 CVC；加载本身不会绑卡</small></span><em>{cardReady ? 'COMPLETE' : session ? 'READY' : 'NOT LOADED'}</em></header>
        <div className="classic-card-workspace">
          <div className="protocol-card-fields">
            <Field label="卡号"><div ref={numberRef} className="input-glass protocol-stripe-field classic-stripe-host" data-placeholder={session ? '' : '先加载安全卡片输入框'} /></Field>
            <div className="protocol-card-small-fields">
              <Field label="有效期"><div ref={expiryRef} className="input-glass protocol-stripe-field classic-stripe-host" data-placeholder={session ? '' : 'MM / YY'} /></Field>
              <Field label="CVC"><div ref={cvcRef} className="input-glass protocol-stripe-field classic-stripe-host" data-placeholder={session ? '' : 'CVC'} /></Field>
            </div>
          </div>
          <div className="classic-card-actions">
            <GlassButton variant="primary" type="button" loading={busy && ['input', 'loading'].includes(phase)} onClick={loadCard} disabled={busy || paymentBusy || !cardServiceReady || !tokens.length || !proxies.length || Boolean(session)}>加载安全卡片输入框</GlassButton>
            <small>{!cardServiceReady ? '卡片账户服务尚未配置' : session ? cardReady ? '卡片信息已完整，可开始串行绑卡' : '输入框已加载，请填写完整卡片' : !tokens.length ? '先粘贴至少一个 AT' : !proxies.length ? '再填写 US 代理' : '资料已齐，可加载卡框'}</small>
            <BillingAddressApiPanel />
          </div>
        </div>
      </section> : null}

      <div className="classic-run-row">
        <div className="classic-run-actions">
          <GlassButton variant="primary" type="button" loading={busy && ['binding', 'extracting'].includes(phase) && !paused} onClick={bindAndExtract} disabled={busy || paymentBusy || !tokens.length || !proxies.length || !proxyCountriesReady || (!form.bindOnlyMode && (!promoProxies.length || !checkoutTargetReady)) || (!form.linkOnlyMode && (!cardReady || !billingReady))}>{form.linkOnlyMode ? '开始批量提链' : form.bindOnlyMode ? '开始串行绑卡（暂不提链）' : '开始串行绑卡并自动提链'}</GlassButton>
          <GlassButton variant="glass" type="button" icon={Square} onClick={togglePause} disabled={!busy || !['binding', 'protocol'].includes(phase)}>{paused ? '继续' : '暂停'}</GlassButton>
        </div>
        <div className="classic-live-status"><span>{message}</span><i style={{ width: `${taskResults.length ? Math.round(taskResults.filter((row) => ['bound', 'done', 'failed'].includes(row.status)).length * 100 / taskResults.length) : 0}%` }} /></div>
      </div>

      <section className="classic-flow-section classic-results-section">
        <header><span><b>提链结果</b><small>账号与 Checkout 链接分开显示</small></span><em>{taskResults.filter((row) => row.status === 'done').length} READY</em></header>
        <div className="classic-result-toolbar">
          <GlassButton variant="glass" type="button" onClick={toggleAllPayments} disabled={paymentBusy || !payableRows.length}>{selectedPayRows.length === payableRows.length && payableRows.length ? '取消全选' : '全选成功任务'}</GlassButton>
          <GlassButton data-payment-mode="synchronized-batch" aria-label="同步执行最后支付" title="同步执行最后支付；批次运行中也可支付已完成行" variant="primary" type="button" loading={paymentBusy} onClick={() => confirmProtocol()} disabled={paymentBusy || !selectedPayRows.length}>支付已选（{selectedPayRows.length}）</GlassButton>
          <GlassButton variant="glass" type="button" onClick={exportCheckoutCsv} disabled={!taskResults.length}>导出 CSV</GlassButton>
          <GlassButton variant="glass" type="button" icon={Copy} onClick={copyAllCheckoutLinks} disabled={!taskResults.some((row) => row.link)}>复制全部链接</GlassButton>
          <GlassButton variant="glass" type="button" onClick={clearCheckoutResults} disabled={busy || paymentBusy || !taskResults.length}>清空</GlassButton>
        </div>
        {taskResults.length ? <div className="classic-result-list">{taskResults.map((row) => <div className={'classic-result-row status-' + row.status} key={row.index}>
          <i>{row.index + 1}</i>
          <span><b>{row.email || '账号 ' + (row.index + 1)}</b><small>{row.detail || '等待开始'}{row.error ? '：' + row.error : ''}{row.paymentStatus ? ' · ' + row.paymentStatus : ''}{row.paymentError ? ' · ' + row.paymentError : ''}</small></span>
          <StatusBadge ok={['done', 'bound'].includes(row.status)}>{row.status === 'bound' ? '绑卡完成' : ['支付完成', '需要额外验证'].includes(row.paymentStatus) ? '支付已提交' : row.status === 'done' ? '提链成功' : row.status === 'binding' ? '串行绑卡' : row.status === 'extracting' ? '提链中' : row.status === 'failed' ? '失败' : '等待'}</StatusBadge>
          <div className="classic-result-actions">
            {row.status === 'done' && row.link && !['支付完成', '需要额外验证'].includes(row.paymentStatus) ? <label className="classic-pay-select"><input type="checkbox" checked={Boolean(row.selectedForPayment)} disabled={paymentBusy} onChange={(event) => setTaskResults((rows) => rows.map((item) => item.index === row.index ? { ...item, selectedForPayment: event.target.checked } : item))} />选择支付</label> : null}
            {row.status === 'failed' && row.bindSucceeded && row.failureStage === '生成 Checkout 提链' ? <GlassButton className="classic-retry-link" variant="glass" type="button" icon={RefreshCw} loading={Boolean(row.retrying)} disabled={busy || paymentBusy} onClick={() => retryExtract(row)}>重试提链</GlassButton> : null}
            {row.status === 'bound' && row.bindSucceeded ? <GlassButton className="classic-retry-link" variant="glass" type="button" icon={RefreshCw} loading={Boolean(row.retrying)} disabled={busy || paymentBusy} onClick={() => retryExtract(row)}>开始提链</GlassButton> : null}
            {row.status === 'done' && row.bindSucceeded && row.link && !['支付完成', '需要额外验证'].includes(row.paymentStatus) ? <GlassButton className="classic-refresh-link" variant="glass" type="button" icon={RefreshCw} loading={Boolean(row.retrying)} disabled={busy || paymentBusy} onClick={() => retryExtract(row)}>重新提链</GlassButton> : null}
            {row.status === 'failed' && !row.linkOnly && row.failureStage && row.failureStage !== '生成 Checkout 提链' ? <GlassButton className="classic-retry-bind" variant="glass" type="button" icon={RefreshCw} loading={Boolean(row.retrying)} disabled={busy || paymentBusy || !cardReady} onClick={() => retryBind(row)}>重新绑卡</GlassButton> : null}
            {row.link ? <GlassButton variant="glass" type="button" icon={Copy} onClick={() => navigator.clipboard.writeText(row.link)}>复制链接</GlassButton> : null}
            {row.status === 'done' && row.link && !['支付完成', '需要额外验证'].includes(row.paymentStatus) ? <GlassButton variant="glass" type="button" disabled={paymentBusy || Boolean(row.retrying)} onClick={() => confirmProtocol([row])}>直接协议支付</GlassButton> : null}
            {row.link ? <a href={row.link} target="_blank" rel="noreferrer"><ExternalLink size={13} />Checkout</a> : null}
          </div>
        </div>)}</div> : <div className="classic-empty-results">开始后，账号会在这里按顺序显示绑卡、提链和支付状态。</div>}
        <div className="classic-final-pay">
          <small>批量支付只处理已选择且提链成功的账号；准备成功的账号统一同时放行，个别准备失败不会阻断，也不会重新绑卡或重新提链。</small>
        </div>
      </section>
    </GlassPanel>
  </div>;
}

function CardProtocolWorkspace({ status, refresh, loading }) {
  return <CardBindLinkWorkspace />;
}

const PAYMENT_CENTER_MODE_KEY = 'automyai.payment-center.mode.v1';

function PayPalProtocolWorkspace() {
  const [form, setForm] = useState(loadPayPalForm);
  const [status, setStatus] = useState(null);
  const [catalog, setCatalog] = useState([]);
  const [summary, setSummary] = useState(null);
  const [search, setSearch] = useState('');
  const [task, setTask] = useState(null);
  const [recent, setRecent] = useState([]);
  const [otp, setOtp] = useState('');
  const [otpBusy, setOtpBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [statusResult, countryResult, jobsResult] = await Promise.all([
        paypalProtocolApi.status(),
        paypalProtocolApi.countries(),
        paypalProtocolApi.listJobs(),
      ]);
      setStatus(statusResult);
      setSummary(countryResult);
      setCatalog(Array.isArray(countryResult?.countries) ? countryResult.countries : []);
      const jobs = Array.isArray(jobsResult?.items) ? jobsResult.items : [];
      setRecent(jobs);
      setTask((current) => current || jobs[0] || null);
      setForm((current) => current.country ? current : { ...current, country: statusResult?.defaultCountry || 'GB' });
    } catch (reason) {
      setError(reason);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => { storeBrowserValue(PAYPAL_FORM_STORAGE_KEY, form); }, [form]);
  useEffect(() => {
    if (!task?.id || ['completed', 'authorized', 'failed'].includes(task.status)) return undefined;
    let active = true;
    const poll = async () => {
      try {
        const response = await paypalProtocolApi.getJob(task.id);
        if (active && response?.task) {
          setTask(response.task);
          if (['completed', 'authorized', 'failed'].includes(response.task.status)) {
            setRecent((items) => [response.task, ...items.filter((item) => item.id !== response.task.id)].slice(0, 20));
          }
        }
      } catch (reason) { if (active) setError(reason); }
    };
    const timer = window.setInterval(poll, 1200);
    poll();
    return () => { active = false; window.clearInterval(timer); };
  }, [task?.id, task?.status]);

  const selectedCountry = useMemo(() => catalog.find((item) => item.code === form.country) || null, [catalog, form.country]);
  const countryOptions = useMemo(() => catalog.map((item) => ({
    value: item.code,
    label: `${item.code} · ${item.name_zh || item.name_en || item.code} · ${item.calling_code || ''}`,
  })), [catalog]);
  const filteredCountries = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return catalog;
    return catalog.filter((item) => [item.code, item.name_zh, item.name_en, item.calling_code].some((value) => String(value || '').toLowerCase().includes(needle)));
  }, [catalog, search]);
  const proxyCount = useMemo(() => String(form.proxies || '').split(/\r?\n/).map((line) => line.trim()).filter((line) => line && !line.startsWith('#')).length, [form.proxies]);

  const update = (key, value) => setForm((current) => ({ ...current, [key]: value }));
  const chooseCountry = (country) => {
    const next = catalog.find((item) => item.code === country);
    setForm((current) => {
      const phoneHasOldPrefix = selectedCountry?.calling_code && String(current.phone || '').startsWith(selectedCountry.calling_code);
      return { ...current, country, phone: !current.phone || phoneHasOldPrefix ? (next?.calling_code || '') : current.phone };
    });
  };
  const start = async (event) => {
    event?.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await paypalProtocolApi.createJob(form);
      setTask(created);
      setRecent((current) => [created, ...current.filter((item) => item.id !== created.id)].slice(0, 20));
      setOtp('');
    } catch (reason) {
      setError(reason);
    } finally {
      setBusy(false);
    }
  };
  const submitOtp = async (event) => {
    event?.preventDefault();
    if (!task?.id || !otp.trim()) return;
    setOtpBusy(true);
    setError(null);
    try {
      await paypalProtocolApi.submitOtp(task.id, /^\d{6}$/.test(otp.trim()) ? { code: otp.trim() } : { phone: otp.trim() });
      setTask((current) => current ? { ...current, status: 'running', otpRequired: false, stage: '已提交验证码，正在校验' } : current);
      setOtp('');
    } catch (reason) { setError(reason); } finally { setOtpBusy(false); }
  };
  const terminal = ['completed', 'authorized', 'failed'].includes(task?.status);
  const paid = task?.status === 'completed' && task?.result?.settlement_status === 'confirmed';
  const pendingVerification = task?.result?.settlement_status === 'pending_verification';
  const running = Boolean(task && !terminal);
  const countryExecutable = (status?.executorCountries || ['BR', 'GB']).includes(form.country);
  const phaseOrder = ['initial_load', 'risk_controls', 'account', 'verification', 'waiting_otp', 'authorize', 'completed'];
  const visiblePhase = task?.phase === 'failed' ? task.failedPhase : task?.phase;
  const phaseIndex = task?.phase === 'authorized' ? 6 : Math.max(0, phaseOrder.indexOf(visiblePhase));

  return <div className="paypal-protocol-workspace">
    <ErrorBanner error={error} onRetry={loading ? undefined : refresh} />
    <div className="console-metrics payment-center-metrics">
      <MetricCard label="PP 协议服务" value={loading ? 'LOADING' : status?.ok ? 'READY' : 'ERROR'} tone={status?.ok ? 'success' : undefined} />
      <MetricCard label="国家/地区" value={summary?.count ?? catalog.length} hint={`实跑 ${summary?.realOkCount ?? 0} · 模板 ${summary?.theoreticalOkCount ?? 0}`} />
      <MetricCard label="当前地区" value={selectedCountry?.code || form.country || '—'} hint={selectedCountry?.name_zh || selectedCountry?.name_en || ''} />
      <MetricCard label="代理池" value={proxyCount} hint="每行一条，最多 500 条" />
    </div>

    <div className="payment-center-workspace paypal-main-workspace">
      <GlassPanel className="payment-center-config">
        <div className="payment-center-panel-head"><span><ShieldCheck size={17} />PP 协议参数</span><StatusBadge ok={status?.ok}>{status?.ok ? '可用' : loading ? '加载中' : '异常'}</StatusBadge></div>
        <form onSubmit={start}>
          <div className="console-grid">
            <Field label="BA 链接 / BA Token" wide hint="可粘贴完整 PayPal agreements 链接，或直接填写 BA- Token">
              <textarea className="input-glass console-code payment-center-token" value={form.paypalUrl} onChange={(event) => update('paypalUrl', event.target.value)} required autoComplete="off" placeholder="https://www.paypal.com/agreements/approve?ba_token=BA-...\n或 BA-..." />
            </Field>
            <Field label="国家 / 地区">
              <CustomSelect value={form.country} onChange={chooseCountry} options={countryOptions} ariaLabel="PayPal 国家或地区" />
            </Field>
            <Field label="国际手机号" hint={selectedCountry?.calling_code ? `需以 ${selectedCountry.calling_code} 开头` : '使用 +国家码格式'}>
              <input className="input-glass payment-center-phone" value={form.phone} onChange={(event) => update('phone', event.target.value)} required autoComplete="tel" placeholder={selectedCountry?.calling_code ? `${selectedCountry.calling_code}...` : '+447512345678'} />
            </Field>
            {selectedCountry ? <div className={`payment-center-country-card support-${selectedCountry.support_level || 'unsupported'}`}>
              <div className="payment-center-country-card-head">
                <span><Globe2 size={18} /><b>{selectedCountry.code} · {selectedCountry.name_zh || selectedCountry.name_en}</b><small>{selectedCountry.name_en}</small></span>
                <StatusBadge tone={supportTone[selectedCountry.support_level]}>{selectedCountry.support_label || '未适配'}</StatusBadge>
              </div>
              <div className="payment-center-country-facts">
                <span><small>国际区号</small><b>{selectedCountry.calling_code || '—'}</b></span>
                <span><small>Locale</small><b>{selectedCountry.locale || '—'}</b></span>
                <span><small>币种</small><b>{selectedCountry.currency || '—'}</b></span>
                <span><small>地区逻辑</small><b>{selectedCountry.internal_logic || selectedCountry.support_detail || '—'}</b></span>
              </div>
            </div> : null}
            <Field label="代理池" wide hint={`当前 ${proxyCount} 条；支持 host:port、host:port:user:pass 或带协议 URL`}>
              <textarea className="input-glass console-code payment-center-proxies" value={form.proxies} onChange={(event) => update('proxies', event.target.value)} placeholder={'每行一条代理，最多 500 条\nhost:port:username:password'} />
            </Field>
            <Field label="卡片 contingency 尝试" hint="1–5；返回 EUAT 后立即进入身份提升，不重复注册">
              <CompactNumberInput value={form.maxCardAttempts} onChange={(value) => update('maxCardAttempts', value)} min={1} max={5} ariaLabel="PP 卡片尝试次数" />
            </Field>
          </div>
          <div className="payment-center-actions">
            <span>{countryExecutable ? '同一会话完成 Guest 创建、短信验证、身份提升、授权和商户回跳确认。' : '该地区保留参数准备；全链路执行当前覆盖 GB 与 BR。'}</span>
            <GlassButton variant="primary" type="submit" loading={busy || running} disabled={loading || !status?.executorAvailable || running || !countryExecutable} icon={ShieldCheck}>{running ? '全链路执行中' : '开始 PP 全链路支付'}</GlassButton>
          </div>
        </form>
      </GlassPanel>

      <div className="payment-center-side-stack">
        <GlassPanel className="payment-center-runtime">
          <div className="payment-center-panel-head"><span><Search size={17} />地区支持查询</span><StatusBadge>{filteredCountries.length} / {catalog.length}</StatusBadge></div>
          <div className="payment-center-search"><Search size={15} /><input className="input-glass" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索国家、代码或国际区号" /></div>
          <div className="paypal-country-list">
            {filteredCountries.map((item) => <button type="button" className={item.code === form.country ? 'active' : ''} key={item.code} onClick={() => chooseCountry(item.code)}>
              <span><b>{item.code}</b><small>{item.name_zh || item.name_en}</small></span>
              <StatusBadge tone={supportTone[item.support_level]}>{item.support_label}</StatusBadge>
            </button>)}
          </div>
        </GlassPanel>
        <GlassPanel className="payment-center-runtime">
          <div className="payment-center-panel-head"><span><TerminalSquare size={17} />实时支付链路</span><StatusBadge tone={paid ? 'bg-success' : task?.status === 'failed' ? 'bg-error' : pendingVerification ? 'bg-warning' : undefined}>{paid ? 'PAID' : pendingVerification ? 'PENDING VERIFICATION' : task?.status?.toUpperCase() || 'WAITING'}</StatusBadge></div>
          <div className="payment-center-phase-list">
            {[
              ['加载协议页', '保持 BA、EC、Cookie 和路由会话'],
              ['同步风控信号', '指纹、Tealeaf、GraphQL 初始查询'],
              ['创建 Guest 上下文', '生成账户并保持当前 EC'],
              ['短信验证与身份提升', 'EUAT、checkout/drop、Hermes'],
              ['提交最终授权', 'EC 作为 billingAgreementId'],
              ['确认商户回跳', 'redirect_status=succeeded 才计为完成'],
            ].map(([label, detail], index) => {
              const ranges = [[0], [1], [2], [3, 4], [5], [6]];
              const done = paid || phaseIndex > Math.max(...ranges[index]);
              const active = !done && ranges[index].includes(phaseIndex);
              return <div className={`payment-center-phase ${active ? 'active' : ''}`} key={label}><i>{index + 1}</i><span><b>{label}</b><small>{active ? (task?.stage || detail) : detail}</small></span>{done ? <CheckCircle2 size={16} /> : <CircleDot size={16} />}</div>;
            })}
          </div>
          {task?.otpRequired ? <form className="paypal-otp-form" onSubmit={submitOtp}>
            <label><span>短信验证码</span><input className="input-glass console-code" inputMode="numeric" value={otp} onChange={(event) => setOtp(event.target.value)} placeholder="6位验证码；换号可输入 +44…" autoFocus /></label>
            <GlassButton variant="primary" type="submit" loading={otpBusy} disabled={!otp.trim()} icon={Phone}>提交验证</GlassButton>
          </form> : null}
          {task ? <div className={`paypal-live-state status-${task.status}`}>
            <b>{task.stage}</b><small>{task.phone} · {task.country} / {task.locale} · {task.proxyCount} 条代理</small>
            {task.error ? <p>{task.error}</p> : null}
          </div> : null}
          {task?.challenge ? <div className="paypal-challenge-state">
            <span><b>{task.challenge.smsCreated === false ? '短信发送前触发 PP 身份检查' : 'PP 身份检查已触发'}</b><small>{task.challenge.smsCreated === false ? '本次请求尚未创建短信验证码；继续换手机号不会绕过该检查。' : '本次短信验证码已经通过；重新提交旧验证码不会消除该检查。'}</small></span>
            <code>{task.challenge.pageFamily} · HTTP {task.challenge.httpStatus || '—'} · {task.challenge.paypalDebugId || 'NO DEBUG ID'}{task.challenge.form ? ' · 表单 ' + (task.challenge.form.formAction || '/auth/validatecaptcha') + ' · reCAPTCHA ' + (task.challenge.form.captchaIframePresent ? '已检测' : '未检测') : ''}</code>
            <GlassButton variant="primary" type="button" loading={busy} disabled={busy || running} onClick={start} icon={RefreshCw}>完成检查后重新开始</GlassButton>
          </div> : null}
        </GlassPanel>
      </div>
    </div>

    {task?.result ? <GlassPanel className={`payment-center-result paypal-result-panel ${paid ? 'paypal-payment-confirmed' : ''}`}>
      <div className="payment-center-panel-head"><span><CheckCircle2 size={17} />{paid ? 'PP 支付已确认' : pendingVerification ? 'PP 协议已授权，等待商户验证' : 'PP 授权已提交'}</span><StatusBadge tone={paid ? 'bg-success' : 'bg-warning'}>{task.result.settlement_status?.toUpperCase()}</StatusBadge></div>
      <dl>
        <div><dt>BA / EC</dt><dd><code>{task.result.ba_token} / {task.result.ec_token}</code></dd></div>
        <div><dt>结算状态</dt><dd>{task.result.settlement_status} / {task.result.redirect_status || '无回跳状态'}</dd></div>
        <div><dt>支付动作</dt><dd>{task.result.payment_action || '—'}</dd></div>
        <div><dt>买家模式</dt><dd>{task.result.buyer_mode === 'identity_elevation' ? 'Guest → Member 身份提升' : '原版账户流程'}</dd></div>
        <div><dt>买家身份</dt><dd>{task.result.identity_elevation?.buyer_ready ? 'READY' : 'PENDING'} · {task.result.identity_elevation?.user_id || '—'}</dd></div>
        <div><dt>认证刷新</dt><dd>{task.result.identity_elevation?.auth_refreshed ? 'checkout/drop + Hermes 已完成' : '待刷新'}</dd></div>
        <div><dt>资金工具</dt><dd>{task.result.identity_elevation?.funding_available ? `${task.result.identity_elevation.funding_available_count} 个可用` : (task.result.identity_elevation?.funding_checkpoints || []).join(', ') || '未选中；授权已继续'}</dd></div>
        <div><dt>商户结果</dt><dd><code>{task.result.final_redirect_url || '<redacted>'}</code></dd></div>
      </dl>
    </GlassPanel> : null}

    <GlassPanel className="payment-center-result payment-center-recent">
      <div className="payment-center-panel-head"><span><RefreshCw size={17} />最近支付任务</span><StatusBadge>{recent.length}</StatusBadge></div>
      {recent.length ? recent.map((item) => <button type="button" className={item.id === task?.id ? 'active' : ''} key={item.id} onClick={() => setTask(item)}>
        <span><b>{item.country} · {item.status === 'completed' ? '支付完成' : item.status === 'authorized' ? (item.result?.settlement_status === 'pending_verification' ? '授权成功（待验证）' : '授权成功') : item.status === 'failed' ? '失败' : '执行中'}</b><code>{item.id}</code><small>{item.createdAt ? new Date(item.createdAt * 1000).toLocaleString() : ''}</small></span>
        <StatusBadge tone={item.status === 'completed' ? 'bg-success' : item.status === 'failed' ? 'bg-error' : undefined}>{item.status?.toUpperCase()}</StatusBadge>
      </button>) : <p>暂无 PP 协议支付任务。</p>}
    </GlassPanel>
  </div>;
}

export default function PaymentCenter() {
  const [mode, setMode] = useState(() => {
    try { return new URLSearchParams(window.location.search).get('mode') === 'card' ? 'card' : 'paypal'; } catch (_) { return 'paypal'; }
  });
  const switchMode = (next) => {
    setMode(next);
    try { window.localStorage.setItem(PAYMENT_CENTER_MODE_KEY, next); } catch (_) {}
  };
  const paypalMode = mode === 'paypal';
  return (
    <div className="page-container payment-center-page">
      <div className="page-header">
        <div className="page-title-group">
          <h1>{paypalMode ? 'PP 协议支付' : '直卡协议'}</h1>
          <p>{paypalMode ? '以英国成功链路执行 Guest 创建、短信验证、身份提升、协议授权与商户回跳确认。' : '填写账号与双代理，加载安全卡片，串行绑卡并逐个提链。'}</p>
        </div>
        <div className="payment-center-mode-switch" role="tablist" aria-label="支付协议模块">
          <button type="button" role="tab" aria-selected={paypalMode} className={paypalMode ? 'active' : ''} onClick={() => switchMode('paypal')}><Globe2 size={15} />PP 协议支付</button>
          <button type="button" role="tab" aria-selected={!paypalMode} className={!paypalMode ? 'active' : ''} onClick={() => switchMode('card')}><CreditCard size={15} />直卡协议</button>
        </div>
      </div>
      {paypalMode ? <PayPalProtocolWorkspace /> : <CardProtocolWorkspace />}
    </div>
  );
}
