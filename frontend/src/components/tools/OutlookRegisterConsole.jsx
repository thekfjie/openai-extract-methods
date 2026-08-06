import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Copy, Play, RefreshCw, Square } from 'lucide-react';
import apiClient from '../../api/client';
import {
  CollapsiblePanel,
  CompactNumberInput,
  ErrorBanner,
  Field,
  MetricCard,
  OutputBox,
  StatusBadge,
  Toggle,
} from '../../ui/ConsolePrimitives';
import GlassButton from '../../ui/GlassButton';
import GlassPanel from '../../ui/GlassPanel';
import CustomSelect from '../../ui/CustomSelect';

const STORAGE_KEY = 'automyai.outlookRegister.form.v1';

const DEFAULT_FORM = {
  crToken: '',
  proxy: '',
  proxyText: '',
  domain: 'outlook.com',
  country: 'US',
  threads: 1,
  fillAuth: false,
  importToDefaultGroup: true,
  importGroupName: '默认分组',
  importUrl: '',
  importPassword: '',
};

function readForm() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_FORM };
    return { ...DEFAULT_FORM, ...JSON.parse(raw) };
  } catch (_) {
    return { ...DEFAULT_FORM };
  }
}

function writeForm(form) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(form));
  } catch (_) {}
}

async function copyText(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const helper = document.createElement('textarea');
  helper.value = text;
  helper.setAttribute('readonly', '');
  helper.style.position = 'fixed';
  helper.style.opacity = '0';
  document.body.appendChild(helper);
  helper.select();
  const ok = document.execCommand('copy');
  helper.remove();
  if (!ok) throw new Error('复制失败');
}

