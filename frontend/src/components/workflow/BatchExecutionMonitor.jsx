import React from 'react';
import { CheckCircle2, Eye, Server, Users, XCircle } from 'lucide-react';
import GlassPanel from '../../ui/GlassPanel';
import { CompactNumberInput, MetricCard, StatusBadge } from '../../ui/ConsolePrimitives';

export default function BatchExecutionMonitor({
  concurrency = 5,
  stats = { total: 100, success: 82, failed: 4, running: 5 },
  proxyStats = { total: 12, active: 10 },
  browserLiveUrl = null,
  onConcurrencyChange
}) {
  return (
    <GlassPanel variant="strong" className="batch-monitor">
      <div className="batch-monitor-heading">
        <div>
          <h3><Users size={17} />并发执行状态</h3>
          <small>批次产出、失败与工作线程保持在首屏可见</small>
        </div>
        <StatusBadge tone={stats.running > 0 ? 'bg-success' : 'bg-glass'}>{stats.running > 0 ? 'Workers Active' : 'Standby'}</StatusBadge>
      </div>

      <div className="batch-monitor-metrics">
        <MetricCard label="任务总量" value={stats.total} />
        <MetricCard label={<><CheckCircle2 size={12} />成功产出</>} value={stats.success} tone="success" />
        <MetricCard label={<><XCircle size={12} />异常失败</>} value={stats.failed} tone="danger" />
        <MetricCard label="并发工作中" value={stats.running} tone="warning" />
      </div>

      <div className="batch-monitor-controls">
        <div className="batch-worker-control">
          <span>工作线程</span>
          <CompactNumberInput value={concurrency} min={1} max={50} ariaLabel="工作线程" onChange={(value) => onConcurrencyChange?.(value)} />
        </div>
        <div className="batch-proxy-status">
          <div>
            <b>代理池状态</b>
            <small>可用 {proxyStats.active} / {proxyStats.total}</small>
          </div>
          <Server size={20} />
        </div>
      </div>

      {browserLiveUrl && (
        <div className="batch-browser-preview">
          <b><Eye size={14} />实时浏览器画面</b>
          <div>
            <img src={browserLiveUrl} alt="Browser Live" />
          </div>
        </div>
      )}
    </GlassPanel>
  );
}
