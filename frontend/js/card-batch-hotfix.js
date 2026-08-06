(() => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const api = async (path, options = {}) => {
    const response = await fetch('/card-payment-api' + path, { credentials: 'include', ...options });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false || data.error) throw new Error(data.error || data.message || `HTTP ${response.status}`);
    return data;
  };
  const post = (path, body) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const tokens = () => {
    const raw = document.querySelector('.classic-at-section textarea')?.value || '';
    const seen = new Set();
    return (raw.match(/[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g) || []).filter((token) => !seen.has(token) && seen.add(token));
  };
  const proxyPool = () => String(document.querySelector('.classic-proxy-stack textarea')?.value || '').replace(/\r/g, '').split('\n').map((item) => item.trim()).filter(Boolean);
  const billing = () => {
    try {
      const item = JSON.parse(localStorage.getItem('automyai.payment.us-tax-free-address.v1') || '{}');
      const profile = item.profile || {}, address = item.address || {};
      return { name: profile.name || '', email: profile.email || '', phone: profile.phone || '', address: { line1: address.line1 || '', line2: address.line2 || '', city: address.city || '', state: address.state || '', postal_code: address.postalCode || address.postal_code || '', country: String(address.country || 'US').toUpperCase() } };
    } catch (_) { return {}; }
  };
  const migrateCurrentPage = () => {
    if (document.querySelector('[data-payment-mode="synchronized-batch"]')) return;
    const allTokens = tokens();
    const proxyAreas = [...document.querySelectorAll('.classic-proxy-stack textarea')];
    const details = billing();
    const address = details.address || {};
    const form = {
      accessToken: document.querySelector('.classic-at-section textarea')?.value || '',
      proxyPool: proxyAreas[0]?.value || '', proxyPool2: proxyAreas[1]?.value || '',
      billingName: details.name || '', billingEmail: details.email || '', billingPhone: details.phone || '',
      billingLine1: address.line1 || '', billingLine2: address.line2 || '', billingCity: address.city || '',
      billingState: address.state || '', billingPostalCode: address.postal_code || '', billingCountry: address.country || 'US',
    };
    try { localStorage.setItem('automyai.card.unified.protocol-form.v1', JSON.stringify(form)); } catch (_) {}
    const taskResults = [...document.querySelectorAll('.classic-result-row')].map((row) => {
      const index = Math.max(0, Number(row.querySelector(':scope > i')?.textContent || 1) - 1);
      const text = row.textContent || '';
      const link = row.querySelector('a[href*="/checkout/"]')?.href || '';
      const failed = row.classList.contains('status-failed');
      return {
        index, token: allTokens[index] || '', email: row.querySelector(':scope > span > b')?.textContent || `账号 ${index + 1}`,
        status: link ? 'done' : failed ? 'failed' : 'waiting', detail: row.querySelector(':scope > span > small')?.textContent || '已从刷新前页面恢复',
        link, error: '', paymentStatus: text.includes('支付完成') ? '支付完成' : text.includes('需要额外验证') ? '需要额外验证' : '',
        paymentError: '', bindSucceeded: Boolean(link) || text.includes('绑卡已保留'), recordId: '', paymentMethodId: '',
        failureStage: failed && text.includes('提链') ? '生成 Checkout 提链' : '', retrying: false,
      };
    }).filter((row) => row.token);
    if (!allTokens.length || !taskResults.length) return;
    try {
      localStorage.setItem('automyai.card.unified.card-flow-state.v2', JSON.stringify({
        tokenSignature: allTokens.join('\n'), session: null, phase: taskResults.some((row) => row.status === 'done') ? 'done' : 'input',
        message: '已从刷新前页面恢复账号结果；最终支付统一使用同步批量模式。', taskResults,
      }));
    } catch (_) {}
  };
  migrateCurrentPage();
  if (document.querySelector('[data-payment-mode="synchronized-batch"]')) return;
  if (window.__automyaiBatchPayHotfix) return;
  window.__automyaiBatchPayHotfix = true;
  const rowStatus = (row, text, tone = '') => {
    const small = row.querySelector('span small');
    if (small) small.textContent = `${small.dataset.batchBase || (small.dataset.batchBase = small.textContent)} · ${text}`;
    row.dataset.batchPayment = tone || 'running';
  };
  const poll = async (jobID, allowPrepared, row) => {
    for (let index = 0; index < 600; index += 1) {
      const data = await api('/protocol-pay/jobs/' + encodeURIComponent(jobID));
      const job = data.job || {};
      rowStatus(row, `${job.stage || '协议处理中'} ${Number(job.progress || 0)}%`);
      if (allowPrepared && job.status === 'prepared') return job;
      if (['ready', 'verification_required', 'error', 'cancelled'].includes(job.status)) return job;
      await sleep(1000);
    }
    throw new Error('协议支付状态查询超时');
  };
  const run = async (button) => {
    const allTokens = tokens();
    const proxies = proxyPool();
    const rows = [...document.querySelectorAll('.classic-result-row')].map((row) => {
      const index = Math.max(0, Number(row.querySelector(':scope > i')?.textContent || 1) - 1);
      return { row, index, token: allTokens[index] || '', link: row.querySelector('a[href*="/checkout/"]')?.href || '' };
    }).filter((item) => item.token && item.link && !['支付完成', '需要额外验证'].some((text) => item.row.textContent.includes(text)));
    if (!rows.length) throw new Error('当前页面没有可支付的已提链账号');
    if (!proxies.length) throw new Error('当前页面没有 US 代理池');
    button.disabled = true; button.textContent = `并发准备 ${rows.length} 个账号…`;
    const preparedResults = await Promise.all(rows.map(async (item) => {
      try {
        rowStatus(item.row, '并发准备支付');
        const created = await post('/protocol-pay/jobs', { access_token: item.token, checkout_url: item.link, proxy_pool: proxies, defer_confirm: true, billing_details: billing() });
        const jobID = created.job?.id;
        if (!jobID) throw new Error('任务未返回 ID');
        const state = await poll(jobID, true, item.row);
        if (state.status !== 'prepared') throw new Error(state.error || state.message || '支付准备失败');
        rowStatus(item.row, '准备完成，等待统一放行', 'prepared');
        return { ...item, jobID };
      } catch (error) {
        rowStatus(item.row, '准备失败：' + error.message, 'failed');
        return null;
      }
    }));
    const prepared = preparedResults.filter(Boolean);
    if (!prepared.length) throw new Error('全部账号支付准备失败');
    button.textContent = `统一放行 ${prepared.length} 个账号…`;
    prepared.forEach((item) => rowStatus(item.row, '已统一放行，正在同时支付'));
    await post('/protocol-pay/batch-confirm', { job_ids: prepared.map((item) => item.jobID), burst_count: 1 });
    const finals = await Promise.all(prepared.map(async (item) => {
      try {
        const state = await poll(item.jobID, false, item.row);
        if (state.status === 'ready') { rowStatus(item.row, '支付完成', 'success'); return 'success'; }
        if (state.status === 'verification_required') { rowStatus(item.row, '需要额外验证', 'warning'); return 'verification'; }
        throw new Error(state.error || state.message || '支付失败');
      } catch (error) { rowStatus(item.row, '支付失败：' + error.message, 'failed'); return 'failed'; }
    }));
    const success = finals.filter((item) => item === 'success').length;
    const verification = finals.filter((item) => item === 'verification').length;
    const failed = rows.length - success - verification;
    button.textContent = `完成：成功 ${success} / 验证 ${verification} / 失败 ${failed}`;
    button.disabled = false;
  };
  const host = document.querySelector('.classic-final-pay');
  if (!host) { alert('当前页面没有找到最后支付区域'); return; }
  const button = document.createElement('button');
  button.type = 'button'; button.className = 'btn-primary'; button.style.cssText = 'min-height:40px;padding:.55rem .8rem;border-radius:9px;font-weight:700';
  button.textContent = '当前页面：同步批量支付';
  button.addEventListener('click', () => run(button).catch((error) => { button.disabled = false; button.textContent = '同步批量支付失败：' + error.message; }));
  host.prepend(button);
})();
