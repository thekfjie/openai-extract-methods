/**
 * AutoMyAI Decoupled API Client SDK
 * Supports multiple backend service bases: main (/api), openai2, openai3, openai4, grok2
 * Handles authorization (cookie mode / header mode), JSON parsing, and error normalization.
 */

const getServiceBase = (service = 'main') => {
  const windowConfig = window.AUTOMYAI_RUNTIME_CONFIG || window.__RUNTIME_CONFIG__ || {};
  switch (service) {
    case 'openai2':
      return windowConfig.openai2ApiBase || '/openai2/api';
    case 'openai3':
      return windowConfig.openai3ApiBase || '/openai3/api';
    case 'openai4':
      return windowConfig.openai4ApiBase || '/openai4/api';
    case 'openai5':
      return windowConfig.openai5ApiBase || '/openai5/api';
    case 'openai6':
      return windowConfig.openai6ApiBase || '/openai6/api';
    case 'openai7':
      return windowConfig.openai7ApiBase || windowConfig.openai6ApiBase || '/openai6/api';
    case 'grok2':
      return windowConfig.grok2Base || '/grok2';
    case 'paypalProtocol':
      return windowConfig.paypalProtocolApiBase || '/paypal-protocol/api';
    case 'cardPaymentPortal':
      return windowConfig.cardPaymentPortalApiBase || '/card-payment-api';
    case 'main':
    default:
      return windowConfig.mainApiBase || '/api';
  }
};

const getAuthHeaders = () => {
  const windowConfig = window.AUTOMYAI_RUNTIME_CONFIG || window.__RUNTIME_CONFIG__ || {};
  const headers = {
    'Content-Type': 'application/json',
  };

  if (windowConfig.authMode === 'header') {
    const pwd = sessionStorage.getItem('automyai_admin_password');
    if (pwd) {
      headers['X-Admin-Password'] = pwd;
    }
  }

  return headers;
};


function formatApiError(data, status) {
  if (typeof data === 'string' && data.trim()) return data;
  if (!data || typeof data !== 'object') return `HTTP Error ${status}`;

  const detail = data.detail ?? data.error ?? data.message;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const parts = detail.map((item) => {
      if (typeof item === 'string') return item;
      if (!item || typeof item !== 'object') return String(item);
      const loc = Array.isArray(item.loc) ? item.loc.filter((part) => part !== 'body').join('.') : '';
      const msg = item.msg || item.message || JSON.stringify(item);
      return loc ? `${loc}: ${msg}` : msg;
    }).filter(Boolean);
    if (parts.length) return parts.join('; ');
  }
  if (detail && typeof detail === 'object') {
    try { return JSON.stringify(detail); } catch (_) {}
  }
  return `HTTP Error ${status}`;
}

async function request(path, options = {}, service = 'main') {
  const baseUrl = getServiceBase(service);
  const url = path.startsWith('http') ? path : `${baseUrl}${path.startsWith('/') ? '' : '/'}${path}`;

  const defaultOptions = {
    credentials: 'include',
    headers: {
      ...getAuthHeaders(),
      ...(options.headers || {}),
    },
  };

  const finalOptions = {
    ...defaultOptions,
    ...options,
    headers: {
      ...defaultOptions.headers,
      ...(options.headers || {}),
    },
  };

  if (finalOptions.body && typeof finalOptions.body === 'object' && !(finalOptions.body instanceof FormData)) {
    finalOptions.body = JSON.stringify(finalOptions.body);
  }

  try {
    const response = await fetch(url, finalOptions);

    if (response.status === 401) {
      window.dispatchEvent(new CustomEvent('automyai-unauthorized'));
    }

    const contentType = response.headers.get('content-type') || '';
    let data = null;

    if (contentType.includes('application/json')) {
      data = await response.json();
    } else {
      data = await response.text();
    }

    if (!response.ok) {
      const errorMsg = formatApiError(data, response.status);
      const err = new Error(errorMsg);
      err.status = response.status;
      err.data = data;
      throw err;
    }

    return data;
  } catch (error) {
    console.error(`API Error [${service}:${path}]:`, error);
    throw error;
  }
}

