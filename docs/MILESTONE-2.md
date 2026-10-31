# Milestone 2 — Agency Dashboard

## Scope

Server-rendered HTML dashboard under `/dashboard/`. No authentication, no
tenancy, no billing, no monitoring. Development-prototype banner on every page.

The existing JSON API (`/`, `/health`, `/notes`, `/clients`, `/applications`)
is unchanged. The dashboard does not consume it; it uses the same SQLAlchemy
models directly via `app/services/dashboard.py`.

## Stack choices

- **Jinja2** server-side templates. No build step, no Node.
- **HTMX 2.0.11**, vendored at `app/static/htmx.min.js` (BSD 2-Clause, see
  `app/static/htmx.LICENSE`). No CDN at runtime.
- **Hand-written CSS** at `app/static/style.css`.
- Progressive enhancement: every form has a real `action` and `method`. With
  JavaScript disabled, forms submit normally and the server returns a 303
  redirect. With HTMX present, edit forms swap the affected `<tr>` in place.

## Routes

| Method | Path | Purpose |
|---|---|---|
| GET | `/dashboard/` | Stats tiles + environment distribution |
| GET | `/dashboard/clients` | Client list |
| GET | `/dashboard/clients/{id}` | Client detail + applications |
| POST | `/dashboard/clients` | Create client (form) |
| POST | `/dashboard/clients/{id}/edit` | Update client (form) |
| POST | `/dashboard/clients/{id}/applications` | Create application (form) |
| POST | `/dashboard/applications/{id}/edit` | Update application (form) |

## Progressive-enhancement contract

- `GET` navigation works with or without HTMX.
- Create forms always redirect (303) — works without HTMX.
- Edit forms return an HTML fragment when `HX-Request: true`, otherwise 303.
- Forms are wrapped in `<details>` so edit UI is reachable with CSS only.

## HTMX upgrade procedure

    curl -fsSL https://unpkg.com/htmx.org@<version>/dist/htmx.min.js \
      -o app/static/htmx.min.js
    grep -o 'version:"[^"]*"' app/static/htmx.min.js

Update `app/static/htmx.LICENSE` only if the upstream license changes.

## Deliberate limitations

- No delete buttons in the UI (JSON API still supports delete).
- No search, filter, or sort beyond ID ordering.
- No pagination in the UI.
