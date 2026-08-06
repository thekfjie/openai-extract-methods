/**
 * Apple Mail Full Auto (Automyai tools/apple_mail)
 * 基于 flowgpt_console.js 清洗版：iCloud / Apple Hide My Email -> ChatGPT 注册接码 / 导号
 *
 * 独立通道，不与 OpenAI 1/2/3 混用。
 * 指纹不过关时，优先改用 Firefox 147。
 *
 * 用法（在 ChatGPT 页面控制台）:
 *   AppleMail.config.adminAuth = 'MAIL_ADMIN_PASSWORD'
 *   await AppleMail.auto()
 *   await AppleMail.autoBatch(3)
 */
(() => {


  const DEFAULTS = {
    mailBase: (window.AppleMailConfig && window.AppleMailConfig.mailBase) || localStorage.getItem('AppleMail.mailBase') || 'https://apimail.kfjie.me',
    adminAuth: localStorage.getItem('AppleMail.adminAuth') || localStorage.getItem('FlowGPT.adminAuth') || '',
    importBase: (window.AppleMailConfig && window.AppleMailConfig.importBase) || localStorage.getItem('AppleMail.importBase') || 'https://cloud.opus.sryze.cc',
    importApiKey: localStorage.getItem('AppleMail.importApiKey') || localStorage.getItem('FlowGPT.importApiKey') || '',
    timeoutMs: 180000,
    intervalMs: 2500,
    skewMs: 10000,
    autoTickMs: 1200,
    autoMaxMs: 12 * 60 * 1000,
    senderAllow: ['openai.com', 'tm.openai.com', 'tm1.openai.com', 'noreply@', 'privaterelay', 'icloud.com'],
    subjectAllow: ['verify', 'verification', 'code', 'confirm', '验证码', 'temporary'],
    billingChannelOverride: '',
    manualPlus: false,
    autoFlag: true,
    defaultPassword: localStorage.getItem('AppleMail.password') || localStorage.getItem('FlowGPT.password') || '',
    fingerprintBrowser: 'firefox',
    fingerprintVersion: '147',
    requireProxy: true,
    proxyUrl: (window.AppleMailConfig && window.AppleMailConfig.proxyUrl) || 'http://172.19.0.1:7905',
  };


  const EMAILS = Array.isArray(window.AppleMailEmails) && window.AppleMailEmails.length
    ? window.AppleMailEmails.slice()
    : (() => {
        try { const raw = localStorage.getItem('AppleMail.emails'); const arr = raw ? JSON.parse(raw) : []; return Array.isArray(arr) ? arr : []; }
        catch { return []; }
      })();

  const JP_NAMES = Array.isArray(window.AppleMailNames) && window.AppleMailNames.length
    ? window.AppleMailNames.slice()
    : (() => {
        try { const raw = localStorage.getItem('AppleMail.names'); const arr = raw ? JSON.parse(raw) : []; return Array.isArray(arr) ? arr : []; }
        catch { return []; }
      })();

  const state = {
    cursor: Number(localStorage.getItem('AppleMail.cursor') || localStorage.getItem('FlowGPT.cursor') || 0) || 0,
    lastProfile: null,
    lastCode: null,
    lastMail: null,
    lastSender: null,
    lastSession: null,
    lastImport: null,
    sentAt: 0,
    auto: { running: false, stop: false, phase: 'idle', startedAt: 0, codeRequested: false, codeFilled: false, imported: false, logs: [] },
  };

  const CFG = new Proxy({ ...DEFAULTS }, {
    set(obj, key, value) {
      obj[key] = value;
      if (key === 'adminAuth') localStorage.setItem('FlowGPT.adminAuth', String(value || ''));
      if (key === 'importApiKey') localStorage.setItem('FlowGPT.importApiKey', String(value || ''));
      if (key === 'defaultPassword') localStorage.setItem('FlowGPT.password', String(value || ''));
      return true;
    }
  });

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const log = (msg, level = 'info') => {
    const line = `[${new Date().toLocaleTimeString()}] ${msg}`;
    state.auto.logs.push(line);
    (level === 'error' ? console.error : level === 'warn' ? console.warn : console.log)(line);
  };

  function adminHeaders() {
    if (!CFG.adminAuth) throw new Error('Set FlowGPT.config.adminAuth first');
    return { Accept: 'application/json', 'x-admin-auth': CFG.adminAuth };
  }
  function importHeaders() {
    return {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      'X-API-Key': CFG.importApiKey,
      Authorization: 'Bearer ' + CFG.importApiKey,
    };
  }
  function joinMail(path) { return CFG.mailBase.replace(/\/+$/, '') + path; }

  async function listMails(address, limit = 20, offset = 0) {
    const url = joinMail('/admin/mails?limit=' + limit + '&offset=' + offset + '&address=' + encodeURIComponent(address));
    const r = await fetch(url, { headers: adminHeaders() });
    const text = await r.text();
    if (!r.ok) throw new Error('listMails HTTP ' + r.status + ': ' + text.slice(0, 200));
    const data = JSON.parse(text);
    const results = Array.isArray(data.results) ? data.results
      : Array.isArray(data.data) ? data.data
      : Array.isArray(data.messages) ? data.messages
      : Array.isArray(data) ? data : [];
    return { raw: data, results };
  }

  function parseTime(v) {
    if (v == null || v === '') return 0;
    if (typeof v === 'number') return v < 1e12 ? v * 1000 : v;
    const s = String(v).trim();
    let t = Date.parse(s);
    if (!Number.isNaN(t)) return t;
    t = Date.parse(s.replace(' ', 'T') + 'Z');
    return Number.isNaN(t) ? 0 : t;
  }
  function mailTimestamp(item = {}) {
    for (const c of [item.created_at, item.createdAt, item.date, item.time, item.timestamp, item.received_at, item.receiveTime, item.receivedAt]) {
      const t = parseTime(c); if (t) return t;
    }
    const m = String(item.raw || '').match(/^Date:\s*(.+)$/im);
    if (m) return parseTime(m[1].replace(/\(UTC\)/ig, '').trim());
    return 0;
  }
  function getSubject(item = {}) {
    if (item.subject) return String(item.subject);
    if (item.decodedSubject) return String(item.decodedSubject);
    const m = String(item.raw || '').match(/^Subject:\s*(.+)$/im);
    return m ? m[1].trim() : '';
  }
  function getSender(item = {}) {
    // Prefer OpenAI Hide-My-Email relay From, never postmaster@outbound.*
    const raw = String(item.raw || '');
    const hme = raw.match(/((?:noreply|otp)_at_tm[0-9]*_openai_com_[A-Za-z0-9_]+@icloud\.com)/i);
    if (hme && hme[1]) return hme[1].trim().toLowerCase();
    for (const re of [/^From:\s*.*?<([^>]+)>/im, /^From:\s*([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})/im, /^Reply-To:\s*<?([^>\s]+@[^>\s]+)>?/im]) {
      const m = raw.match(re);
      if (m && m[1] && !/postmaster@outbound\./i.test(m[1]) && !/bounces\+/i.test(m[1])) return m[1].trim().toLowerCase();
    }
    for (const c of [item.from, item.mail_from, item.mailFrom, item.sender, item.envelopeFrom, item.source]) {
      const s = String(c || '').trim();
      if (!s) continue;
      if (/postmaster@outbound\./i.test(s)) continue;
      const m = s.match(/[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}/i);
      const val = (m ? m[0] : s).toLowerCase();
      if (val) return val;
    }
    return '';
  }
  function getText(item = {}) {
    const parts = [];
    for (const k of ['decodedText', 'text', 'content', 'body', 'html', 'intro', 'snippet', 'raw']) {
      const v = item[k];
      if (typeof v === 'string' && v.trim()) parts.push(v);
    }
    return parts.join('\n').replace(/<[^>]+>/g, ' ');
  }
  function extractCode(text = '', subject = '') {
    const src = subject + '\n' + text;
    for (const p of [/(?:verification code|temporary code|验证码|临时验证码|输入此临时验证码以继续)[^\d]{0,40}(\d{6})/i, /\b(\d{6})\b/, /(?:code)[:\s]+(\d{4,8})/i]) {
      const m = src.match(p); if (m) return m[1];
    }
    return null;
  }
  function looksLikeOpenAI(item) {
    const blob = (getSender(item) + '\n' + getSubject(item) + '\n' + getText(item)).toLowerCase();
    return CFG.senderAllow.some(x => blob.includes(String(x).toLowerCase()))
      || CFG.subjectAllow.some(x => blob.includes(String(x).toLowerCase()))
      || /\b\d{6}\b/.test(blob);
  }

  function next(index) {
    const i = (index == null) ? state.cursor : Number(index);
    if (!EMAILS.length) throw new Error('AppleMail email pool is empty. Set window.AppleMailEmails or localStorage AppleMail.emails');
    if (i < 0 || i >= EMAILS.length) throw new Error('email index out of range: ' + i);
    const email = EMAILS[i];
    const name = JP_NAMES[i % JP_NAMES.length];
    const parts = String(name).split(/\s+/);
    const profile = {
      index: i, email, name,
      lastName: parts[0] || name,
      firstName: parts.slice(1).join(' ') || name,
      password: CFG.defaultPassword,
    };
    state.cursor = Math.min(i + 1, EMAILS.length - 1);
    localStorage.setItem('AppleMail.cursor', String(state.cursor));
    state.lastProfile = profile;
    state.lastCode = null; state.lastMail = null; state.lastSender = null; state.lastSession = null; state.lastImport = null; state.sentAt = 0;
    state.auto.codeRequested = false; state.auto.codeFilled = false; state.auto.imported = false;
    log('[PROFILE] ' + profile.email + ' / ' + profile.name);
    return profile;
  }

  async function waitCode(address, sentAt) {
    const email = address || state.lastProfile?.email;
    if (!email) throw new Error('no email');
    const start = sentAt || state.sentAt || Date.now();
    state.sentAt = start;
    const minTs = start - CFG.skewMs;
    const deadline = Date.now() + CFG.timeoutMs;
    log('[WAIT CODE] ' + email);
    while (Date.now() < deadline) {
      if (state.auto.stop) throw new Error('auto stopped');
      const { results } = await listMails(email, 20, 0);
      const candidates = results.map(m => ({ ...m, _ts: mailTimestamp(m) }))
        .filter(m => !m._ts || m._ts >= minTs)
        .filter(m => looksLikeOpenAI(m))
        .sort((a,b) => (b._ts||0)-(a._ts||0));
      log('[POLL] total=' + results.length + ' fresh=' + candidates.length);
      for (const m of candidates) {
        const subject = getSubject(m), text = getText(m), code = extractCode(text, subject);
        if (!code) continue;
        const sender = getSender(m);
        state.lastCode = { email, code, sender, subject, id: m.id, item: m };
        state.lastMail = m; state.lastSender = sender;
        log('[FOUND CODE] ' + code + ' sender=' + sender);
        return code;
      }
      await sleep(CFG.intervalMs);
    }
    throw new Error('timeout waiting code');
  }
  async function afterSend(address) { state.sentAt = Date.now(); state.auto.codeRequested = true; return waitCode(address || state.lastProfile?.email, state.sentAt); }

  async function latestSender(address) {
    const email = address || state.lastProfile?.email;
    const { results } = await listMails(email, 10, 0);
    if (!results.length) return null;
    const sorted = results.map(m => ({ ...m, _ts: mailTimestamp(m) })).sort((a,b)=>(b._ts||0)-(a._ts||0));
    const preferred = sorted.find(m => looksLikeOpenAI(m)) || sorted[0];
    const sender = getSender(preferred);
    state.lastMail = preferred; state.lastSender = sender;
    log('[LATEST SENDER] ' + sender);
    return { email, sender, subject: getSubject(preferred), id: preferred.id, sourceRaw: preferred.source || null };
  }

  function qsa(sel){ return Array.from(document.querySelectorAll(sel)); }
  function visible(el){ if(!el) return false; const st=getComputedStyle(el); if(st.display==='none'||st.visibility==='hidden'||st.opacity==='0') return false; const r=el.getBoundingClientRect(); return r.width>0&&r.height>0; }
  function firstVisible(selectors){ for(const sel of selectors){ const el=qsa(sel).find(visible); if(el) return el; } return null; }
  function setVal(el, value){
    if(!el) return false; el.focus();
    const proto = el.tagName==='TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const desc = Object.getOwnPropertyDescriptor(proto, 'value');
    if(desc && desc.set) desc.set.call(el, value); else el.value = value;
    el.dispatchEvent(new Event('input', { bubbles:true }));
    el.dispatchEvent(new Event('change', { bubbles:true }));
    return true;
  }
  function clickText(texts=[]) {
    const wants = texts.map(t => String(t).toLowerCase());
    for (const el of qsa('button,[role=button],input[type=submit],a')) {
      if (!visible(el)) continue;
      const t = ((el.innerText || el.value || el.getAttribute('aria-label') || '') + '').trim().toLowerCase();
      if (!t) continue;
      if (wants.some(w => t === w || t.includes(w))) { el.click(); return t; }
    }
    return '';
  }
  function detectPage() {
    const url = location.href;
    const hasEmail = !!firstVisible(['input[type=email]','input[name=email]','input[autocomplete=email]']);
    const hasPassword = !!firstVisible(['input[name=new-password]','input[autocomplete=new-password]','input[type=password]']);
    const hasCode = !!firstVisible(['input[name=code]','input[autocomplete=one-time-code]','input[inputmode=numeric]']);
    const hasName = !!firstVisible(['input[name=name]','input[autocomplete=name]']);
    const hasAge = !!firstVisible(['input[name=age]','input[autocomplete=bday]']);
    if (/chatgpt\.com|chat\.openai\.com/i.test(url) && !/auth|login|signup/i.test(url)) return 'chatgpt-home';
    if (hasCode) return 'code';
    if (hasName || hasAge) return 'profile';
    if (hasPassword && !hasEmail) return 'password';
    if (hasEmail) return 'email';
    if (/auth\.openai\.com|signup|log-in|login/i.test(url)) return 'auth';
    return 'unknown';
  }
  function fill(profile = state.lastProfile) {
    if (!profile) throw new Error('call next() first');
    let n = 0;
    n += setVal(firstVisible(['input[type=email]','input[name=email]','input[autocomplete=email]']), profile.email) ? 1 : 0;
    n += setVal(firstVisible(['input[name=name]','input[autocomplete=name]']), profile.name) ? 1 : 0;
    n += setVal(firstVisible(['input[name=new-password]','input[autocomplete=new-password]','input[type=password]']), profile.password) ? 1 : 0;
    n += setVal(firstVisible(['input[name=age]','input[autocomplete=bday]']), '22') ? 1 : 0;
    log('[FILL] fields=' + n); return n;
  }
  function fillCode(code = state.lastCode?.code) {
    if (!code) throw new Error('no code');
    const el = firstVisible(['input[name=code]','input[autocomplete=one-time-code]','input[inputmode=numeric]']);
    if (!el) throw new Error('code input not found');
    setVal(el, code); state.auto.codeFilled = true; log('[FILL CODE] ' + code); return code;
  }

  function captureSession() {
    const out = { accessToken:'', sessionToken:'', user:null, account:null, expires:'', raw:{} };
    const cookies = Object.fromEntries(document.cookie.split(';').map(x=>x.trim().split('=')).filter(x=>x[0]).map(([k,...v])=>[k, decodeURIComponent(v.join('=')||'')]));
    out.sessionToken = cookies['__Secure-next-auth.session-token'] || cookies['next-auth.session-token'] || '';
    for (const store of [localStorage, sessionStorage]) {
      try {
        for (let i=0;i<store.length;i++){
          const key=store.key(i), val=store.getItem(key)||'';
          if (!out.accessToken && /accessToken|access_token/i.test(key+val)) {
            try {
              const j=JSON.parse(val);
              out.accessToken = j.accessToken || j.access_token || out.accessToken;
              out.sessionToken = out.sessionToken || j.sessionToken || '';
              out.user = out.user || j.user || null;
              out.account = out.account || j.account || null;
              out.expires = out.expires || j.expires || '';
            } catch {
              const m = val.match(/eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+/);
              if (m) out.accessToken = out.accessToken || m[0];
            }
          }
        }
      } catch {}
    }
    state.lastSession = out; return out;
  }
  async function fetchSessionFromApi() {
    for (const url of ['/api/auth/session','https://chatgpt.com/api/auth/session','https://chat.openai.com/api/auth/session']) {
      try {
        const r = await fetch(url, { credentials:'include' });
        if (!r.ok) continue;
        const j = await r.json();
        if (!j || (!j.accessToken && !j.user)) continue;
        const cap = captureSession();
        const session = {
          WARNING_BANNER: j.WARNING_BANNER || '',
          user: j.user || null,
          expires: j.expires || '',
          account: j.account || null,
          accessToken: j.accessToken || '',
          authProvider: j.authProvider || '',
          sessionToken: cap.sessionToken || '',
          rumViewTags: j.rumViewTags || null,
          raw: j,
        };
        state.lastSession = session;
        log('[SESSION] ' + (session.user?.email || '') + ' token=' + Boolean(session.accessToken));
        return session;
      } catch {}
    }
    return captureSession();
  }

  function buildImportPayload(options = {}) {
    const profile = options.profile || state.lastProfile || {};
    const email = options.email || profile.email;
    if (!email) throw new Error('missing email');
    // ChatGPT signup password is separate; Opus mailbox login password is auto-generated by Opus.
    const sender = options.sender || state.lastSender || '';
    const session = options.session || state.lastSession || null;
    const accessToken = session?.accessToken || '';
    const sessionToken = session?.sessionToken || '';
    const credential = options.credential || (sessionToken ? (email + '---' + sessionToken) : (accessToken ? (email + '---' + accessToken) : ''));
    const payload = {
      email,
      // Opus mailbox password is auto-generated server-side (do not send password).
      address: sender, sender, mailFrom: sender, from: sender,
      sourceEmail: sender || undefined,
      toEmail: email,
      note: ['sender=' + sender, profile.name ? ('name=' + profile.name) : '', session?.user?.id ? ('user=' + session.user.id) : ''].filter(Boolean).join(' | '),
      billingChannelOverride: options.billingChannelOverride ?? CFG.billingChannelOverride,
      manualPlus: options.manualPlus ?? CFG.manualPlus,
      sold: false,
      autoFlag: true,
    };
    if (!payload.sourceEmail) delete payload.sourceEmail;
    if (credential) payload.credential = credential;
    if (sessionToken) payload.sessionToken = sessionToken;
    if (accessToken) payload.accessToken = accessToken;
    if (session) { payload.session = session; payload.sessionJson = JSON.stringify(session); payload.chatgptSession = session; }
    return payload;
  }

  async function importAccount(options = {}) {
    if (!(options.sender || state.lastSender)) await latestSender(options.email || state.lastProfile?.email);
    if (!(options.session || state.lastSession)) await fetchSessionFromApi();
    const payload = buildImportPayload(options);
    log('[IMPORT] ' + payload.email + ' address=' + payload.address);
    const r = await fetch(CFG.importBase.replace(/\/+$/, '') + '/api/v1/accounts', {
      method: 'POST', headers: importHeaders(), body: JSON.stringify(payload),
    });
    const text = await r.text();
    let data; try { data = JSON.parse(text); } catch { data = { raw: text }; }
    if (!r.ok) throw new Error('import failed HTTP ' + r.status + ': ' + text.slice(0, 300));
    state.lastImport = data; state.auto.imported = true;
    log('[IMPORTED] ok'); console.log(data); return data;
  }
  async function finishAndImport(options = {}) {
    const email = options.email || state.lastProfile?.email;
    const senderInfo = await latestSender(email);
    const session = await fetchSessionFromApi();
    return importAccount({ ...options, email, sender: senderInfo?.sender || state.lastSender, session });
  }

  async function autoStepOnce(profile) {
    const page = detectPage();
    state.auto.phase = page;
    log('[AUTO PAGE] ' + page);
    if (page === 'email') {
      fill(profile);
      const clicked = clickText(['continue','继续','next','下一步','sign up','signup','create account']);
      if (clicked) { state.sentAt = Date.now(); state.auto.codeRequested = true; log('[AUTO] clicked ' + clicked); }
      return 'email';
    }
    if (page === 'password') { fill(profile); clickText(['continue','继续','next','下一步']); return 'password'; }
    if (page === 'code') {
      if (!state.auto.codeRequested) { state.sentAt = state.sentAt || (Date.now() - 15000); state.auto.codeRequested = true; }
      if (!state.lastCode?.code) await waitCode(profile.email, state.sentAt);
      if (state.lastCode?.code) { fillCode(state.lastCode.code); clickText(['continue','继续','next','verify','验证']); }
      return 'code';
    }
    if (page === 'profile') { fill(profile); clickText(['continue','继续','next','done','完成']); return 'profile'; }
    if (page === 'chatgpt-home') {
      if (!state.auto.imported) await finishAndImport({ email: profile.email });
      return 'done';
    }
    clickText(['continue','继续','sign up','create account','signup','next']);
    return page;
  }

  async function auto(options = {}) {
    if (state.auto.running) throw new Error('auto already running');
    if (!CFG.adminAuth) throw new Error('Set FlowGPT.config.adminAuth first');
    const profile = options.profile || next(options.index);
    state.auto.running = true; state.auto.stop = false; state.auto.startedAt = Date.now();
    log('[AUTO START] ' + profile.email);
    try {
      while (!state.auto.stop && Date.now() - state.auto.startedAt < CFG.autoMaxMs) {
        const result = await autoStepOnce(profile);
        if (result === 'done' && state.auto.imported) {
          log('[AUTO DONE]');
          return { profile, code: state.lastCode, sender: state.lastSender, session: state.lastSession, importResult: state.lastImport };
        }
        await sleep(CFG.autoTickMs);
      }
      if (state.auto.stop) throw new Error('auto stopped by user');
      throw new Error('auto timeout, phase=' + state.auto.phase);
    } finally { state.auto.running = false; }
  }
  function stop(){ state.auto.stop = true; log('[AUTO STOP]'); }

  async function autoBatch(count = 1, startIndex = null) {
    const results = [];
    let idx = startIndex == null ? state.cursor : startIndex;
    for (let i=0;i<count;i++) {
      if (state.auto.stop) break;
      log('[BATCH] ' + (i+1) + '/' + count + ' index=' + idx);
      try { results.push({ ok:true, ...(await auto({ index: idx })) }); }
      catch(e){ results.push({ ok:false, index: idx, error: String(e) }); log('[BATCH FAIL] ' + e, 'error'); }
      idx += 1; await sleep(2000);
    }
    console.log('[BATCH RESULT]', results); return results;
  }

  async function debug(address) {
    const email = address || state.lastProfile?.email || EMAILS[0];
    const res = await listMails(email, 5, 0);
    const enriched = (res.results||[]).map(m => ({ id:m.id, created_at:m.created_at, sender:getSender(m), subject:getSubject(m), source:m.source }));
    console.log('[DEBUG]', email, enriched); return { email, enriched, res };
  }


  const API = {
    __loaded: true,
    __channel: 'apple-mail',
    config: CFG,
    emails: EMAILS,
    names: JP_NAMES,
    state,
    next, afterSend, waitCode, latestSender, captureSession, fetchSessionFromApi,
    buildImportPayload, importAccount, finishAndImport, fill, fillCode, detectPage,
    auto, autoBatch, stop, debug, listMails,
    fingerprintTip() {
      return {
        preferredBrowser: CFG.fingerprintBrowser || 'firefox',
        preferredVersion: CFG.fingerprintVersion || '147',
        message: '指纹不过关时，优先改用 Firefox 147，很大概率解决。',
      };
    },
    saveConfig() {
      try {
        if (CFG.adminAuth) localStorage.setItem('AppleMail.adminAuth', CFG.adminAuth);
        if (CFG.importApiKey) localStorage.setItem('AppleMail.importApiKey', CFG.importApiKey);
        if (CFG.defaultPassword) localStorage.setItem('AppleMail.password', CFG.defaultPassword);
        if (CFG.mailBase) localStorage.setItem('AppleMail.mailBase', CFG.mailBase);
        if (CFG.importBase) localStorage.setItem('AppleMail.importBase', CFG.importBase);
      } catch {}
    },
  };
  window.AppleMail = API;
  window.FlowGPT = API; // 兼容原脚本调用名

  console.log('%cApple Mail FULL AUTO ready | emails=' + EMAILS.length + ' names=' + JP_NAMES.length, 'color:#0a7;font-weight:bold');
  console.log('%c指纹建议: Firefox 147（不过关时优先切换）', 'color:#c60;font-weight:bold');
  console.log('1) AppleMail.config.adminAuth = "MAIL_ADMIN_PASSWORD"');
  console.log('2) AppleMail.config.importApiKey = "IMPORT_API_KEY"  // 可选');
  console.log('3) await AppleMail.auto()');
  console.log('4) batch: await AppleMail.autoBatch(3)');
  console.log('stop: AppleMail.stop()');
})();
