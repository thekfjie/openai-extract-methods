import React from 'react';
import { Navigate, useLocation } from 'react-router-dom';

/** Compatibility redirect only; extraction and payment centers have separate routes. */
export default function Payments() {
  const location = useLocation();
  const requestedSub = new URLSearchParams(location.search).get('sub');
  return <Navigate to={requestedSub === 'center' ? '/payments/center' : '/payments/extract'} replace />;
}
