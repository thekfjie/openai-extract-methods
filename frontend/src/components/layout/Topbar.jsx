import React, { useState, useEffect } from 'react';
import { LogOut, Activity, ShieldCheck, Menu } from 'lucide-react';
import GlassPanel from '../../ui/GlassPanel';
import GlassButton from '../../ui/GlassButton';
import { useAuth } from '../../contexts/AuthContext';
import apiClient from '../../api/client';
import ThemePicker from '../../ui/ThemePicker';

export default function Topbar({ title = 'AutoMyAI 流程控制台', onToggleMenu, sidebarExpanded = false }) {
  const { logout } = useAuth();
  const [online, setOnline] = useState(true);

  useEffect(() => {
    const checkHealth = async () => {
      try {
        await apiClient.get('/health');
        setOnline(true);
      } catch (err) {
        setOnline(false);
      }
    };
    checkHealth();
    const timer = setInterval(checkHealth, 15000);
    return () => clearInterval(timer);
  }, []);

  return (
    <GlassPanel className="topbar">
      <div className="topbar-left">
        <GlassButton variant="icon" className={`shell-menu-btn ${sidebarExpanded ? 'expanded' : ''}`} onClick={onToggleMenu} title="展开或收起菜单" aria-expanded={sidebarExpanded}>
          <Menu className="shell-menu-icon" size={20} />
        </GlassButton>
        <h2 className="topbar-title">{title}</h2>
      </div>

      <div className="topbar-right">
        {/* Connection Status Badge */}
        <span className={`status-badge ${online ? 'bg-success' : 'bg-error'}`}>
          <Activity size={12} className={online ? 'animate-pulse' : ''} />
          {online ? '服务正常' : '后端连通异常'}
        </span>

        <ThemePicker compact />

        {/* Admin Tag */}
        <span className="status-badge bg-accent" style={{ padding: '0.4rem 0.75rem' }}>
          <ShieldCheck size={14} />
          Admin
        </span>

        {/* Logout */}
        <GlassButton variant="glass" onClick={logout} title="退出登录">
          <LogOut size={16} />
          <span style={{ fontSize: '0.85rem' }}>退出</span>
        </GlassButton>
      </div>
    </GlassPanel>
  );
}
