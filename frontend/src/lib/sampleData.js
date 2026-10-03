/**
 * TW-295 — sample-data payloads for public-first browsing.
 *
 * Every record is fictional (555 phones, .example emails) and carries
 * is_demo: true where the backend convention exists. Nothing here may be
 * mistaken for a real business.
 *
 * Prepared by Twistor Holdings LLC.
 */

const SHOP_NAME = 'Mile High Air Pros'

export const SAMPLE_ORG = {
  id: 'sample-org-001',
  name: SHOP_NAME,
  plan: 'basic',
  trade: 'hvac',
  city: 'Denver',
  state: 'CO',
  email: 'shop@example.com',
  is_demo: true,
}

export const SAMPLE_WORKSPACE_STATUS = {
  workspace_mode: 'demo',
  template: 'hvac',
  latest_sync: null,
  latest_audit: null,
  is_demo: true,
}

export const SAMPLE_METRICS_DASHBOARD = {
  revenue: {
    total: 48250,
    by_source: { website: 18200, referral: 12400, google: 9600, repeat: 8050 },
    margin_pct: 34.2,
  },
  leads: {
    total: 38,
    won: 9,
    lost: 6,
    missed_follow_ups: 4,
    conversion_rate_pct: 23.7,
  },
  customers: { total: 122, repeat: 31, repeat_pct: 25.4 },
  expenses: {
    total: 31800,
    by_category: {
      payroll: 14000,
      parts: 8200,
      fuel: 3100,
      ads: 2800,
      insurance: 2400,
      other: 1300,
    },
  },
  analyst_brief: { variance_breakdown: {}, data_confidence: 0.85 },
  is_demo: true,
}

export const SAMPLE_REVENUE_TREND = [
  { date: '2026-07-13', revenue: 9800 },
  { date: '2026-07-20', revenue: 11200 },
  { date: '2026-07-27', revenue: 10500 },
  { date: '2026-08-03', revenue: 12100 },
  { date: '2026-08-10', revenue: 11800 },
  { date: '2026-08-17', revenue: 13400 },
  { date: '2026-08-24', revenue: 12900 },
  { date: '2026-08-31', revenue: 14100 },
  { date: '2026-09-07', revenue: 13700 },
  { date: '2026-09-14', revenue: 15200 },
  { date: '2026-09-21', revenue: 14800 },
  { date: '2026-09-28', revenue: 15600 },
]

export const SAMPLE_SEGMENTS = {
  segments: [
    { source: 'website', revenue: 18200, conversion_rate_pct: 24.1, leads: 14 },
    { source: 'referral', revenue: 12400, conversion_rate_pct: 31.5, leads: 9 },
    { source: 'google', revenue: 9600, conversion_rate_pct: 18.2, leads: 11 },
    { source: 'repeat', revenue: 8050, conversion_rate_pct: 42.0, leads: 4 },
  ],
  best_by_conversion: { source: 'repeat' },
  best_by_revenue: { source: 'website' },
  channel_insight:
    'Repeat customers convert at nearly double the rate of any acquisition channel — a reactivation offer is the cheapest growth lever in this sample.',
  is_demo: true,
}

export const SAMPLE_FORECAST = {
  trend_direction: 'growing',
  weekly_change_pct: 2.4,
  summary: { next_30_days: 52000, next_60_days: 108000, next_90_days: 167000 },
  narrative:
    'Sample forecast: revenue is trending up about 2.4% per week on demo data. Connect your tools to forecast from real history.',
  is_demo: true,
}

export const SAMPLE_LEADS = [
  {
    id: 'sample-lead-1',
    name: 'Maria Delgado',
    email: 'maria.d@example.com',
    phone: '(555) 010-2231',
    source: 'website',
    status: 'new',
    service_interest: 'AC replacement',
    estimated_value: 8500,
    notes: 'Sample record — not a real person.',
  },
  {
    id: 'sample-lead-2',
    name: 'James Okafor',
    email: 'j.okafor@example.com',
    phone: '(555) 010-4472',
    source: 'referral',
    status: 'contacted',
    service_interest: 'Furnace tune-up',
    estimated_value: 189,
    notes: 'Sample record — not a real person.',
  },
  {
    id: 'sample-lead-3',
    name: 'Priya Nair',
    email: 'priya.n@example.com',
    phone: '(555) 010-8814',
    source: 'google',
    status: 'qualified',
    service_interest: 'Duct cleaning',
    estimated_value: 450,
    notes: 'Sample record — not a real person.',
  },
  {
    id: 'sample-lead-4',
    name: 'Tom Becker',
    email: 'tom.b@example.com',
    phone: '(555) 010-1190',
    source: 'yelp',
    status: 'proposal',
    service_interest: 'Heat pump install',
    estimated_value: 12400,
    notes: 'Sample record — not a real person.',
  },
  {
    id: 'sample-lead-5',
    name: 'Aisha Rahman',
    email: 'aisha.r@example.com',
    phone: '(555) 010-6623',
    source: 'website',
    status: 'won',
    service_interest: 'AC repair',
    estimated_value: 620,
    notes: 'Sample record — not a real person.',
  },
  {
    id: 'sample-lead-6',
    name: 'Dan Whitfield',
    email: 'dan.w@example.com',
    phone: '(555) 010-3358',
    source: 'cold_call',
    status: 'lost',
    service_interest: 'Maintenance plan',
    estimated_value: 299,
    notes: 'Sample record — not a real person.',
  },
]

