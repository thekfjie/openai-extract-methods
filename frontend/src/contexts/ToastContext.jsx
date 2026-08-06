import React, { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react';
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from 'lucide-react';

const ToastContext = createContext(null);

const toneMeta = {
  success: { icon: CheckCircle2, title: '操作完成' },
  error: { icon: XCircle, title: '操作失败' },
  warning: { icon: AlertTriangle, title: '请注意' },
  info: { icon: Info, title: '状态更新' },
};

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const sequence = useRef(0);

  const dismiss = useCallback((id) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const notify = useCallback((message, tone = 'info', options = {}) => {
    const id = `${Date.now()}-${sequence.current += 1}`;
    const toast = {
      id,
      message: String(message || ''),
      tone: toneMeta[tone] ? tone : 'info',
      title: options.title,
    };
    setToasts((current) => [...current.slice(-3), toast]);
    window.setTimeout(() => dismiss(id), options.duration || 3400);
    return id;
  }, [dismiss]);

  const value = useMemo(() => ({ notify, dismiss }), [dismiss, notify]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toast-viewport" aria-live="polite" aria-atomic="false">
        {toasts.map((toast) => {
          const meta = toneMeta[toast.tone];
          const Icon = meta.icon;
          return (
            <div className={`app-toast toast-${toast.tone}`} role="status" key={toast.id}>
              <Icon size={18} />
              <span><b>{toast.title || meta.title}</b><small>{toast.message}</small></span>
              <button type="button" onClick={() => dismiss(toast.id)} aria-label="关闭通知"><X size={15} /></button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) throw new Error('useToast must be used inside ToastProvider');
  return context;
}
