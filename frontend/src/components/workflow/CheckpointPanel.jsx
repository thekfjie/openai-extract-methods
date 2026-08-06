import React, { useState } from 'react';
import { HelpCircle, CheckCircle, AlertOctagon, Send, Key } from 'lucide-react';
import GlassPanel from '../../ui/GlassPanel';
import GlassButton from '../../ui/GlassButton';

export default function CheckpointPanel({ checkpoint, onSubmit, onCancel }) {
  const [value, setValue] = useState('');
  const [submitting, setSubmitting] = useState(false);

  if (!checkpoint) return null;

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      await onSubmit(value);
      setValue('');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <GlassPanel style={{ padding: '1.5rem', borderLeft: '4px solid var(--warning-color)' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: '1rem' }}>
        <div style={{ padding: '0.6rem', borderRadius: '50%', background: 'var(--warning-bg)', color: 'var(--warning-color)' }}>
          <AlertOctagon size={24} />
        </div>

        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <h4 style={{ fontSize: '1.05rem', fontWeight: 700 }}>
              {checkpoint.title || '流程等待人工决策 / 验证码输入'}
            </h4>
            <span className="status-badge bg-warning">Action Required</span>
          </div>

          <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', margin: '0.5rem 0 1rem 0' }}>
            {checkpoint.prompt || '当前节点需要提供验证码、二步验证 Pin 码或决策确认。'}
          </p>

          <form onSubmit={handleSubmit} style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
            <input
              type="text"
              className="input-glass"
              style={{ flex: 1, minWidth: '220px' }}
              placeholder={checkpoint.placeholder || '输入验证码或确认数据...'}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              required
            />

            <GlassButton variant="primary" type="submit" loading={submitting} icon={Send}>
              提交决策
            </GlassButton>

            {onCancel && (
              <GlassButton variant="glass" onClick={onCancel} type="button">
                跳过此节点
              </GlassButton>
            )}
          </form>
        </div>
      </div>
    </GlassPanel>
  );
}
