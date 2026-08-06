import React, { useCallback, useEffect, useState } from 'react';
import { ArrowUpRight, Bot, Mail, RefreshCw, Server, ShieldCheck, Zap } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import apiClient from '../api/client';
import { CollapsiblePanel, ErrorBanner, MetricCard, OutputBox, StatusBadge } from '../ui/ConsolePrimitives';
import GlassButton from '../ui/GlassButton';
import GlassPanel from '../ui/GlassPanel';
import Skeleton from '../ui/Skeleton';

export default function Dashboard() {
  const navigate = useNavigate();
  const [data, setData] = useState({});
  const [error, setError] = useState(null);
  const [checking, setChecking] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);

  const refresh = useCallback(async () => {
    setError(null);
    const results = await Promise.allSettled([
      apiClient.get('/health'),
      apiClient.get('/extensions/status'),
      apiClient.get('/cpa/monitor/status'),
      apiClient.get('/sub2api/monitor/status'),
      apiClient.openai2.get('/health'),
      apiClient.openai3.get('/status'),
      apiClient.openai4.get('/status'),
      apiClient.get('/traffic?tail=10'),
      apiClient.get('/outlook-email/inventory'),
      apiClient.get('/phones/pool?limit=20'),
      apiClient.get('/browser-live/status'),
    ]);
    const keys = ['health', 'extensions', 'cpa', 'sub2', 'openai2', 'openai3', 'openai1', 'traffic', 'mail', 'phones', 'browser'];
    setData((current) => ({ ...current, ...Object.fromEntries(results.map((result, index) => [keys[index], result.status === 'fulfilled' ? result.value : null])) }));
    const rejected = results.find((item) => item.status === 'rejected');
    if (rejected) setError(rejected.reason);
    setInitialLoading(false);
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 10000);
    return () => clearInterval(timer);
  }, [refresh]);

  const check = async (path) => {
    setChecking(true);
    setError(null);
    try {
      await apiClient.post(path, {});
      await refresh();
    } catch (reason) {
      setError(reason);
    } finally {
      setChecking(false);
    }
  };

  const o1 = data.openai1?.state || data.openai1 || {};
  const o3 = data.openai3?.state || data.openai3 || {};
  const results = Array.isArray(o1.results) ? o1.results : [];
  const phoneCount = data.phones?.total || data.phones?.items?.length || 0;
  const percentage = o1.total > 0 ? Math.min(100, Math.round(((o1.completed || 0) / o1.total) * 100)) : 0;
  const launchers = [
    { title: 'OpenAI 注册', hint: o1.running ? `运行中：${o1.current_email || o1.currentEmail || '处理中'}` : '有头 Chromium 注册、OAuth 与 Sub2API 导入', icon: Bot, path: '/openai?sub=openai4', tone: 'var(--accent-color)' },
    { title: 'Grok 自动化', hint: 'TTK、历史注册与 Grok2API / CPA 同步', icon: Zap, path: '/grok', tone: 'var(--warning-color)' },
    { title: '邮箱与号码', hint: `Mail ${data.mail?.usable || 0}/${data.mail?.total || 0} · 号码 ${phoneCount}`, icon: Mail, path: '/infrastructure', tone: 'var(--success-color)' },
    { title: '系统设置', hint: '代理模式、分组、指纹、购买配置', icon: Server, path: '/settings', tone: 'var(--text-secondary)' },
  ];

  return (
    <div className="page-container dashboard-page">
      <div className="page-header">
        <div className="page-title-group"><h1>控制总览</h1></div>
        <div className="console-actions"><GlassButton variant="glass" disabled={checking} onClick={() => check('/cpa/monitor/check')}>检测 CPA</GlassButton><GlassButton variant="glass" disabled={checking} onClick={() => check('/sub2api/monitor/check')}>检测 Sub2API</GlassButton><GlassButton variant="primary" icon={RefreshCw} onClick={refresh}>刷新</GlassButton></div>
      </div>
      <ErrorBanner error={error} onRetry={refresh} />

      {initialLoading ? (
        <div className="dashboard-loading"><Skeleton height="184px" /><div className="dashboard-launcher-strip">{[1, 2, 3, 4].map((item) => <Skeleton key={item} height="104px" />)}</div></div>
      ) : (
        <>
          <div className="dashboard-priority-grid">
            <GlassPanel variant="strong" className={`dashboard-run-focus ${o1.running ? 'running' : ''}`}>
              <div className="dashboard-focus-heading"><div><span><Bot size={15} />OpenAI 注册流水线</span><h2>{o1.phase || o1.currentStepLabel || (o1.running ? '正在执行' : '空闲')}</h2></div><StatusBadge ok={!!o1.running}>{o1.running ? '实时运行' : '待机'}</StatusBadge></div>
              <div className="dashboard-focus-copy"><span>{o1.current_email || o1.currentEmail || '当前没有正在处理的邮箱'}</span><strong>{o1.completed || 0} / {o1.total || 0}<em>{percentage}%</em></strong></div>
              <div className="runner-progress-track"><i style={{ width: `${percentage}%` }} /></div>
              <div className="dashboard-focus-metrics"><span>成功 <b>{o1.success || 0}</b></span><span>失败 <b>{o1.failed || 0}</b></span><span>浏览器 <b>{data.browser?.running ? '有头运行中' : '空闲'}</b></span></div>
              <GlassButton variant="primary" onClick={() => navigate('/openai?sub=openai4')} icon={ArrowUpRight}>进入实时工作台</GlassButton>
            </GlassPanel>

            <div className="dashboard-launchers">
              <h3>常用入口</h3>
              <div className="dashboard-launcher-strip">
                {launchers.map(({ title, hint, icon: Icon, path, tone }) => (
                  <GlassPanel className="dashboard-launcher-card" key={title} hoverable onClick={() => navigate(path)}>
                    <div><Icon size={20} style={{ color: tone }} /><ArrowUpRight size={15} /></div><h4>{title}</h4><p>{hint}</p>
                  </GlassPanel>
                ))}
              </div>
            </div>
          </div>

          <div className="console-metrics dashboard-metrics">
            <MetricCard label="OpenAI 3" value={o3.running ? '运行中' : (o3.phase || (data.openai3 ? '在线' : '离线'))} tone={o3.running ? 'success' : ''} />
            <MetricCard label="OpenAI 2" value={data.openai2?.ok ? '在线' : '离线'} hint={`可用 ${data.openai2?.stats?.available ?? '—'}`} />
            <MetricCard label="Mail Admin" value={`${data.mail?.usable || 0}/${data.mail?.total || 0}`} hint="可用 / 总账号" />
            <MetricCard label="号码池" value={phoneCount} hint="电话号码地区与代理完全解耦" />
          </div>
        </>
      )}

      <CollapsiblePanel title="服务状态与近期结果" summary="CPA、Sub2API、Grok2API、主 API、运行结果和流量记录">
        <div className="operation-support-grid">
          <div className="operation-subpanel"><div className="console-toolbar"><h4>外部服务</h4><ShieldCheck size={17} /></div><div className="console-metrics"><MetricCard label="CPA" value={data.cpa?.ok || data.extensions?.cpa?.localReady ? '就绪' : '待检查'} /><MetricCard label="Sub2API" value={data.sub2?.ok ? '正常' : (data.health?.sub2apiConfigured ? '已配置' : '未配置')} /><MetricCard label="Grok2API" value={data.extensions?.grok2api?.authenticated ? '鉴权正常' : '未就绪'} /><MetricCard label="主 API" value={data.health?.ok ? '正常' : '异常'} /></div></div>
          <div className="operation-subpanel"><OutputBox value={results.length ? results.slice(-10) : '暂无 OpenAI 注册结果'} title="最近 OpenAI 注册结果" filename="dashboard-openai-results.json" /></div>
        </div>
        <div className="operation-subpanel dashboard-traffic-detail"><OutputBox value={data.traffic || '暂无流量记录'} title="近期任务流量" filename="dashboard-traffic.json" /></div>
      </CollapsiblePanel>
    </div>
  );
}