async function requestBlob(path, options = {}, service = 'main') {
  const baseUrl = getServiceBase(service);
  const url = path.startsWith('http') ? path : `${baseUrl}${path.startsWith('/') ? '' : '/'}${path}`;
  const headers = { ...getAuthHeaders(), ...(options.headers || {}) };
  delete headers['Content-Type'];
  const response = await fetch(url, {
    credentials: 'include',
    ...options,
    headers,
  });
  if (response.status === 401) {
    window.dispatchEvent(new CustomEvent('automyai-unauthorized'));
  }
  if (!response.ok) {
    let message = `HTTP Error ${response.status}`;
    try {
      const data = await response.json();
      message = formatApiError(data, response.status);
    } catch (_) {}
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return response.blob();
}

export const apiClient = {
  get: (path, options = {}, service = 'main') => request(path, { ...options, method: 'GET' }, service),
  post: (path, body, options = {}, service = 'main') => request(path, { ...options, method: 'POST', body }, service),
  put: (path, body, options = {}, service = 'main') => request(path, { ...options, method: 'PUT', body }, service),
  patch: (path, body, options = {}, service = 'main') => request(path, { ...options, method: 'PATCH', body }, service),
  delete: (path, options = {}, service = 'main') => request(path, { ...options, method: 'DELETE' }, service),
  blob: (path, options = {}, service = 'main') => requestBlob(path, { ...options, method: 'GET' }, service),

  // Service helpers
  openai2: {
    get: (path, options = {}) => request(path, { ...options, method: 'GET' }, 'openai2'),
    post: (path, body, options = {}) => request(path, { ...options, method: 'POST', body }, 'openai2'),
  },
  openai3: {
    get: (path, options = {}) => request(path, { ...options, method: 'GET' }, 'openai3'),
    post: (path, body, options = {}) => request(path, { ...options, method: 'POST', body }, 'openai3'),
  },
  openai4: {
    get: (path, options = {}) => request(path, { ...options, method: 'GET' }, 'openai4'),
    post: (path, body, options = {}) => request(path, { ...options, method: 'POST', body }, 'openai4'),
  },
  openai5: {
    get: (path, options = {}) => request(path, { ...options, method: 'GET' }, 'openai5'),
    post: (path, body, options = {}) => request(path, { ...options, method: 'POST', body }, 'openai5'),
  },
  openai6: {
    get: (path, options = {}) => request(path, { ...options, method: 'GET' }, 'openai6'),
    post: (path, body, options = {}) => request(path, { ...options, method: 'POST', body }, 'openai6'),
    blob: (path, options = {}) => requestBlob(path, { ...options, method: 'GET' }, 'openai6'),
  },
  openai7: {
    get: (path, options = {}) => request(path, { ...options, method: 'GET' }, 'openai7'),
    post: (path, body, options = {}) => request(path, { ...options, method: 'POST', body }, 'openai7'),
    blob: (path, options = {}) => requestBlob(path, { ...options, method: 'GET' }, 'openai7'),
  },
  grok2: {
    get: (path, options = {}) => request(path, { ...options, method: 'GET' }, 'grok2'),
    post: (path, body, options = {}) => request(path, { ...options, method: 'POST', body }, 'grok2'),
  },
  paypalProtocol: {
    get: (path, options = {}) => request(path, { ...options, method: 'GET' }, 'paypalProtocol'),
    post: (path, body, options = {}) => request(path, { ...options, method: 'POST', body }, 'paypalProtocol'),
    delete: (path, options = {}) => request(path, { ...options, method: 'DELETE' }, 'paypalProtocol'),
  },
  cardPaymentPortal: {
    get: (path, options = {}) => request(path, { ...options, method: 'GET' }, 'cardPaymentPortal'),
    post: (path, body, options = {}) => request(path, { ...options, method: 'POST', body }, 'cardPaymentPortal'),
    patch: (path, body, options = {}) => request(path, { ...options, method: 'PATCH', body }, 'cardPaymentPortal'),
    delete: (path, options = {}) => request(path, { ...options, method: 'DELETE' }, 'cardPaymentPortal'),
  },

  // Auth specific methods
  getAuthStatus: () => apiClient.get('/auth/status'),
  login: (password) => apiClient.post('/auth/login', { password }),
  logout: () => apiClient.post('/auth/logout', {}),
};

export default apiClient;
