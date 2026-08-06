import apiClient from './client';

const portal = apiClient.cardPaymentPortal;
const id = (value) => encodeURIComponent(String(value || ''));

const cardPaymentPortalApi = {
  health: () => portal.get('/health'),
  cdkStatus: () => portal.get('/cdk/status'),
  activateCdk: (code) => portal.post('/cdk/activate', { code }),
  mergeCdks: (codes) => portal.post('/cdk/merge', { codes }),
  queryCdkTasks: (code = '') => portal.post('/cdk/tasks/query', { code }),
  lookupCdkMerge: (code) => portal.post('/cdk/merge-lookup', { code }),
  listCdkCodes: () => portal.get('/cdk-admin/codes'),
  createCdkCodes: (payload) => portal.post('/cdk-admin/codes', payload),
  adminMergeCdks: (codes) => portal.post('/cdk-admin/merge', { codes }),
  setCdkEnabled: (cdkID, enabled) => portal.patch(`/cdk-admin/codes/${id(cdkID)}`, { enabled }),
  deleteCdk: (cdkID) => portal.delete(`/cdk-admin/codes/${id(cdkID)}`),

  cardBindConfig: () => portal.get('/card-bind/config'),
  billingAddress: (state = '') => portal.get(`/billing-address${state ? `?state=${encodeURIComponent(state)}` : ''}`),
  createCardBindSession: (payload) => portal.post('/card-bind/session', payload),
  getCardKeyProbe: (probeID) => portal.get(`/card-bind/key-probe/${id(probeID)}`),
  setDefaultCard: (payload) => portal.post('/card-bind/default', payload),
  reportCardClientEvent: (payload) => portal.post('/card-bind/client-event', payload),
  cardAudit: (limit = 100) => portal.get(`/card-bind/audit?limit=${encodeURIComponent(limit)}`),

  inspectSourceContext: (taskID, accessToken = '') => portal.post('/card-flow/context', { task_id: taskID, access_token: accessToken }),
  createServerToken: (taskID, accessToken = '') => portal.post('/card-flow/server-token', { task_id: taskID, access_token: accessToken }),
  createQuickCheckout: (payload) => portal.post('/card-flow/quick-checkout', payload),
  getQuickCheckout: (taskID) => portal.get(`/card-flow/task/${id(taskID)}`),
  cancelQuickCheckout: (taskID) => portal.post(`/card-flow/task/${id(taskID)}/cancel`, {}),
  clearQuickCheckouts: () => portal.post('/card-flow/tasks/clear', {}),

  sourceTasks: () => portal.get('/source/tasks'),
  listJobs: () => portal.get('/jobs'),
  createJob: (payload) => portal.post('/jobs', payload),
  getJob: (jobID) => portal.get(`/jobs/${id(jobID)}`),
  cancelJob: (jobID) => portal.post(`/jobs/${id(jobID)}/cancel`, {}),
  resumeJob: (jobID) => portal.post(`/jobs/${id(jobID)}/resume`, {}),
  deleteJob: (jobID) => portal.delete(`/jobs/${id(jobID)}`),
  openJobUrl: (jobID) => `/card-link/jobs/${id(jobID)}/open`,

  createProtocolJob: (payload) => portal.post('/protocol-pay/jobs', payload),
  confirmProtocolBatch: (jobIDs) => portal.post('/protocol-pay/batch-confirm', { job_ids: jobIDs, burst_count: 1 }),
  getProtocolJob: (jobID) => portal.get(`/protocol-pay/jobs/${id(jobID)}`),
};

export default cardPaymentPortalApi;
