const $ = (id) => document.getElementById(id);
const API_BASE = document.body.dataset.base || '';
const sourceTasks = new Map();

function escapeHtml(value) { return String(value ?? '').replace(/[&<>'"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c])); }
function formatTime(value) { return value ? new Date(Number(value) * 1000).toLocaleString('zh-CN', {hour12:false}) : '—'; }
function compact(value) { const text = String(value || ''); return text.length > 28 ? `${text.slice(0,14)}…${text.slice(-10)}` : (text || '—'); }
function money(item) { return `${(Number(item?.amount || 0) / 100).toFixed(2)} ${String(item?.currency || 'PHP').toUpperCase()}`; }
function setError(message = '') { $('createError').textContent = message; $('createError').hidden = !message; }
function applyTheme(mode) { const dark = mode === 'dark' || (mode === 'system' && matchMedia('(prefers-color-scheme: dark)').matches); document.documentElement.classList.toggle('dark', dark); localStorage.setItem('reg153-ph-theme', mode); }

async function api(url, options = {}) {
  const response = await fetch(url, {cache:'no-store', ...options});
  const data = await response.json().catch(() => ({error:`HTTP ${response.status}`}));
  if (!response.ok || data.ok === false) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

async function healthCheck() {
  try { const data = await api(`${API_BASE}/healthz`); $('serviceState').innerHTML = `<i></i> 服务在线 · ${Number(data.active_jobs || 0)} 个运行任务`; $('serviceState').classList.add('online'); }
  catch (_) { $('serviceState').innerHTML = '<i></i> 服务状态异常'; $('serviceState').classList.remove('online'); }
}

function selectSource(taskId) {
  const item = sourceTasks.get(taskId); $('createJob').disabled = !item; $('sourceCard').classList.toggle('is-empty', !item);
  $('sourceEmail').textContent = item?.email || '尚未选择账号'; $('sourceCreated').textContent = item ? `来源任务 ${item.task_id}` : '选择后读取原 Checkout'; $('sourceAmount').textContent = item ? money(item) : '—'; $('sourceCurrency').textContent = String(item?.currency || 'PHP').toUpperCase(); $('sourceCheckout').textContent = item ? compact(item.checkout_session_id) : '—';
}

async function loadSources() {
  const select = $('sourceTask');
  try {
    const data = await api(`${API_BASE}/api/source/tasks`); const items = data.items || []; sourceTasks.clear(); select.innerHTML = '<option value="">请选择已完成的菲律宾短链任务</option>';
    items.forEach((item) => { sourceTasks.set(item.task_id, item); const option = document.createElement('option'); option.value = item.task_id; option.textContent = `${item.email || '未知账号'} · ${money(item)} · ${compact(item.checkout_session_id)}`; select.appendChild(option); });
    $('sourceCount').textContent = `${items.length} 个可用任务`; if (!items.length) select.innerHTML = '<option value="">暂无已完成的菲律宾短链任务</option>';
    const requested = new URLSearchParams(location.search).get('task_id'); const preferred = requested && sourceTasks.has(requested) ? requested : (items[0]?.task_id || ''); if (preferred) { select.value = preferred; selectSource(preferred); }
  } catch (error) { select.innerHTML = '<option value="">任务读取失败</option>'; $('sourceCount').textContent = '读取失败'; setError(error.message); }
}

function statusMeta(status) {
  return ({queued:['排队中','queued'],running:['运行中','running'],verification_required:['等待验证','waiting'],ready:['支付完成','done'],error:['失败','error'],cancelled:['已停止','cancelled']})[status] || [status || '等待','idle'];
}
function taskActions(job) {
  if (job.status === 'verification_required') {
    const result = job.result || {}; const action = result.next_action || {}; const url = action.redirect_url || result.short_url || '';
    return `${url ? `<a class="task-open" href="${escapeHtml(url)}" target="_blank" rel="noopener">打开验证</a>` : ''}<button class="task-resume" data-resume="${escapeHtml(job.id)}">验证后继续</button><button class="task-stop" data-cancel="${escapeHtml(job.id)}">停止</button>`;
  }
  if (['queued','running'].includes(job.status)) return `<button class="task-stop" data-cancel="${escapeHtml(job.id)}">停止</button>`;
  return `<button class="task-clear" data-delete="${escapeHtml(job.id)}">清除</button>`;
}
function renderLogs(logs) {
  const rows = (logs || []).slice(-10);
  if (!rows.length) return '';
  return `<div class="task-protocol-logs">${rows.map((x) => `<div class="mini-log ${escapeHtml(x.type || 'info')}"><time>${escapeHtml(x.time || '')}</time><span>${escapeHtml(x.message || '')}</span></div>`).join('')}</div>`;
}
function renderJob(job) {
  const [label, cls] = statusMeta(job.status); const progress = Math.max(0, Math.min(100, Number(job.progress || 0))); const result = job.result || {};
  const error = job.error ? `<div class="task-message error"><small>最后错误</small><b>${escapeHtml(job.error)}</b></div>` : '';
  const nextAction = result.next_action || {}; const verifyUrl = nextAction.redirect_url ? `<a class="task-open" href="${escapeHtml(nextAction.redirect_url)}" target="_blank" rel="noopener">打开支付验证</a>` : '';
  const resultBlock = ['ready','verification_required'].includes(job.status) ? `<div class="task-result"><div><small>支付状态</small><b>${escapeHtml(result.status || label)}</b></div><div><small>金额</small><b>${escapeHtml(String(result.checkout_amount ?? result.amount_minor ?? '—'))} ${escapeHtml(result.checkout_currency || result.currency || 'PHP')}</b></div>${verifyUrl}</div>` : '';
  return `<article class="task-card status-${escapeHtml(cls)}"><div class="task-card-head"><div class="task-account"><span class="task-signal"></span><div><small>${escapeHtml(label)}</small><h3>${escapeHtml(job.account_email || '未知账号')}</h3></div></div><div class="task-actions">${taskActions(job)}</div></div><div class="task-progress"><i style="width:${progress}%"></i></div><div class="task-facts"><div><small>当前阶段</small><b>${escapeHtml(job.stage || '等待执行')}</b></div><div><small>进度</small><b>${progress}%</b></div><div><small>创建时间</small><b>${escapeHtml(formatTime(job.created_at))}</b></div></div>${error}${resultBlock}${renderLogs(job.logs)}</article>`;
}

async function loadJobs() {
  try { const data = await api(`${API_BASE}/api/jobs`); const items = data.items || []; $('taskList').innerHTML = items.length ? items.map(renderJob).join('') : '<div class="empty-task"><span class="empty-orbit"><i></i></span><h3>等待任务</h3><p>提交后显示账号、协议阶段、重试错误和最终到账结果。</p></div>'; bindActions(); }
  catch (error) { $('taskList').innerHTML = `<div class="empty-task error-state"><h3>任务读取失败</h3><p>${escapeHtml(error.message)}</p></div>`; }
}
function bindActions() {
  document.querySelectorAll('[data-cancel]').forEach((button) => button.onclick = async () => { button.disabled = true; await fetch(`${API_BASE}/api/jobs/${encodeURIComponent(button.dataset.cancel)}/cancel`, {method:'POST'}).catch(() => {}); loadJobs(); });
  document.querySelectorAll('[data-resume]').forEach((button) => button.onclick = async () => { button.disabled = true; await fetch(`${API_BASE}/api/jobs/${encodeURIComponent(button.dataset.resume)}/resume`, {method:'POST'}).catch(() => {}); loadJobs(); });
  document.querySelectorAll('[data-delete]').forEach((button) => button.onclick = async () => { button.disabled = true; await fetch(`${API_BASE}/api/jobs/${encodeURIComponent(button.dataset.delete)}`, {method:'DELETE'}).catch(() => {}); loadJobs(); });
}

$('themeToggle').onclick = () => applyTheme((localStorage.getItem('reg153-ph-theme') || 'system') === 'dark' ? 'light' : 'dark');
$('sourceTask').onchange = (event) => selectSource(event.target.value);
$('cards').oninput = () => { const text = $('cards').value; const matches = text.match(/(?:\d[ -]?){12,19}/g) || []; const count = matches.length || text.split(/\r?\n/).filter((x) => x.trim()).length; $('cardCount').textContent = `${Math.min(count,20)} / 20`; };
$('proxies').oninput = () => { const count = $('proxies').value.split(/\r?\n/).filter((x) => x.trim()).length; $('proxyCount').textContent = `${Math.min(count,100)} / 100`; };
$('refreshJobs').onclick = () => { loadJobs(); healthCheck(); };
$('shortForm').onsubmit = async (event) => {
  event.preventDefault(); const button = $('createJob'); setError(''); button.disabled = true; button.innerHTML = '<span>正在提交协议任务…</span><i>→</i>';
  try { await api(`${API_BASE}/api/jobs`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task_id:$('sourceTask').value,cards:$('cards').value,proxies:$('proxies').value,card_retry_count:Number($('cardRetries').value || 2)})}); await loadJobs(); await healthCheck(); }
  catch (error) { setError(error.message); }
  finally { button.disabled = false; button.innerHTML = '<span>开始协议支付</span><i>→</i>'; }
};

applyTheme(localStorage.getItem('reg153-ph-theme') || 'system'); loadSources(); loadJobs(); healthCheck(); setInterval(() => { loadJobs(); healthCheck(); }, 1600);
