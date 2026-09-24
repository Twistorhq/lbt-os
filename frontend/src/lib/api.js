import axios from 'axios'

const BASE_URL = import.meta.env.VITE_API_URL || ''

const api = axios.create({
  baseURL: `${BASE_URL}/api/v1`,
  headers: { 'Content-Type': 'application/json' },
})

// Separate instance for billing routes — lives under /api/stripe, not /api/v1
const billingAxios = axios.create({
  baseURL: `${BASE_URL}/api/stripe`,
  headers: { 'Content-Type': 'application/json' },
})

// Inject Clerk token on every request
export function setAuthToken(token) {
  if (token) {
    api.defaults.headers.common['Authorization'] = `Bearer ${token}`
    billingAxios.defaults.headers.common['Authorization'] = `Bearer ${token}`
  } else {
    delete api.defaults.headers.common['Authorization']
    delete billingAxios.defaults.headers.common['Authorization']
  }
}

// ---- Organizations ----
export const orgApi = {
  create: (data)          => api.post('/organizations', data),
  bootstrapDemo: (data)   => api.post('/organizations/bootstrap-demo', data),
  getMe: ()               => api.get('/organizations/me'),
  updateMe: (data)        => api.patch('/organizations/me', data),
  workspaceStatus: ()     => api.get('/organizations/workspace-status'),
  reseedDemo: (data)      => api.post('/organizations/demo/reseed', data),
  clearDemo: ()           => api.post('/organizations/demo/clear'),
  getTemplates: ()        => api.get('/organizations/templates'),
  getTemplate: (industry) => api.get(`/organizations/templates/${industry}`),
}

// ---- Leads ----
export const leadsApi = {
  list:    (params) => api.get('/leads', { params }),
  create:  (data)   => api.post('/leads', data),
  get:     (id)     => api.get(`/leads/${id}`),
  update:  (id, d)  => api.patch(`/leads/${id}`, d),
  delete:  (id)     => api.delete(`/leads/${id}`),
  convert: (id)     => api.post(`/leads/${id}/convert`),
}

// ---- Customers ----
export const customersApi = {
  list:   (params) => api.get('/customers', { params }),
  create: (data)   => api.post('/customers', data),
  get:    (id)     => api.get(`/customers/${id}`),
  update: (id, d)  => api.patch(`/customers/${id}`, d),
  delete: (id)     => api.delete(`/customers/${id}`),
}

// ---- Sales ----
export const salesApi = {
  list:   (params) => api.get('/sales', { params }),
  create: (data)   => api.post('/sales', data),
  get:    (id)     => api.get(`/sales/${id}`),
  update: (id, d)  => api.patch(`/sales/${id}`, d),
  delete: (id)     => api.delete(`/sales/${id}`),
}

// ---- Expenses ----
export const expensesApi = {
  list:   (params) => api.get('/expenses', { params }),
  create: (data)   => api.post('/expenses', data),
  get:    (id)     => api.get(`/expenses/${id}`),
  update: (id, d)  => api.patch(`/expenses/${id}`, d),
  delete: (id)     => api.delete(`/expenses/${id}`),
}

// ---- Metrics ----
export const metricsApi = {
  dashboard:    (days = 30)           => api.get('/metrics/dashboard', { params: { days } }),
  revenueTrend: (weeks = 12)          => api.get('/metrics/revenue-trend', { params: { weeks } }),
  segments:     (days = 30)           => api.get('/metrics/segments', { params: { days } }),
  forecast:     (lookback_weeks = 16) => api.get('/metrics/forecast', { params: { lookback_weeks } }),
}

// ---- AI Audit ----
export const auditApi = {
  run:       () => api.post('/audit/run'),
  latest:    () => api.get('/audit/latest'),
  history:   (limit = 10) => api.get('/audit/history', { params: { limit } }),
  exportPdf: () => api.get('/audit/latest/export.pdf', { responseType: 'blob' }),
}

// ---- Billing ----
export const billingApi = {
  checkout:       (plan)      => billingAxios.post(`/billing/checkout?plan=${plan}`),
  checkoutStatus: (sessionId) => billingAxios.get(`/billing/checkout/session/${sessionId}`),
  portal:         ()          => billingAxios.post('/billing/portal'),
}

