import React, { useEffect, useState } from 'react';
import { Link, NavLink, useLocation } from 'react-router-dom';
import { ChevronDown, GitMerge, X } from 'lucide-react';
import { activeSubForLocation, groupForPath, NAV_ITEMS } from './navigation';

export default function Sidebar({ collapsed, onExpand, onCloseMobile, onNavigate, mobileMenuOpen }) {
  const location = useLocation();
  const activeGroup = groupForPath(location.pathname);
  const activeSub = activeSubForLocation(activeGroup, location.pathname, location.search);
  const [openSubmenu, setOpenSubmenu] = useState(() => activeGroup?.id || null);

  useEffect(() => {
    setOpenSubmenu(activeGroup?.id || null);
  }, [activeGroup?.id]);

  const handleGroup = (group) => {
    if (collapsed && !mobileMenuOpen) {
      onExpand();
      setOpenSubmenu(group.id);
      return;
    }
    setOpenSubmenu((current) => current === group.id ? null : group.id);
  };

  const sidebarClassName = [
    'glass-panel',
    'sidebar',
    collapsed && !mobileMenuOpen ? 'collapsed' : '',
    mobileMenuOpen ? 'mobile-open' : '',
  ].filter(Boolean).join(' ');

  return (
    <aside className={sidebarClassName}>
      <div className="sidebar-header">
        <GitMerge className="sidebar-brand-icon" size={28} />
        <span className="sidebar-brand-label">AutoMyAI Flow</span>
        <button type="button" className="sidebar-mobile-close" onClick={onCloseMobile} aria-label="关闭侧边栏" title="关闭侧边栏">
          <X size={18} />
        </button>
      </div>

      <nav className="sidebar-nav" aria-label="主菜单">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;

          if (item.type === 'link') {
            return (
              <NavLink
                key={item.id}
                to={item.to}
                end={item.end}
                onClick={(event) => onNavigate(event, item.to)}
                className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
                title={item.label}
              >
                <Icon size={20} />
                <span className="nav-label">{item.label}</span>
              </NavLink>
            );
          }

          const open = openSubmenu === item.id;
          const groupActive = activeGroup?.id === item.id;
          const submenuId = `sidebar-submenu-${item.id}`;

          return (
            <div className={`nav-group ${open ? 'open' : ''}`} key={item.id}>
              <button
                type="button"
                aria-controls={submenuId}
                aria-expanded={open}
                className={`nav-item nav-group-trigger ${groupActive ? 'active' : ''}`}
                onClick={() => handleGroup(item)}
                title={item.label}
              >
                <span className="nav-item-main">
                  <Icon size={20} style={item.tone ? { color: item.tone } : undefined} />
                  <span className="nav-label">{item.label}</span>
                </span>
                <ChevronDown className="nav-chevron" size={16} />
              </button>
              <div className="nav-submenu" id={submenuId}>
                <div className="nav-submenu-inner">
                  {item.items.map((subitem) => {
                    const target = subitem.to || `${item.path}?sub=${subitem.sub}`;
                    const subitemActive = groupActive && activeSub === subitem.sub;
                    return (
                      <Link
                        key={subitem.sub}
                        to={target}
                        onClick={(event) => onNavigate(event, target)}
                        className={`nav-subitem ${subitemActive ? 'active' : ''}`}
                        aria-current={subitemActive ? 'page' : undefined}
                      >
                        <span className="nav-sub-dot" />
                        <span className="nav-label">{subitem.label}</span>
                      </Link>
                    );
                  })}
                </div>
              </div>
            </div>
          );
        })}
      </nav>
    </aside>
  );
}
