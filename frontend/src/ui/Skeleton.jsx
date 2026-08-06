import React from 'react';

export default function Skeleton({ width = '100%', height = '20px', borderRadius, className = '', style = {} }) {
  return (
    <div
      className={`skeleton ${className}`}
      style={{
        width,
        height,
        borderRadius,
        ...style
      }}
    />
  );
}
