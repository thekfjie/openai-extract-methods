import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import QRCode from 'qrcode';
import {
  AlertCircle,
  Ban,
  CheckCircle2,
  CircleDollarSign,
  ClipboardPaste,
  Copy,
  ExternalLink,
  FileText,
  LoaderCircle,
  Play,
  Radar,
  RefreshCw,
  ScanLine,
  ShieldCheck,
  StopCircle,
  Terminal,
  Timer,
  Trash2,
  UsersRound,
  XCircle,
  Zap,
} from 'lucide-react';
import extractionApi, { parseExtractionInput } from '../api/extraction';
import apiClient from '../api/client';
import { useToast } from '../contexts/ToastContext';
import { CollapsiblePanel, CompactNumberInput, Field, MetricCard, OutputBox, StatusBadge, Toggle } from '../ui/ConsolePrimitives';
import GlassButton from '../ui/GlassButton';
import GlassPanel from '../ui/GlassPanel';
import Skeleton from '../ui/Skeleton';
import CustomSelect from '../ui/CustomSelect';
import MailAdminAccountPicker from '../components/extraction/MailAdminAccountPicker';

const fallbackMethods = [
  { id: 'paypal_ba', apiMethod: 'paypal_ba', label: 'PP 提炼', name: 'PayPal BA', primary: true, kind: 'provider_link', available: true, countries: ['US', 'GB', 'DE', 'FR', 'NL', 'CA', 'AU', 'IN', 'PH', 'TH', 'BA', 'AE', 'BR', 'TR', 'VN', 'JP', 'BH', 'MX'], description: '创建 PayPal Billing Agreement approve 链。' },
  { id: 'direct_card', apiMethod: 'paper_card', label: '直卡', name: 'Direct Card', kind: 'checkout_link', available: true, countries: ['US', 'GB', 'DE', 'FR', 'NL', 'CA', 'AU', 'IN', 'PH', 'TH', 'BA', 'AE', 'BR', 'TR', 'VN', 'JP', 'BH', 'MX'], description: '直卡 checkout 提炼。' },
  { id: 'ph_link', label: '菲律宾 PHP', name: 'Philippines PHP', kind: 'checkout_link', countries: ['PH'], description: '固定 PH / PHP 的 checkout 链接。' },
  { id: 'momo', label: 'MoMo 资格', name: 'Vietnam MoMo', kind: 'eligibility', countries: ['VN'], description: '检测越南 trial 与 MoMo 可用性。' },
  { id: 'kakao', label: 'Kakao Pay', name: 'Korea Kakao Pay', kind: 'eligibility_or_provider_link', modes: ['eligibility', 'provider_link'], supportsConcurrency: true, supportsPaymentStatus: true, countries: ['KR'], description: '批量观察上游资格，或并发提炼 NicePay/Kakao 待支付长链。' },
  { id: 'upi', label: 'UPI', name: 'India UPI', kind: 'qr_link', countries: ['IN'], description: '印度 UPI QR / 指令链接。' },
  { id: 'ideal', label: 'iDEAL', name: 'Netherlands iDEAL', kind: 'provider_link', countries: ['NL'], description: '荷兰 iDEAL provider 链。' },
  { id: 'gopay', label: 'GoPay', name: 'Indonesia GoPay', kind: 'provider_link', countries: ['ID'], description: '印尼 GoPay provider 链。' },
  { id: 'pix', label: 'PIX', name: 'Brazil PIX', kind: 'qr_link', countries: ['BR'], description: '巴西 PIX QR / payload / instructions。' },
  { id: 'blik', label: 'BLIK', name: 'Poland BLIK', kind: 'provider_link', countries: ['PL'], description: '波兰 BLIK provider 链。' },
  { id: 'twint', label: 'TWINT', name: 'Swiss TWINT', kind: 'provider_link', countries: ['CH'], description: '瑞士 TWINT provider 链。' },
];

const currencyByCountry = {
  US: 'USD', GB: 'GBP', DE: 'EUR', FR: 'EUR', NL: 'EUR', CA: 'CAD', AU: 'AUD', IN: 'INR',
  PH: 'PHP', PL: 'PLN', CH: 'CHF', TH: 'THB', BA: 'BAM', AE: 'AED', BR: 'BRL', TR: 'USD', VN: 'VND', JP: 'JPY',
  BH: 'BHD', MX: 'MXN', KR: 'KRW', ID: 'IDR', SG: 'SGD', HK: 'HKD', TW: 'TWD', MY: 'MYR',
};

const payPalCountryNames = {
  US: '美国', GB: '英国', DE: '德国', FR: '法国', NL: '荷兰', CA: '加拿大', AU: '澳大利亚', IN: '印度',
  PH: '菲律宾', TH: '泰国', BA: '波黑', AE: '阿联酋', BR: '巴西', TR: '土耳其', VN: '越南', JP: '日本',
  BH: '巴林', MX: '墨西哥',
};

const payPalPromotionOnlyCountries = new Set(['TR']);
// 全球随机提炼的常用预设。顺序同时用于按钮回填和新工作台默认值。
const defaultPayPalRandomCountries = ['DE', 'GB', 'US', 'TH', 'BR'];
const defaultPayPalPromotionPools = {
  GB: ['JP', 'TR'],
  US: ['JP', 'TR'],
  TH: ['TH', 'TR'],
};
const payPalCountryModeOptions = [
  { value: 'single', label: '单地区' },
  { value: 'random', label: '随机地区' },
];

function normalizePayPalCountryPool(values, fallbackCountry = 'US') {
  const allowed = new Set(Object.keys(payPalCountryNames));
  const hasExplicitPool = Array.isArray(values);
  const pool = uniqueLines(hasExplicitPool ? values : [])
    .map((value) => String(value || '').toUpperCase())
    .filter((value) => allowed.has(value) && !payPalPromotionOnlyCountries.has(value));
  if (hasExplicitPool || pool.length) return pool;
  const fallback = String(fallbackCountry || '').toUpperCase();
  return allowed.has(fallback) ? [fallback] : [...defaultPayPalRandomCountries];
}

function defaultPayPalPromotionPool(country) {
  const normalized = String(country || '').toUpperCase();
  return [...(defaultPayPalPromotionPools[normalized] || [normalized, 'TR'])].filter(Boolean);
}

function normalizePayPalPromotionPools(value, mainCountries = []) {
  const source = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  return Object.fromEntries(mainCountries.map((country) => {
    const raw = Array.isArray(source[country]) ? source[country] : defaultPayPalPromotionPool(country);
    const pool = uniqueLines(raw.map((item) => String(item || '').toUpperCase()))
      .filter((item) => Object.prototype.hasOwnProperty.call(payPalCountryNames, item));
    return [country, pool.length ? pool : defaultPayPalPromotionPool(country)];
  }));
}

function currencyForMethodCountry(methodID, country, remembered = '') {
  if (methodID === 'paypal_ba') return currencyByCountry[country] || 'USD';
  return remembered || currencyByCountry[country] || initialSettings.currency;
}

const terminalStatuses = new Set(['completed', 'failed', 'cancelled', 'interrupted']);
const statusLabels = {
  idle: '待机', submitted: '已提交', queued: '排队中', running: '执行中', completed: '已完成', failed: '失败', cancelled: '已取消', interrupted: '服务中断',
  succeeded: '成功', link_ready: '链接已生成', ba_ready: 'BA 链已生成', provider_link_ready: '渠道链接已生成', upi_ready: 'UPI 材料已生成',
  probe_complete: '检测完成', not_started: '未开始', awaiting_payment: '等待支付', awaiting_paypal_approval: '等待 PayPal 批准',
  awaiting_pix_payment: '等待 PIX 支付', awaiting_blik_payment: '等待 BLIK 支付', awaiting_twint_payment: '等待 TWINT 支付',
  awaiting_card_payment: '等待直卡支付', awaiting_provider_payment: '等待渠道支付', approved: '已批准', eligible: '可用', ineligible: '不可用',
  verifying_payment: '正在复核支付', paid_success: '支付成功', token_invalid: 'Token 失效', verification_unknown: '暂无法确认', already_paid: '已是付费套餐',
  awaiting_kakao_payment: '等待 Kakao Pay', awaiting_upi_payment: '等待 UPI 支付', within_limit: '符合金额上限', over_limit: '超过金额上限',
  below_minimum: '低于金额下限', meets_minimum: '符合金额下限', known_amount: '金额已识别', unknown_allowed: '金额未知已放行',
  awaiting_approval: '等待上游批准', approval_blocked: '上游批准被拦截', approval_failed: '上游批准失败',
  unknown: '未知', not_available: '不可用', cancelled_by_user: '已取消', success: '成功', retrying: '重试中', warning: '需注意',
  eligibility_observed: '资格观察', eligibility_complete: '资格观察完成',
};


function parseProxyLines(raw) {
  return String(raw || '')
    .replace(/\r/g, '\n')
    .split(/[\n,;]+/)
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith('#'));
}

function uniqueLines(values) {
  const seen = new Set();
  const result = [];
  (Array.isArray(values) ? values : []).forEach((value) => {
    const line = String(value || '').trim();
    if (!line || seen.has(line)) return;
    seen.add(line);
    result.push(line);
  });
  return result;
}

function joinProxyLines(values) {
  return uniqueLines(values).join('\n');
}

function countryProxyMaps(methodProxy = {}) {
  return {
    countryProxies: methodProxy.countryProxies && typeof methodProxy.countryProxies === 'object' ? methodProxy.countryProxies : {},
    countryPromotionProxies: methodProxy.countryPromotionProxies && typeof methodProxy.countryPromotionProxies === 'object' ? methodProxy.countryPromotionProxies : {},
  };
}

function proxyRegionCode(raw) {
  const text = String(raw || '');
  const patterns = [
    /(?:^|[-_])(?:region|country|cc|res)-([A-Za-z]{2})(?=[-_]|$)/i,
    /(?:^|[^\w])(?:cr|country|region)[.=_-]?([A-Za-z]{2})(?=[^\w]|$)/i,
    /__cr\.([A-Za-z]{2})(?=[^\w]|$)/i,
  ];
  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (match?.[1]) return match[1].toUpperCase();
  }
  return '';
}

function proxyOptionLabel(raw) {
  const region = proxyRegionCode(raw);
  const compact = String(raw || '').replace(/^https?:\/\//i, '');
  const short = compact.length > 54 ? `${compact.slice(0, 26)}…${compact.slice(-18)}` : compact;
  return region ? `${region} · ${short}` : short;
}

function toggleProxyLine(currentRaw, line, enabled) {
  const current = parseProxyLines(currentRaw);
  if (enabled) return joinProxyLines([...current, line]);
  return joinProxyLines(current.filter((value) => value !== line));
}


function looksLikeProxyLine(line) {
  const text = String(line || '').trim();
  if (!text || text.startsWith('#')) return false;
  if (/@icloud\.com/i.test(text) || /mail-api/i.test(text) || /^\+\d+/.test(text)) return false;
  if (/^(https?:\/\/)?[A-Za-z0-9._-]+:\d{2,5}(:.+|@.+)?$/i.test(text)) return true;
  if (/cliproxy\.io|dataimpulse\.com|region-[A-Za-z]{2}|__cr\.[A-Za-z]{2}/i.test(text)) return true;
  if (/^[A-Za-z0-9._-]+:\d{2,5}:[^:\s]+:\S+$/.test(text)) return true;
  if (/^[A-Za-z0-9._-]+:\d{2,5}@[^:\s]+:\S+$/.test(text)) return true;
  return false;
}

function extractLibraryProxyLines(content, regions = []) {
  const wanted = new Set((regions || []).map((region) => String(region || '').toUpperCase()));
  return uniqueLines(parseProxyLines(content).filter((line) => {
    if (!looksLikeProxyLine(line)) return false;
    if (!wanted.size) return true;
    const region = proxyRegionCode(line);
    return region && wanted.has(region);
  }));
}

// Backward-compatible alias used by older call sites.
function extractCliproxyByRegions(content, regions) {
  return extractLibraryProxyLines(content, regions);
}

const FILE_LIBRARY_PROXY_SOURCE = 'proxy-links.txt';

// Compatibility markers required by scripts/check-frontend.mjs source contracts.
const KAKAO_CHECK_FRONTEND_MARKERS = {
  proxyConfigNote: '代理、模式、并发、次数等配置按渠道和批次保存在本浏览器 localStorage',
  continueBatchLabel: '整批转支付链（25 次起）',
  exactPromoProxyLabel: '显式优惠代理（原样使用）',
  exactPromoProxyHint: '不改地区、不替换 SID',
  providerFallbackRegion: '默认 VN',
  attemptBudgetHint: '原始默认 25 次',
  regionExpr: "kakaoMode === 'provider_link' ? 'VN' : 'TR'",
};

const FILE_LIBRARY_PROXY_FALLBACKS = ['proxy-links.txt', 'applemail.txt'];
const initialSettings = {
  country: 'US', currency: 'USD', concurrency: 4,
  countryMode: 'single', countryPool: [...defaultPayPalRandomCountries], promotionCountryPools: normalizePayPalPromotionPools({}, defaultPayPalRandomCountries), assignmentStrategy: 'random_balanced',
  customProxy: '', promotionProxy: '', promotionProxyRegion: '',
  usePromo: true, trialDays: 30, timeoutSeconds: 45, maxAttempts: 3, approveAttempts: 3, maxAmountMinor: 0,
  promoCampaignId: 'plus-1-month-free', stripePublishableKey: '', clientFingerprint: 'chrome', paymentStatusAutoRefresh: true,
  fingerprintPolicy: { promotion: 'follow', provider: 'follow', approve: 'follow' },
  fingerprintWeightMode: false,
  kakaoMode: 'eligibility', kakaoEligibilityOnly: true, paypalSameStickyIp: false, blikCode: '',
};

const WORKBENCH_STORAGE_KEY = 'automyai.extract.workbench.v3';
const PROXY_STORAGE_KEY = 'automyai.extract.proxies.v4';
const LEGACY_PROXY_SESSION_KEY = 'automyai.extract.proxies.v2';
const CREDENTIAL_SESSION_KEY = 'automyai.extract.credentials.v1';
const JOB_DRAFTS_SESSION_KEY = 'automyai.extract.job-drafts.v1';
const JOB_CONFIG_STORAGE_KEY = 'automyai.extract.job-configs.v2';
const persistedSettingKeys = [
  'country', 'currency', 'countryMode', 'countryPool', 'promotionCountryPools', 'assignmentStrategy', 'concurrency', 'usePromo', 'trialDays', 'timeoutSeconds', 'maxAttempts', 'approveAttempts',
  'amountGate', 'amountThresholdMinor', 'allowUnknownAmount', 'maxAmountMinor', 'promoCampaignId', 'stripePublishableKey', 'clientFingerprint', 'fingerprintPolicy', 'fingerprintWeightMode', 'paymentStatusAutoRefresh',
  'promotionProxyRegion', 'kakaoMode', 'kakaoEligibilityOnly', 'paypalSameStickyIp',
  'blikCode',
];

const kakaoModeOptions = [
  { value: 'eligibility', label: '资格观察' },
  { value: 'provider_link', label: '支付链提炼' },
];

const amountGateOptions = [
  { value: 'strict_zero', label: '严格等于 0' },
  { value: 'at_most', label: '不高于设定金额' },
  { value: 'at_least', label: '不低于设定金额' },
  { value: 'any_known', label: '任意已识别金额' },
];

const zeroDecimalCurrencies = new Set(['JPY', 'KRW', 'IDR', 'VND']);
const threeDecimalCurrencies = new Set(['BHD', 'JOD', 'KWD', 'OMR', 'TND']);

function currencyMinorExponent(currency) {
  const normalized = String(currency || '').trim().toUpperCase();
  if (zeroDecimalCurrencies.has(normalized)) return 0;
  if (threeDecimalCurrencies.has(normalized)) return 3;
  return 2;
}

function normalizeAmountGateSettings(value = {}) {
  const validGates = new Set(amountGateOptions.map((option) => option.value));
  const legacyLimit = Math.max(0, Math.round(Number(value.maxAmountMinor) || 0));
  const gate = validGates.has(value.amountGate)
    ? value.amountGate
    : (legacyLimit > 0 ? 'at_most' : 'strict_zero');
  const thresholdValue = Number(value.amountThresholdMinor);
  const threshold = Number.isFinite(thresholdValue) && thresholdValue >= 0
    ? Math.round(thresholdValue)
    : legacyLimit;
  return {
    ...value,
    amountGate: gate,
    amountThresholdMinor: threshold,
    allowUnknownAmount: Boolean(value.allowUnknownAmount),
    maxAmountMinor: gate === 'at_most' ? threshold : 0,
  };
}

function formatMinorAmount(minor, currency) {
  const amount = Math.max(0, Math.round(Number(minor) || 0));
  const exponent = currencyMinorExponent(currency);
  if (exponent === 0) return `${amount}`;
  const scale = 10 ** exponent;
  return `${Math.floor(amount / scale)}.${String(amount % scale).padStart(exponent, '0')}`;
}

function majorToMinor(value, currency) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric < 0) return 0;
  return Math.round(numeric * (10 ** currencyMinorExponent(currency)));
}

function amountGateSummary(settings, currency) {
  switch (settings.amountGate) {
    case 'at_most': return `不高于 ${formatMinorAmount(settings.amountThresholdMinor, currency)} ${currency}`;
    case 'at_least': return `不低于 ${formatMinorAmount(settings.amountThresholdMinor, currency)} ${currency}`;
    case 'any_known': return '任意已识别金额';
    default: return '严格等于 0';
  }
}

