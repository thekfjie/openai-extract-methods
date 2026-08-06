import React, { useState } from 'react';
import { Shield, Lock, ArrowRight, GitMerge } from 'lucide-react';
import GlassPanel from '../ui/GlassPanel';
import GlassButton from '../ui/GlassButton';
import { useAuth } from '../contexts/AuthContext';
import { useNavigate } from 'react-router-dom';

export default function Login() {
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!password) return;
    setSubmitting(true);
    setError('');

    try {
      const success = await login(password);
      if (success) {
        navigate('/', { replace: true });
      } else {
        setError('密码错误，请重试');
      }
    } catch (err) {
      setError(err.message || '登录验证失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div style={{ width: '100vw', height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', position: 'relative', overflow: 'hidden' }}>
      <div className="bg-orb orb-1" />
      <div className="bg-orb orb-2" />

      <GlassPanel style={{ width: '100%', maxWidth: '420px', padding: '2.5rem', margin: '1rem', zIndex: 10 }}>
        <div style={{ textAlign: 'center', marginBottom: '2rem' }}>
          <div style={{ width: '56px', height: '56px', borderRadius: '50%', background: 'var(--accent-bg)', color: 'var(--accent-color)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', marginBottom: '1rem' }}>
            <GitMerge size={30} />
          </div>
          <h1 style={{ fontSize: '1.5rem', fontWeight: 700 }}>AutoMyAI 流程控制台</h1>
          <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginTop: '0.4rem' }}>
            请输入管理员密码以进入自动化管理界面
          </p>
        </div>

        {error && (
          <div style={{ padding: '0.75rem', borderRadius: 'var(--radius-sm)', background: 'var(--danger-bg)', border: '1px solid var(--danger-border)', color: 'var(--danger-color)', fontSize: '0.85rem', marginBottom: '1.25rem', textAlign: 'center' }}>
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
          <div>
            <label className="label-glass" style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
              <Lock size={14} /> 管理员口令 (Admin Password)
            </label>
            <input
              type="password"
              className="input-glass"
              placeholder="请输入管理员密码..."
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoFocus
              required
            />
          </div>

          <GlassButton variant="primary" type="submit" loading={submitting} icon={ArrowRight} style={{ justifyContent: 'center', padding: '0.75rem' }}>
            解禁并登录 (Access Console)
          </GlassButton>
        </form>
      </GlassPanel>
    </div>
  );
}
