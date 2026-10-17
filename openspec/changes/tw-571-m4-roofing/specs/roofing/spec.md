# Roofing pro pack (TW-571)

## ADDED Requirements

### Requirement: Estimating takeoff computes exact-cent line items
The system SHALL compute roofing takeoff line items with Decimal exact-cent
math and store the takeoff payload on `quotes.attributes.takeoff`.

#### Scenario: Takeoff with waste
- GIVEN a quote with no takeoff and material `architectural_shingle`
- WHEN `POST /quotes/{id}/takeoff` receives
  `{squares: 28.5, pitch: "6/12", waste_pct: 10, material: "architectural_shingle"}`
- THEN material quantity is 31.35 squares, material amount is $4,545.75,
  labor amount is $2,707.50, and `quotes.attributes.takeoff` holds the inputs.

#### Scenario: Unknown material rejected
- WHEN `POST /quotes/{id}/takeoff` receives an unknown material
- THEN the API returns 422 and no quote row is modified.

### Requirement: Purchase orders send to suppliers
The system SHALL transition a draft PO to sent and email the supplier's
ordering address.

#### Scenario: Send transitions status
- GIVEN a draft PO with an ordering email
- WHEN `POST /purchase-orders/{id}/send` is called
- THEN status becomes `sent`, `sent_at` is set, `total_cost` equals the
  exact-cent sum of line items, and a toll event `po.sent` is recorded.

### Requirement: Storm campaigns target existing customers only
The system SHALL create storm campaigns from keyless NWS alerts and message
only existing customers in affected ZIPs.

#### Scenario: Campaign creates leads
- GIVEN an active NWS hail alert and customers with matching ZIPs in address
- WHEN `POST /storms/campaigns` is called
- THEN a campaign row and one lead per recipient (`source='storm_campaign'`)
  exist, and toll events `storm.campaign_launched` + per-recipient
  `sms.auto_reply` are recorded.

#### Scenario: Weather service down
- GIVEN api.weather.gov is unreachable
- WHEN `GET /storms/recent?zip=` is called
- THEN the API returns 502 and no fabricated alerts are returned.
