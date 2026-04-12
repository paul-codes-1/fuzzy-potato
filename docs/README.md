# CivicLens Docs

Use this as the entry point for the repo.

## Start Here

- [PRODUCT_GUIDE.md](PRODUCT_GUIDE.md): what CivicLens does, the feature inventory, and the major user workflows.
- [OPERATOR_GUIDE.md](OPERATOR_GUIDE.md): what actually runs, where state lives, and the day-2 operator model.
- [ARCHITECTURE_REVIEW.md](ARCHITECTURE_REVIEW.md): architecture review, current risks, and the path to an enterprise-grade deployment.
- [QUICKSTART.md](QUICKSTART.md): first query against the API.
- [SELF_HOSTING.md](SELF_HOSTING.md): local and containerized deployment guidance.

## Core Technical Docs

- [API.md](API.md): authenticated API endpoints and request/response shapes.
- [DATABASE.md](DATABASE.md): SQLite vs PostgreSQL strategy, migration notes, and production guidance.
- [SECURITY.md](SECURITY.md): security controls, auth model, and encryption notes.
- [AUDIT_LOGGING.md](AUDIT_LOGGING.md): audit log behavior.
- [DATA_EXPORT.md](DATA_EXPORT.md): exports and FOIA workflows.

## Integrations

- [SLACK_INTEGRATION.md](SLACK_INTEGRATION.md)
- [TEAMS_INTEGRATION.md](TEAMS_INTEGRATION.md)
- [ZAPIER_INTEGRATION.md](ZAPIER_INTEGRATION.md)
- [SSO_SETUP.md](SSO_SETUP.md)
- [EMBED_WIDGET.md](EMBED_WIDGET.md)

## Repo Map

- `main.py`: ingestion pipeline for Granicus meeting data.
- `api/server.py`: FastAPI entry point for the SaaS/API application.
- `api/query.py`: retrieval and synthesis logic for Q&A and chat.
- `frontend/src/App.jsx`: top-level frontend routes.
- `sdk/`: API clients for external developers.
- `widget/`: embeddable chat widget.
- `infra/`: Terraform deployment config.

## If You Are New To The Codebase

Read these in order:

1. [PRODUCT_GUIDE.md](PRODUCT_GUIDE.md)
2. [OPERATOR_GUIDE.md](OPERATOR_GUIDE.md)
3. [ARCHITECTURE_REVIEW.md](ARCHITECTURE_REVIEW.md)
4. [README.md](../README.md)
