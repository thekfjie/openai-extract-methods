import apiClient from './client';

const CATALOG_PATHS = ['/extract/catalog', '/extract-methods/catalog'];
const JOBS_PATH = '/extract/jobs';
const COMPAT_RUN_PATH = '/extract-methods/run';
const TERMINAL_JOB_STATUSES = new Set(['completed', 'failed', 'cancelled', 'interrupted']);

// The older compatibility API calls direct-card `paper_card`. Keep that
// transport detail out of the UI while preserving it for the request body.
const METHOD_ALIASES = {
  paper_card: 'direct_card',
  'paper-card': 'direct_card',
};

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function asObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
}

function firstPresent(...values) {
  return values.find((value) => value !== undefined && value !== null && value !== '');
}

function numberOr(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function canonicalMethodID(value) {
  const id = String(value || '').trim().toLowerCase();
  return METHOD_ALIASES[id] || id;
}

function parseHTTPURL(value) {
  try {
    const parsed = new URL(String(value || '').trim());
    return ['http:', 'https:'].includes(parsed.protocol) ? parsed : null;
  } catch (_) {
    return null;
  }
}

function isStaticPaymentMethodAsset(value) {
  const parsed = parseHTTPURL(value);
  if (!parsed) return false;
  const host = parsed.hostname.toLowerCase();
  const path = parsed.pathname.toLowerCase();
  return host === 'js.stripe.com' || [
    '/fingerprinted/img/payment-methods/',
    '/img/payment-methods/',
    '/payment-methods/icon-pm-',
    'icon-pm-upi',
    'icon-pm-pix',
  ].some((marker) => path.includes(marker));
}

function validUPIQRURL(value) {
  return Boolean(parseHTTPURL(value)) && !isStaticPaymentMethodAsset(value);
}

function validUPIInstructionURL(value) {
  const parsed = parseHTTPURL(value);
  if (!parsed || isStaticPaymentMethodAsset(value)) return false;
  const host = parsed.hostname.toLowerCase();
  if (['js.stripe.com', 'm.stripe.network', 'api.stripe.com', 'r.stripe.com', 'q.stripe.com', 'checkout.stripe.com', 'pay.openai.com', 'chatgpt.com', 'www.chatgpt.com'].includes(host)) return false;
  const pathAndQuery = `${parsed.pathname}?${parsed.searchParams.toString()}`.toLowerCase();
  if (host === 'pm-redirects.stripe.com' && pathAndQuery.includes('/redirect/complete')) return true;
  const stripeHost = host === 'stripe.com' || host.endsWith('.stripe.com') || host === 'stripe.network' || host.endsWith('.stripe.network');
  if (!stripeHost) return true;
  if (host === 'qr.stripe.com' && parsed.pathname && parsed.pathname !== '/') return true;
  return ['/qr/', 'qr_code', 'instruction', 'upi', 'pix'].some((marker) => pathAndQuery.includes(marker));
}

function validUPIPayload(value) {
  return String(value || '').trim().toLowerCase().startsWith('upi://pay');
}

function validUPILegacyLongURL(value) {
  if (!validUPIInstructionURL(value)) return false;
  const parsed = parseHTTPURL(value);
  const host = parsed.hostname.toLowerCase();
  const stripeHost = host === 'stripe.com' || host.endsWith('.stripe.com') || host === 'stripe.network' || host.endsWith('.stripe.network');
  if (stripeHost) return true;
  const pathAndQuery = `${parsed.pathname}?${parsed.searchParams.toString()}`.toLowerCase();
  return ['upi', '/qr', 'qr_', 'instruction', '/pay'].some((marker) => pathAndQuery.includes(marker));
}

function normalizedMethod(method = {}) {
  const apiMethod = String(method.apiMethod || method.id || '').trim();
  const id = canonicalMethodID(apiMethod);
  const directCard = id === 'direct_card';
  return {
    ...method,
    id,
    apiMethod: apiMethod || id,
    name: directCard ? 'Direct Card' : firstPresent(method.name, method.label, id),
    label: directCard ? '直卡' : firstPresent(method.label, method.name, id),
    countries: asArray(method.countries),
    adaptedCountries: asArray(method.adaptedCountries),
    available: method.available !== false,
    runnable: method.runnable !== false && method.available !== false,
    supportsConcurrency: method.supportsConcurrency !== false,
    supportsPaymentStatus: method.supportsPaymentStatus !== false,
    implementation: firstPresent(method.implementation, 'go'),
  };
}

export function normalizeExtractionCatalog(payload = {}, sourcePath = '') {
  return {
    ...payload,
    sourcePath,
    transport: sourcePath === '/extract/catalog' ? 'jobs' : 'compatibility',
    defaultMethod: canonicalMethodID(payload.defaultMethod || 'paypal_ba'),
    methods: asArray(payload.methods).map(normalizedMethod),
    limits: {
      maxItems: numberOr(payload?.limits?.maxItems, 500),
      maxConcurrency: numberOr(payload?.limits?.maxConcurrency, 32),
    },
  };
}

export function normalizeExtractionItem(item = {}, jobMethod = '') {
  const result = asObject(item.result);
  let steps = asArray(item.steps).length ? asArray(item.steps) : asArray(result.steps);
  const metadata = { ...(Object.keys(asObject(item.metadata)).length ? asObject(item.metadata) : asObject(result.metadata)) };
  const method = canonicalMethodID(jobMethod || item.method || result.method);
  let status = firstPresent(item.status, item.itemStatus, result.status, 'queued');
  let stage = firstPresent(item.stage, result.stage, '');
  let detail = firstPresent(item.detail, result.detail, item.message, result.message, item.error, result.error, '');
  let error = firstPresent(item.error, result.error, '');
  let extractionStatus = firstPresent(item.extractionStatus, item.extraction_status, result.extractionStatus, result.extraction_status, 'queued');
  let paymentStatus = firstPresent(item.paymentStatus, item.payment_status, result.paymentStatus, result.payment_status, 'not_started');
  let longUrl = firstPresent(item.longUrl, item.long_url, item.url, item.link, result.longUrl, result.long_url, result.url, result.link, '');
  let providerRedirectUrl = firstPresent(item.providerRedirectUrl, item.provider_redirect_url, item.approveUrl, item.approve_url, result.providerRedirectUrl, result.provider_redirect_url, result.approveUrl, result.approve_url, '');
  let stripeRedirectUrl = firstPresent(item.stripeRedirectUrl, item.stripe_redirect_url, result.stripeRedirectUrl, result.stripe_redirect_url, '');
  let upiPayload = firstPresent(item.upiPayload, item.upi_payload, result.upiPayload, result.upi_payload, metadata.upiPayload, metadata.upi_payload, '');
  let upiInstructionUrl = firstPresent(item.upiInstructionUrl, item.upi_instruction_url, result.upiInstructionUrl, result.upi_instruction_url, metadata.instructionUrl, metadata.instruction_url, '');
  let qrPngUrl = firstPresent(item.qrPngUrl, item.qr_png_url, result.qrPngUrl, result.qr_png_url, metadata.qrPngUrl, metadata.qr_png_url, '');
  let qrSvgUrl = firstPresent(item.qrSvgUrl, item.qr_svg_url, result.qrSvgUrl, result.qr_svg_url, metadata.qrSvgUrl, metadata.qr_svg_url, '');
  let paymentPayload = firstPresent(item.paymentPayload, item.payment_payload, result.paymentPayload, result.payment_payload, metadata.paymentPayload, metadata.payment_payload, '');
  let paymentInstructionUrl = firstPresent(item.paymentInstructionUrl, item.payment_instruction_url, result.paymentInstructionUrl, result.payment_instruction_url, metadata.paymentInstructionUrl, metadata.payment_instruction_url, '');
  let linkGeneratedAt = firstPresent(item.linkGeneratedAt, item.link_generated_at, result.linkGeneratedAt, result.link_generated_at, metadata.linkGeneratedAt, metadata.link_generated_at, item.finishedAt, result.finishedAt, '');
  let expiresAt = firstPresent(item.expiresAt, item.expires_at, result.expiresAt, result.expires_at, metadata.expiresAt, metadata.expires_at, metadata.providerLinkExpiresAt, metadata.provider_link_expires_at, '');
  let linkTtlSeconds = numberOr(firstPresent(item.linkTtlSeconds, item.link_ttl_seconds, result.linkTtlSeconds, result.link_ttl_seconds, metadata.linkTtlSeconds, metadata.link_ttl_seconds, 0), 0);
  if (!expiresAt && linkGeneratedAt && (method === 'kakao' || extractionStatus === 'provider_link_ready')) {
    const generatedMs = Date.parse(linkGeneratedAt);
    const ttlMs = (linkTtlSeconds > 0 ? linkTtlSeconds : 600) * 1000;
    if (Number.isFinite(generatedMs)) expiresAt = new Date(generatedMs + ttlMs).toISOString();
  }
  if (!linkTtlSeconds && (method === 'kakao' || extractionStatus === 'provider_link_ready') && (expiresAt || longUrl || providerRedirectUrl)) {
    linkTtlSeconds = 600;
  }

  if (method === 'upi') {
    upiPayload = validUPIPayload(upiPayload) ? upiPayload : '';
    upiInstructionUrl = validUPIInstructionURL(upiInstructionUrl) ? upiInstructionUrl : '';
    qrPngUrl = validUPIQRURL(qrPngUrl) ? qrPngUrl : '';
    qrSvgUrl = validUPIQRURL(qrSvgUrl) ? qrSvgUrl : '';
    providerRedirectUrl = validUPIInstructionURL(providerRedirectUrl) ? providerRedirectUrl : '';
    stripeRedirectUrl = validUPIInstructionURL(stripeRedirectUrl) ? stripeRedirectUrl : '';
    if (!upiPayload && validUPIPayload(longUrl)) upiPayload = longUrl;
    if (!providerRedirectUrl && !stripeRedirectUrl && validUPILegacyLongURL(longUrl)) providerRedirectUrl = longUrl;
    longUrl = firstPresent(upiInstructionUrl, providerRedirectUrl, stripeRedirectUrl, qrPngUrl, qrSvgUrl, upiPayload, '');
    const metadataValues = { upiPayload, instructionUrl: upiInstructionUrl, qrPngUrl, qrSvgUrl };
    Object.entries(metadataValues).forEach(([key, value]) => {
      if (value) metadata[key] = value;
      else delete metadata[key];
    });
    ['upi_payload', 'instruction_url', 'qr_png_url', 'qr_svg_url'].forEach((key) => delete metadata[key]);
    const hasMaterial = Boolean(upiPayload || upiInstructionUrl || qrPngUrl || qrSvgUrl || providerRedirectUrl || stripeRedirectUrl);
    if (!hasMaterial && (status === 'succeeded' || extractionStatus === 'upi_ready')) {
      const message = '静态 UPI 图标或普通 Stripe Checkout 链接已过滤，未获得真实 UPI 支付材料';
      status = 'failed';
      stage = 'upi.material.validation';
      detail = message;
      error = message;
      extractionStatus = 'not_available';
      paymentStatus = 'not_started';
      let found = false;
      steps = steps.map((step) => {
        if (String(step?.stage || '').toLowerCase() !== 'upi.material') return step;
        found = true;
        return { ...step, status: 'failed', detail: message };
      });
      if (!found) steps = [...steps, { at: item.finishedAt || result.finishedAt || '', stage, status: 'failed', detail: message }];
    }
  }

  const normalizedResult = {
    ...result,
    ok: method === 'upi' && status === 'failed' ? false : result.ok,
    longUrl,
    providerRedirectUrl,
    stripeRedirectUrl,
    upiPayload,
    upiInstructionUrl,
    qrPngUrl,
    qrSvgUrl,
    paymentPayload,
    paymentInstructionUrl,
    linkGeneratedAt,
    expiresAt,
    linkTtlSeconds,
    extractionStatus,
    paymentStatus,
    metadata,
    steps,
  };
  return {
    ...item,
    status,
    stage,
    detail,
    error,
    extractionStatus,
    paymentStatus,
    longUrl,
    providerRedirectUrl,
    stripeRedirectUrl,
    upiPayload,
    upiInstructionUrl,
    qrPngUrl,
    qrSvgUrl,
    paymentPayload,
    paymentInstructionUrl,
    linkGeneratedAt,
    expiresAt,
    linkTtlSeconds,
    checkoutId: firstPresent(item.checkoutId, item.checkout_id, result.checkoutId, result.checkout_id, ''),
    checkoutType: firstPresent(item.checkoutType, item.checkout_type, result.checkoutType, result.checkout_type, metadata.checkoutType, ''),
    paymentMethodId: firstPresent(item.paymentMethodId, item.payment_method_id, result.paymentMethodId, result.payment_method_id, ''),
    processorEntity: firstPresent(item.processorEntity, item.processor_entity, result.processorEntity, result.processor_entity, ''),
    country: firstPresent(item.country, result.country, ''),
    currency: firstPresent(item.currency, result.currency, ''),
    amount: firstPresent(item.amount, result.amount, ''),
    amountDisplay: firstPresent(item.amountDisplay, item.amount_display, result.amountDisplay, result.amount_display, ''),
    amountStatus: firstPresent(item.amountStatus, item.amount_status, result.amountStatus, result.amount_status, ''),
    decision: firstPresent(item.decision, result.decision, ''),
    availableMethods: asArray(item.availableMethods).length ? asArray(item.availableMethods) : asArray(result.availableMethods || result.available_methods),
    metadata,
    steps,
    result: normalizedResult,
  };
}

function isKakaoEligibilityItem(item = {}) {
  const status = String(item.status || '').toLowerCase();
  const extraction = String(item.extractionStatus || '').toLowerCase();
  const detail = String(item.detail || item.message || '');
  const decision = String(item.decision || '').toLowerCase();
  const hasProviderMaterial = Boolean(String(item.longUrl || item.providerRedirectUrl || '').trim());
  if (hasProviderMaterial) return false;
  return (
    status === 'eligibility_observed'
    || extraction === 'probe_complete'
    || extraction === 'eligibility_complete'
    || detail.includes('上游资格观察')
    || detail.includes('资格观察')
    || decision === 'eligible'
    || decision === 'ineligible'
  );
}

function demoteKakaoEligibilityItems(method, items = []) {
  if (canonicalMethodID(method) !== 'kakao') return items;
  return items.map((item) => {
    if (String(item.status || '').toLowerCase() !== 'succeeded') return item;
    if (!isKakaoEligibilityItem(item)) return item;
    return {
      ...item,
      status: 'eligibility_observed',
      stage: firstPresent(item.stage, 'eligibility_observed'),
      extractionStatus: item.extractionStatus === 'link_ready' || !item.extractionStatus ? 'probe_complete' : item.extractionStatus,
      detail: item.detail && !String(item.detail).includes('资格观察')
        ? item.detail
        : 'Kakao 上游资格观察已完成（非提炼成功）',
    };
  });
}

export function normalizeExtractionJob(job = {}) {
  const method = canonicalMethodID(job.method || job.methodId);
  const rawItems = asArray(job.items).map((item) => normalizeExtractionItem(item, method));
  const items = demoteKakaoEligibilityItems(method, rawItems);
  const total = numberOr(job.total, items.length);
  const count = (status) => items.filter((item) => item.status === status).length;
  const hasDetailedItems = items.length > 0;
  const succeeded = hasDetailedItems ? count('succeeded') : numberOr(job.succeeded, 0);
  const failed = hasDetailedItems ? count('failed') : numberOr(job.failed, 0);
  const cancelled = hasDetailedItems ? count('cancelled') : numberOr(job.cancelled, 0);
  let status = firstPresent(job.status, 'queued');
  if (hasDetailedItems && status === 'completed' && succeeded === 0 && failed > 0) status = 'failed';
  const options = asObject(job.options);
  const kakaoMode = firstPresent(options.kakaoMode, options.mode, '');
  const diagnosticOnly = method === 'kakao' && (
    String(kakaoMode).toLowerCase() === 'eligibility'
    || options.kakaoEligibilityOnly === true
    || options.eligibilityOnly === true
    || (hasDetailedItems && items.every(isKakaoEligibilityItem))
  );
  return {
    ...job,
    id: firstPresent(job.id, job.jobId, ''),
    method,
    methodLabel: firstPresent(job.methodLabel, job.label, job.method, '提炼任务'),
    status,
    total,
    queued: hasDetailedItems ? count('queued') : numberOr(job.queued, 0),
    running: hasDetailedItems ? count('running') : numberOr(job.running, 0),
    // Never surface diagnostic eligibility as extraction success.
    succeeded: diagnosticOnly ? 0 : succeeded,
    failed,
    cancelled,
    concurrency: numberOr(job.concurrency, 1),
    options,
    items,
  };
}

function normalizeJobPayload(payload = {}) {
  const job = payload?.job || payload?.data?.job;
  return job ? { ...payload, job: normalizeExtractionJob(job), jobId: payload.jobId || job.id } : payload;
}

function normalizeRunPayload(payload = {}) {
  const jobItem = payload?.job?.items?.[0];
  const source = jobItem || payload.item || payload.result || payload.data || payload;
  const normalized = normalizeExtractionItem({ ...asObject(payload), ...asObject(source) }, firstPresent(payload?.job?.method, payload.method, source?.method, ''));
  const ok = payload.ok !== false && source?.ok !== false && normalized.status !== 'failed';
  return {
    ...normalized,
    ok,
    status: ok ? 'succeeded' : 'failed',
    extractionStatus: ok ? normalized.extractionStatus : firstPresent(normalized.extractionStatus, 'failed'),
    rawResponse: payload,
  };
}

function abortError() {
  if (typeof DOMException !== 'undefined') return new DOMException('任务已取消', 'AbortError');
  const error = new Error('任务已取消');
  error.name = 'AbortError';
  return error;
}

function wait(milliseconds, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError());
      return;
    }
    const timer = (typeof window !== 'undefined' ? window.setTimeout : setTimeout)(resolve, milliseconds);
    signal?.addEventListener('abort', () => {
      (typeof window !== 'undefined' ? window.clearTimeout : clearTimeout)(timer);
      reject(abortError());
    }, { once: true });
  });
}

