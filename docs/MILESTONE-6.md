# Milestone 6 — Backup Verification

## Scope
Local, single-agency prototype. No authentication, no real cloud storage integrations.

## Architecture
The system introduces two tables:
- `backup_records`: Metadata about a backup (location, size, timestamp).
- `backup_verifications`: The history of restore-test attempts.

A `RestoreBackend` abstraction is used. The default implementation is `SimulatedRestoreBackend`, which creates an ephemeral SQLite database, executes a dummy schema, and verifies data integrity. 

## Simulation Limitation
**We do not claim that a backup is recoverable merely because a backup file exists.** 

The current implementation uses a *simulation* to prove the orchestration and state machine work. In this local prototype, it does not actually download a real remote backup file, nor does it run `pg_restore` against a real PostgreSQL instance. 

To implement a real restore test in the future (M8+), a `PostgresRestoreBackend` must be created that:
1. Fetches the backup from secure cloud storage.
2. Spins up a temporary PostgreSQL container/instance.
3. Runs `pg_restore`.
4. Executes a test query.
5. Tears down the temporary instance.
