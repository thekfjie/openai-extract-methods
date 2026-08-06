import React, { useEffect, useState } from 'react';
import { flushSync } from 'react-dom';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import Sidebar from './Sidebar';
import Topbar from './Topbar';
import { navigationTitle, sidebarExpandedForPath, targetExpandsSidebar } from './navigation';

const MOBILE_MEDIA_QUERY = '(max-width: 768px)';

function isMobileViewport() {
  return window.matchMedia(MOBILE_MEDIA_QUERY).matches;
}

export default function Layout() {
  const location = useLocation();
  const navigate = useNavigate();
  const [sidebarExpanded, setSidebarExpanded] = useState(() => sidebarExpandedForPath(location.pathname));
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  useEffect(() => {
    setMobileMenuOpen(false);
    if (!isMobileViewport()) setSidebarExpanded(sidebarExpandedForPath(location.pathname));
  }, [location.pathname, location.search]);

  useEffect(() => {
    if (!mobileMenuOpen) return undefined;
    document.body.style.overflow = 'hidden';
    const closeOnEscape = (event) => {
      if (event.key === 'Escape') setMobileMenuOpen(false);
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => {
      document.body.style.overflow = '';
      window.removeEventListener('keydown', closeOnEscape);
    };
  }, [mobileMenuOpen]);

  const toggleMenu = () => {
    if (isMobileViewport()) setMobileMenuOpen((value) => !value);
    else setSidebarExpanded((value) => !value);
  };

  const expandSidebar = () => {
    if (isMobileViewport()) setMobileMenuOpen(true);
    else flushSync(() => setSidebarExpanded(true));
  };

  const navigateLeaf = (event, target) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();

    const mobile = isMobileViewport();
    const targetExpanded = targetExpandsSidebar(target);

    // Match MathModels: keep the route stage mounted and let the flex shell
    // reflow continuously while the sidebar width/padding transitions.
    flushSync(() => {
      setMobileMenuOpen(false);
      if (!mobile) setSidebarExpanded(targetExpanded);
    });
    navigate(target);
  };

  const currentTitle = navigationTitle(location.pathname, location.search);

  return (
    <>
      {/* Background Ambient Glass Orbs */}
      <div className="bg-orb orb-1" />
      <div className="bg-orb orb-2" />

      <div className={`app-container ${sidebarExpanded ? 'sidebar-expanded' : 'sidebar-collapsed'}`}>
        <div
          className={`sidebar-backdrop ${mobileMenuOpen ? 'visible' : ''}`}
          onClick={() => setMobileMenuOpen(false)}
        />

        <Sidebar
          collapsed={!sidebarExpanded}
          onExpand={expandSidebar}
          onCloseMobile={() => setMobileMenuOpen(false)}
          onNavigate={navigateLeaf}
          mobileMenuOpen={mobileMenuOpen}
        />

        <main className="main-content">
          <Topbar
            title={currentTitle}
            onToggleMenu={toggleMenu}
            sidebarExpanded={sidebarExpanded || mobileMenuOpen}
          />

          <div className="workspace">
            <div className="route-stage">
              <Outlet />
            </div>
          </div>
        </main>
      </div>
    </>
  );
}
