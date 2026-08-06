import React from 'react';

export default function GlassButton({
  children,
  variant = 'glass', // 'glass', 'primary', 'danger', 'icon'
  loading = false,
  disabled = false,
  onClick,
  type = 'button',
  className = '',
  icon: Icon = null,
  ...props
}) {
  const variantClass = {
    glass: 'btn-glass',
    primary: 'btn-primary',
    danger: 'btn-danger',
    icon: 'btn-icon',
  }[variant] || 'btn-glass';

  return (
    <button
      type={type}
      className={`${variantClass} ${className}`}
      disabled={disabled || loading}
      onClick={onClick}
      data-loading={loading ? 'true' : 'false'}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading ? <span className="button-loading-spinner" /> : null}
      <span className="button-content">
        {Icon ? <Icon size={16} /> : null}
        {children}
      </span>
    </button>
  );
}
