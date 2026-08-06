import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import process from 'node:process';

const apiPort = process.env.API_PORT || '13030';
const apiTarget = process.env.VITE_API_TARGET || `http://127.0.0.1:${apiPort}`;
const wsTarget = apiTarget.replace(/^http/, 'ws');

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  base: '/ui/',
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
      },
      '/paypal-protocol': {
        target: process.env.VITE_PAYPAL_PROTOCOL_TARGET || 'http://127.0.0.1:18795',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/paypal-protocol/, ''),
      },
      '/openai2': {
        target: apiTarget,
        changeOrigin: true,
      },
      '/openai3': {
        target: apiTarget,
        changeOrigin: true,
      },
      '/openai4': {
        target: apiTarget,
        changeOrigin: true,
      },
      '/grok2': {
        target: apiTarget,
        changeOrigin: true,
      },
      '/ws': {
        target: wsTarget,
        ws: true,
      },
    },
  },
});