export const SAMPLE_CUSTOMERS = [
  {
    id: 'sample-cust-1',
    name: 'Maria Delgado',
    phone: '(555) 010-2231',
    total_orders: 3,
    lifetime_value: 9400,
    last_purchase_at: '2026-09-18T00:00:00Z',
    tags: ['repeat', 'maintenance-plan'],
  },
  {
    id: 'sample-cust-2',
    name: 'Robert Chen',
    phone: '(555) 010-7741',
    total_orders: 1,
    lifetime_value: 620,
    last_purchase_at: '2026-08-02T00:00:00Z',
    tags: ['new'],
  },
  {
    id: 'sample-cust-3',
    name: 'Angela Foster',
    phone: '(555) 010-9902',
    total_orders: 5,
    lifetime_value: 15200,
    last_purchase_at: '2026-09-27T00:00:00Z',
    tags: ['repeat', 'commercial'],
  },
  {
    id: 'sample-cust-4',
    name: 'Sam Ortiz',
    phone: '(555) 010-5517',
    total_orders: 2,
    lifetime_value: 2100,
    last_purchase_at: '2026-06-14T00:00:00Z',
    tags: ['at-risk'],
  },
]

export const SAMPLE_SALES = [
  {
    id: 'sample-sale-1',
    service: 'AC replacement',
    amount: 8500,
    profit: 2890,
    payment_status: 'paid',
    source: 'website',
    sold_at: '2026-09-20T00:00:00Z',
  },
  {
    id: 'sample-sale-2',
    service: 'Furnace tune-up',
    amount: 189,
    profit: 120,
    payment_status: 'paid',
    source: 'repeat',
    sold_at: '2026-09-22T00:00:00Z',
  },
  {
    id: 'sample-sale-3',
    service: 'Duct cleaning',
    amount: 450,
    profit: 280,
    payment_status: 'pending',
    source: 'google',
    sold_at: '2026-09-25T00:00:00Z',
  },
  {
    id: 'sample-sale-4',
    service: 'Heat pump install',
    amount: 12400,
    profit: 4216,
    payment_status: 'partial',
    source: 'referral',
    sold_at: '2026-09-28T00:00:00Z',
  },
]

export const SAMPLE_EXPENSES = [
  {
    id: 'sample-exp-1',
    description: 'Technician payroll',
    category: 'payroll',
    amount: 14000,
    vendor: 'Sample Payroll Co.',
    is_recurring: true,
    expense_date: '2026-09-30T00:00:00Z',
  },
  {
    id: 'sample-exp-2',
    description: 'Capacitors and contactors',
    category: 'parts',
    amount: 8200,
    vendor: 'Sample Supply House',
    is_recurring: false,
    expense_date: '2026-09-21T00:00:00Z',
  },
  {
    id: 'sample-exp-3',
    description: 'Fleet fuel',
    category: 'fuel',
    amount: 3100,
    vendor: 'Sample Fuel Co.',
    is_recurring: true,
    expense_date: '2026-09-29T00:00:00Z',
  },
  {
    id: 'sample-exp-4',
    description: 'Google Ads',
    category: 'ads',
    amount: 2800,
    vendor: 'Sample Ads Platform',
    is_recurring: true,
    expense_date: '2026-09-30T00:00:00Z',
  },
]

export const SAMPLE_RI_LTV = {
  total_customers: 122,
  avg_ltv: 2850,
  total_revenue: 347700,
  top_customers: [
    { name: 'Angela Foster', ltv: 15200, orders: 5 },
    { name: 'Maria Delgado', ltv: 9400, orders: 3 },
    { name: 'Sam Ortiz', ltv: 2100, orders: 2 },
  ],
  is_demo: true,
}

