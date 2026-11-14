## ADDED Requirements

### Requirement: Metrics startup preserves native HTTP access logging
Starting the secondary metrics server SHALL preserve the primary Uvicorn logger handlers, formatter and level. Both main HTTP requests and metrics scrapes SHALL retain structured native access records with populated client, request and status fields. Request query values SHALL be excluded from those records.

#### Scenario: Main requests continue logging after a metrics scrape
- **GIVEN** a primary server using JSON access logging
- **WHEN** the native metrics server starts and both HTTP ports receive requests
- **THEN** the primary logger configuration remains intact
- **AND** both responses have corresponding populated JSON access records without query values