// ---- Integrations ----
export const integrationsApi = {
  providers:   () => api.get('/integrations/providers'),
  overview:    (params) => api.get('/integrations/overview', { params }),
  connections: () => api.get('/integrations/connections'),
  createConnection: (data) => api.post('/integrations/connections', data),
  disconnect:  (id) => api.delete(`/integrations/connections/${id}`),
  syncRuns:    (limit = 20) => api.get('/integrations/sync-runs', { params: { limit } }),
  startOAuth:  (provider) => api.post(`/integrations/oauth/${provider}/start`),
  syncOne:     (id) => api.post(`/integrations/connections/${id}/sync`),
  syncAll:     () => api.post('/integrations/sync-all'),
  manualImport: (entityType, file) => {
    const form = new FormData()
    form.append('file', file)
    return api.post(`/integrations/manual-import?entity_type=${entityType}`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  importHistory: (limit = 30) => api.get('/integrations/import-history', { params: { limit } }),
  exportWorkspace: () => api.get('/integrations/export/workspace.zip', { responseType: 'blob' }),
  recurringScan: () => api.post('/integrations/recurring-scan'),
}

// ---- Admin ----
export const adminApi = {
  me:          ()                       => api.get('/admin/me'),
  stats:       ()                       => api.get('/admin/stats'),
  integrationHealth: (params)           => api.get('/admin/integrations/health', { params }),
  orgs:        (params)                 => api.get('/admin/organizations', { params }),
  getOrg:      (id)                     => api.get(`/admin/organizations/${id}`),
  updatePlan:  (id, plan)               => api.patch(`/admin/organizations/${id}/plan`, { plan }),
  updateStatus:(id, subscription_status)=> api.patch(`/admin/organizations/${id}/status`, { subscription_status }),
}

// ---- Messages ----
export const messagesApi = {
  bots:          ()                          => api.get('/messages/bots'),
  channels:      ()                          => api.get('/messages/channels'),
  createChannel: (body)                      => api.post('/messages/channels', body),
  getMessages:   (channelId, params)         => api.get(`/messages/channels/${channelId}/messages`, { params }),
  sendMessage:   (channelId, body)           => api.post(`/messages/channels/${channelId}/messages`, body),
  askAI:         (channelId, question)       => api.post(`/messages/channels/${channelId}/ask`, { question }),
  react:         (messageId, emoji)          => api.post(`/messages/messages/${messageId}/react`, { emoji }),
  uploadFile:    (channelId, file)           => {
    const form = new FormData()
    form.append('file', file)
    return api.post(`/messages/channels/${channelId}/files`, form, { headers: { 'Content-Type': 'multipart/form-data' } })
  },
  getFileUrl:    (fileId)                    => api.get(`/messages/files/${fileId}/url`),
  exportXlsx:    (channelId)                 => api.get(`/messages/channels/${channelId}/export.xlsx`, { responseType: 'arraybuffer' }),
}

// ---- Revenue Intelligence ----
export const revenueIntelligenceApi = {
  ltv:          ()                              => api.get('/revenue-intelligence/ltv'),
  stageVelocity:(days = 90)                     => api.get('/revenue-intelligence/stage-velocity', { params: { days } }),
  winLoss:      (days = 90)                     => api.get('/revenue-intelligence/win-loss', { params: { days } }),
  dataQuality:  ()                              => api.get('/revenue-intelligence/data-quality'),
  expansion:    ()                              => api.get('/revenue-intelligence/expansion'),
  speedToLead:  (days = 30)                     => api.get('/revenue-intelligence/speed-to-lead', { params: { days } }),
  stageAging:   ()                              => api.get('/revenue-intelligence/stage-aging'),
}

// ---- Leak engine (morning brief) ----
export const leakApi = {
  brief:            ()                                    => api.get('/leaks/brief'),
  detectors:        ()                                    => api.get('/leaks/detectors'),
}

// ---- Strategy ----
export const strategyApi = {
  briefing:           ()                                    => api.get('/strategy/briefing'),
  ask:                (question, conversation_history = []) => api.post('/strategy/ask', { question, conversation_history }),
  searchCompetitors:  (extra_query = '', max_results = 8)   => api.post('/strategy/search-competitors', { extra_query, max_results }),
  analyzeCompetitors: (competitor_urls)                     => api.post('/strategy/analyze-competitors', { competitor_urls }),
}

// ---- Visitor Events ----
export const visitorEventsApi = {
  capture: (data)             => api.post('/visitor-events', data),
  recent:  (limit = 50)       => api.get('/visitor-events/recent', { params: { limit } }),
  summary: (days = 7)         => api.get('/visitor-events/summary', { params: { days } }),
}

export default api
