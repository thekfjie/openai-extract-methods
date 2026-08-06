import React from 'react';
import { Settings, Play, CheckCircle2, AlertTriangle, Layers, Sliders } from 'lucide-react';
import GlassPanel from '../../ui/GlassPanel';
import GlassButton from '../../ui/GlassButton';

export default function FlowNodeCard({ node, onExecute, onConfigure }) {
  if (!node) return null;

  return (
    <GlassPanel hoverable style={{ padding: '1.25rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <div style={{ padding: '0.5rem', borderRadius: 'var(--radius-sm)', background: 'var(--accent-bg)', color: 'var(--accent-color)' }}>
            <Layers size={20} />
          </div>
          <div>
            <h4 style={{ fontSize: '0.95rem', fontWeight: 600 }}>{node.name}</h4>
            <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{node.category || 'Pipeline Node'}</span>
          </div>
        </div>

        <span className={`status-badge ${node.enabled ? 'bg-success' : 'bg-glass'}`}>
          {node.enabled ? '已启用' : '已停用'}
        </span>
      </div>

      {node.description && (
        <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
          {node.description}
        </p>
      )}

      {/* Metrics Row */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem', background: 'var(--bg-secondary)', padding: '0.6rem', borderRadius: 'var(--radius-sm)', fontSize: '0.75rem' }}>
        <div>
          <span style={{ color: 'var(--text-muted)' }}>成功率: </span>
          <span style={{ fontWeight: 600, color: 'var(--success-color)' }}>{node.successRate || '99.4%'}</span>
        </div>
        <div>
          <span style={{ color: 'var(--text-muted)' }}>耗时: </span>
          <span style={{ fontFamily: 'var(--font-mono)' }}>{node.avgDuration || '1.2s'}</span>
        </div>
      </div>

      {/* Action buttons */}
      <div style={{ display: 'flex', gap: '0.5rem', marginTop: 'auto' }}>
        <GlassButton variant="glass" style={{ flex: 1, justifyContent: 'center' }} onClick={() => onConfigure && onConfigure(node)} icon={Sliders}>
          配置参数
        </GlassButton>
        <GlassButton variant="primary" onClick={() => onExecute && onExecute(node)} icon={Play}>
          单节点测试
        </GlassButton>
      </div>
    </GlassPanel>
  );
}
