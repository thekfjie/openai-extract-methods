import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Activity, ArrowDown, Play, RefreshCw, Square, Terminal } from 'lucide-react';
import GlassPanel from '../../ui/GlassPanel';
import GlassButton from '../../ui/GlassButton';

const displayTime = (value) => {
  const raw = String(value || '');
  const matched = raw.match(/(?:T|\s)(\d{2}:\d{2}:\d{2})/) || raw.match(/^(\d{2}:\d{2}:\d{2})/);
  return matched?.[1] || raw || '—';
};

export default function AutomationFlowRunner({
  title = '流程运行控制台',
  running = false,
  progress = { current: 0, total: 100, step: 'Ready' },
  logs = [],
  onStart,
  onStop,
  onRefresh,
  extraActions,
  className = '',
}) {
  const [logFilter, setLogFilter] = useState('all');
  const [followLogs, setFollowLogs] = useState(true);
  const logRef = useRef(null);

  const filteredLogs = useMemo(() => logs.filter((log) => {
    const level = String(log?.level || log?.type || 'info').toLowerCase();
    if (logFilter === 'all') return true;
    if (logFilter === 'error') return level === 'error' || level === 'fatal';
    if (logFilter === 'warn') return level === 'warn' || level === 'warning';
    return true;
  }), [logFilter, logs]);

  const percentage = progress.total > 0 ? Math.min(100, Math.round((progress.current / progress.total) * 100)) : 0;

  useEffect(() => {
    if (!followLogs || !logRef.current) return;
    logRef.current.scrollTo({ top: logRef.current.scrollHeight, behavior: running ? 'smooth' : 'auto' });
  }, [filteredLogs.length, followLogs, running]);

  const handleScroll = () => {
    const element = logRef.current;
    if (!element) return;
    setFollowLogs(element.scrollHeight - element.scrollTop - element.clientHeight < 36);
  };

  return (
    <GlassPanel variant="strong" className={`automation-runner ${running ? 'running' : ''} ${className}`}>
      <div className="runner-heading">
        <div>
          <h3><Terminal size={17} />{title}</h3>
          <small>运行状态、当前节点和实时输出保持在同一视区</small>
        </div>
        <div className="runner-actions">
          <span className={`openai-live-indicator ${running ? 'active' : ''}`}><i />{running ? '实时运行' : '待机'}</span>
          {onRefresh ? <GlassButton variant="icon" onClick={onRefresh} title="刷新日志状态"><RefreshCw size={16} /></GlassButton> : null}
          {!running && onStart ? <GlassButton variant="primary" onClick={onStart} icon={Play}>启动流程</GlassButton> : null}
          {running && onStop ? <GlassButton variant="danger" onClick={onStop} icon={Square}>停止任务</GlassButton> : null}
          {extraActions}
        </div>
      </div>

      <div className={`runner-focus ${running ? 'active' : ''}`}>
        <div className="runner-current-step">
          <span><Activity size={14} />当前节点</span>
          <strong>{progress.step || '等待启动'}</strong>
          <small>{running ? '状态与日志自动刷新' : '启动后这里会持续显示执行位置'}</small>
        </div>
        <div className="runner-progress-copy">
          <span>完成进度</span>
          <strong>{progress.current || 0} / {progress.total || 0}</strong>
          <em>{percentage}%</em>
        </div>
      </div>

      <div className="runner-progress-track" aria-label={`完成进度 ${percentage}%`}>
        <i style={{ width: `${percentage}%` }} />
      </div>

      <div className="runner-log-heading">
        <span><Terminal size={14} />实时运行日志 <em>{filteredLogs.length}</em></span>
        <div className="runner-log-tools">
          <div className="segmented-tabs runner-log-filters" role="tablist">
            {[['all', '全部'], ['warn', '警告'], ['error', '错误']].map(([level, label]) => (
              <button type="button" role="tab" aria-selected={logFilter === level} key={level} onClick={() => setLogFilter(level)} className={`segmented-tab ${logFilter === level ? 'active' : ''}`}>{label}</button>
            ))}
          </div>
          {!followLogs ? <GlassButton variant="glass" icon={ArrowDown} onClick={() => setFollowLogs(true)}>回到最新</GlassButton> : null}
        </div>
      </div>

      <div ref={logRef} className="runner-log-stream" role="log" aria-live="polite" onScroll={handleScroll}>
        {filteredLogs.length ? filteredLogs.map((log, index) => {
          const time = typeof log === 'string' ? '' : log?.time;
          const message = typeof log === 'string' ? log : (log?.message || log?.text || JSON.stringify(log));
          const level = String((typeof log === 'string' ? 'info' : log?.level || log?.type) || 'info').toLowerCase();
          return <div className={`runner-log-line level-${level}`} key={`${time || ''}-${index}-${message.slice(0, 20)}`}><time title={time || ''}>{displayTime(time)}</time><em>{level.toUpperCase()}</em><span>{message}</span></div>;
        }) : <div className="runner-log-empty">暂无控制台输出。任务启动后，这里会自动跟随最新步骤。</div>}
      </div>
    </GlassPanel>
  );
}
