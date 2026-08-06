import apiClient from './client';

const paypalProtocolApi = {
  status: () => apiClient.paypalProtocol.get('/status'),
  countries: () => apiClient.paypalProtocol.get('/countries'),
  prepare: (payload) => apiClient.paypalProtocol.post('/prepare', payload),
  createJob: (payload) => apiClient.paypalProtocol.post('/jobs', payload),
  listJobs: () => apiClient.paypalProtocol.get('/jobs'),
  getJob: (taskID) => apiClient.paypalProtocol.get(`/jobs/${encodeURIComponent(taskID)}`),
  submitOtp: (taskID, payload) => apiClient.paypalProtocol.post(`/jobs/${encodeURIComponent(taskID)}/otp`, payload),
  deleteJob: (taskID) => apiClient.paypalProtocol.delete(`/jobs/${encodeURIComponent(taskID)}`),
  cardStatus: () => apiClient.paypalProtocol.get('/card/status'),
  createCardJob: (payload) => apiClient.paypalProtocol.post('/card/jobs', payload),
  listCardJobs: () => apiClient.paypalProtocol.get('/card/jobs'),
  inspectCardCheckout: (payload) => apiClient.paypalProtocol.post('/card/checkout-context', payload),
  loadCardElements: (payload) => apiClient.paypalProtocol.post('/card/elements-context', payload),
  preflightCardProxies: (payload) => apiClient.paypalProtocol.post('/card/proxy-preflight', payload),
  getCardJob: (taskID) => apiClient.paypalProtocol.get(`/card/jobs/${encodeURIComponent(taskID)}`),
  deleteCardJob: (taskID) => apiClient.paypalProtocol.delete(`/card/jobs/${encodeURIComponent(taskID)}`),
};

export default paypalProtocolApi;
