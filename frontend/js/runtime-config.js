/*
 * AutoMyAI frontend runtime configuration.
 *
 * Keep secrets out of this file. For a separately hosted frontend, change
 * only the public service origins and authMode. See frontend/README.md.
 */
(() => {
  const inferredUIBase = location.pathname === '/ui' || location.pathname.startsWith('/ui/') ? '/ui' : '';
  const supplied = window.AUTOMYAI_RUNTIME_CONFIG || {};
  window.AUTOMYAI_RUNTIME_CONFIG = Object.freeze({
    uiBase: inferredUIBase,
    mainApiBase: '/api',
    paypalProtocolApiBase: '/paypal-protocol/api',
    cardPaymentPortalApiBase: '/card-payment-api',
    openai2UiBase: '/openai2',
    openai2ApiBase: '/openai2/api',
    openai3UiBase: '/openai3',
    openai3ApiBase: '/openai3/api',
    openai4UiBase: '/openai4',
    openai4ApiBase: '/openai4/api',
    openai5ApiBase: '/openai5/api',
    openai6UiBase: '/openai6/',
    openai7ApiBase: '/openai6/api',
    grok2Base: '/grok2',
    authMode: 'cookie',
    requestTimeoutMs: 30000,
    autoRedirectOnUnauthorized: true,
    ...supplied,
  });
})();
