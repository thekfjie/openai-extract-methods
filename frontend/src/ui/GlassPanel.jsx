import React from 'react';

export default function GlassPanel({
  children,
  className = '',
  opaque = false,
  variant = 'default',
  hoverable = false,
  hover = false,
  active = false,
  onClick,
  style = {},
  ...props
}) {
  const interactive = typeof onClick === 'function';
  const classes = [
    opaque ? 'glass-panel-opaque' : 'glass-panel',
    variant !== 'default' ? `glass-panel-${variant}` : '',
    interactive && (hoverable || hover) ? 'glass-panel-hover' : '',
    interactive ? 'glass-panel-interactive' : '',
    active ? 'glass-panel-active' : '',
    className
  ].filter(Boolean).join(' ');

  return (
    <div className={classes} onClick={onClick} style={style} {...props}>
      {children}
    </div>
  );
}
