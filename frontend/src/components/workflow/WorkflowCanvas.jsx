import React from 'react';
import { Play, CheckCircle2, AlertTriangle, Clock, ArrowRight, Server, Mail, ShieldAlert, Cpu, Key } from 'lucide-react';
import GlassPanel from '../../ui/GlassPanel';

const NODE_ICONS = {
  mail: Mail,
  proxy: Server,
  captcha: ShieldAlert,
  bot: Cpu,
  token: Key,
};

export default function WorkflowCanvas({ nodes = [], activeNodeId = null, onSelectNode }) {
  return (
    <GlassPanel className="p-4" style={{ padding: '1.5rem', overflowX: 'auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1.25rem' }}>
        <h3 style={{ fontSize: '1rem', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: 'var(--accent-color)' }} />
          自动化流程结构解构 (Workflow Pipeline Canvas)
        </h3>
        <span className="status-badge bg-accent">DAG Dynamic Visualizer</span>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '1.5rem', padding: '1rem 0.5rem', minWidth: '700px' }}>
        {nodes.map((node, index) => {
          const IconComp = NODE_ICONS[node.type] || Cpu;
          const isActive = activeNodeId === node.id;
          const isCompleted = node.status === 'completed';
          const isRunning = node.status === 'running';
          const isError = node.status === 'error';

          let statusClass = 'bg-glass';
          if (isCompleted) statusClass = 'bg-success';
          if (isRunning) statusClass = 'bg-accent';
          if (isError) statusClass = 'bg-error';

          return (
            <React.Fragment key={node.id}>
              {/* Node Card */}
              <div
                onClick={() => onSelectNode && onSelectNode(node)}
                style={{
                  flex: 1,
                  minWidth: '180px',
                  padding: '1rem',
                  borderRadius: 'var(--radius-md)',
                  background: isActive ? 'var(--glass-bg-hover)' : 'var(--glass-bg)',
                  border: isActive ? '1.5px solid var(--accent-color)' : '1px solid var(--glass-border)',
                  boxShadow: isActive ? '0 0 16px var(--accent-glow)' : 'none',
                  cursor: 'pointer',
                  transition: 'all var(--transition)',
                  position: 'relative'
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.75rem' }}>
                  <div style={{ width: '32px', height: '32px', borderRadius: 'var(--radius-sm)', background: 'var(--accent-bg)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--accent-color)' }}>
                    <IconComp size={18} />
                  </div>
                  <span className={`status-badge ${statusClass}`}>
                    {isRunning && <Clock size={10} className="animate-spin" />}
                    {node.status || 'idle'}
                  </span>
                </div>

                <div style={{ fontWeight: 600, fontSize: '0.9rem', marginBottom: '0.25rem' }}>
                  {node.name}
                </div>
                <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                  {node.provider || 'Core Adapter'}
                </div>

                {node.duration && (
                  <div style={{ marginTop: '0.75rem', fontSize: '0.7rem', color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)' }}>
                    ⏱ {node.duration}
                  </div>
                )}
              </div>

              {/* Connecting Flow Arrow */}
              {index < nodes.length - 1 && (
                <div style={{ color: isCompleted ? 'var(--success-color)' : 'var(--text-muted)', display: 'flex', alignItems: 'center' }}>
                  <ArrowRight size={20} className={isRunning && index === nodes.findIndex(n => n.id === activeNodeId) ? 'animate-pulse' : ''} />
                </div>
              )}
            </React.Fragment>
          );
        })}
      </div>
    </GlassPanel>
  );
}