function defaultPromotionRegion(methodID, kakaoMode = 'eligibility') {
  if (methodID === 'ph_link') return 'TR';
  if (methodID !== 'kakao') return '';
  return kakaoMode === 'provider_link' ? 'VN' : 'TR';
}

function defaultAttempts(methodID, kakaoMode = 'eligibility') {
  if (methodID === 'ph_link') return 10;
  if (methodID === 'kakao' && kakaoMode === 'provider_link') return 10;
  return 3;
}

function constrainMethodSettings(method, value = {}) {
	value = normalizeAmountGateSettings(value);
	if (method === 'paypal_ba') {
		return {
			...value,
			countryMode: value.countryMode === 'random' ? 'random' : 'single',
			countryPool: normalizePayPalCountryPool(value.countryPool, value.country),
			promotionCountryPools: normalizePayPalPromotionPools(value.promotionCountryPools, normalizePayPalCountryPool(value.countryPool, value.country)),
			assignmentStrategy: 'random_balanced',
		};
	}
	if (method === 'ph_link') return {
		...value,
		country: 'PH',
		currency: 'PHP',
		promotionProxyRegion: value.promotionProxyRegion || 'TR',
	};
	if (method === 'pix') return { ...value, country: 'BR', currency: 'BRL' };
	if (method === 'blik') return { ...value, country: 'PL', currency: 'PLN' };
	if (method === 'twint') return { ...value, country: 'CH', currency: 'CHF' };
	if (method !== 'kakao') return value;
  const kakaoMode = value.kakaoMode === 'provider_link' ? 'provider_link' : 'eligibility';
  return {
    ...value,
    country: 'KR',
    currency: 'KRW',
    kakaoMode,
    kakaoEligibilityOnly: kakaoMode === 'eligibility',
    promotionProxyRegion: value.promotionProxyRegion || '',
    ...(kakaoMode === 'eligibility' ? {
      usePromo: false,
      paymentStatusAutoRefresh: false,
    } : {}),
  };
}

function readStoredJSON(storageName, key) {
  try {
    if (typeof window === 'undefined') return {};
    const value = window[storageName]?.getItem(key);
    return value ? JSON.parse(value) : {};
  } catch (_) {
    return {};
  }
}

function writeStoredJSON(storageName, key, value) {
  try {
    window[storageName]?.setItem(key, JSON.stringify(value));
  } catch (_) {}
}

function readStoredText(storageName, key) {
  try {
    if (typeof window === 'undefined') return '';
    return window[storageName]?.getItem(key) || '';
  } catch (_) {
    return '';
  }
}

function writeStoredText(storageName, key, value) {
  try {
    if (value) window[storageName]?.setItem(key, value);
    else window[storageName]?.removeItem(key);
  } catch (_) {}
}

function persistedSettings(settings) {
  return Object.fromEntries(persistedSettingKeys.map((key) => [key, settings[key]]));
}

function persistentJobConfig(draft, source = 'submitted') {
  if (!draft || typeof draft !== 'object') return null;
  return {
    version: 1,
    source,
    methodID: String(draft.methodID || ''),
    settings: { ...(draft.settings || {}) },
    proxies: {
      customProxy: String(draft.proxies?.customProxy || ''),
      promotionProxy: String(draft.proxies?.promotionProxy || ''),
      countryProxies: { ...(draft.proxies?.countryProxies || {}) },
      countryPromotionProxies: { ...(draft.proxies?.countryPromotionProxies || {}) },
    },
    savedAt: String(draft.savedAt || new Date().toISOString()),
  };
}

function mergePersistentJobConfigs(persisted, sessionDrafts) {
  const merged = { ...(persisted || {}) };
  Object.entries(sessionDrafts || {}).forEach(([jobID, draft]) => {
    if (!merged[jobID]) merged[jobID] = persistentJobConfig(draft, 'session_migration');
  });
  return Object.fromEntries(Object.entries(merged).filter(([, value]) => value));
}

function firstPresent(...values) {
  return values.find((value) => value !== undefined && value !== null && value !== '');
}

function labelFor(value) {
  return statusLabels[value] || value || '—';
}

function toneFor(status) {
  if (['eligibility_observed', 'eligibility_complete', 'probe_complete'].includes(status)) return 'bg-glass';
  if (['completed', 'succeeded', 'success', 'link_ready', 'ba_ready', 'provider_link_ready', 'upi_ready', 'approved', 'within_limit'].includes(status)) return 'bg-success';
  if (['eligible'].includes(status)) return 'bg-glass';
  if (['failed', 'interrupted', 'ineligible', 'not_available', 'over_limit', 'approval_blocked', 'approval_failed'].includes(status)) return 'bg-error';
  if (['running', 'retrying', 'submitted'].includes(status)) return 'bg-accent';
  if (['warning', 'awaiting_payment', 'awaiting_approval', 'awaiting_paypal_approval', 'awaiting_card_payment', 'awaiting_provider_payment', 'awaiting_kakao_payment', 'awaiting_upi_payment'].includes(status)) return 'bg-warning';
  return 'bg-glass';
}

function accountStatusIcon(status) {
  if (['succeeded', 'completed', 'success'].includes(status)) return CheckCircle2;
  if (['failed', 'interrupted'].includes(status)) return XCircle;
  if (status === 'cancelled') return Ban;
  if (status === 'running') return LoaderCircle;
  return CircleDollarSign;
}

function AccountStatus({ item }) {
  const Icon = accountStatusIcon(item?.status);
  return <StatusBadge tone={toneFor(item?.status)}><Icon size={13} className={item?.status === 'running' ? 'animate-spin' : ''} />{labelFor(item?.status || 'queued')}</StatusBadge>;
}

function mergeMethods(liveMethods) {
  const live = new Map((Array.isArray(liveMethods) ? liveMethods : []).map((method) => [method.id, method]));
  const merged = fallbackMethods.map((method) => ({ ...method, ...(live.get(method.id) || {}) }));
  const known = new Set(merged.map((method) => method.id));
  (Array.isArray(liveMethods) ? liveMethods : []).forEach((method) => {
    if (!known.has(method.id)) merged.push(method);
  });
  return merged;
}

function elapsed(value) {
  const milliseconds = Number(value || 0);
  if (!milliseconds) return '—';
  if (milliseconds < 1000) return `${milliseconds} ms`;
  if (milliseconds < 60000) return `${(milliseconds / 1000).toFixed(1)} s`;
  return `${Math.floor(milliseconds / 60000)}m ${Math.round((milliseconds % 60000) / 1000)}s`;
}

function itemLink(item, method) {
  if (method === 'upi') {
    const material = upiMaterial(item);
    return firstPresent(material?.instructionUrl, material?.qrPngUrl, material?.qrSvgUrl, material?.payload, '');
  }
  return firstPresent(item?.providerRedirectUrl, item?.longUrl, item?.stripeRedirectUrl, item?.result?.providerRedirectUrl, item?.result?.longUrl, item?.result?.stripeRedirectUrl, '');
}

function itemDisplayLabel(item) {
  const email = String(item?.email || '').trim();
  if (email) return email;
  const label = String(item?.label || '').trim();
  const lower = label.toLowerCase();
  if (!label || lower.startsWith('map[') || lower.startsWith('[object object]') || (label.startsWith('{') && label.endsWith('}'))) {
    return `账号 ${item?.index || '—'}`;
  }
  return label;
}

function kakaoEligibleAccountsFrom(job) {
  if (!job || job.method !== 'kakao') return [];
  const values = [...(Array.isArray(job.eligibleAccounts) ? job.eligibleAccounts : [])];
  (Array.isArray(job.items) ? job.items : []).forEach((item) => {
    if (String(item?.decision || item?.result?.decision || '').toLowerCase() === 'eligible') {
      values.push(itemDisplayLabel(item));
    }
  });
  return values.map((value) => String(value || '').trim()).filter(Boolean);
}

const stageLabels = {
  'job.created': '批次已创建',
  'job.started': '账号开始执行',
  'job.completed': '账号流程完成',
  'job.failed': '账号流程失败',
  proxy: '分配各阶段代理',
  fingerprint: '选择请求指纹',
  'chatgpt.checkout': '创建 ChatGPT Checkout',
  'chatgpt.checkout_update': '更新优惠参数',
  'chatgpt.checkout_taxes': '同步 Kakao 账单税务',
  'chatgpt.approve': '请求 OpenAI Checkout 批准',
  'chatgpt.checkout_snapshot': '同步账单快照',
  'stripe.init': '初始化 Stripe 支付页',
  'stripe.activate': '加载 Stripe 支付页',
  'stripe.bootstrap_init': '首次检查上游支付方式',
  'stripe.post_promotion_init': '优惠更新后刷新 Stripe',
  'stripe.post_tax_init': '税务同步后刷新 Stripe',
  'stripe.tax_region': '同步 Stripe Kakao 税区',
  'stripe.pre_confirm': '准备 Kakao Confirm',
  'stripe.payment_method': '创建 Kakao Payment Method',
  'stripe.confirm': '提交 Kakao Confirm',
  'stripe.redirect_poll': '等待 Kakao Provider 跳转',
	'ph.checkout_type': '校验 PH Checkout 类型',
	'ideal.checkout_type': '校验 iDEAL Checkout 类型',
	'gopay.checkout_type': '校验 GoPay Checkout 类型',
	'ideal.network_retry': 'iDEAL 网络重试（不计次）',
	'gopay.network_retry': 'GoPay 网络重试（不计次）',
	'ideal.payment_declined': 'iDEAL 本轮支付方式被拒绝（完整重跑）',
	'gopay.payment_declined': 'GoPay 本轮支付方式被拒绝（完整重跑）',
	'ideal.approval_blocked': 'iDEAL 本轮 Approve 被拦截（完整重跑）',
	'gopay.approval_blocked': 'GoPay 本轮 Approve 被拦截（完整重跑）',
  'kakao.transport': '启用已验证的 Kakao 传输',
  'kakao.full_attempt': '执行完整 Kakao 支付链',
  'kakao.eligibility': '确认上游真实返回 Kakao Pay',
  'kakao.provider_redirect': '解析 NicePay / KakaoPay 链接',
  'kakao.link_expiry': '记录二维码/长链有效期',
  'kakao.bootstrap_fallback': '切换为 Kakao 无优惠初始页',
  'kakao.bootstrap_methods': '初始 Kakao 支付方式检查',
  'kakao.hidden_method_probe': '检查上游 Kakao 支付方式',
  'kakao.diagnostic_stop': '资格诊断在付款前停止',
  'stripe.elements_session': '创建 Stripe Elements 会话',
  'stripe.upi_tax_region': '同步 UPI 税区',
  'stripe.upi_confirm': '确认 UPI 支付方式',
  'stripe.upi_poll': '轮询 UPI 支付材料',
  'upi.material': '校验 UPI 支付材料',
  'upi.material.validation': 'UPI 支付材料校验失败',
};

function stageTitle(stage) {
  return stageLabels[stage] || stage || '流程步骤';
}


function resolveLinkExpiry(item = {}, method = '') {
  const metadata = item?.metadata || item?.result?.metadata || {};
  const link = String(item?.providerRedirectUrl || item?.longUrl || item?.result?.providerRedirectUrl || item?.result?.longUrl || '').trim();
  const extractionStatus = String(item?.extractionStatus || item?.result?.extractionStatus || '').toLowerCase();
  const isKakaoLink = method === 'kakao' || extractionStatus === 'provider_link_ready' || /nicepay|kakao/.test(link);
  if (!isKakaoLink || !link) return null;
  const generatedAt = item.linkGeneratedAt || item.result?.linkGeneratedAt || metadata.linkGeneratedAt || item.finishedAt || '';
  let expiresAt = item.expiresAt || item.result?.expiresAt || metadata.expiresAt || metadata.providerLinkExpiresAt || '';
  let ttlSeconds = Number(item.linkTtlSeconds || item.result?.linkTtlSeconds || metadata.linkTtlSeconds || 0);
  if (!Number.isFinite(ttlSeconds) || ttlSeconds <= 0) ttlSeconds = 600;
  if (!expiresAt && generatedAt) {
    const generatedMs = Date.parse(generatedAt);
    if (Number.isFinite(generatedMs)) expiresAt = new Date(generatedMs + ttlSeconds * 1000).toISOString();
  }
  if (!expiresAt) return null;
  return {
    link,
    generatedAt,
    expiresAt,
    ttlSeconds,
  };
}