export default function OutlookRegisterConsole() {
  const [form, setForm] = useState(readForm);
  const [status, setStatus] = useState(null);
  const [accounts, setAccounts] = useState([]);
  const [accountTotal, setAccountTotal] = useState(0);
  const [accountWithToken, setAccountWithToken] = useState(0);
  const [rawAccounts, setRawAccounts] = useState('');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');

  const state = status?.state || {};
  const running = !!state.running;
  const logs = status?.logs || [];

  const update = (patch) => {
    setForm((current) => {
      const next = { ...current, ...patch };
      writeForm(next);
      return next;
    });
  };

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const [statusResult, accountResult] = await Promise.all([
        apiClient.get('/outlook-register/status?tail=250'),
        apiClient.get('/outlook-register/accounts?limit=50&raw=1'),
      ]);
      setStatus(statusResult);
      setAccounts(accountResult.items || []);
      setAccountTotal(accountResult.total || 0);
      setAccountWithToken(accountResult.withToken || 0);
      setRawAccounts(accountResult.raw || '');
      const defaults = statusResult?.importDefaults || {};
      if (defaults.groupName) {
        setForm((current) => {
          if (current.importGroupName && current.importGroupName !== DEFAULT_FORM.importGroupName) {
            return current;
          }
          const next = {
            ...current,
            importGroupName: defaults.groupName || current.importGroupName || '默认分组',
          };
          writeForm(next);
          return next;
        });
      }
    } catch (reason) {
      setError(reason);
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(() => {
      apiClient.get('/outlook-register/status?tail=250').then(setStatus).catch(() => {});
    }, 2500);
    return () => clearInterval(timer);
  }, [refresh]);

  const act = async (task, successMessage = '') => {
    setBusy(true);
    setError(null);
    setNotice('');
    try {
      const result = await task();
      if (successMessage) setNotice(successMessage);
      await refresh();
      return result;
    } catch (reason) {
      setError(reason);
      throw reason;
    } finally {
      setBusy(false);
    }
  };

  const start = () => act(
    () => apiClient.post('/outlook-register/start', {
      crToken: form.crToken,
      proxy: form.proxy,
      proxyText: form.proxyText,
      domain: form.domain,
      country: form.country,
      threads: form.threads,
      fillAuth: form.fillAuth,
      importToDefaultGroup: form.importToDefaultGroup,
      importGroupName: form.importGroupName || '默认分组',
      importUrl: form.importUrl,
      importPassword: form.importPassword,
    }),
    form.fillAuth ? '已启动 OAuth 补全任务' : '已启动 Outlook 注册任务',
  );

  const stop = () => act(() => apiClient.post('/outlook-register/stop'), '已发送停止请求');

  const saveProxies = () => act(
    () => apiClient.post('/outlook-register/proxies', { proxyText: form.proxyText }),
    '代理列表已保存',
  );

  const logText = useMemo(
    () => logs.map((item) => `${item.time || ''} [${item.level || 'info'}] ${item.message || ''}`).join('\n'),
    [logs],
  );

  const accountColumnsPreview = accounts.slice(0, 12);

  return (
    <div className="operations-stack">
      <GlassPanel style={{ padding: '1.25rem' }}>
        <div className="console-toolbar">
          <div>
            <h3>Outlook 注册机</h3>
            <div className="muted" style={{ marginTop: 4 }}>
              微软邮箱纯协议注册（outlook.com / hotmail.com），产出 4 段账号。不是 ChatGPT 注册。
            </div>
          </div>
          <div className="console-actions">
            <GlassButton variant="glass" icon={RefreshCw} disabled={busy} onClick={() => refresh()}>刷新</GlassButton>
            {running ? (
              <GlassButton variant="danger" icon={Square} disabled={busy} onClick={() => stop()}>停止</GlassButton>
            ) : (
              <GlassButton variant="primary" icon={Play} disabled={busy} onClick={() => start()}>
                {form.fillAuth ? '补全 OAuth' : '开始注册'}
              </GlassButton>
            )}
          </div>
        </div>

        <div className="console-metrics" style={{ marginTop: '1rem' }}>
          <MetricCard label="状态" value={state.phase || (running ? 'running' : 'idle')} tone={running ? 'success' : ''} />
          <MetricCard label="账号总数" value={accountTotal} />
          <MetricCard label="含 refresh" value={accountWithToken} />
          <MetricCard label="线程 / 代理" value={`${state.threads || form.threads || 1} / ${state.proxyCount ?? '—'}`} />
        </div>

        <div style={{ marginTop: '0.85rem' }}>
          <StatusBadge ok={running}>{running ? `运行中 PID ${state.pid || '—'}` : `空闲 · 最近退出 ${state.last_exit_code ?? '—'}`}</StatusBadge>
          {state.last_error ? <span className="muted" style={{ marginLeft: 8 }}>错误: {state.last_error}</span> : null}
          {notice ? <span className="muted" style={{ marginLeft: 8 }}>{notice}</span> : null}
        </div>
      </GlassPanel>

      <ErrorBanner error={error} onRetry={refresh} />

      <GlassPanel style={{ padding: '1.25rem' }}>
        <div className="console-grid-wide">
          <Field label="CaptchaRun Token" hint="也可配环境变量 CAPTCHARUN_TOKEN；补 OAuth 时可不填">
            <input className="input-glass" type="password" value={form.crToken} onChange={(event) => update({ crToken: event.target.value })} placeholder="captcha-run token" autoComplete="off" />
          </Field>
          <Field label="单代理" hint="http://user:pass@host:port">
            <input className="input-glass" value={form.proxy} onChange={(event) => update({ proxy: event.target.value })} placeholder="http://user:pass@host:port" />
          </Field>
          <Field label="域名">
            <CustomSelect
              ariaLabel="域名"
              value={form.domain}
              onChange={(value) => update({ domain: value })}
              options={[
                { value: 'outlook.com', label: 'outlook.com' },
                { value: 'hotmail.com', label: 'hotmail.com' },
              ]}
            />
          </Field>
          <Field label="国家 ISO">
            <input className="input-glass" value={form.country} onChange={(event) => update({ country: event.target.value.toUpperCase() })} placeholder="US" />
          </Field>
          <Field label="并发线程" hint="1-20，多线程会随机生成账号">
            <CompactNumberInput value={form.threads} min={1} max={20} onChange={(value) => update({ threads: value })} ariaLabel="并发线程" />
          </Field>
          <Field label="仅补 OAuth">
            <Toggle checked={form.fillAuth} onChange={(checked) => update({ fillAuth: checked })} label="扫描已有 accounts 补 refresh_token" hint="不跑新注册" />
          </Field>
          <Field label="写入默认分组" hint="注册成功且有 refresh_token 后自动导入 OutlookEmail">
            <Toggle
              checked={form.importToDefaultGroup}
              onChange={(checked) => update({ importToDefaultGroup: checked })}
              label={`导入到「${form.importGroupName || '默认分组'}」`}
              hint="产物四段：email----password----client_id----refresh_token"
            />
          </Field>
          <Field label="目标分组" hint="默认使用 OutlookEmail 源分组">
            <input
              className="input-glass"
              value={form.importGroupName}
              onChange={(event) => update({ importGroupName: event.target.value })}
              placeholder="默认分组"
              disabled={!form.importToDefaultGroup}
            />
          </Field>
          <Field label="外部导入地址" hint="可选，旧 mail_manager；一般留空">
            <input className="input-glass" value={form.importUrl} onChange={(event) => update({ importUrl: event.target.value })} placeholder="通常留空，走本机 OutlookEmail" />
          </Field>
          <Field label="外部导入密码">
            <input className="input-glass" type="password" value={form.importPassword} onChange={(event) => update({ importPassword: event.target.value })} placeholder="可选" autoComplete="off" />
          </Field>
        </div>

        <Field label="代理池（每行一个）" wide hint="保存后供 --proxy-file 使用；也可只填上方单代理">
          <textarea
            className="input-glass"
            rows={6}
            value={form.proxyText}
            onChange={(event) => update({ proxyText: event.target.value })}
            placeholder={'http://user:pass@host:port\nuser:pass@host:port'}
          />
        </Field>
        <div className="console-actions" style={{ marginTop: '0.75rem' }}>
          <GlassButton variant="glass" disabled={busy} onClick={() => saveProxies()}>保存代理池</GlassButton>
          <GlassButton
            variant="glass"
            icon={Copy}
            disabled={!rawAccounts}
            onClick={async () => {
              try {
                await copyText(rawAccounts);
                setNotice('已复制全部账号原文');
              } catch (reason) {
                setError(reason);
              }
            }}
          >
            复制账号文件
          </GlassButton>
        </div>
      </GlassPanel>

      <GlassPanel style={{ padding: '1.25rem' }}>
        <div className="console-toolbar">
          <h3>最近账号</h3>
          <div className="muted">共 {accountTotal} 条，含 token {accountWithToken} 条 · 四段 email----password----client_id----refresh_token</div>
        </div>
        <div className="console-table-wrap" style={{ marginTop: '0.75rem' }}>
          <table className="console-table">
            <thead>
              <tr>
                <th>邮箱</th>
                <th>OAuth</th>
                <th>密码</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {accountColumnsPreview.length ? accountColumnsPreview.map((item) => (
                <tr key={item.email}>
                  <td>{item.email}</td>
                  <td><StatusBadge ok={item.hasRefreshToken}>{item.hasRefreshToken ? '已授权' : '缺 token'}</StatusBadge></td>
                  <td className="muted">{item.passwordMasked || '—'}</td>
                  <td>
                    <GlassButton
                      variant="glass"
                      icon={Copy}
                      onClick={async () => {
                        try {
                          await copyText(item.line || item.email);
                          setNotice(`已复制 ${item.email}`);
                        } catch (reason) {
                          setError(reason);
                        }
                      }}
                    >
                      复制
                    </GlassButton>
                  </td>
                </tr>
              )) : (
                <tr><td colSpan={4} className="console-empty">暂无账号，先配置 token/代理后开始注册</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </GlassPanel>

      <CollapsiblePanel title="运行日志" summary="实时 stdout / 状态事件" defaultOpen storageKey="outlook-register-logs">
        <OutputBox value={logText || '暂无日志'} title="Outlook 注册日志" filename="outlook-register.log" />
      </CollapsiblePanel>

      <CollapsiblePanel title="路径与命令" summary="脚本、数据目录、最近命令摘要">
        <OutputBox
          value={{
            paths: status?.paths || {},
            state,
            commandSummary: state.command_summary || [],
          }}
          title="Outlook 注册机状态"
          filename="outlook-register-status.json"
        />
      </CollapsiblePanel>
    </div>
  );
}
