# EasyPlan Backend

EasyPlan is an intent-driven task planning backend built around FastAPI, Pydantic, SQLAlchemy, and LangGraph.

## Runtime

- Recommended Python: **3.11+** for stronger async runtime performance.
- Minimum verified locally: Python 3.10.
- Install dependencies:

```bash
python -m pip install -r requirements.txt
```

## API Contract

The generated OpenAPI contract lives at:

```text
docs/openapi.json
```

When changing request or response fields, update the FastAPI schema and regenerate `docs/openapi.json` in the same change.

## Tests

```bash
python -m pytest tests -q
```

## Local App

```bash
uvicorn app.main:app --reload
```
