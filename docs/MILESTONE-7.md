# Milestone 7 — Client Reporting

## Scope
Generate a professional reliability report that an agency can share with their clients. 

## Architecture
- **Reporting Service (`app/services/reporting.py`)**: Aggregates uptime stats, incidents, and backups over a configurable timeframe (default 30 days). 
- **HTML Report (`/dashboard/clients/{id}/report`)**: A standalone, standalone HTML page with custom print-friendly CSS.
- **Zero-Budget PDF**: The user clicks a "Print / Save as PDF" button (`window.print()`), allowing the browser's native PDF generator to handle the formatting. This keeps the Docker image lean (no WeasyPrint/Puppeteer).

## Features Included
- **Uptime Percentage**: Calculated as `(successful_checks / total_checks) * 100`.
- **Latency**: Average latency of successful checks.
- **Incidents**: A list of incidents within the period, including their duration (MTTR) and resolution notes.
- **Backups**: Metadata and verification status for backups taken during the period.

## Design Decisions
- The report does not extend the main dashboard's `base.html`. It is designed as a standalone document to ensure no navigation bars or development banners appear in the printed PDF.
- Empty states are handled gracefully (e.g., "No incidents reported").
