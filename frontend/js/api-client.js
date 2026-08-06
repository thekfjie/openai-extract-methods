(() => {
  const config = window.AUTOMYAI_RUNTIME_CONFIG || {};
  const HEADER_PASSWORD_KEY = 'automyai.session.adminPassword';

  function cleanBase(value) {
    return String(value || '').replace(/\/+$/, '');
  }

  function cleanPath(value) {
    const path = String(value || '');
    return path ? (path.startsWith('/') ? path : `/${path}`) : '';
  }

  function join(base, path) {
    if (/^https?:\/\//i.test(String(path || ''))) return String(path);
    return cleanBase(base) + cleanPath(path);
  }

  const bases = Object.freeze({
    main: cleanBase(config.mainApiBase || '/api'),
    openai2: cleanBase(config.openai2ApiBase || '/openai2/api'),
    openai3: cleanBase(config.openai3ApiBase || '/openai3/api'),
    openai4: cleanBase(config.openai4ApiBase || '/openai4/api'),
    grok2: cleanBase(config.grok2Base || '/grok2'),
  });
  const uiBases = Object.freeze({
    main: cleanBase(config.uiBase || ''),
    openai2: cleanBase(config.openai2UiBase || '/openai2'),
    openai3: cleanBase(config.openai3UiBase || '/openai3'),
    openai4: cleanBase(config.openai4UiBase || '/openai4'),
    grok2: bases.grok2,
  });

  function authPageURL(reason = '') {
    const uiBase = cleanBase(config.uiBase || '');
    const current = `${location.pathname}${location.search}${location.hash}`;
    const params = new URLSearchParams({ next: current });
    if (reason) params.set('reason', reason);
    return `${uiBase}/auth/login.html?${params.toString()}`;
  }

  function redirectToLogin(reason = '') {
    if (location.pathname.endsWith('/auth/login.html')) return;
    location.assign(authPageURL(reason));
  }

  function headerPassword() {
    if (config.authMode !== 'header') return '';
    try { return sessionStorage.getItem(HEADER_PASSWORD_KEY) || ''; }
    catch { return ''; }
  }

  async function request(service, method, path, body, options = {}) {
    const base = bases[service];
    if (!base) throw new Error(`Unknown API service: ${service}`);
    const controller = new AbortController();
    const timeout = Number(options.timeoutMs || config.requestTimeoutMs || 30000);
    const timer = setTimeout(() => controller.abort(), Math.max(1000, timeout));
    const headers = { Accept: 'application/json', ...(options.headers || {}) };
    const password = headerPassword();
    if (password) headers['X-Admin-Password'] = password;
    const init = {
      method,
      headers,
      credentials: options.credentials || 'include',
      signal: controller.signal,
    };
    if (body !== undefined) {
      headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
    try {
      const response = await fetch(join(base, path), init);
      const text = await response.text();
      let payload = null;
      try { payload = text ? JSON.parse(text) : null; }
      catch { payload = { raw: text }; }
      if (response.status === 401 && config.autoRedirectOnUnauthorized !== false && options.redirectOnUnauthorized !== false) {
        redirectToLogin(payload?.error || payload?.message || '需要登录');
      }
      if (!response.ok) {
        const error = new Error(payload?.detail || payload?.error || payload?.message || `HTTP ${response.status}`);
        error.status = response.status;
        error.payload = payload;
        throw error;
      }
      return payload;
    } catch (error) {
      if (error?.name === 'AbortError') throw new Error(`请求超时（${timeout}ms）`);
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }

  async function requestBlob(service, path, options = {}) {
    const base = bases[service];
    if (!base) throw new Error(`Unknown API service: ${service}`);
    const controller = new AbortController();
    const timeout = Number(options.timeoutMs || config.requestTimeoutMs || 30000);
    const timer = setTimeout(() => controller.abort(), Math.max(1000, timeout));
    const headers = { ...(options.headers || {}) };
    const password = headerPassword();
    if (password) headers['X-Admin-Password'] = password;
    try {
      const response = await fetch(join(base, path), {
        method: 'GET',
        headers,
        credentials: options.credentials || 'include',
        signal: controller.signal,
      });
      if (response.status === 401 && config.autoRedirectOnUnauthorized !== false && options.redirectOnUnauthorized !== false) {
        redirectToLogin('需要登录');
      }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.blob();
    } catch (error) {
      if (error?.name === 'AbortError') throw new Error(`请求超时（${timeout}ms）`);
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }

  function service(name) {
    return Object.freeze({
      baseURL: bases[name],
      url: (path = '') => join(bases[name], path),
      request: (method, path, body, options) => request(name, method, path, body, options),
      get: (path, options) => request(name, 'GET', path, undefined, options),
      post: (path, body, options) => request(name, 'POST', path, body, options),
      delete: (path, body, options) => request(name, 'DELETE', path, body, options),
      blob: (path, options) => requestBlob(name, path, options),
    });
  }

  const main = service('main');
  const openai2 = service('openai2');
  const openai3 = service('openai3');
  const openai4 = service('openai4');
  const grok2 = service('grok2');

  async function login(password) {
    const value = String(password || '');
    if (config.authMode === 'header') {
      try { sessionStorage.setItem(HEADER_PASSWORD_KEY, value); }
      catch { throw new Error('浏览器不允许写入 sessionStorage'); }
      try {
        const status = await main.get('/auth/status', { redirectOnUnauthorized: false });
        if (!status?.authenticated) throw new Error('管理员密码错误');
        return status;
      } catch (error) {
        try { sessionStorage.removeItem(HEADER_PASSWORD_KEY); } catch {}
        throw error;
      }
    }
    return main.post('/auth/login', { password: value }, { redirectOnUnauthorized: false });
  }

  async function logout() {
    if (config.authMode === 'header') {
      try { sessionStorage.removeItem(HEADER_PASSWORD_KEY); } catch {}
      return { authenticated: false };
    }
    return main.post('/auth/logout', {}, { redirectOnUnauthorized: false });
  }

  function uiURL(path = '') {
    return join(config.uiBase || '', path);
  }

  async function fetchAsset(path, responseType = 'text') {
    const response = await fetch(uiURL(path), { credentials: 'include' });
    if (!response.ok) throw new Error(`静态资源读取失败：${response.status}`);
    return responseType === 'json' ? response.json() : response.text();
  }

  window.AutoMyAIAPI = Object.freeze({
    config,
    bases,
    uiBases,
    main,
    openai2,
    openai3,
    openai4,
    grok2,
    login,
    logout,
    uiURL,
    fetchAsset,
    redirectToLogin,
  });
})();