export const SAMPLE_RI_VELOCITY = {
  stages: [
    { stage: 'new', avg_days: 1.8 },
    { stage: 'contacted', avg_days: 3.4 },
    { stage: 'qualified', avg_days: 5.2 },
    { stage: 'proposal', avg_days: 7.9 },
  ],
  is_demo: true,
}

export const SAMPLE_RI_WINLOSS = {
  cohorts: [
    { source: 'website', won: 12, lost: 8, win_rate: 60 },
    { source: 'referral', won: 9, lost: 3, win_rate: 75 },
    { source: 'google', won: 7, lost: 9, win_rate: 43.8 },
  ],
  is_demo: true,
}

export const SAMPLE_RI_DATA_QUALITY = {
  grade: 'B',
  overall_score: 78,
  lead_count: 38,
  customer_count: 122,
  sale_count: 41,
  fields: [
    { name: 'phone', complete_pct: 92 },
    { name: 'email', complete_pct: 84 },
    { name: 'service_interest', complete_pct: 71 },
    { name: 'estimated_value', complete_pct: 63 },
  ],
  is_demo: true,
}

export const SAMPLE_RI_EXPANSION = {
  signals: [
    {
      id: 'sample-exp-sig-1',
      name: 'Angela Foster',
      email: 'angela.f@example.com',
      lifetime_value: 15200,
      days_inactive: 45,
    },
    {
      id: 'sample-exp-sig-2',
      name: 'Sam Ortiz',
      email: 'sam.o@example.com',
      lifetime_value: 2100,
      days_inactive: 110,
    },
  ],
  is_demo: true,
}

export const SAMPLE_RI_SPEED = {
  by_source: [
    { source: 'website', avg_hours: 3.2 },
    { source: 'referral', avg_hours: 1.4 },
    { source: 'google', avg_hours: 6.8 },
  ],
  overall_avg_hours: 4.1,
  is_demo: true,
}

export const SAMPLE_RI_AGING = {
  stages: [
    { stage: 'new', count: 9 },
    { stage: 'contacted', count: 14 },
    { stage: 'qualified', count: 8 },
    { stage: 'proposal', count: 7 },
  ],
  total_open: 38,
  is_demo: true,
}

export const SAMPLE_BOTS = {
  bots: [
    { id: 'bot_answer', name: 'Answer Bot', description: 'Sample assistant — answers from demo CRM data.' },
  ],
  is_demo: true,
}

export const SAMPLE_CHANNELS = [
  { id: 'sample-ch-1', name: 'dispatch' },
  { id: 'sample-ch-2', name: 'front-office' },
]

export const SAMPLE_MESSAGES = {
  messages: [
    {
      id: 'sample-msg-1',
      sender_id: 'tech_ana',
      sender_name: 'Ana Torres',
      content: 'Running 20 min behind on the Elm St. tune-up — parts run took longer than expected.',
      created_at: '2026-10-02T14:32:00Z',
      message_type: 'text',
      reactions: {},
    },
    {
      id: 'sample-msg-2',
      sender_id: 'ai_assistant',
      sender_name: 'Answer Bot',
      content: 'Sample reply: 2 tune-ups are still unscheduled for tomorrow. Want me to draft the reminder texts?',
      created_at: '2026-10-02T14:33:00Z',
      message_type: 'text',
      reactions: { '👍': ['tech_ana'] },
    },
  ],
  is_demo: true,
}

export const SAMPLE_INTEGRATIONS_OVERVIEW = {
  providers: [
    { key: 'quickbooks', name: 'QuickBooks', category: 'accounting', connected: false },
    { key: 'stripe', name: 'Stripe', category: 'payments', connected: false },
    { key: 'hubspot', name: 'HubSpot', category: 'crm', connected: false },
  ],
  connections: [],
  sync_runs: [],
  import_history: [],
  summary: { connected_count: 0, last_sync_at: null },
  is_demo: true,
}