async function resolveAsyncJob(payload, signal) {
  const jobID = payload?.jobId || payload?.job?.id;
  if (!jobID) return normalizeRunPayload(payload);
  let job = normalizeExtractionJob(payload.job || {});
  while (!TERMINAL_JOB_STATUSES.has(job?.status)) {
    await wait(800, signal);
    const response = await apiClient.get(`${JOBS_PATH}/${encodeURIComponent(jobID)}`, { signal });
    job = normalizeExtractionJob(response?.job || response);
  }
  const item = job?.items?.[0] || {};
  if (item.status === 'failed') throw new Error(item.error || item.detail || '提炼失败');
  if (item.status === 'cancelled') throw abortError();
  return normalizeRunPayload({ ok: item.status === 'succeeded', job, item });
}

function methodTransportID(method) {
  return method?.apiMethod || (method?.id === 'direct_card' ? 'paper_card' : method?.id) || method;
}

export function parseExtractionInput(rawInput) {
  const text = String(rawInput || '').trim();
  if (!text) return [];
  let values;
  try {
    const parsed = JSON.parse(text);
    if (Array.isArray(parsed)) values = parsed;
    else {
      const nested = ['items', 'accounts', 'credentials', 'data', 'results'].map((key) => parsed?.[key]).find(Array.isArray);
      values = nested || [parsed];
    }
  } catch {
    values = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).map((line) => {
      try { return JSON.parse(line); } catch { return line; }
    });
  }
  return values.map((value, index) => {
    const object = asObject(value);
    const label = firstPresent(object.label, object.email, object.name, object.accountId, object.account_id, object.id, `账号 ${index + 1}`);
    return {
      index: index + 1,
      label: typeof label === 'string' ? label : `账号 ${index + 1}`,
      email: firstPresent(object.email, object.account?.email, ''),
      input: typeof value === 'string' ? value : JSON.stringify(value),
      value,
    };
  });
}

