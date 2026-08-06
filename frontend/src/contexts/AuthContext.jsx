import React, { createContext, useContext, useState, useEffect } from 'react';
import apiClient from '../api/client';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [authenticated, setAuthenticated] = useState(null); // null = checking
  const [authRequired, setAuthRequired] = useState(true);
  const [loading, setLoading] = useState(true);

  const checkAuth = async () => {
    try {
      setLoading(true);
      const res = await apiClient.getAuthStatus();
      setAuthRequired(res.authRequired ?? true);
      setAuthenticated(res.authenticated ?? false);
    } catch (err) {
      console.warn('Auth status check failed:', err);
      setAuthenticated(false);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    checkAuth();

    const handleUnauthorized = () => {
      setAuthenticated(false);
    };

    window.addEventListener('automyai-unauthorized', handleUnauthorized);
    return () => window.removeEventListener('automyai-unauthorized', handleUnauthorized);
  }, []);

  const login = async (password) => {
    const res = await apiClient.login(password);
    if (res.authenticated) {
      const windowConfig = window.AUTOMYAI_RUNTIME_CONFIG || window.__RUNTIME_CONFIG__ || {};
      if (windowConfig.authMode === 'header') {
        sessionStorage.setItem('automyai_admin_password', password);
      }
      setAuthenticated(true);
      return true;
    }
    return false;
  };

  const logout = async () => {
    try {
      await apiClient.logout();
    } catch (e) {
      // ignore
    }
    sessionStorage.removeItem('automyai_admin_password');
    setAuthenticated(false);
  };

  return (
    <AuthContext.Provider value={{ authenticated, authRequired, loading, login, logout, checkAuth }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
