// Prepared by Twistor Holdings LLC.
//
// Sample dataset for the Trade View internal command edition page.
// These are DEMO records — fictional companies used to exercise the UI.
// Every record carries provenance. Never treat these as real businesses.

export const SAMPLE_PROVENANCE = 'Twistor demo sample set — fictional companies, not real businesses.'

export const SAMPLE_COMPANIES = [
  {
    id: 'sample-hvac-01',
    name: 'Mile High Air Pros',
    kind: 'prospect',
    trade: 'HVAC',
    address: '1420 Blake St, Denver, CO 80202',
    lat: 39.7525,
    lng: -104.9995,
    phone: '(303) 555-0142',
    owner: 'D. Rivera',
    email: 'hello@milehighairpros.example',
    pitch: 'No follow-up system — quoted jobs go cold after 48h.',
    marketing: 'Google Business Profile is thin; 12 reviews, no posts since March.',
    provenance: SAMPLE_PROVENANCE,
    publicRecords: [
      { label: 'Colorado Secretary of State — business search', url: 'https://www.sos.colorado.gov' },
      { label: 'DORA — license lookup', url: 'https://dora.colorado.gov' },
    ],
    insights: null, // not assessed — UI must show honest empty states
  },
  {
    id: 'sample-plumb-02',
    name: 'Front Range Flow Plumbing',
    kind: 'prospect',
    trade: 'Plumbing',
    address: '8800 E Colfax Ave, Denver, CO 80220',
    lat: 39.74,
    lng: -104.88,
    phone: '(303) 555-0177',
    owner: 'A. Chen',
    email: 'office@frontrangeflow.example',
    pitch: 'After-hours calls go to voicemail — emergency work leaks to competitors.',
    marketing: 'No website booking; Facebook page last updated 2024.',
    provenance: SAMPLE_PROVENANCE,
    publicRecords: [
      { label: 'Colorado Secretary of State — business search', url: 'https://www.sos.colorado.gov' },
    ],
    insights: null,
  },
  {
    id: 'sample-elec-03',
    name: 'Denver Current Electric',
    kind: 'client',
    trade: 'Electrical',
    address: '300 W 11th Ave, Denver, CO 80204',
    lat: 39.7218,
    lng: -104.9876,
    phone: '(303) 555-0119',
    owner: 'M. Okafor',
    email: 'team@denvercurrent.example',
    pitch: 'Active pilot — What Happened tier since September.',
    marketing: 'Monthly content cadence running; review velocity up 3x.',
    provenance: SAMPLE_PROVENANCE,
    publicRecords: [
      { label: 'Colorado Secretary of State — business search', url: 'https://www.sos.colorado.gov' },
      { label: 'DORA — license lookup', url: 'https://dora.colorado.gov' },
    ],
    insights: null,
  },
]

export const TRADE_FILTERS = ['HVAC', 'Plumbing', 'Electrical']
export const KIND_FILTERS = ['prospect', 'client']

// Insight panel definitions — sample data carries none, so the UI renders
// honest "not assessed" states. Panels still document what they would show.
export const INSIGHT_PANELS = [
  { id: 'digital-grade', title: 'Digital maturity grade', desc: 'Letter grade + what is missing online.' },
  { id: 'ghost-web', title: 'Ghost-web presence', desc: 'Score /100 + directory checklist.' },
  { id: 'ad-pressure', title: 'Competitor ad pressure', desc: 'Who is bidding on their keywords.' },
  { id: 'repeat', title: 'Repeat-customer signals', desc: 'Membership and maintenance-plan fit.' },
]