export const extractionApi = {
  async getCatalog() {
    let lastError;
    for (const path of CATALOG_PATHS) {
      try {
        const payload = await apiClient.get(path);
        if (Array.isArray(payload?.methods)) return normalizeExtractionCatalog(payload, path);
      } catch (error) {
        lastError = error;
        if (![404, 405].includes(error?.status)) throw error;
      }
    }
    throw lastError || new Error('提炼目录接口不可用');
  },

  async listJobs(limit) {
    try {
      const query = Number.isFinite(limit) && limit > 0 ? `?limit=${encodeURIComponent(limit)}` : '';
      const payload = await apiClient.get(`${JOBS_PATH}${query}`);
      return { ...payload, jobs: asArray(payload?.jobs).map(normalizeExtractionJob) };
    } catch (error) {
      if ([404, 405].includes(error?.status)) return { ok: false, jobs: [], unavailable: true };
      throw error;
    }
  },

  async getJob(jobID, requestOptions = {}) {
    const payload = await apiClient.get(`${JOBS_PATH}/${encodeURIComponent(jobID)}`, requestOptions);
    return { ...payload, job: normalizeExtractionJob(payload?.job || payload) };
  },

  async deleteJob(jobID, requestOptions = {}) {
    return apiClient.delete(`${JOBS_PATH}/${encodeURIComponent(jobID)}`, requestOptions);
  },

  async createJob({ method, input, concurrency, options = {} }, requestOptions = {}) {
    const body = {
      method: methodTransportID(method),
      input: String(input || ''),
      concurrency: Number(concurrency || 1),
      options,
    };
    try {
      const payload = await apiClient.post(JOBS_PATH, body, requestOptions);
      return normalizeJobPayload(payload);
    } catch (error) {
      if (![404, 405].includes(error?.status)) throw error;
      const compatPayload = await apiClient.post(COMPAT_RUN_PATH, {
        ...body,
        ...options,
      }, requestOptions);
      return normalizeJobPayload(compatPayload);
    }
  },

  async cancelJob(jobID, requestOptions = {}) {
    const payload = await apiClient.post(`${JOBS_PATH}/${encodeURIComponent(jobID)}/cancel`, {}, requestOptions);
    return { ...payload, job: normalizeExtractionJob(payload?.job || payload) };
  },

  async retryJob(jobID, retryOptions = {}, requestOptions = {}) {
	const payload = await apiClient.post(`${JOBS_PATH}/${encodeURIComponent(jobID)}/retry`, retryOptions, requestOptions);
	return { ...payload, job: normalizeExtractionJob(payload?.job || payload) };
  },

  async verifyPayment(jobID, options = {}, requestOptions = {}) {
    const payload = await apiClient.post(`${JOBS_PATH}/${encodeURIComponent(jobID)}/verify-payment`, options, requestOptions);
    return { ...payload, job: normalizeExtractionJob(payload?.job || payload) };
  },

  async requestKakaoProviderContinuation(jobID, maxAttempts = 25, requestOptions = {}) {
    const payload = await apiClient.post(`${JOBS_PATH}/${encodeURIComponent(jobID)}/continue-provider`, { maxAttempts }, requestOptions);
    return { ...payload, job: normalizeExtractionJob(payload?.job || payload) };
  },

  async markKakaoProviderContinuationSubmitted(jobID, submittedJobId, requestOptions = {}) {
    const payload = await apiClient.post(`${JOBS_PATH}/${encodeURIComponent(jobID)}/continue-provider`, { submittedJobId }, requestOptions);
    return { ...payload, job: normalizeExtractionJob(payload?.job || payload) };
  },

  async runItem({ method, input, options = {} }, requestOptions = {}) {
    const body = { method: methodTransportID(method), input, ...options, options };
    const payload = await apiClient.post(COMPAT_RUN_PATH, body, requestOptions);
    if (payload?.async || payload?.job || payload?.jobId) return resolveAsyncJob(payload, requestOptions.signal);
    const result = normalizeRunPayload(payload);
    if (!result.ok) throw new Error(result.error || result.detail || payload?.error || '提炼失败');
    return result;
  },
};

export default extractionApi;
