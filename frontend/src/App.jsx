import React, { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import Layout from './components/layout/Layout';
import Dashboard from './pages/Dashboard';
import WorkflowStudio from './pages/WorkflowStudio';
import WorkflowDetail from './pages/WorkflowDetail';
import OpenAIAutomation from './pages/OpenAIAutomation';
import GrokAutomation from './pages/GrokAutomation';
import Converters from './pages/Converters';
import Payments from './pages/Payments';
import Infrastructure from './pages/Infrastructure';
import Tools from './pages/Tools';
import Settings from './pages/Settings';
import Login from './pages/Login';
import AppleMail from './pages/AppleMail';
import FileLibrary from './pages/FileLibrary';
import OpenAIMailPool from './pages/OpenAIMailPool';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { ThemeProvider } from './contexts/ThemeContext';
import { ToastProvider } from './contexts/ToastContext';

const ExtractionCenter = lazy(() => import('./pages/ExtractionCenter'));
const PaymentCenter = lazy(() => import('./pages/PaymentCenter'));

function ModuleLoading() {
  return <div className="app-auth-loading"><div className="animate-spin" /></div>;
}

function ProtectedRoute({ children }) {
  const { authenticated, authRequired, loading } = useAuth();

  if (loading) {
    return (
      <div className="app-auth-loading" style={{ width: '100vw', height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div style={{ border: '3px solid var(--glass-border)', borderTopColor: 'var(--accent-color)', borderRadius: '50%', width: '28px', height: '28px' }} className="animate-spin" />
      </div>
    );
  }

  if (authRequired && !authenticated) {
    return <Navigate to="/login" replace />;
  }

  return children;
}

export default function App() {
  return (
    <AuthProvider>
      <ThemeProvider>
        <ToastProvider>
          <BrowserRouter basename="/ui">
            <Routes>
              <Route path="/login" element={<Login />} />

              <Route
                path="/"
                element={
                  <ProtectedRoute>
                    <Layout />
                  </ProtectedRoute>
                }
              >
                <Route index element={<Dashboard />} />
                <Route path="workflows" element={<WorkflowStudio />} />
                <Route path="workflows/:id" element={<WorkflowDetail />} />
                <Route path="openai" element={<OpenAIAutomation />} />
                <Route path="grok" element={<GrokAutomation />} />
                <Route path="payments" element={<Payments />} />
                <Route path="payments/extract" element={<Suspense fallback={<ModuleLoading />}><ExtractionCenter /></Suspense>} />
                <Route path="payments/center" element={<Suspense fallback={<ModuleLoading />}><PaymentCenter /></Suspense>} />
                <Route path="extract" element={<Navigate to="/payments/extract" replace />} />
                <Route path="converters" element={<Converters />} />
                <Route path="infrastructure" element={<Infrastructure />} />
                <Route path="openai-mail-pool" element={<OpenAIMailPool />} />
                <Route path="apple_mail" element={<AppleMail />} />
                <Route path="file-library" element={<FileLibrary />} />
                <Route path="tools" element={<Tools />} />
                <Route path="settings" element={<Settings />} />
              </Route>

              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </BrowserRouter>
        </ToastProvider>
      </ThemeProvider>
    </AuthProvider>
  );
}
