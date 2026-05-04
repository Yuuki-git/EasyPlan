from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_backend_dockerfile_uses_slim_python_and_exposes_8000():
    content = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "python:3.11-slim" in content
    assert "EXPOSE 8000" in content
    assert "--no-cache-dir" in content
    assert "uvicorn" in content


def test_frontend_dockerfile_builds_static_assets_with_nginx():
    content = (ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM node:" in content
    assert "npm run build" in content
    assert "FROM nginx:" in content
    assert "/usr/share/nginx/html" in content


def test_compose_defines_pgvector_backend_frontend_and_healthchecks():
    content = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "db:" in content
    assert "pgvector/pgvector:" in content
    assert "postgres_data:" in content
    assert "backend:" in content
    assert "depends_on:" in content
    assert "condition: service_healthy" in content
    assert "env_file:" in content
    assert "- .env" in content
    assert "curl -fsS http://localhost:8000/health" in content
    assert "frontend:" in content
    assert "8080:80" in content
