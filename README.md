# Cloud Portfolio Platform

A containerized three-tier application built with FastAPI, PostgreSQL, Nginx, Docker and Docker Compose.

## Architecture

- Nginx: Reverse proxy and application entry point.
- FastAPI: REST API for creating and retrieving notes.
- PostgreSQL: Persistent relational database.
- Docker Compose: Container orchestration and service health checks.
- GitHub Actions: Automated build and integration testing.

## Features

- REST API with Swagger documentation.
- PostgreSQL-backed note creation and retrieval.
- Application and database health checks.
- Persistent database storage using Docker volumes.
- Nginx reverse proxy.
- Automated integration tests.
- GitHub Actions CI workflow.

## Requirements

- Docker
- Docker Compose
- Python 3

## Run locally

1. Clone the repository.
2. Copy `.env.example` to `.env`.
3. Run `docker compose up -d --build`.

Open the API documentation at http://localhost:8080/docs.

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | / | API information |
| GET | /health | Application and database health |
| POST | /notes | Create a note |
| GET | /notes | Retrieve notes |

## Run integration tests

Start the application, then run:

`python3 -m unittest discover -s tests -v`

## Database Persistence

PostgreSQL data is stored in the Docker named volume `postgres_data`.

Use `docker compose down` followed by `docker compose up -d` to recreate containers without deleting the database volume.

## Continuous Integration

GitHub Actions validates the Compose configuration, builds the services, checks API readiness and runs integration tests.

## Security

The local `.env` file is excluded from Git. Use `.env.example` as a configuration template.

The sample credentials are intended only for local development.