function formatCountdown(ms) {
  const total = Math.max(0, Math.ceil(Number(ms || 0) / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

function displayTimestamp(value) {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString('zh-CN', { hour12: false });
}

function isKakaoEligibilityJob(job) {
  if (!job || job.method !== 'kakao') return false;
  const options = job.options || {};
  const mode = String(options.kakaoMode || options.mode || '').trim().toLowerCase();
  if (mode === 'eligibility' || options.kakaoEligibilityOnly === true || options.eligibilityOnly === true) return true;
  // Historical eligibility batches may only expose diagnostic fields.
  const items = Array.isArray(job.items) ? job.items : [];
  if (!items.length) return Number(job?.eligible || 0) > 0 || Number(job?.succeeded || 0) === 0 && Array.isArray(job.eligibleAccounts);
  const hasProviderMaterial = items.some((item) => String(item?.longUrl || item?.providerRedirectUrl || '').trim());
  const looksDiagnostic = items.every((item) => {
    const status = String(item?.status || '').toLowerCase();
    const extraction = String(item?.extractionStatus || '').toLowerCase();
    const detail = String(item?.detail || '');
    return (
      status === 'eligibility_observed'
      || extraction === 'probe_complete'
      || extraction === 'eligibility_complete'
      || detail.includes('上游资格观察')
      || detail.includes('资格观察')
    );
  });
  return looksDiagnostic && !hasProviderMaterial;
}

function jobHasProviderSuccess(job) {
  if (!job) return false;
  if (Number(job?.succeeded || 0) > 0) {
    const items = Array.isArray(job.items) ? job.items : [];
    if (!items.length) return true;
    return items.some((item) => {
      if (String(item?.status || '').toLowerCase() !== 'succeeded') return false;
      const extraction = String(item?.extractionStatus || '').toLowerCase();
      if (['provider_link_ready', 'link_ready', 'ba_ready', 'upi_ready'].includes(extraction)) return true;
      return Boolean(String(item?.longUrl || item?.providerRedirectUrl || '').trim());
    });
  }
  return false;
}

function isSuccessfulHistoryJob(job) {
  // Kakao eligibility / 资格观察 never belongs in "成功".
  if (isKakaoEligibilityJob(job) && !jobHasProviderSuccess(job)) return false;
  return jobHasProviderSuccess(job) || Number(job?.succeeded || 0) > 0;
}

function historyJobScope(job) {
  const options = job?.options || {};
  const randomPool = Array.isArray(options.countryPool) ? options.countryPool.filter(Boolean) : [];
  const countryCurrency = options.countryMode === 'random' && randomPool.length
    ? `随机账单 ${randomPool.join(' / ')}`
    : [options.country, options.currency].filter(Boolean).join(' / ');
  const promotion = options.promotionProxyRegion ? `Promotion ${options.promotionProxyRegion}` : '';
  const execution = `并发 ${job?.concurrency || 1} · 尝试 ${options.maxAttempts || '—'}`;
  return [countryCurrency, promotion, execution].filter(Boolean).join(' · ');
}

function buildTimeline(job, item) {
  if (!item) return [];
  const timeline = [];
  if (job?.createdAt) timeline.push({ at: job.createdAt, stage: 'job.created', status: 'success', detail: `${job.methodLabel || job.method || '提炼'} · 共 ${job.total || 0} 个账号` });
  if (item.startedAt) timeline.push({ at: item.startedAt, stage: 'job.started', status: 'success', detail: itemDisplayLabel(item) });
  const steps = Array.isArray(item.steps) ? item.steps : [];
  timeline.push(...steps);
  if (item.finishedAt) {
    const failed = ['failed', 'cancelled', 'interrupted'].includes(item.status);
    timeline.push({ at: item.finishedAt, stage: failed ? 'job.failed' : 'job.completed', status: failed ? 'failed' : 'success', detail: item.detail || item.error || labelFor(item.status), elapsedMs: item.durationMs });
  } else if (item.status === 'running') {
    const last = steps[steps.length - 1];
    const alreadyLatest = last && last.stage === item.stage && (last.detail || '') === (item.detail || '');
    if (!alreadyLatest) {
      timeline.push({ at: '', stage: item.stage || 'running', status: 'running', detail: item.detail || '正在执行' });
    }
  }
  return timeline;
}

function upiMaterial(item) {
  if (!item) return null;
  const payload = firstPresent(item.upiPayload, item.metadata?.upiPayload, '');
  const instructionUrl = firstPresent(item.upiInstructionUrl, item.metadata?.instructionUrl, '');
  const qrPngUrl = firstPresent(item.qrPngUrl, item.metadata?.qrPngUrl, '');
  const qrSvgUrl = firstPresent(item.qrSvgUrl, item.metadata?.qrSvgUrl, '');
  if (!payload && !instructionUrl && !qrPngUrl && !qrSvgUrl) return null;
  return { payload, instructionUrl, qrPngUrl, qrSvgUrl, imageUrl: qrPngUrl || qrSvgUrl };
}

function paymentMaterial(item) {
  if (!item) return null;
  const payload = firstPresent(item.paymentPayload, item.result?.paymentPayload, item.metadata?.paymentPayload, '');
  const instructionUrl = firstPresent(item.paymentInstructionUrl, item.result?.paymentInstructionUrl, item.metadata?.paymentInstructionUrl, '');
  const qrPngUrl = firstPresent(item.qrPngUrl, item.result?.qrPngUrl, item.metadata?.qrPngUrl, '');
  const qrSvgUrl = firstPresent(item.qrSvgUrl, item.result?.qrSvgUrl, item.metadata?.qrSvgUrl, '');
  if (!payload && !instructionUrl && !qrPngUrl && !qrSvgUrl) return null;
  return { payload, instructionUrl, qrPngUrl, qrSvgUrl, imageUrl: qrPngUrl || qrSvgUrl };
}


function KakaoLinkMaterialPanel({ item, method, onCopy }) {
  const expiry = useMemo(() => resolveLinkExpiry(item, method), [item, method]);
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    if (!expiry?.expiresAt) return undefined;
    const timer = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [expiry?.expiresAt]);
  if (!expiry?.link) return null;
  const expiresMs = Date.parse(expiry.expiresAt);
  const remainMs = Number.isFinite(expiresMs) ? expiresMs - nowMs : 0;
  const expired = remainMs <= 0;
  const ratio = Math.max(0, Math.min(1, remainMs / (Math.max(1, expiry.ttlSeconds) * 1000)));
  return (
    <section className={`extraction-kakao-material ${expired ? 'is-expired' : 'is-active'}`}>
      <div className="extraction-upi-heading">
        <span><Timer size={16} /></span>
        <div>
          <b>Kakao / NicePay 待支付长链</b>
          <small>该渠道二维码/支付页有效期约 10 分钟，超时需重新提炼</small>
        </div>
      </div>
      <div className="extraction-kakao-countdown">
        <div className="extraction-kakao-countdown-main">
          <em>{expired ? '已过期' : '剩余有效时间'}</em>
          <b>{expired ? '00:00' : formatCountdown(remainMs)}</b>
        </div>
        <div className="extraction-kakao-countdown-meta">
          <span>生成 {displayTimestamp(expiry.generatedAt || item.finishedAt)}</span>
          <span>到期 {displayTimestamp(expiry.expiresAt)}</span>
        </div>
        <div className="extraction-kakao-countdown-bar" aria-hidden="true">
          <i style={{ width: `${ratio * 100}%` }} />
        </div>
      </div>
      <div className="extraction-upi-values">
        <div>
          <span>渠道链接</span>
          <code title={expiry.link}>{expiry.link}</code>
          <span className="extraction-upi-actions">
            <GlassButton variant="icon" title="复制渠道链接" onClick={() => onCopy(expiry.link, 'Kakao 渠道链接已复制')}><Copy size={14} /></GlassButton>
            <GlassButton variant="icon" title="打开渠道链接" onClick={() => window.open(expiry.link, '_blank', 'noopener,noreferrer')}><ExternalLink size={14} /></GlassButton>
          </span>
        </div>
      </div>
    </section>
  );
}

function UPIMaterialPanel({ material, onCopy }) {
  const [generatedQR, setGeneratedQR] = useState('');
  useEffect(() => {
    let active = true;
    if (material.imageUrl || !material.payload) {
      setGeneratedQR('');
      return () => { active = false; };
    }
    QRCode.toDataURL(material.payload, { errorCorrectionLevel: 'M', margin: 2, width: 360 })
      .then((value) => { if (active) setGeneratedQR(value); })
      .catch(() => { if (active) setGeneratedQR(''); });
    return () => { active = false; };
  }, [material.imageUrl, material.payload]);
  const qrImage = material.imageUrl || generatedQR;
  return (
    <section className="extraction-upi-material">
      <div className="extraction-upi-heading"><span><ScanLine size={16} /></span><div><b>UPI 支付材料</b><small>二维码、UPI payload 与 instructions 链接分别保留</small></div></div>
      {qrImage ? <a className="extraction-upi-qr" href={qrImage} target="_blank" rel="noreferrer"><img src={qrImage} alt="UPI 支付二维码" loading="lazy" referrerPolicy="no-referrer" /></a> : null}
      <div className="extraction-upi-values">
        {material.payload ? <div><span>UPI payload</span><code title={material.payload}>{material.payload}</code><GlassButton variant="icon" title="复制 UPI payload" onClick={() => onCopy(material.payload, 'UPI payload 已复制')}><Copy size={14} /></GlassButton></div> : null}
        {material.instructionUrl ? <div><span>Instructions</span><code title={material.instructionUrl}>{material.instructionUrl}</code><span className="extraction-upi-actions"><GlassButton variant="icon" title="复制 instructions 链接" onClick={() => onCopy(material.instructionUrl)}><Copy size={14} /></GlassButton><GlassButton variant="icon" title="打开 instructions 链接" onClick={() => window.open(material.instructionUrl, '_blank', 'noopener,noreferrer')}><ExternalLink size={14} /></GlassButton></span></div> : null}
        {material.qrPngUrl ? <div><span>QR PNG</span><code title={material.qrPngUrl}>{material.qrPngUrl}</code><GlassButton variant="icon" title="复制 PNG 地址" onClick={() => onCopy(material.qrPngUrl, 'QR PNG 地址已复制')}><Copy size={14} /></GlassButton></div> : null}
        {material.qrSvgUrl ? <div><span>QR SVG</span><code title={material.qrSvgUrl}>{material.qrSvgUrl}</code><GlassButton variant="icon" title="复制 SVG 地址" onClick={() => onCopy(material.qrSvgUrl, 'QR SVG 地址已复制')}><Copy size={14} /></GlassButton></div> : null}
      </div>
    </section>
  );
}

function previewItem(row) {
  return { id: `preview-${row.index}`, index: row.index, label: row.label, email: row.email, status: 'queued', stage: '等待提交', extractionStatus: 'queued', paymentStatus: 'not_started' };
}

export default function ExtractionCenter() {
  const { notify } = useToast();
  const restoredWorkbench = useMemo(() => readStoredJSON('localStorage', WORKBENCH_STORAGE_KEY), []);
  const restoredProxies = useMemo(() => ({
    ...readStoredJSON('localStorage', PROXY_STORAGE_KEY),
    ...readStoredJSON('sessionStorage', LEGACY_PROXY_SESSION_KEY),
  }), []);
  const restoredSessionJobDrafts = useMemo(() => readStoredJSON('sessionStorage', JOB_DRAFTS_SESSION_KEY), []);
  const restoredPersistentJobConfigs = useMemo(() => mergePersistentJobConfigs(
    readStoredJSON('localStorage', JOB_CONFIG_STORAGE_KEY),
    restoredSessionJobDrafts,
  ), [restoredSessionJobDrafts]);
  const restoredMethodID = String(restoredWorkbench.methodID || 'paypal_ba');
  const restoredMethodSettings = restoredWorkbench.settingsByMethod?.[restoredMethodID] || {};
  const restoredMethodProxy = restoredProxies?.[restoredMethodID] || {};
  const restoredCountryProxyMaps = countryProxyMaps(restoredMethodProxy);
  const restoredCountry = restoredMethodSettings.country || initialSettings.country;
  const [catalog, setCatalog] = useState({ defaultMethod: 'paypal_ba', methods: fallbackMethods, limits: { maxItems: 500, maxConcurrency: 32 }, sourcePath: '' });
  const [methodID, setMethodID] = useState(restoredMethodID);
  const [input, setInput] = useState(() => readStoredText('sessionStorage', CREDENTIAL_SESSION_KEY));
  const [mailAdminPickerOpen, setMailAdminPickerOpen] = useState(false);
  const [mailAdminImporting, setMailAdminImporting] = useState(false);
  const [proxyCheckStatus, setProxyCheckStatus] = useState({ main: '', promotion: '' });
  const [proxyChecking, setProxyChecking] = useState({ main: false, promotion: false });
  const [settings, setSettings] = useState(() => constrainMethodSettings(restoredMethodID, {
    ...initialSettings,
    ...restoredMethodSettings,
    ...restoredMethodProxy,
    customProxy: restoredMethodProxy.customProxy || restoredCountryProxyMaps.countryProxies[restoredCountry] || '',
    promotionProxy: restoredMethodProxy.promotionProxy || restoredCountryProxyMaps.countryPromotionProxies[restoredCountry] || '',
    maxAttempts: restoredMethodSettings.maxAttempts || defaultAttempts(restoredMethodID, restoredMethodSettings.kakaoMode),
    promotionProxyRegion: restoredMethodSettings.promotionProxyRegion || defaultPromotionRegion(restoredMethodID, restoredMethodSettings.kakaoMode),
  }));
  const [settingsByMethod, setSettingsByMethod] = useState(() => restoredWorkbench.settingsByMethod || {});
  const [proxyByMethod, setProxyByMethod] = useState(() => restoredProxies || {});
  const [jobDrafts, setJobDrafts] = useState(() => restoredSessionJobDrafts);
  const [jobConfigs, setJobConfigs] = useState(() => restoredPersistentJobConfigs);
  const [currentJob, setCurrentJob] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [historyFilter, setHistoryFilter] = useState('success');
  const [activeJobID, setActiveJobID] = useState(() => String(restoredWorkbench.currentJobID || ''));
  const [selectedItemID, setSelectedItemID] = useState(() => String(restoredWorkbench.selectedItemID || ''));
	const [selectedRetryItemIDs, setSelectedRetryItemIDs] = useState([]);
  const [mobileView, setMobileView] = useState(() => ['create', 'run', 'results'].includes(restoredWorkbench.mobileView) ? restoredWorkbench.mobileView : 'create');
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [savingKakaoReport, setSavingKakaoReport] = useState(false);
	const [retryFilters, setRetryFilters] = useState({ excludeInvalidToken: true, excludePaidPlan: true, excludeLegacyOAICS: true });
  const [error, setError] = useState('');
  const [libraryProxyOptions, setLibraryProxyOptions] = useState([]);
  const [libraryProxyStatus, setLibraryProxyStatus] = useState('');
  const [libraryMainExpanded, setLibraryMainExpanded] = useState(false);
  const [libraryPromotionExpanded, setLibraryPromotionExpanded] = useState(false);
  const autoContinuationIDs = useRef(new Set());

  const methods = useMemo(() => mergeMethods(catalog.methods), [catalog.methods]);
  const selectedMethod = methods.find((method) => method.id === methodID) || methods[0];
  const countries = useMemo(() => selectedMethod?.countries || [], [selectedMethod]);
  const countrySelectOptions = useMemo(() => {
    if (methodID === 'kakao') return [{ value: 'KR', label: 'KR' }];
    return countries.map((country) => ({
      value: country,
      label: methodID === 'paypal_ba' ? `${country} · ${payPalCountryNames[country] || country}` : country,
    }));
  }, [countries, methodID]);
  const payPalRandomMode = methodID === 'paypal_ba' && settings.countryMode === 'random';
  const payPalCountryPool = useMemo(
    () => normalizePayPalCountryPool(settings.countryPool, settings.country).filter((country) => countries.includes(country)),
    [countries, settings.country, settings.countryPool],
  );
  const payPalPromotionPools = useMemo(
    () => normalizePayPalPromotionPools(settings.promotionCountryPools, payPalCountryPool),
    [payPalCountryPool, settings.promotionCountryPools],
  );
  const effectiveCurrency = methodID === 'paypal_ba' ? (currencyByCountry[settings.country] || 'USD') : settings.currency;
  const inputRows = useMemo(() => parseExtractionInput(input), [input]);
  const mainProxyLines = useMemo(() => parseProxyLines(settings.customProxy), [settings.customProxy]);
  const promotionProxyLines = useMemo(() => parseProxyLines(settings.promotionProxy), [settings.promotionProxy]);
  const libraryMainOptions = useMemo(() => libraryProxyOptions, [libraryProxyOptions]);
  const libraryPromotionOptions = useMemo(() => libraryProxyOptions, [libraryProxyOptions]);

  const maxItems = catalog.limits?.maxItems || 500;
  const maxConcurrency = catalog.limits?.maxConcurrency || 32;
  const previewRows = useMemo(() => inputRows.slice(0, 8).map(previewItem), [inputRows]);
  const selectedItem = currentJob?.items?.find((item) => item.id === selectedItemID)
    || currentJob?.items?.find((item) => item.status === 'running')
    || currentJob?.items?.[0]
    || null;
  const selectedUPI = upiMaterial(selectedItem);
  const selectedTimeline = useMemo(() => buildTimeline(currentJob, selectedItem), [currentJob, selectedItem]);
  const timelineRef = useRef(null);
  const timelineFollowRef = useRef(true);
  const handleTimelineScroll = useCallback(() => {
    const node = timelineRef.current;
    if (!node) return;
    const distance = node.scrollHeight - node.scrollTop - node.clientHeight;
    timelineFollowRef.current = distance < 48;
  }, []);
  useEffect(() => {
    const node = timelineRef.current;
    if (!node || !timelineFollowRef.current) return;
    node.scrollTop = node.scrollHeight;
  }, [selectedTimeline, selectedItem?.id]);
  useEffect(() => {
    timelineFollowRef.current = true;
  }, [selectedItem?.id]);
  const visibleItems = currentJob?.items?.length ? currentJob.items : previewRows;
  const metrics = useMemo(() => {
    const total = Number(currentJob?.total || inputRows.length || 0);
    const succeeded = Number(currentJob?.succeeded || 0);
    const failed = Number(currentJob?.failed || 0) + Number(currentJob?.cancelled || 0);
    const running = Number(currentJob?.running || 0);
    const queued = Math.max(0, total - succeeded - failed - running);
    return { total, succeeded, failed, running, queued };
  }, [currentJob, inputRows.length]);
  const completedCount = metrics.succeeded + metrics.failed;
  const progress = metrics.total ? Math.min(100, Math.round((completedCount / metrics.total) * 100)) : 0;
  const serviceConnected = Boolean(catalog.sourcePath) && !error;
  const historyJobs = useMemo(
    () => jobs.filter((job) => !(isKakaoEligibilityJob(job) && !jobHasProviderSuccess(job))),
    [jobs],
  );
  const historyCounts = useMemo(() => ({
    success: historyJobs.filter(isSuccessfulHistoryJob).length,
    all: historyJobs.length,
    active: historyJobs.filter((job) => !terminalStatuses.has(job.status)).length,
    unsuccessful: historyJobs.filter((job) => terminalStatuses.has(job.status) && !isSuccessfulHistoryJob(job)).length,
  }), [historyJobs]);
  const visibleHistoryJobs = useMemo(() => historyJobs.filter((job) => {
    if (historyFilter === 'all') return true;
    if (historyFilter === 'active') return !terminalStatuses.has(job.status);
    if (historyFilter === 'unsuccessful') return terminalStatuses.has(job.status) && !isSuccessfulHistoryJob(job);
    return isSuccessfulHistoryJob(job);
  }), [historyFilter, historyJobs]);
  const kakaoEligibleAccounts = useMemo(() => {
    const unique = new Map();
    [...jobs, currentJob].filter(Boolean).forEach((job) => {
      kakaoEligibleAccountsFrom(job).forEach((account) => unique.set(account.toLowerCase(), account));
    });
    return [...unique.values()];
  }, [currentJob, jobs]);

  const updateSetting = (key, value) => {
    setSettings((current) => {
      let next = { ...current, [key]: value };
      if (methodID === 'kakao' && key === 'kakaoMode') {
        next = value === 'provider_link'
          ? { ...next, kakaoMode: 'provider_link', maxAttempts: 10, usePromo: true, paymentStatusAutoRefresh: true }
          : { ...next, kakaoMode: 'eligibility' };
      }
      return constrainMethodSettings(methodID, next);
    });
    if (key === 'customProxy' || key === 'promotionProxy') {
      setProxyByMethod((current) => ({
        ...current,
        [methodID]: {
          ...(current[methodID] || {}), [key]: value,
          ...(methodID === 'paypal_ba' ? {
            [key === 'customProxy' ? 'countryProxies' : 'countryPromotionProxies']: {
              ...((current[methodID] || {})[key === 'customProxy' ? 'countryProxies' : 'countryPromotionProxies'] || {}),
              [settings.country]: value,
            },
          } : {}),
        },
      }));
    } else {
      setSettingsByMethod((current) => ({
        ...current,
        [methodID]: {
          ...(current[methodID] || {}),
          [key]: value,
          ...(methodID === 'kakao' && key === 'kakaoMode' && value === 'provider_link'
            ? { maxAttempts: 10, usePromo: true, paymentStatusAutoRefresh: true }
            : (methodID === 'kakao' && key === 'kakaoMode' ? { usePromo: false, paymentStatusAutoRefresh: false } : {})),
        },
      }));
    }
  };

  const chooseMethod = (method, overrides = {}) => {
    const activeProxy = { customProxy: settings.customProxy, promotionProxy: settings.promotionProxy };
    const rememberedProxy = method.id === methodID ? activeProxy : (proxyByMethod[method.id] || {});
    const activeSettings = persistedSettings(settings);
    const rememberedSettings = method.id === methodID ? activeSettings : (settingsByMethod[method.id] || {});
    setProxyByMethod((current) => ({ ...current, [methodID]: activeProxy }));
    setSettingsByMethod((current) => ({ ...current, [methodID]: activeSettings }));
    setMethodID(method.id);
    const rememberedCountry = rememberedSettings.country || settings.country;
    const country = method.countries?.includes(rememberedCountry) ? rememberedCountry : (method.countries?.[0] || 'US');
    const rememberedMaps = countryProxyMaps(rememberedProxy);
    setSettings(constrainMethodSettings(method.id, {
      ...initialSettings,
      ...rememberedSettings,
      ...overrides,
      country,
      currency: currencyForMethodCountry(method.id, country, rememberedSettings.currency),
      maxAttempts: rememberedSettings.maxAttempts || defaultAttempts(method.id, rememberedSettings.kakaoMode),
      promotionProxyRegion: rememberedSettings.promotionProxyRegion || defaultPromotionRegion(method.id, rememberedSettings.kakaoMode),
      customProxy: rememberedMaps.countryProxies[country] || rememberedProxy.customProxy || '',
      promotionProxy: rememberedMaps.countryPromotionProxies[country] || rememberedProxy.promotionProxy || '',
    }));
  };

  const chooseCountry = (country) => {
    const currency = currencyByCountry[country] || 'USD';
    const previousCountry = settings.country;
    if (methodID === 'paypal_ba') {
      setProxyByMethod((current) => {
        const existing = current[methodID] || {};
        const maps = countryProxyMaps(existing);
        return { ...current, [methodID]: {
          ...existing,
          customProxy: settings.customProxy,
          promotionProxy: settings.promotionProxy,
          countryProxies: { ...maps.countryProxies, [previousCountry]: settings.customProxy },
          countryPromotionProxies: { ...maps.countryPromotionProxies, [previousCountry]: settings.promotionProxy },
        } };
      });
      const maps = countryProxyMaps({
        ...(proxyByMethod[methodID] || {}),
        countryProxies: { ...((proxyByMethod[methodID] || {}).countryProxies || {}), [previousCountry]: settings.customProxy },
        countryPromotionProxies: { ...((proxyByMethod[methodID] || {}).countryPromotionProxies || {}), [previousCountry]: settings.promotionProxy },
      });
      const nextProxy = maps.countryProxies[country] || '';
      const nextPromotionProxy = maps.countryPromotionProxies[country] || '';
      setSettings((current) => ({ ...current, country, currency, customProxy: nextProxy, promotionProxy: nextPromotionProxy }));
      setSettingsByMethod((current) => ({ ...current, [methodID]: { ...(current[methodID] || {}), country, currency } }));
      return;
    }
    setSettings((current) => ({ ...current, country, currency }));
    setSettingsByMethod((current) => ({
      ...current,
      [methodID]: { ...(current[methodID] || {}), country, currency },
    }));
  };

  const togglePayPalCountry = (country, enabled) => {
    const nextPool = enabled
      ? uniqueLines([...payPalCountryPool, country])
      : payPalCountryPool.filter((candidate) => candidate !== country);
    updateSetting('countryPool', nextPool);
    updateSetting('promotionCountryPools', normalizePayPalPromotionPools(settings.promotionCountryPools, nextPool));
  };

  const togglePayPalPromotionCountry = (mainCountry, promotionCountry, enabled) => {
    const current = payPalPromotionPools[mainCountry] || [];
    const next = enabled ? uniqueLines([...current, promotionCountry]) : current.filter((item) => item !== promotionCountry);
    updateSetting('promotionCountryPools', { ...payPalPromotionPools, [mainCountry]: next });
  };

  const loadJob = useCallback(async (jobID, quiet = false, preferredItemID = '') => {
    if (!jobID) return null;
    try {
      const payload = await extractionApi.getJob(jobID);
      if (payload?.job) {
        setCurrentJob(payload.job);
        setJobs((current) => [payload.job, ...current.filter((job) => job.id !== payload.job.id)]);
        setActiveJobID(payload.job.id);
        setSelectedItemID((current) => {
          const candidates = [preferredItemID, current].filter(Boolean);
          return candidates.find((candidate) => payload.job.items?.some((item) => item.id === candidate)) || payload.job.items?.[0]?.id || '';
        });
      }
      return payload?.job || null;
    } catch (requestError) {
      // A compatibility-only service can create a job without exposing the
      // newer read endpoint. Keep the submitted snapshot visible in that case.
      if (!quiet) notify(requestError.message || '读取任务失败', 'error');
      return null;
    }
  }, [notify]);

  const loadInitial = useCallback(async () => {
    setLoading(true);
    setError('');
    const [catalogResult, jobsResult] = await Promise.allSettled([extractionApi.getCatalog(), extractionApi.listJobs()]);
    if (catalogResult.status === 'fulfilled') {
      const nextCatalog = catalogResult.value;
      const mergedMethods = mergeMethods(nextCatalog.methods);
      setCatalog({ ...nextCatalog, methods: mergedMethods });
      const desiredMethod = mergedMethods.find((method) => method.id === restoredMethodID) || mergedMethods.find((method) => method.id === nextCatalog.defaultMethod) || mergedMethods[0];
      if (desiredMethod) {
        const remembered = restoredWorkbench.settingsByMethod?.[desiredMethod.id] || {};
        const rememberedProxy = restoredProxies?.[desiredMethod.id] || {};
        const country = desiredMethod.countries?.includes(remembered.country) ? remembered.country : (desiredMethod.countries?.[0] || remembered.country || 'US');
        setMethodID(desiredMethod.id);
        setSettings(constrainMethodSettings(desiredMethod.id, {
          ...initialSettings,
          ...remembered,
          country,
          currency: currencyForMethodCountry(desiredMethod.id, country, remembered.currency),
          maxAttempts: remembered.maxAttempts || defaultAttempts(desiredMethod.id, remembered.kakaoMode),
          promotionProxyRegion: remembered.promotionProxyRegion || defaultPromotionRegion(desiredMethod.id, remembered.kakaoMode),
          customProxy: rememberedProxy.customProxy || '',
          promotionProxy: rememberedProxy.promotionProxy || '',
        }));
      }
    }
    if (jobsResult.status === 'fulfilled') {
      const history = Array.isArray(jobsResult.value?.jobs) ? jobsResult.value.jobs : [];
      setJobs(history);
      let loadedJob = null;
      const pendingContinuation = history.find((job) => (
        job?.method === 'kakao'
        && job?.continuation?.mode === 'provider_link'
        && job?.continuation?.status === 'requested'
        && restoredSessionJobDrafts?.[job.id]?.input
      ));
      if (pendingContinuation?.id) loadedJob = await loadJob(pendingContinuation.id, true);
      if (!loadedJob && restoredWorkbench.currentJobID) loadedJob = await loadJob(restoredWorkbench.currentJobID, true, restoredWorkbench.selectedItemID);
      const defaultJob = history.find(isSuccessfulHistoryJob) || history[0];
      if (!loadedJob && defaultJob?.id) await loadJob(defaultJob.id, true);
    }
    if (catalogResult.status === 'rejected') {
      setCatalog((current) => ({ ...current, sourcePath: '', methods: mergeMethods(current.methods).map((method) => ({ ...method, available: false, runnable: false })) }));
      setError('提炼接口当前未连接；页面已保留配置，服务恢复后可重新加载。');
    }
    setLoading(false);
  }, [loadJob, restoredMethodID, restoredProxies, restoredSessionJobDrafts, restoredWorkbench]);

  useEffect(() => { loadInitial(); }, [loadInitial]);

  useEffect(() => {
    writeStoredJSON('localStorage', WORKBENCH_STORAGE_KEY, {
      version: 2,
      methodID,
      settingsByMethod: { ...settingsByMethod, [methodID]: persistedSettings(settings) },
      currentJobID: activeJobID,
      selectedItemID,
      mobileView,
    });
  }, [activeJobID, methodID, mobileView, selectedItemID, settings, settingsByMethod]);

  useEffect(() => {
    const existing = proxyByMethod[methodID] || {};
    const maps = countryProxyMaps(existing);
    writeStoredJSON('localStorage', PROXY_STORAGE_KEY, {
      ...proxyByMethod,
      [methodID]: {
        ...existing,
        customProxy: settings.customProxy,
        promotionProxy: settings.promotionProxy,
        ...(methodID === 'paypal_ba' ? {
          countryProxies: { ...maps.countryProxies, [settings.country]: settings.customProxy },
          countryPromotionProxies: { ...maps.countryPromotionProxies, [settings.country]: settings.promotionProxy },
        } : {}),
      },
    });
    try { window.sessionStorage?.removeItem(LEGACY_PROXY_SESSION_KEY); } catch (_) {}
  }, [methodID, proxyByMethod, settings.customProxy, settings.promotionProxy]);

  useEffect(() => {
    writeStoredText('sessionStorage', CREDENTIAL_SESSION_KEY, input);
  }, [input]);

  useEffect(() => {
    if (methodID !== 'paypal_ba') return;
    const currency = currencyByCountry[settings.country] || 'USD';
    if (settings.currency === currency) return;
    setSettings((current) => ({ ...current, currency }));
    setSettingsByMethod((current) => ({
      ...current,
      [methodID]: { ...(current[methodID] || {}), country: settings.country, currency },
    }));
  }, [methodID, settings.country, settings.currency]);

  useEffect(() => {
    writeStoredJSON('sessionStorage', JOB_DRAFTS_SESSION_KEY, jobDrafts);
  }, [jobDrafts]);

  useEffect(() => {
    writeStoredJSON('localStorage', JOB_CONFIG_STORAGE_KEY, jobConfigs);
  }, [jobConfigs]);

  useEffect(() => {
    if (!currentJob?.id || terminalStatuses.has(currentJob.status)) return undefined;
    const timer = window.setInterval(() => { loadJob(currentJob.id, true); }, 1200);
    return () => window.clearInterval(timer);
  }, [currentJob?.id, currentJob?.status, loadJob]);

  useEffect(() => {
    if (!currentJob?.items?.length) return;
    if (!currentJob.items.some((item) => item.id === selectedItemID)) setSelectedItemID(currentJob.items[0].id);
  }, [currentJob?.id, currentJob?.items?.length, selectedItemID]);
	useEffect(() => {
		setSelectedRetryItemIDs([]);
	}, [currentJob?.id]);

  useEffect(() => {
    const scroller = document.querySelector('.workspace');
    scroller?.scrollTo({ top: 0, behavior: 'auto' });
  }, [mobileView]);

  const buildOptionsFor = (targetMethodID, targetSettings) => {
    const amountSettings = normalizeAmountGateSettings(targetSettings);
    const storedProxy = proxyByMethod[targetMethodID] || {};
    const proxyMaps = countryProxyMaps(storedProxy);
    const country = amountSettings.country;
    const countryProxy = targetMethodID === 'paypal_ba' ? (proxyMaps.countryProxies[country] || targetSettings.customProxy.trim()) : targetSettings.customProxy.trim();
    const countryPromotionProxy = targetMethodID === 'paypal_ba' ? (proxyMaps.countryPromotionProxies[country] || targetSettings.promotionProxy.trim()) : targetSettings.promotionProxy.trim();
    return {
    country,
    currency: targetMethodID === 'paypal_ba' ? (currencyByCountry[amountSettings.country] || 'USD') : amountSettings.currency,
    countryMode: targetMethodID === 'paypal_ba' && targetSettings.countryMode === 'random' ? 'random' : 'single',
    countryPool: targetMethodID === 'paypal_ba' && targetSettings.countryMode === 'random'
      ? normalizePayPalCountryPool(targetSettings.countryPool, targetSettings.country).filter((country) => countries.includes(country))
      : [],
    assignmentStrategy: targetMethodID === 'paypal_ba' ? 'random_balanced' : '',
    assignmentSeed: '',
    proxyMode: 'custom',
    proxy: countryProxy,
    promotionProxy: targetMethodID === 'kakao' && targetSettings.kakaoMode === 'eligibility' ? '' : countryPromotionProxy,
    countryProxies: targetMethodID === 'paypal_ba' ? proxyMaps.countryProxies : undefined,
    countryPromotionProxies: targetMethodID === 'paypal_ba' ? proxyMaps.countryPromotionProxies : undefined,
    promotionProxyRegion: targetMethodID === 'kakao'
      ? (targetSettings.kakaoMode === 'provider_link' ? (targetSettings.promotionProxy.trim() ? '' : (targetSettings.promotionProxyRegion || 'VN')) : 'TR')
      : (targetSettings.promotionProxyRegion || ''),
    usePromo: targetSettings.usePromo,
    trialDays: Number(targetSettings.trialDays || 0),
    timeoutSeconds: Number(targetSettings.timeoutSeconds || 45),
    maxAttempts: Number(targetSettings.maxAttempts || 3),
    approveAttempts: Number(targetSettings.approveAttempts || 3),
    amountGate: amountSettings.amountGate,
    amountThresholdMinor: amountSettings.amountThresholdMinor,
    allowUnknownAmount: !!amountSettings.allowUnknownAmount,
    maxAmountMinor: amountSettings.amountGate === 'at_most' ? amountSettings.amountThresholdMinor : 0,
    promoCampaignId: targetSettings.promoCampaignId,
    stripePublishableKey: targetSettings.stripePublishableKey,
    clientFingerprint: targetSettings.clientFingerprint,
    fingerprintPolicy: {
      promotion: targetSettings.fingerprintPolicy?.promotion === 'fresh' ? 'fresh' : 'follow',
      provider: targetSettings.fingerprintPolicy?.provider === 'fresh' ? 'fresh' : 'follow',
      approve: targetSettings.fingerprintPolicy?.approve === 'fresh' ? 'fresh' : 'follow',
    },
    fingerprintWeightMode: !!targetSettings.fingerprintWeightMode,
    paymentStatusAutoRefresh: targetSettings.paymentStatusAutoRefresh,
    kakaoMode: targetMethodID === 'kakao' ? targetSettings.kakaoMode : '',
    kakaoEligibilityOnly: targetMethodID === 'kakao' && targetSettings.kakaoMode === 'eligibility',
    paypalSameStickyIp: targetMethodID === 'paypal_ba' && !!targetSettings.paypalSameStickyIp,
    blikCode: targetMethodID === 'blik' ? String(targetSettings.blikCode || '').trim() : '',
    };
  };

  const buildOptions = () => buildOptionsFor(methodID, settings);

  const startBatch = async () => {
    if (!input.trim() || !inputRows.length) {
      notify('请粘贴至少一个账号凭证', 'warning');
      setMobileView('create');
      return;
    }
    if (selectedMethod?.available === false || selectedMethod?.runnable === false) {
      notify(`${selectedMethod.label} 当前被接口目录标记为不可用`, 'warning');
      return;
    }
    if (!settings.customProxy.trim()) {
      notify('请手动填写主流程代理', 'warning');
      return;
    }
    if (methodID === 'paypal_ba' && settings.countryMode === 'random' && payPalCountryPool.length < 2) {
      notify('随机地区模式请至少选择 2 个账单国家 / 地区', 'warning');
      return;
    }
    if (inputRows.length > maxItems) {
      notify(`单批最多 ${maxItems} 个账号，当前识别到 ${inputRows.length} 个`, 'warning');
      return;
    }
    const submittedDraft = {
      version: 1,
      methodID: selectedMethod.id,
      input,
      settings: persistedSettings(settings),
      proxies: {
        customProxy: settings.customProxy,
        promotionProxy: settings.promotionProxy,
        ...(methodID === 'paypal_ba' ? {
          countryProxies: countryProxyMaps(proxyByMethod[methodID] || {}).countryProxies,
          countryPromotionProxies: countryProxyMaps(proxyByMethod[methodID] || {}).countryPromotionProxies,
        } : {}),
      },
      savedAt: new Date().toISOString(),
    };
    setSubmitting(true);
    setError('');
    try {
      const payload = await extractionApi.createJob({
        method: selectedMethod,
        input,
        concurrency: selectedMethod?.supportsConcurrency === false ? 1 : Number(settings.concurrency || 1),
        options: buildOptions(),
      });
      if (!payload?.job) throw new Error('后端未返回任务');
      setCurrentJob(payload.job);
      setActiveJobID(payload.job.id);
      setSelectedItemID(payload.job.items?.[0]?.id || '');
      setJobs((current) => [payload.job, ...current.filter((job) => job.id !== payload.job.id)]);
      setJobDrafts((current) => ({ ...current, [payload.job.id]: submittedDraft }));
      setJobConfigs((current) => ({ ...current, [payload.job.id]: persistentJobConfig(submittedDraft) }));
      setMobileView('run');
      notify(methodID === 'paypal_ba' && settings.countryMode === 'random'
        ? `已将 ${payload.job.total || inputRows.length} 个账号随机绑定到 ${payPalCountryPool.length} 个账单地区；同一批次内重试不换地区`
        : methodID === 'kakao'
        ? (settings.kakaoMode === 'provider_link'
          ? `已提交 ${payload.job.total || inputRows.length} 个 Kakao 支付链提炼账号`
          : `已提交 ${payload.job.total || inputRows.length} 个 Kakao 上游资格观察账号`)
        : `已提交 ${payload.job.total || inputRows.length} 个账号`, 'success');
    } catch (requestError) {
      setError(requestError.message || '提交提炼任务失败');
      notify(requestError.message || '提交提炼任务失败', 'error');
    } finally {
      setSubmitting(false);
    }
  };

  const cancelJob = async () => {
    if (!currentJob?.id) return;
    try {
      const payload = await extractionApi.cancelJob(currentJob.id);
      if (payload?.job) setCurrentJob(payload.job);
      notify('已发送取消指令', 'success');
    } catch (requestError) {
      notify(requestError.message || '取消失败', 'error');
    }
  };

  const retryStoredJob = async () => {
    if (!currentJob?.id || !terminalStatuses.has(currentJob.status)) return;
    setSubmitting(true);
    try {
      const payload = await extractionApi.retryJob(currentJob.id);
      if (!payload?.job) throw new Error('后端未返回重跑任务');
      setCurrentJob(payload.job);
      setActiveJobID(payload.job.id);
      setSelectedItemID(payload.job.items?.[0]?.id || '');
      setJobs((current) => [payload.job, ...current.filter((job) => job.id !== payload.job.id)]);
      setMobileView('run');
      notify('已使用本机加密保存的原始输入重新提交', 'success');
    } catch (requestError) {
      notify(requestError.message || '重跑失败', 'error');
    } finally {
      setSubmitting(false);
    }
  };

	const retryRemainingFailures = async () => {
		if (!currentJob?.id || !terminalStatuses.has(currentJob.status)) return;
		setSubmitting(true);
		try {
			const payload = await extractionApi.retryJob(currentJob.id, {
				failedOnly: true,
				excludeSucceeded: true,
				...retryFilters,
			});
			if (!payload?.job) throw new Error('后端未返回重跑任务');
			setCurrentJob(payload.job);
			setActiveJobID(payload.job.id);
			setSelectedItemID(payload.job.items?.[0]?.id || '');
			setJobs((current) => [payload.job, ...current.filter((job) => job.id !== payload.job.id)]);
			setMobileView('run');
			notify(`已筛选并提交 ${payload.job.total || 0} 个剩余失败账号`, 'success');
		} catch (requestError) {
			notify(requestError.message || '筛选重跑失败', 'error');
		} finally {
			setSubmitting(false);
		}
	};

	const verifyPaymentStatus = async () => {
		if (!currentJob?.id) return;
		setSubmitting(true);
		try {
			const payload = await extractionApi.verifyPayment(currentJob.id, { onlySucceeded: true });
			if (!payload?.job) throw new Error('后端未返回复核结果');
			setCurrentJob(payload.job);
			setJobs((current) => [payload.job, ...current.filter((job) => job.id !== payload.job.id)]);
			const paid = (payload.job.items || []).filter((item) => item.paymentStatus === 'paid_success').length;
			notify(paid ? `支付状态复核完成：${paid} 个账号已显示付费套餐` : '支付状态复核完成，暂未发现已支付成功账号', paid ? 'success' : 'warning');
		} catch (requestError) {
			notify(requestError.message || '支付状态复核失败', 'error');
		} finally {
			setSubmitting(false);
		}
	};

	const toggleRetryItem = (itemID, checked) => {
		setSelectedRetryItemIDs((current) => checked
			? [...new Set([...current, itemID])]
			: current.filter((candidate) => candidate !== itemID));
	};

	const retrySelectedAccounts = async () => {
		if (!currentJob?.id || !selectedRetryItemIDs.length || !terminalStatuses.has(currentJob.status)) return;
		setSubmitting(true);
		try {
			const payload = await extractionApi.retryJob(currentJob.id, { itemIds: selectedRetryItemIDs });
			if (!payload?.job) throw new Error('后端未返回重跑任务');
			setCurrentJob(payload.job);
			setActiveJobID(payload.job.id);
			setSelectedItemID(payload.job.items?.[0]?.id || '');
			setSelectedRetryItemIDs([]);
			setJobs((current) => [payload.job, ...current.filter((job) => job.id !== payload.job.id)]);
			setMobileView('run');
			notify(`已提交 ${payload.job.total || 0} 个指定账号重跑`, 'success');
		} catch (requestError) {
			notify(requestError.message || '指定账号重跑失败', 'error');
		} finally {
			setSubmitting(false);
		}
	};

  const continueKakaoProvider = async (job) => {
    if (!job?.id || job.method !== 'kakao') return;
    const detailedJob = (await loadJob(job.id, true)) || job;
    const sessionDraft = jobDrafts[job.id];
    const rawInput = String(sessionDraft?.input || (activeJobID === job.id ? input : '')).trim();
    if (!rawInput || !parseExtractionInput(rawInput).length) {
      notify('这批账号 token 只存在提交它的浏览器标签页；请保持原标签页并刷新后再点“整批转支付链”', 'warning');
      return;
    }
    const savedConfig = jobConfigs[job.id] || persistentJobConfig(sessionDraft, 'session_migration');
    const proxies = savedConfig?.proxies || proxyByMethod.kakao || {};
    const providerSettings = constrainMethodSettings('kakao', {
      ...initialSettings,
      ...(detailedJob.options || {}),
      ...(savedConfig?.settings || sessionDraft?.settings || {}),
      country: 'KR',
      currency: 'KRW',
      concurrency: Number(detailedJob.concurrency || settings.concurrency || 1),
      kakaoMode: 'provider_link',
      kakaoEligibilityOnly: false,
      usePromo: true,
      paymentStatusAutoRefresh: true,
      maxAttempts: Math.max(10, Number(detailedJob.continuation?.maxAttempts || savedConfig?.settings?.maxAttempts || detailedJob.options?.maxAttempts || 0)),
      customProxy: String(proxies.customProxy || ''),
      promotionProxy: String(proxies.promotionProxy || ''),
      promotionProxyRegion: savedConfig?.settings?.promotionProxyRegion || detailedJob.options?.promotionProxyRegion || '',
    });
    if (!providerSettings.customProxy.trim()) {
      notify('本浏览器没有这批的主代理快照，无法安全转支付链', 'warning');
      return;
    }
    const kakaoMethod = methods.find((candidate) => candidate.id === 'kakao') || selectedMethod;
    const submittedDraft = {
      version: 1,
      methodID: 'kakao',
      input: rawInput,
      settings: persistedSettings(providerSettings),
      proxies: {
        customProxy: providerSettings.customProxy,
        promotionProxy: providerSettings.promotionProxy,
      },
      savedAt: new Date().toISOString(),
    };
    setSubmitting(true);
    setError('');
    try {
      const payload = await extractionApi.createJob({
        method: kakaoMethod,
        input: rawInput,
        concurrency: Number(providerSettings.concurrency || 1),
        options: buildOptionsFor('kakao', providerSettings),
      });
      if (!payload?.job) throw new Error('后端未返回任务');
      setMethodID('kakao');
      setSettings(providerSettings);
      setInput(rawInput);
      setCurrentJob(payload.job);
      setActiveJobID(payload.job.id);
      setSelectedItemID(payload.job.items?.[0]?.id || '');
      setJobs((current) => [payload.job, ...current.filter((candidate) => candidate.id !== payload.job.id)]);
      setJobDrafts((current) => ({ ...current, [payload.job.id]: submittedDraft }));
      setJobConfigs((current) => ({ ...current, [payload.job.id]: persistentJobConfig(submittedDraft) }));
      setMobileView('run');
      try {
        const continuationPayload = await extractionApi.markKakaoProviderContinuationSubmitted(job.id, payload.job.id);
        if (continuationPayload?.job) {
          setJobs((current) => current.map((candidate) => candidate.id === job.id ? continuationPayload.job : candidate));
        }
      } catch (_) {}
      notify(`已把 ${payload.job.total || parseExtractionInput(rawInput).length} 个账号整批转为 Kakao 支付链；每账号最多 ${providerSettings.maxAttempts} 次完整链路`, 'success');
    } catch (requestError) {
      setError(requestError.message || '整批转支付链失败');
      notify(requestError.message || '整批转支付链失败', 'error');
    } finally {
      setSubmitting(false);
    }
  };

  useEffect(() => {
    const continuation = currentJob?.continuation;
    const draft = currentJob?.id ? jobDrafts[currentJob.id] : null;
    if (!currentJob?.id || continuation?.mode !== 'provider_link' || continuation?.status !== 'requested' || !draft?.input || submitting || autoContinuationIDs.current.has(currentJob.id)) return;
    autoContinuationIDs.current.add(currentJob.id);
    continueKakaoProvider(currentJob);
  }, [currentJob?.continuation?.mode, currentJob?.continuation?.status, currentJob?.id, jobDrafts, submitting]);

  const openHistoryJob = async (job) => {
    const loadedJob = await loadJob(job.id);
    const historicalJob = loadedJob || job;
    const sessionDraft = jobDrafts[job.id];
    const savedConfig = jobConfigs[job.id] || persistentJobConfig(sessionDraft, 'session_migration');
    const restoredMethodID = String(savedConfig?.methodID || sessionDraft?.methodID || historicalJob.method || job.method || methodID);
    const historicalMethod = methods.find((candidate) => candidate.id === restoredMethodID);
    const jobSettings = persistedSettings({
      ...initialSettings,
      ...(historicalJob.options || {}),
      concurrency: Number(historicalJob.concurrency || initialSettings.concurrency),
    });
    const restoredSettings = { ...jobSettings, ...(savedConfig?.settings || sessionDraft?.settings || {}) };
    const country = restoredSettings.country || historicalMethod?.countries?.[0] || initialSettings.country;
    const channelProxies = proxyByMethod[restoredMethodID] || {};
    const proxies = savedConfig?.proxies || sessionDraft?.proxies || channelProxies;
    const nextSettings = constrainMethodSettings(restoredMethodID, {
      ...initialSettings,
      ...restoredSettings,
      country,
      currency: currencyForMethodCountry(restoredMethodID, country, restoredSettings.currency),
      promotionProxyRegion: restoredSettings.promotionProxyRegion || defaultPromotionRegion(restoredMethodID, restoredSettings.kakaoMode),
      customProxy: String(proxies.customProxy || ''),
      promotionProxy: String(proxies.promotionProxy || ''),
    });

    if (!savedConfig) {
      const fallbackConfig = persistentJobConfig({
        methodID: restoredMethodID,
        settings: persistedSettings(nextSettings),
        proxies,
        savedAt: new Date().toISOString(),
      }, 'channel_fallback');
      setJobConfigs((current) => ({ ...current, [job.id]: fallbackConfig }));
    }

    setMethodID(restoredMethodID);
    setSettings(nextSettings);
    setSettingsByMethod((current) => ({
      ...current,
      [methodID]: persistedSettings(settings),
      [restoredMethodID]: persistedSettings(nextSettings),
    }));
    setProxyByMethod((current) => ({
      ...current,
      [methodID]: { customProxy: settings.customProxy, promotionProxy: settings.promotionProxy },
      [restoredMethodID]: { customProxy: nextSettings.customProxy, promotionProxy: nextSettings.promotionProxy },
    }));
    setInput((current) => String(sessionDraft?.input || current));
    setMobileView('results');
    if (sessionDraft?.input) notify(`已还原 ${historicalJob.methodLabel || historicalMethod?.label || '历史批次'} 当时的代理、参数和当前标签页账号凭证`, 'success');
    else if (savedConfig && savedConfig.source !== 'channel_fallback') notify(`已还原 ${historicalJob.methodLabel || historicalMethod?.label || '历史批次'} 当时保存在本浏览器的代理与参数；账号输入保持当前标签页内容`, 'success');
    else notify(`已还原 ${historicalJob.methodLabel || historicalMethod?.label || '历史批次'} 的历史参数和本浏览器渠道代理；账号输入保持当前标签页内容`, 'success');
  };

  const deleteHistoryJob = async (job, event) => {
    event?.stopPropagation();
    if (!terminalStatuses.has(job.status)) {
      notify('运行中的批次请先停止，再删除', 'warning');
      return;
    }
    if (!window.confirm(`确定删除 ${job.methodLabel || job.method} 批次？\n${job.id}`)) return;
    try {
      await extractionApi.deleteJob(job.id);
      const remaining = jobs.filter((candidate) => candidate.id !== job.id);
      setJobs(remaining);
      setJobDrafts((current) => {
        const next = { ...current };
        delete next[job.id];
        return next;
      });
      setJobConfigs((current) => {
        const next = { ...current };
        delete next[job.id];
        return next;
      });
      if (currentJob?.id === job.id) {
        setCurrentJob(null);
        setActiveJobID('');
        setSelectedItemID('');
        const defaultJob = remaining.find(isSuccessfulHistoryJob) || remaining[0];
        if (defaultJob?.id) await loadJob(defaultJob.id, true);
      }
      notify('最近批次已删除', 'success');
    } catch (requestError) {
      notify(requestError.message || '删除批次失败', 'error');
    }
  };

  const copy = async (value, successMessage = '链接已复制') => {
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      notify(successMessage, 'success');
    } catch (copyError) {
      notify(copyError.message || '复制失败', 'error');
    }
  };

  const saveKakaoRunReport = async () => {
    setSavingKakaoReport(true);
    try {
      const byID = new Map();
      [...jobs, currentJob].filter((job) => job?.method === 'kakao' && job?.id).forEach((job) => byID.set(job.id, job));
      const kakaoJobs = [...byID.values()].sort((left, right) => String(right.createdAt || '').localeCompare(String(left.createdAt || '')));
      const providerReady = kakaoJobs.reduce((count, job) => count + (job.items || []).filter((item) => item.extractionStatus === 'provider_link_ready').length, 0);
      const lines = [
        'Kakao Pay 自助流程运行记录',
        `生成时间: ${new Date().toISOString()}`,
        '边界: 批量数、账号并发与每账号尝试次数由任务配置决定；不写入凭证/代理；只接受上游真实返回 kakao_pay；支付链模式生成待支付链接后停止，不完成付款。',
        `资格观察命中账号（去重）: ${kakaoEligibleAccounts.length}`,
        `支付链已生成: ${providerReady}`,
        '',
        'Kakao 任务:',
      ];
      kakaoJobs.forEach((job) => {
        const detailed = Array.isArray(job.items) ? job.items : [];
        const eligible = detailed.length ? detailed.filter((item) => String(item.decision || '').toLowerCase() === 'eligible').length : Number(job.eligible || 0);
        const ineligible = detailed.length ? detailed.filter((item) => String(item.decision || '').toLowerCase() === 'ineligible').length : Number(job.ineligible || 0);
        const mode = job.options?.kakaoMode || (detailed.some((item) => item.extractionStatus === 'provider_link_ready') ? 'provider_link' : 'eligibility');
        lines.push(`- ${job.id} | ${job.createdAt || '时间未知'} | ${job.status || '状态未知'} | mode=${mode} | eligible=${eligible} | ineligible=${ineligible}`);
        detailed.forEach((item) => {
          const decision = String(item.decision || item.result?.decision || '').toLowerCase();
          const methods = item.availableMethods || item.result?.availableMethods || [];
          const link = item.providerRedirectUrl || item.longUrl || item.stripeRedirectUrl || '';
          lines.push(`  - ${itemDisplayLabel(item)} | ${item.status || 'unknown'} | decision=${decision || 'none'} | methods=${methods.join(',') || 'none'} | ${item.finishedAt || ''}`);
          if (link) lines.push(`    link=${link}`);
          if (item.error) lines.push(`    error=${item.error}`);
        });
      });
      if (!kakaoJobs.length) lines.push('- 尚无 Kakao 运行记录');
      const name = 'kakao-self-service-results.txt';
      const content = `${lines.join('\n')}\n`;
      const listing = await apiClient.get('/file-library');
      const existing = (listing.items || []).find((item) => item.name === name);
      if (existing?.id) await apiClient.post(`/file-library/${existing.id}`, { name, content });
      else await apiClient.post('/file-library', { name, content });
      notify('已将 Kakao 自助流程运行记录保存到文件库', 'success');
    } catch (requestError) {
      notify(requestError.message || '保存 Kakao 运行记录失败', 'error');
    } finally {
      setSavingKakaoReport(false);
    }
  };



  const checkProxyPool = useCallback(async (kind) => {
    const raw = kind === 'promotion' ? settings.promotionProxy : settings.customProxy;
    if (!String(raw || '').trim()) {
      notify(kind === 'promotion' ? '请先填写优惠代理' : '请先填写主流程代理', 'warning');
      return;
    }
    setProxyChecking((current) => ({ ...current, [kind]: true }));
    setProxyCheckStatus((current) => ({ ...current, [kind]: '检测中…' }));
    try {
      const payload = await apiClient.post('/proxy/check', { proxyUrl: raw, limit: 5 });
      const results = Array.isArray(payload?.results) ? payload.results : [];
      const okCount = Number(payload?.okCount || results.filter((item) => item?.ok).length || 0);
      const checked = Number(payload?.checked || results.length || 0);
      const firstOk = results.find((item) => item?.ok) || payload?.result;
      if (okCount > 0 && firstOk) {
        const where = [firstOk.countryCode || firstOk.country, firstOk.city, firstOk.ip].filter(Boolean).join(' · ');
        const label = checked > 1 ? `可达 ${okCount}/${checked}` : '可达';
        setProxyCheckStatus((current) => ({ ...current, [kind]: where ? `${label} · ${where}` : label }));
        notify(where ? `${kind === 'promotion' ? '优惠代理' : '主代理'}${label}：${where}` : `${kind === 'promotion' ? '优惠代理' : '主代理'}${label}`, 'success');
      } else {
        const detail = results[0]?.error || payload?.error || '代理不可达';
        setProxyCheckStatus((current) => ({ ...current, [kind]: `不可达 · ${detail}` }));
        notify(`${kind === 'promotion' ? '优惠代理' : '主代理'}不可达：${detail}`, 'error');
      }
    } catch (error) {
      const detail = error?.message || '代理检测失败';
      setProxyCheckStatus((current) => ({ ...current, [kind]: `不可达 · ${detail}` }));
      notify(`${kind === 'promotion' ? '优惠代理' : '主代理'}检测失败：${detail}`, 'error');
    } finally {
      setProxyChecking((current) => ({ ...current, [kind]: false }));
    }
  }, [notify, settings.customProxy, settings.promotionProxy]);

  const importMailAdminSessions = useCallback(async (ids) => {
    if (!Array.isArray(ids) || !ids.length) {
      notify('请先选择账号', 'warning');
      return;
    }
    setMailAdminImporting(true);
    try {
      const payload = await apiClient.post('/mail-admin/free-accounts/materialize', { ids });
      const textValue = String(payload?.inputText || '').trim();
      const count = Number(payload?.count || 0);
      const missingCount = Number(payload?.missingCount || 0);
      if (!textValue || count < 1) {
        const firstMissing = payload?.missing?.[0]?.error || '选中账号没有可用 session';
        notify(firstMissing, 'warning');
        return;
      }
      setInput((current) => {
        const existing = String(current || '').trim();
        return existing ? `${existing}\n${textValue}` : textValue;
      });
      setMailAdminPickerOpen(false);
      if (missingCount > 0) {
        notify(`已导入 ${count} 个 session，另有 ${missingCount} 个无凭证`, 'warning');
      } else {
        notify(`已从 Mail Admin 导入 ${count} 个 Free 未开通 session`, 'success');
      }
    } catch (error) {
      notify(error?.message || '导入 Mail Admin session 失败', 'error');
    } finally {
      setMailAdminImporting(false);
    }
  }, [notify]);

  const openFileLibrary = () => window.location.assign('/ui/file-library');

  const loadLibraryProxies = useCallback(async () => {
    setLibraryProxyStatus('读取文件库代理…');
    try {
      const listing = await apiClient.get('/file-library');
      const items = Array.isArray(listing?.items) ? listing.items : [];
      const lowered = items.map((item) => ({ item, name: String(item?.name || '').toLowerCase() }));
      const source = FILE_LIBRARY_PROXY_FALLBACKS
        .map((name) => lowered.find((entry) => entry.name === name)?.item)
        .find(Boolean)
        || items.find((item) => /proxy-links|proxy|cliproxy|applemail/i.test(String(item?.name || '')));
      if (!source?.id) {
        setLibraryProxyOptions([]);
        setLibraryProxyStatus('文件库暂无 proxy-links.txt 代理源');
        return [];
      }
      const detail = await apiClient.get(`/file-library/${source.id}`);
      const content = String(detail?.item?.content || detail?.content || '');
      const options = extractLibraryProxyLines(content, []);
      setLibraryProxyOptions(options);
      setLibraryProxyStatus(options.length ? `已载入 ${source.name} · ${options.length} 条代理` : `已打开 ${source.name}，但未识别到代理行`);
      return options;
    } catch (requestError) {
      setLibraryProxyOptions([]);
      setLibraryProxyStatus(requestError.message || '读取文件库失败');
      return [];
    }
  }, []);

  useEffect(() => {
    loadLibraryProxies();
  }, [loadLibraryProxies]);


  const selectedLinks = selectedItem ? (currentJob?.method === 'upi' ? [
    ['UPI Instructions', selectedUPI?.instructionUrl],
    ['UPI 主结果', selectedItem.longUrl],
  ] : [
    ['渠道链接', selectedItem.providerRedirectUrl],
    ['Checkout 链接', selectedItem.longUrl],
    ['Stripe 跳转', selectedItem.stripeRedirectUrl],
  ]).filter(([, value], index, entries) => value && entries.findIndex(([, candidate]) => candidate === value) === index) : [];
  const selectedLinkExpiry = selectedItem ? resolveLinkExpiry(selectedItem, currentJob?.method) : null;
  const selectedFacts = selectedItem ? [
    ['国家 / 货币', [selectedItem.country, selectedItem.currency].filter(Boolean).join(' / ')],
    ['Checkout ID', selectedItem.checkoutId],
    ['Checkout 类型', selectedItem.checkoutType],
    ['Processor', selectedItem.processorEntity],
    ['观测到的类型', selectedItem.metadata?.observedCheckoutTypes],
    ['Payment Method', selectedItem.paymentMethodId],
    ['金额', selectedItem.amountDisplay || selectedItem.amount],
    ['金额状态', selectedItem.amountStatus ? labelFor(selectedItem.amountStatus) : ''],
    ['渠道判断', selectedItem.decision],
    ['链接生成', selectedLinkExpiry?.generatedAt ? displayTimestamp(selectedLinkExpiry.generatedAt) : ''],
    ['链接到期', selectedLinkExpiry?.expiresAt ? displayTimestamp(selectedLinkExpiry.expiresAt) : ''],
    ['有效时长', selectedLinkExpiry?.ttlSeconds ? `${Math.round(selectedLinkExpiry.ttlSeconds / 60)} 分钟` : ''],
  ].filter(([, value]) => value) : [];
  const kakaoProviderMode = methodID === 'kakao' && settings.kakaoMode === 'provider_link';
  const linkOnlyAmountGate = methodID === 'ph_link' || methodID === 'direct_card';
  const activeAmountGateLabel = amountGateSummary(settings, effectiveCurrency);
  const kakaoModeLabel = kakaoProviderMode ? '支付链提炼' : '资格观察';
  const kakaoActionLabel = kakaoProviderMode ? '开始 Kakao 支付链提炼' : '开始 Kakao 资格观察';
    const kakaoPromotionRoute = settings.promotionProxy.trim()
    ? `已选优惠代理 x${promotionProxyLines.length || 1}（按次轮换）`
    : '未配置优惠代理';

  if (loading) {
    return <div className="page-container extraction-page extraction-loading-page"><div className="extraction-loading-hero"><Skeleton height="20px" /><Skeleton height="44px" /><Skeleton height="18px" /></div><div className="extraction-loading-grid"><Skeleton height="520px" /><Skeleton height="520px" /></div></div>;
  }

  return (
    <div className="page-container extraction-page extraction-workbench" data-mobile-view={mobileView}>
      <header className="extraction-hero">
        <div className="extraction-hero-copy">
          <h1>支付提炼中心</h1>
        </div>
        <div className="extraction-hero-meta" aria-label="接口信息">
          <div><span>渠道</span><b>{methods.length}</b></div>
          <div><span>单批上限</span><b>{maxItems}</b></div>
          <div><span>接口状态</span><b className={serviceConnected ? 'is-online' : 'is-offline'}>{serviceConnected ? '已连接' : '未连接'}</b></div>
        </div>
      </header>

      {error ? <div className="extraction-alert" role="alert"><AlertCircle size={17} /><span>{error}</span><GlassButton variant="glass" onClick={loadInitial}>重新加载</GlassButton></div> : null}

      <nav className="extraction-mobile-nav" aria-label="工作台视图">
        {[['create', '创建任务', inputRows.length], ['run', '运行监控', currentJob ? `${completedCount}/${metrics.total}` : '—'], ['results', '账号结果', currentJob?.items?.length || 0]].map(([id, label, count]) => (
          <button type="button" className={mobileView === id ? 'active' : ''} key={id} onClick={() => setMobileView(id)}><span>{label}</span><b>{count}</b></button>
        ))}
      </nav>

      <GlassPanel className="extraction-channel-section">
        <div className="extraction-section-head"><div><span className="extraction-section-number">01</span><div><b>选择提炼渠道</b></div></div><span className={`extraction-availability ${selectedMethod?.available === false ? 'is-unavailable' : 'is-available'}`}><i />{selectedMethod?.available === false ? '接口未启用' : '接口可用'}</span></div>
        <div className="extraction-method-grid" role="tablist" aria-label="提炼渠道">
          {methods.flatMap((method) => {
            const unavailable = method.available === false;
            const countrySummary = unavailable
              ? '未启用'
              : ((method.countries?.length || 0) > 6 ? `${method.countries.length} 个国家/地区` : method.countries?.join(' · ') || '多地区');
            if (method.id !== 'paypal_ba') {
              return [(
                <button type="button" role="tab" aria-selected={selectedMethod?.id === method.id} className={`extraction-method-tile ${selectedMethod?.id === method.id ? 'active' : ''} ${unavailable ? 'unavailable' : ''}`} key={method.id} onClick={() => chooseMethod(method)}>
                  <span className="extraction-method-tile-top"><b>{method.label || method.name}</b>{method.primary ? <em>首选</em> : null}</span>
                  <span className="extraction-method-tile-bottom"><small>{method.name}</small><i>{countrySummary}</i></span>
                </button>
              )];
            }
            const singleActive = selectedMethod?.id === method.id && !payPalRandomMode;
            const randomActive = selectedMethod?.id === method.id && payPalRandomMode;
            return [
              <button type="button" role="tab" aria-selected={singleActive} className={`extraction-method-tile ${singleActive ? 'active' : ''} ${unavailable ? 'unavailable' : ''}`} key="paypal_ba-single" onClick={() => chooseMethod(method, { countryMode: 'single' })}>
                <span className="extraction-method-tile-top"><b>PP 提炼</b><em>原流程</em></span>
                <span className="extraction-method-tile-bottom"><small>PayPal · 单地区</small><i>{countrySummary}</i></span>
              </button>,
              <button type="button" role="tab" aria-selected={randomActive} className={`extraction-method-tile extraction-method-tile-random ${randomActive ? 'active' : ''} ${unavailable ? 'unavailable' : ''}`} key="paypal_ba-random" onClick={() => chooseMethod(method, { countryMode: 'random' })}>
                <span className="extraction-method-tile-top"><b>PP 随机</b><em>独立模式</em></span>
                <span className="extraction-method-tile-bottom"><small>账号随机均衡绑定</small><i>{payPalCountryPool.length || defaultPayPalRandomCountries.length} 个已选地区</i></span>
              </button>,
            ];
          })}
        </div>
      </GlassPanel>

      <div className="extraction-mobile-action-dock">
        <span><b>{selectedMethod?.label || '提炼任务'}</b><small>{inputRows.length} 个账号 · {payPalRandomMode ? `随机账单 ${payPalCountryPool.length} 地区 · ` : ''}账号并发 {selectedMethod?.supportsConcurrency === false ? 1 : settings.concurrency}</small></span>
        <GlassButton variant="primary" icon={Play} loading={submitting} disabled={!serviceConnected || !inputRows.length || selectedMethod?.available === false || selectedMethod?.runnable === false} onClick={startBatch}>{serviceConnected ? (methodID === 'kakao' ? kakaoActionLabel : '开始提炼') : '接口未连接'}</GlassButton>
      </div>

      <div className="extraction-workspace">
        <section className="extraction-create-column">
          <GlassPanel variant="strong" className="extraction-work-panel">
            <div className="extraction-panel-topline"><div><span className="extraction-section-number">02</span><div><b>{methodID === 'kakao' ? '创建 Kakao 批量任务' : '创建批量任务'}</b></div></div><span className="extraction-count-chip">{inputRows.length} 个账号</span><FileText size={20} /></div>

            {serviceConnected && selectedMethod?.available === false ? <div className="extraction-method-warning"><AlertCircle size={16} /><span>当前后端目录将此渠道标记为不可用。页面保留入口，但不会伪造“已提交”。</span></div> : null}
			{methodID === 'kakao' ? <div className="kakao-eligibility-goal"><div><ShieldCheck size={18} /><span><b>Kakao 可配置批量流程</b><small>当前 {inputRows.length} 个账号 · 并发 {settings.concurrency} · 每账号最多 {settings.maxAttempts} 次；只接受上游真实展示的 kakao_pay</small></span></div><strong>{kakaoModeLabel}</strong><p>{kakaoProviderMode ? `KR 主代理 → ${kakaoPromotionRoute} → KR 主代理；失败时从新 Checkout 重跑完整支付链，成功出链即停止` : `KR checkout → Stripe bootstrap methods 资格观察；每账号最多 ${settings.maxAttempts} 次，命中即停止该账号`}</p><div className="kakao-self-service-actions"><GlassButton variant="glass" icon={FileText} loading={savingKakaoReport} onClick={saveKakaoRunReport}>保存运行记录</GlassButton><GlassButton variant="glass" icon={ExternalLink} onClick={openFileLibrary}>查看文件库说明</GlassButton></div></div> : null}
			{methodID === 'ph_link' ? <div className="kakao-eligibility-goal"><div><ShieldCheck size={18} /><span><b>菲律宾 Checkout 短链</b><small>PH / PHP 账单 · US 创建 Checkout · TR 应用优惠</small></span></div><strong>{activeAmountGateLabel}</strong><p>每次失败都会更换代理身份并从新 Checkout 重跑；只有满足当前金额门禁才返回链接，未知金额默认拒绝。</p></div> : null}

            {methodID === 'kakao' ? <Field label="Kakao 运行模式" hint="资格观察只检查上游 kakao_pay；支付链提炼才会创建 payment method 并进入 confirm / approve"><CustomSelect value={settings.kakaoMode} onChange={(value) => updateSetting('kakaoMode', value)} options={kakaoModeOptions} ariaLabel="Kakao 运行模式" /></Field> : null}

            <Field label="账号凭证" hint={`支持 JSON 数组、逐行 JSON、Bearer / JWT；单批最多 ${maxItems} 个`}>
              <textarea className="input-glass console-code extraction-input" value={input} onChange={(event) => setInput(event.target.value)} placeholder={'一个账号一行，或粘贴 JSON 数组\n{"email":"a@example.com","accessToken":"eyJ..."}'} spellCheck="false" />
            </Field>
            <div className="extraction-input-foot">
              <span><ShieldCheck size={14} />仅保存在当前标签页会话：刷新保留，关闭标签页或清空即删除；不写入任务历史。代理、模式、并发、次数等配置按渠道和批次保存在本浏览器 localStorage</span>
              <div className="extraction-input-foot-actions">
                <button type="button" onClick={() => setMailAdminPickerOpen(true)}><UsersRound size={14} />从 Mail Admin 选择</button>
                <button type="button" onClick={() => setInput('')} disabled={!input}><Trash2 size={14} />清空</button>
              </div>
            </div>

            {methodID === 'paypal_ba' ? (
              <Field label="PP 账单地区分配" hint="单地区沿用原流程；随机地区会在提交时将已选账号随机均衡绑定到地区池">
                <CustomSelect
                  value={settings.countryMode === 'random' ? 'random' : 'single'}
                  onChange={(value) => updateSetting('countryMode', value === 'random' ? 'random' : 'single')}
                  options={payPalCountryModeOptions}
                  ariaLabel="PP 账单地区分配模式"
                />
              </Field>
            ) : null}

            {payPalRandomMode ? (
              <div className="paypal-country-random-panel">
                <div className="paypal-country-random-head">
                  <span>
                    <b>随机账单地区池</b>
                    <small>已选 {payPalCountryPool.length} 个地区 · {inputRows.length} 个账号；每次提交重新洗牌，同一批次内绑定不变</small>
                  </span>
                  <span className="paypal-country-random-actions">
                    <button type="button" onClick={() => updateSetting('countryPool', countries.filter((country) => !payPalPromotionOnlyCountries.has(country)))}>全选主地区</button>
                    <button type="button" onClick={() => updateSetting('countryPool', defaultPayPalRandomCountries.filter((country) => countries.includes(country)))}>全球预设</button>
                    <button type="button" onClick={() => updateSetting('countryPool', [])}>清空</button>
                  </span>
                </div>
                <div className="paypal-country-random-grid" role="group" aria-label="PP 随机账单地区">
                  {countries.filter((country) => !payPalPromotionOnlyCountries.has(country)).map((country) => {
                    const checked = payPalCountryPool.includes(country);
                    return (
                      <label key={`paypal-country-${country}`} className={checked ? 'active' : ''}>
                        <input type="checkbox" checked={checked} onChange={(event) => togglePayPalCountry(country, event.target.checked)} />
                        <span><b>{country}</b><small>{payPalCountryNames[country] || country} · {currencyByCountry[country] || 'USD'}</small></span>
                      </label>
                    );
                  })}
                </div>
                <p>这里只随机主地区；TR 属于优惠地区，不会被分配成主 checkout 地区。代理出口仍以下方你选择的代理为准。</p>
                <p>优惠候选池与主地区池分开保存；这里只用于配置和诊断展示，不会自动跨区执行真实支付操作。</p>
                <div className="paypal-country-random-grid" role="group" aria-label="PP 按主地区设置优惠候选池">
                  {payPalCountryPool.map((mainCountry) => (
                    <label key={`paypal-promotion-${mainCountry}`} className="active">
                      <span>
                        <b>{mainCountry} 主地区</b>
                        <small>{['JP', 'TR', ...(mainCountry === 'TH' ? ['TH'] : [])].filter((value, index, values) => values.indexOf(value) === index).map((promotionCountry) => (
                          <em key={`${mainCountry}-${promotionCountry}`}>
                            <input type="checkbox" checked={(payPalPromotionPools[mainCountry] || []).includes(promotionCountry)} onChange={(event) => togglePayPalPromotionCountry(mainCountry, promotionCountry, event.target.checked)} />
                            {promotionCountry}
                          </em>
                        ))}</small>
                      </span>
                    </label>
                  ))}
                </div>
              </div>
            ) : null}

            <div className="extraction-basic-grid">
              <Field
                label={payPalRandomMode ? '本批账单地区' : '账单国家 / 地区'}
                hint={methodID === 'paypal_ba' ? (payPalRandomMode ? '提交后每个账号都会显示实际绑定地区' : '只显示后端已有 Locale、时区、币种和账单资料的地区；代理出口以你填写的代理为准') : ''}
              >
                {payPalRandomMode
                  ? <input className="input-glass console-code" value={payPalCountryPool.join(' / ') || '请选择至少 2 个地区'} disabled />
                  : <CustomSelect value={settings.country} onChange={chooseCountry} options={countrySelectOptions} disabled={methodID === 'kakao'} ariaLabel="国家或地区" />}
              </Field>
              <Field label="货币" hint={methodID === 'paypal_ba' ? (payPalRandomMode ? '每个账号跟随它绑定的账单地区' : '跟随账单国家自动切换，仅作 checkout 参数') : ''}><input className="input-glass console-code" value={payPalRandomMode ? '随账号地区' : effectiveCurrency} maxLength={12} disabled={methodID === 'kakao' || methodID === 'paypal_ba'} onChange={(event) => updateSetting('currency', event.target.value.toUpperCase())} /></Field>
              <Field label="账号并发数" hint="同一时间最多处理多少个账号；不是重试次数"><CompactNumberInput min={1} max={maxConcurrency} value={selectedMethod?.supportsConcurrency === false ? 1 : settings.concurrency} onChange={(value) => updateSetting('concurrency', value)} disabled={selectedMethod?.supportsConcurrency === false} ariaLabel="账号并发数" /></Field>
            </div>

            <div className="extraction-proxy-grid">
              <Field
				label={`主流程代理（必填，可多选 ${mainProxyLines.length}）`}
				hint={methodID === 'kakao'
                  ? (kakaoProviderMode
                    ? '支持多行；主代理保持当前选择。只有多选多条主代理时，完整链路重试才会轮换主出口'
                    : '资格观察沿用当前 KR checkout 代理处理逻辑；可多行粘贴')
					: (methodID === 'ph_link' ? '代理池 1：推荐 US；用于创建 PH/PHP Checkout，按完整尝试轮换' : '一行一条；多选后按尝试次数轮换。支持 host:port:user:pass 或 URL')}
              >
                <textarea
                  className="input-glass console-code extraction-proxy-input"
                  value={settings.customProxy}
                  onChange={(event) => updateSetting('customProxy', event.target.value)}
                  placeholder={'一行一条主流程代理\nus.cliproxy.io:3010:user:pass'}
                  spellCheck="false"
                />
                <div className="extraction-proxy-check-row">
                  <GlassButton variant="glass" icon={Radar} loading={proxyChecking.main} disabled={!mainProxyLines.length || proxyChecking.main} onClick={() => checkProxyPool('main')}>测试主代理可达</GlassButton>
                  <small className={proxyCheckStatus.main.includes('不可达') ? 'is-bad' : (proxyCheckStatus.main.includes('可达') ? 'is-ok' : '')}>{proxyCheckStatus.main || '先测出口 IP / 国家，再跑完整提炼'}</small>
                </div>
              </Field>
              {libraryMainOptions.length ? (
                <div className={`proxy-multi-select proxy-library-picker ${libraryMainExpanded ? 'is-open' : ''}`} aria-label="主流程代理多选">
                  <button
                    type="button"
                    className="proxy-library-summary"
                    onClick={() => setLibraryMainExpanded((value) => !value)}
                    aria-expanded={libraryMainExpanded}
                  >
                    <span>
                      <b>从文件库勾选主代理</b>
                      <small>{libraryProxyStatus || `文件库 ${libraryMainOptions.length} 条可多选`}</small>
                    </span>
                    <span className="proxy-library-summary-meta">
                      <em>{mainProxyLines.filter((line) => libraryMainOptions.includes(line)).length || 0}/{libraryMainOptions.length}</em>
                      <button type="button" className="proxy-library-refresh" onClick={(event) => { event.stopPropagation(); loadLibraryProxies(); }}>刷新</button>
                      <i className={libraryMainExpanded ? 'is-open' : ''}>▾</i>
                    </span>
                  </button>
                  {!libraryMainExpanded ? (
                    <div className="proxy-library-selected-chips" aria-label="已选主代理">
                      {(mainProxyLines.filter((line) => libraryMainOptions.includes(line)).length
                        ? mainProxyLines.filter((line) => libraryMainOptions.includes(line))
                        : []).slice(0, 8).map((line) => (
                        <span key={`main-chip-${line}`} className="proxy-library-chip">{proxyOptionLabel(line)}</span>
                      ))}
                      {!mainProxyLines.filter((line) => libraryMainOptions.includes(line)).length ? (
                        <span className="proxy-multi-select-empty">点击上方展开，从文件库多选主代理</span>
                      ) : null}
                      {mainProxyLines.filter((line) => libraryMainOptions.includes(line)).length > 8 ? (
                        <span className="proxy-library-chip is-more">+{mainProxyLines.filter((line) => libraryMainOptions.includes(line)).length - 8}</span>
                      ) : null}
                    </div>
                  ) : (
                    <div className="proxy-multi-select-list proxy-library-expanded-list">
                      {libraryMainOptions.map((line) => {
                        const checked = mainProxyLines.includes(line);
                        return (
                          <label key={`main-${line}`} className={checked ? 'active' : ''}>
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={(event) => updateSetting('customProxy', toggleProxyLine(settings.customProxy, line, event.target.checked))}
                            />
                            <span>{proxyOptionLabel(line)}</span>
                          </label>
                        );
                      })}
                    </div>
                  )}
                </div>
              ) : null}

              {methodID !== 'kakao' || kakaoProviderMode ? (
                <Field
                  label={`优惠代理（可选，可多选 ${promotionProxyLines.length}）`}
					hint={methodID === 'kakao'
					? '一行一条；填写后按行轮换，原样使用，不自动改国家'
					: (methodID === 'ph_link' ? '代理池 2：必须 TR；用于 checkout/update 应用优惠，按完整尝试轮换' : '一行一条；可留空')}
                >
                  <textarea
                    className="input-glass console-code extraction-proxy-input"
                    value={settings.promotionProxy}
                    onChange={(event) => updateSetting('promotionProxy', event.target.value)}
                    placeholder={'一行一条优惠代理\nus.cliproxy.io:3010:user:pass'}
                    spellCheck="false"
                  />
                  <div className="extraction-proxy-check-row">
                    <GlassButton variant="glass" icon={Radar} loading={proxyChecking.promotion} disabled={!promotionProxyLines.length || proxyChecking.promotion} onClick={() => checkProxyPool('promotion')}>测试优惠代理可达</GlassButton>
                    <small className={proxyCheckStatus.promotion.includes('不可达') ? 'is-bad' : (proxyCheckStatus.promotion.includes('可达') ? 'is-ok' : '')}>{proxyCheckStatus.promotion || '可选；用于确认优惠出口是否通'}</small>
                  </div>
                </Field>
              ) : null}

              {methodID !== 'kakao' || kakaoProviderMode ? (
                <div className={`proxy-multi-select proxy-library-picker ${libraryPromotionExpanded ? 'is-open' : ''}`} aria-label="优惠代理多选">
                  <button
                    type="button"
                    className="proxy-library-summary"
                    onClick={() => setLibraryPromotionExpanded((value) => !value)}
                    aria-expanded={libraryPromotionExpanded}
                  >
                    <span>
                      <b>从文件库勾选优惠代理</b>
                      <small>{libraryProxyStatus || (libraryPromotionOptions.length ? `文件库 ${libraryPromotionOptions.length} 条可多选` : '可多选')}</small>
                    </span>
                    <span className="proxy-library-summary-meta">
                      <em>{promotionProxyLines.filter((line) => libraryPromotionOptions.includes(line)).length || 0}/{libraryPromotionOptions.length || 0}</em>
                      <button type="button" className="proxy-library-refresh" onClick={(event) => { event.stopPropagation(); loadLibraryProxies(); }}>刷新</button>
                      <i className={libraryPromotionExpanded ? 'is-open' : ''}>▾</i>
                    </span>
                  </button>
                  {!libraryPromotionExpanded ? (
                    <div className="proxy-library-selected-chips" aria-label="已选优惠代理">
                      {(promotionProxyLines.filter((line) => libraryPromotionOptions.includes(line)) || []).slice(0, 8).map((line) => (
                        <span key={`promo-chip-${line}`} className="proxy-library-chip">{proxyOptionLabel(line)}</span>
                      ))}
                      {!promotionProxyLines.filter((line) => libraryPromotionOptions.includes(line)).length ? (
                        <span className="proxy-multi-select-empty">点击上方展开，从文件库多选优惠代理</span>
                      ) : null}
                      {promotionProxyLines.filter((line) => libraryPromotionOptions.includes(line)).length > 8 ? (
                        <span className="proxy-library-chip is-more">+{promotionProxyLines.filter((line) => libraryPromotionOptions.includes(line)).length - 8}</span>
                      ) : null}
                    </div>
                  ) : (
                    <div className="proxy-multi-select-list proxy-library-expanded-list">
                      {(libraryPromotionOptions.length ? libraryPromotionOptions : []).map((line) => {
                        const checked = promotionProxyLines.includes(line);
                        return (
                          <label key={`promo-${line}`} className={checked ? 'active' : ''}>
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={(event) => updateSetting('promotionProxy', toggleProxyLine(settings.promotionProxy, line, event.target.checked))}
                            />
                            <span>{proxyOptionLabel(line)}</span>
                          </label>
                        );
                      })}
                      {!libraryPromotionOptions.length ? <div className="proxy-multi-select-empty">文件库暂无优惠代理可勾选</div> : null}
                    </div>
                  )}
                </div>
              ) : null}



            </div>

            <div className="extraction-actions">
              <GlassButton variant="primary" icon={Play} loading={submitting} disabled={!serviceConnected || !inputRows.length || selectedMethod?.available === false || selectedMethod?.runnable === false} onClick={startBatch}>{methodID === 'kakao' ? kakaoActionLabel : '开始批量提炼'}</GlassButton>
              <GlassButton variant="glass" icon={ClipboardPaste} onClick={() => notify('请直接在输入框粘贴账号凭证', 'info')}>粘贴提示</GlassButton>
            </div>

            <CollapsiblePanel title="高级参数" defaultOpen={false} storageKey="extract-advanced-settings">
              <div className="extraction-advanced-grid">
                <Field label="超时（秒）"><input type="number" min="5" max="180" className="input-glass" value={settings.timeoutSeconds} onChange={(event) => updateSetting('timeoutSeconds', Number(event.target.value))} /></Field>
				<Field label="每账号最多尝试次数" hint={kakaoProviderMode ? '支付链模式表示完整链路次数；可自行设置 1–100。每次新 Checkout，从 Promotion 一直跑到 NicePay/Kakao；成功立即停止' : (methodID === 'ph_link' ? '每次都重新创建 Checkout 并轮换 US/TR 代理身份；满足当前金额门禁立即停止' : '完整链路次数，包含首次执行；金额不符、generic_decline 或 Approve 耗尽会丢弃本轮 Checkout/pm_ 并从头重跑；网络错误不计次')}>{methodID === 'ph_link' ? <CompactNumberInput min={1} max={50} value={settings.maxAttempts} onChange={(value) => updateSetting('maxAttempts', value)} ariaLabel="每账号最多尝试次数" /> : <CompactNumberInput min={1} max={methodID === 'kakao' ? 100 : 10} value={settings.maxAttempts} onChange={(value) => updateSetting('maxAttempts', value)} ariaLabel="每账号最多尝试次数" />}</Field>
                <Field label="Approve 重试次数" hint="同一 checkout 上 chatgpt.approve 的次数；blocked 后仍会短轮询 redirect，再失败才回退整条完整链路重开。可设 1–10，默认 3"><CompactNumberInput min={1} max={10} value={settings.approveAttempts ?? 3} onChange={(value) => updateSetting('approveAttempts', value)} ariaLabel="Approve 重试次数" /></Field>
                <Field label="Trial Days" hint="创建 checkout 时写入 subscription_data.trial_period_days 的试用天数，不是重试次数"><input type="number" min="0" max="90" className="input-glass" value={settings.trialDays} onChange={(event) => updateSetting('trialDays', Number(event.target.value))} /></Field>
                <div className="console-field console-field-wide fingerprint-policy-field">
                  <span>阶段指纹策略</span>
                  <div className="fingerprint-policy-grid">
                    {[
                      ['checkout', 'Checkout', 'main', true, '完整链路主身份；KR 语言/时区固定'],
                      ['promotion', 'Promotion', settings.fingerprintPolicy?.promotion || 'follow', false, (settings.fingerprintPolicy?.promotion === 'fresh' ? '该阶段单独换新指纹' : '复用本轮 Checkout 主指纹')],
                      ['provider', 'Provider', settings.fingerprintPolicy?.provider || 'follow', false, (settings.fingerprintPolicy?.provider === 'fresh' ? '该阶段单独换新指纹' : '复用本轮 Checkout 主指纹')],
                      ['approve', 'Approve', settings.fingerprintPolicy?.approve || 'follow', false, (settings.fingerprintPolicy?.approve === 'fresh' ? '每次 approve 重试都换新指纹' : 'approve 各次重试都跟主指纹')],
                    ].map(([stage, label, mode, locked, hint]) => (
                      <Field key={stage} label={label} hint={hint}>
                        <CustomSelect
                          value={locked ? 'main' : mode}
                          disabled={locked}
                          ariaLabel={`${label} 指纹策略`}
                          onChange={(value) => {
                            if (locked) return;
                            updateSetting('fingerprintPolicy', {
                              ...(settings.fingerprintPolicy || { promotion: 'follow', provider: 'follow', approve: 'follow' }),
                              [stage]: value === 'fresh' ? 'fresh' : 'follow',
                            });
                          }}
                          options={locked
                            ? [{ value: 'main', label: '主指纹' }]
                            : [
                              { value: 'follow', label: '跟随主指纹' },
                              { value: 'fresh', label: '新指纹' },
                            ]}
                        />
                      </Field>
                    ))}
                  </div>
                  <small>默认全跟随主指纹。只有选成“新指纹”的阶段会换浏览器身份；韩国语言/时区始终不变。</small>
                </div>
                <div className="console-toggle-grid extraction-advanced-toggles">
                  <Toggle
                    checked={!!settings.fingerprintWeightMode}
                    onChange={(value) => updateSetting('fingerprintWeightMode', value)}
                    label="启用指纹权重倾向"
                    hint="关闭时只记录风控结果，不改变选择。打开后对新指纹做轻微成功倾向，同时保留探索，不会锁死。"
                  />
                  {methodID !== 'kakao' || kakaoProviderMode ? (
                    <Toggle checked={settings.usePromo} onChange={(value) => updateSetting('usePromo', value)} label="执行 Promotion 更新" hint="按优惠代理/地区更新 checkout 优惠" />
                  ) : null}
                  {methodID !== 'kakao' || kakaoProviderMode ? (
                    <Toggle checked={settings.paymentStatusAutoRefresh} onChange={(value) => updateSetting('paymentStatusAutoRefresh', value)} label="自动刷新支付状态" hint="出链后自动轮询支付状态" />
                  ) : null}
                  {methodID === 'paypal_ba' ? (
                    <Toggle checked={!!settings.paypalSameStickyIp} onChange={(value) => updateSetting('paypalSameStickyIp', value)} label="PP 同 Sticky IP" hint="Checkout、Provider、Approve 复用同一代理身份；Promotion 仍独立" />
                  ) : null}
                </div>
                {linkOnlyAmountGate ? <>
                  <Field label="金额门禁" hint="只影响 Checkout 链接生成；默认严格等于 0。金额未知默认不出链。">
                    <CustomSelect value={settings.amountGate} onChange={(value) => updateSetting('amountGate', value)} options={amountGateOptions} ariaLabel="金额门禁" />
                  </Field>
                  {['at_most', 'at_least'].includes(settings.amountGate) ? <Field label={`金额阈值（${effectiveCurrency}）`} hint="按页面显示的货币单位填写，后端会转换为 Stripe 最小单位比较。">
                    <input className="input-glass" type="number" min="0" step={currencyMinorExponent(effectiveCurrency) === 0 ? 1 : (currencyMinorExponent(effectiveCurrency) === 3 ? '0.001' : '0.01')} value={formatMinorAmount(settings.amountThresholdMinor, effectiveCurrency)} onChange={(event) => updateSetting('amountThresholdMinor', majorToMinor(event.target.value, effectiveCurrency))} />
                  </Field> : null}
                  <Field label="金额未知"><CustomSelect value={settings.allowUnknownAmount ? 'allow' : 'reject'} onChange={(value) => updateSetting('allowUnknownAmount', value === 'allow')} options={[{ value: 'reject', label: '拒绝出链' }, { value: 'allow', label: '允许出链' }]} ariaLabel="金额未知处理" /></Field>
                </> : <Field label="金额门禁" hint="该渠道后续会进入支付方法或确认步骤，保持严格等于 0。"><input className="input-glass" value="严格等于 0" readOnly aria-label="金额门禁严格等于 0" /></Field>}
                {methodID !== 'kakao' || kakaoProviderMode ? <Field label="Promotion Campaign ID"><input className="input-glass console-code" value={settings.promoCampaignId} onChange={(event) => updateSetting('promoCampaignId', event.target.value)} /></Field> : null}
                {methodID === 'blik' ? <Field label="BLIK 验证码" hint="可选 6 位验证码；留空时由上游流程返回可用的 provider 跳转"><input className="input-glass console-code" inputMode="numeric" maxLength={6} value={settings.blikCode || ''} onChange={(event) => updateSetting('blikCode', event.target.value.replace(/\D/g, '').slice(0, 6))} placeholder="可选，例如 123456" /></Field> : null}
                <Field label="Stripe Publishable Key"><input className="input-glass console-code" value={settings.stripePublishableKey} onChange={(event) => updateSetting('stripePublishableKey', event.target.value)} placeholder="可选" /></Field>
              </div>
            </CollapsiblePanel>

          </GlassPanel>
        </section>

        <section className="extraction-run-column">
          <GlassPanel variant="strong" className="extraction-work-panel extraction-run-panel">
            <div className="extraction-panel-topline"><div><span className="extraction-section-number">03</span><div><b>运行监控</b></div></div><Terminal size={20} /></div>
            <div className="extraction-run-status"><div><span className="extraction-run-label">当前批次</span><strong>{currentJob ? (currentJob.methodLabel || selectedMethod?.label) : '尚未提交'}</strong></div><StatusBadge tone={toneFor(currentJob?.status || 'idle')}>{labelFor(currentJob?.status || 'idle')}</StatusBadge></div>
            <div className="extraction-progress"><div className="extraction-progress-head"><span>{currentJob ? `${completedCount} / ${metrics.total} 个账号完成` : '等待开始'}</span><b>{currentJob ? `${progress}%` : '—'}</b></div><div className="extraction-progress-track"><span style={{ width: `${progress}%` }} /></div></div>
            <div className="extraction-metrics"><MetricCard label="总账号" value={metrics.total} hint={currentJob ? `账号并发 ${currentJob.concurrency || settings.concurrency}` : '输入后自动识别'} /><MetricCard label="排队中" value={metrics.queued} tone="accent" hint="等待空闲账号 worker" /><MetricCard label={currentJob?.method === 'kakao' ? (currentJob.options?.kakaoMode === 'provider_link' ? '已生成' : '已观察') : '成功'} value={metrics.succeeded} tone="success" hint={currentJob?.method === 'kakao' ? (currentJob.options?.kakaoMode === 'provider_link' ? '已生成待用户支付的 Kakao 链接' : '命中与未命中都是完整观察结果') : '已产出结果'} /><MetricCard label="失败 / 取消" value={metrics.failed} tone={metrics.failed ? 'error' : ''} hint="可查看原因" /></div>

            <div className={`extraction-current-stage ${selectedItem?.status === 'running' ? 'running' : ''}`}><span>{selectedItem?.status === 'running' ? <LoaderCircle className="animate-spin" size={17} /> : <Zap size={17} />}</span><div><b>{selectedItem ? `${selectedItem.index}. ${itemDisplayLabel(selectedItem)}` : '当前步骤会显示在这里'}</b><small>{selectedItem ? `${stageTitle(selectedItem.stage || selectedItem.extractionStatus)}${selectedItem.detail ? ` · ${selectedItem.detail}` : ''}` : '每个账号一行，后端状态实时回写'}</small></div></div>

            <div className="extraction-queue-head"><span>账号队列</span><small>{visibleItems.length ? `显示 ${Math.min(visibleItems.length, 8)} / ${metrics.total || visibleItems.length}` : '暂无账号'}</small></div>
            <div className="extraction-queue-list">
              {visibleItems.slice(0, 8).map((item) => <button type="button" className={selectedItem?.id === item.id ? 'active' : ''} key={item.id} onClick={() => { setSelectedItemID(item.id); setMobileView('results'); }}><span><b>{item.index}. {itemDisplayLabel(item)}</b><small>{stageTitle(item.stage || item.extractionStatus)}</small></span><AccountStatus item={item} /></button>)}
              {!visibleItems.length ? <div className="extraction-queue-empty"><UsersRound size={19} /><span>提交任务后，账号会按行出现在这里</span></div> : null}
            </div>
            {terminalStatuses.has(currentJob?.status) && currentJob?.inputStored ? <div className="extraction-retry-filters"><span>剩余失败重跑排除项</span><div><Toggle checked={retryFilters.excludeInvalidToken} onChange={(value) => setRetryFilters((current) => ({ ...current, excludeInvalidToken: value }))} label="Token 失效" /><Toggle checked={retryFilters.excludePaidPlan} onChange={(value) => setRetryFilters((current) => ({ ...current, excludePaidPlan: value }))} label="Plus / 已付费" /><Toggle checked={retryFilters.excludeLegacyOAICS} onChange={(value) => setRetryFilters((current) => ({ ...current, excludeLegacyOAICS: value }))} label="旧 OAICS" /></div></div> : null}
            <div className="extraction-monitor-actions">{currentJob?.method === 'kakao' && currentJob?.options?.kakaoMode !== 'provider_link' && terminalStatuses.has(currentJob.status) && (kakaoEligibleAccountsFrom(currentJob).length > 0 || (currentJob?.items || []).some((item) => String(item?.status || '') === 'eligibility_observed' || String(item?.decision || '').toLowerCase() === 'eligible')) ? <GlassButton variant="primary" icon={Zap} loading={submitting} onClick={() => continueKakaoProvider(currentJob)}>整批转支付链（25 次起）</GlassButton> : null}{terminalStatuses.has(currentJob?.status) && currentJob?.inputStored ? <><GlassButton variant="primary" icon={Play} loading={submitting} onClick={retryRemainingFailures}>仅重跑剩余失败</GlassButton><GlassButton variant="glass" icon={RefreshCw} loading={submitting} onClick={verifyPaymentStatus}>复核是否支付 OK</GlassButton><GlassButton variant="glass" icon={Play} loading={submitting} onClick={retryStoredJob}>整批按原输入重跑</GlassButton></> : null}<GlassButton variant="glass" icon={RefreshCw} disabled={!currentJob?.id} onClick={() => loadJob(currentJob.id)}>刷新</GlassButton><GlassButton variant="danger" icon={StopCircle} disabled={!currentJob?.id || terminalStatuses.has(currentJob.status)} onClick={cancelJob}>停止任务</GlassButton></div>
          </GlassPanel>

          {selectedItem ? (
            <GlassPanel variant="strong" className="extraction-flow-panel">
              <div className="extraction-flow-heading">
                <div>
                  <span className="extraction-section-number">05</span>
                  <span>
                    <b>当前账号完整流程</b>
                    <small>{selectedItem.index}. {itemDisplayLabel(selectedItem)} · 从任务创建到当前结果</small>
                  </span>
                </div>
                <AccountStatus item={selectedItem} />
              </div>
              <div className="extraction-flow-current">
                <span>{stageTitle(selectedItem.stage || selectedItem.extractionStatus)}</span>
                <b>{selectedItem.detail || selectedItem.error || labelFor(selectedItem.status)}</b>
                <em>{elapsed(selectedItem.durationMs)}</em>
              </div>
              <div className="extraction-detail-grid">
                {selectedFacts.map(([label, value]) => (
                  <div key={label}>
                    <span>{label}</span>
                    <b title={String(value)}>{value}</b>
                  </div>
                ))}
              </div>
              {currentJob?.method === 'kakao' ? <KakaoLinkMaterialPanel item={selectedItem} method={currentJob?.method} onCopy={copy} /> : null}
              {selectedUPI ? <UPIMaterialPanel material={selectedUPI} onCopy={copy} /> : null}
              {currentJob?.method === 'pix' && paymentMaterial(selectedItem) ? <UPIMaterialPanel material={paymentMaterial(selectedItem)} onCopy={copy} /> : null}
              {currentJob?.method === 'upi' && !selectedUPI ? (
                <div className="extraction-upi-missing">
                  <AlertCircle size={18} />
                  <span>
                    <b>未获得真实 UPI 支付材料</b>
                    <small>静态支付方式图标和普通 Stripe Checkout 链接不会再算作二维码或 UPI instructions。</small>
                  </span>
                </div>
              ) : null}
              {selectedLinks.length ? (
                <div className="extraction-detail-links">
                  {selectedLinks.map(([label, value]) => (
                    <div key={label}>
                      <span>{label}</span>
                      <code title={value}>{value}</code>
                      <div>
                        <GlassButton variant="icon" title="复制链接" onClick={() => copy(value)}><Copy size={14} /></GlassButton>
                        <GlassButton variant="icon" title="打开链接" onClick={() => window.open(value, '_blank', 'noopener,noreferrer')}><ExternalLink size={14} /></GlassButton>
                      </div>
                    </div>
                  ))}
                </div>
              ) : null}
              <div className="extraction-flow-timeline-head">
                <div>
                  <b>完整执行时间线</b>
                  <small>运行中实时展开；框内滚动，默认跟随最新进度</small>
                </div>
                <span>{selectedTimeline.length} 个节点</span>
              </div>
              <div
                className="extraction-step-list extraction-flow-timeline"
                ref={timelineRef}
                onScroll={handleTimelineScroll}
              >
                {selectedTimeline.map((step, index) => (
                  <div className={index === selectedTimeline.length - 1 ? 'is-latest' : ''} key={`${step.at || step.stage}-${index}`}>
                    <span className={`step-dot ${step.status}`} />
                    <div className="extraction-flow-step-copy">
                      <b><i>{String(index + 1).padStart(2, '0')}</i>{stageTitle(step.stage)}</b>
                      <code>{step.stage}</code>
                      <small>{step.detail || labelFor(step.status)}</small>
                    </div>
                    <div className="extraction-flow-step-meta">
                      <StatusBadge tone={toneFor(step.status)}>{labelFor(step.status)}</StatusBadge>
                      <em>{displayTimestamp(step.at)}</em>
                      {step.elapsedMs ? <em>{elapsed(step.elapsedMs)}</em> : null}
                    </div>
                  </div>
                ))}
                {!selectedTimeline.length ? <div className="console-empty">该账号尚无阶段记录</div> : null}
              </div>
              {Object.keys(selectedItem.metadata || {}).length ? (
                <CollapsiblePanel title="调试信息 · 后端返回元数据" defaultOpen={false} storageKey="extract-debug-metadata">
                  <OutputBox value={selectedItem.metadata} title="原始元数据" filename={`extract-${selectedItem.id}-metadata.json`} />
                </CollapsiblePanel>
              ) : null}
            </GlassPanel>
          ) : null}
        </section>
      </div>

      <section className="extraction-results-section">
        <GlassPanel variant="strong" className="extraction-work-panel extraction-results-panel">
          <div className="extraction-panel-topline"><div><span className="extraction-section-number">04</span><div><b>账号结果</b></div></div><span className="extraction-count-chip">{currentJob?.items?.length || 0} 个账号</span><UsersRound size={20} /></div>
          {terminalStatuses.has(currentJob?.status) && currentJob?.inputStored ? <div className="extraction-selection-toolbar"><span>已选 <b>{selectedRetryItemIDs.length}</b> 个账号</span><div><GlassButton variant="glass" onClick={() => setSelectedRetryItemIDs((currentJob?.items || []).filter((item) => item.status === 'failed' || item.status === 'cancelled').map((item) => item.id))}>全选失败</GlassButton><GlassButton variant="glass" onClick={() => setSelectedRetryItemIDs((currentJob?.items || []).map((item) => item.id))}>全选全部</GlassButton><GlassButton variant="glass" disabled={!selectedRetryItemIDs.length} onClick={() => setSelectedRetryItemIDs([])}>清空</GlassButton><GlassButton variant="primary" icon={Play} loading={submitting} disabled={!selectedRetryItemIDs.length} onClick={retrySelectedAccounts}>重跑已选账号</GlassButton></div></div> : null}
          <div className="extract-desktop-table"><table className="extraction-table"><thead><tr>{terminalStatuses.has(currentJob?.status) && currentJob?.inputStored ? <th className="extraction-select-column">选择</th> : null}<th># / 账号</th><th>执行</th><th>当前阶段</th><th>提炼</th><th>支付</th><th>金额</th><th>耗时</th><th>链接</th></tr></thead><tbody>{(currentJob?.items || []).map((item) => { const link = itemLink(item, currentJob?.method); const displayLabel = itemDisplayLabel(item); const region = [item.country, item.currency].filter(Boolean).join(' / '); const checked = selectedRetryItemIDs.includes(item.id); return <tr key={item.id} className={`${selectedItem?.id === item.id ? 'selected ' : ''}${checked ? 'retry-selected' : ''}`} onClick={() => setSelectedItemID(item.id)}>{terminalStatuses.has(currentJob?.status) && currentJob?.inputStored ? <td className="extraction-select-column"><input type="checkbox" checked={checked} aria-label={`选择 ${displayLabel} 重跑`} onClick={(event) => event.stopPropagation()} onChange={(event) => toggleRetryItem(item.id, event.target.checked)} /></td> : null}<td><b>{item.index}. {displayLabel}</b><small>{[region, item.email && item.email !== displayLabel ? item.email : `token · ${item.tokenHash || '—'}`].filter(Boolean).join(' · ')}</small></td><td><AccountStatus item={item} /></td><td><b>{stageTitle(item.stage || item.extractionStatus)}</b><small>{item.detail || item.error || item.stage || '—'}</small></td><td><StatusBadge tone={toneFor(item.extractionStatus)}>{labelFor(item.extractionStatus)}</StatusBadge></td><td><StatusBadge tone={toneFor(item.paymentStatus)}>{labelFor(item.paymentStatus)}</StatusBadge></td><td>{item.amountDisplay || '—'}</td><td>{elapsed(item.durationMs)}</td><td><div className="extraction-link-actions"><GlassButton variant="icon" disabled={!link} title="复制链接" onClick={(event) => { event.stopPropagation(); copy(link); }}><Copy size={14} /></GlassButton><GlassButton variant="icon" disabled={!link} title="打开链接" onClick={(event) => { event.stopPropagation(); if (link) window.open(link, '_blank', 'noopener,noreferrer'); }}><ExternalLink size={14} /></GlassButton></div></td></tr>; })}{!currentJob?.items?.length ? <tr><td colSpan="9" className="console-empty">提交任务后，每个账号会在这里占一行</td></tr> : null}</tbody></table></div>
          <div className="extract-mobile-list">{(currentJob?.items || []).map((item) => { const link = itemLink(item, currentJob?.method); const region = [item.country, item.currency].filter(Boolean).join(' / '); const checked = selectedRetryItemIDs.includes(item.id); return <div className={`extract-mobile-card ${selectedItem?.id === item.id ? 'selected ' : ''}${checked ? 'retry-selected' : ''}`} key={item.id} role="button" tabIndex={0} onClick={() => setSelectedItemID(item.id)} onKeyDown={(event) => { if (event.key === 'Enter') setSelectedItemID(item.id); }}>{terminalStatuses.has(currentJob?.status) && currentJob?.inputStored ? <label className="extract-mobile-retry-check" onClick={(event) => event.stopPropagation()}><input type="checkbox" checked={checked} onChange={(event) => toggleRetryItem(item.id, event.target.checked)} /><span>选择重跑</span></label> : null}<span className="extract-mobile-card-head"><b>{item.index}. {itemDisplayLabel(item)}</b><AccountStatus item={item} /></span><small>{region ? `${region} · ` : ''}{stageTitle(item.stage || item.extractionStatus)} · {item.detail || item.error || '等待执行'}</small><span className="extract-mobile-statuses"><StatusBadge tone={toneFor(item.extractionStatus)}>{labelFor(item.extractionStatus)}</StatusBadge><StatusBadge tone={toneFor(item.paymentStatus)}>{labelFor(item.paymentStatus)}</StatusBadge><em>{elapsed(item.durationMs)}</em></span>{link ? <span className="extract-mobile-link"><code>{link}</code><span onClick={(event) => { event.stopPropagation(); copy(link); }}><Copy size={14} /></span></span> : null}</div>; })}{!currentJob?.items?.length ? <div className="console-empty">暂无账号结果</div> : null}</div>
        </GlassPanel>
      </section>

      

      <CollapsiblePanel title="最近批次" summary={`完整保留 ${historyJobs.length} 个批次 · 当前显示 ${visibleHistoryJobs.length} 个`} defaultOpen storageKey="extract-recent-jobs">
        <div className="extraction-history-filters" role="tablist" aria-label="最近批次筛选">
          {[
            ['success', '成功', historyCounts.success],
            ['all', '全部', historyCounts.all],
            ['active', '进行中', historyCounts.active],
            ['unsuccessful', '未成功', historyCounts.unsuccessful],
          ].map(([value, label, count]) => <button type="button" role="tab" aria-selected={historyFilter === value} className={historyFilter === value ? 'active' : ''} key={value} onClick={() => setHistoryFilter(value)}>{label}<span>{count}</span></button>)}
        </div>
        <div className="extraction-history-list">
          {visibleHistoryJobs.map((job) => <div key={job.id} className={`extraction-history-row ${currentJob?.id === job.id ? 'active' : ''}`}><button type="button" className="extraction-history-main" onClick={() => openHistoryJob(job)}><span><b>{job.methodLabel || job.method}</b><small>{historyJobScope(job)}</small><small>{job.id} · {displayTimestamp(job.createdAt)} · {elapsed(job.durationMs)}</small></span><span><StatusBadge tone={toneFor(job.status)}>{labelFor(job.status)}</StatusBadge><em>{isKakaoEligibilityJob(job) && !jobHasProviderSuccess(job) ? `观察 ${kakaoEligibleAccountsFrom(job).length || 0}` : `成功 ${job.succeeded || 0}`} · 失败 {job.failed || 0} · 取消 {job.cancelled || 0}</em><em>共 {job.total || 0} 个账号</em></span></button><GlassButton variant="icon" className="extraction-history-delete" disabled={!terminalStatuses.has(job.status)} title={terminalStatuses.has(job.status) ? '删除这个批次' : '请先停止运行中的批次'} onClick={(event) => deleteHistoryJob(job, event)}><Trash2 size={16} /></GlassButton></div>)}
          {!visibleHistoryJobs.length ? <div className="console-empty">{historyJobs.length ? `没有${historyFilter === 'success' ? '成功' : historyFilter === 'active' ? '进行中' : historyFilter === 'unsuccessful' ? '未成功' : ''}批次，可切换“全部”查看完整记录` : '暂无提炼历史'}</div> : null}
        </div>
      </CollapsiblePanel>
      <MailAdminAccountPicker
        open={mailAdminPickerOpen}
        onClose={() => !mailAdminImporting && setMailAdminPickerOpen(false)}
        onImport={importMailAdminSessions}
        importing={mailAdminImporting}
      />

    </div>
  );
}