export const SAMPLE_LEAK_BRIEF = {
  totals: { findings: 3, dollars_at_stake: 4850 },
  generated_at: '2026-10-03T07:00:00Z',
  data_status: 'sample',
  what_happened: [
    {
      detector: 'missed_followup',
      severity: 'urgent',
      title: '4 warm leads never got a follow-up',
      detail: 'Sample finding: 4 demo leads went quiet after first contact. At a 24% close rate that is roughly $4,850 walking out the door.',
      estimated_value: 4850,
      count: 4,
      is_demo: true,
    },
  ],
  what_will_happen: [
    {
      detector: 'slow_response',
      severity: 'warning',
      title: 'Google leads wait 6.8 hours for first contact',
      detail: 'Sample finding: every hour of delay measurably lowers close rate. A 15-minute response target would recover an estimated share of these.',
      estimated_value: 0,
      is_demo: true,
    },
  ],
  what_should_we_do: [
    {
      detector: 'reactivation',
      severity: 'info',
      title: '2 past customers are going quiet',
      detail: 'Sample finding: a simple tune-up reminder to the 2 inactive sample customers typically books 1 in 4.',
      estimated_value: 900,
      count: 2,
      is_demo: true,
    },
  ],
  is_demo: true,
}

export const SAMPLE_AUDIT_LATEST = {
  generated_at: '2026-10-01T09:00:00Z',
  period_start: '2026-09-01',
  period_end: '2026-09-30',
  model_used: 'sample',
  health_score: 72,
  health_rationale: 'Sample audit: pipeline is active but follow-up speed drags the score. Connect your tools for a real diagnosis.',
  biggest_leverage_point: 'Sample: cutting first-response time from 6.8 hours to under 1 hour is the single biggest lever in this demo data.',
  insights: [
    { title: 'Follow-up speed', detail: 'Sample: Google leads wait 6.8h on average.', dollar_impact: 2400 },
    { title: 'Repeat rate', detail: 'Sample: 25.4% of customers return — healthy for HVAC.', dollar_impact: 0 },
  ],
  recommendations: [
    { title: 'Set a 15-minute response SLA', effort: 'low', dollar_impact: 2400 },
    { title: 'Launch a fall tune-up reactivation offer', effort: 'medium', dollar_impact: 1800 },
  ],
  is_truncated: false,
  is_demo: true,
}

export const SAMPLE_STRATEGY_BRIEFING = {
  health_score: 72,
  health_label: 'Growing',
  signals: [
    {
      type: 'opportunity',
      icon: '◈',
      title: 'Reactivation window is open',
      detail: 'Sample: fall tune-up season starts in 3 weeks and 31 past customers have no booked visit.',
      action: 'Draft the tune-up offer this week',
    },
    {
      type: 'warning',
      icon: '⚠',
      title: 'Google lead response lags',
      detail: 'Sample: 6.8h average first response vs. a 15-minute best practice.',
      action: 'Assign a response owner per shift',
    },
  ],
  is_demo: true,
}

/**
 * [matcher, payload] — matcher is an exact path or a RegExp tested against
 * the request path. Payload may be a value or a function(url) => value.
 * Order matters: first match wins.
 */
export const SAMPLE_GETS = [
  ['/organizations/me', SAMPLE_ORG],
  ['/organizations/workspace-status', SAMPLE_WORKSPACE_STATUS],
  ['/metrics/dashboard', SAMPLE_METRICS_DASHBOARD],
  ['/metrics/revenue-trend', SAMPLE_REVENUE_TREND],
  ['/metrics/segments', SAMPLE_SEGMENTS],
  ['/metrics/forecast', SAMPLE_FORECAST],
  ['/leads', SAMPLE_LEADS],
  ['/customers', SAMPLE_CUSTOMERS],
  ['/sales', SAMPLE_SALES],
  ['/expenses', SAMPLE_EXPENSES],
  ['/revenue-intelligence/ltv', SAMPLE_RI_LTV],
  ['/revenue-intelligence/stage-velocity', SAMPLE_RI_VELOCITY],
  ['/revenue-intelligence/win-loss', SAMPLE_RI_WINLOSS],
  ['/revenue-intelligence/data-quality', SAMPLE_RI_DATA_QUALITY],
  ['/revenue-intelligence/expansion', SAMPLE_RI_EXPANSION],
  ['/revenue-intelligence/speed-to-lead', SAMPLE_RI_SPEED],
  ['/revenue-intelligence/stage-aging', SAMPLE_RI_AGING],
  ['/messages/bots', SAMPLE_BOTS],
  ['/messages/channels', SAMPLE_CHANNELS],
  [/^\/messages\/channels\/[^/]+\/messages/, SAMPLE_MESSAGES],
  ['/integrations/overview', SAMPLE_INTEGRATIONS_OVERVIEW],
  ['/leaks/brief', SAMPLE_LEAK_BRIEF],
  ['/audit/latest', SAMPLE_AUDIT_LATEST],
  ['/audit/history', []],
  ['/strategy/briefing', SAMPLE_STRATEGY_BRIEFING],
]
