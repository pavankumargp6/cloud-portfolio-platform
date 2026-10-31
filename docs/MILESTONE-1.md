# Milestone 1 — Agency Application Management

## Scope

Local, single-agency prototype. No authentication, no tenancy, no monitoring.
Synthetic data only.

## Migrations

Schema is owned by Alembic (`alembic/versions/0001_initial.py`). The container
entrypoint runs `alembic upgrade head` before starting Uvicorn. Startup
`CREATE TABLE` is no longer used.

### Safe on both fresh and existing databases

The initial migration creates `notes` with `CREATE TABLE IF NOT EXISTS`, so:

- Fresh volume: `notes`, `clients`, `applications` and enums are created.
- Existing volume (old code ran, `notes` already exists): the `notes`
  statement is a no-op; `clients`, `applications` and enums are created; the
  revision is stamped. No note rows are touched.

### Local commands

    docker compose exec api alembic upgrade head     # apply
    docker compose exec api alembic current          # inspect
    docker compose exec api alembic history
    docker compose exec api alembic downgrade base   # does NOT drop `notes`

### Deliberate limitations

- `notes` is legacy; it is not associated with clients or applications.
- `base_url` is syntactically validated only. This is not SSRF protection.
- Enums are PostgreSQL native types.
- `updated_at` is set by the ORM, not a database trigger.

## Delete-client policy

`DELETE /clients/{id}` returns 409 if the client has any applications.
There is no `?cascade=true`. Applications must be removed or archived first.
The foreign key is `ON DELETE RESTRICT` as defence-in-depth.
