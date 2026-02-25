"""Tests for Docker deployment: entrypoint.sh and server startup behavior."""

import os
import subprocess


class TestEntrypoint:
    """Test entrypoint.sh exists and has correct structure."""

    def test_entrypoint_exists(self):
        entrypoint = os.path.join(os.path.dirname(__file__), "..", "entrypoint.sh")
        assert os.path.exists(entrypoint), "entrypoint.sh must exist at project root"

    def test_entrypoint_is_executable(self):
        entrypoint = os.path.join(os.path.dirname(__file__), "..", "entrypoint.sh")
        assert os.access(entrypoint, os.X_OK), "entrypoint.sh must be executable"

    def test_entrypoint_syncs_chroma_db(self):
        entrypoint = os.path.join(os.path.dirname(__file__), "..", "entrypoint.sh")
        with open(entrypoint) as f:
            content = f.read()
        assert "chroma_db" in content, "entrypoint must sync chroma_db from S3"
        assert "s3://" in content or "$S3_BUCKET" in content, "entrypoint must reference S3"

    def test_entrypoint_syncs_metadata(self):
        entrypoint = os.path.join(os.path.dirname(__file__), "..", "entrypoint.sh")
        with open(entrypoint) as f:
            content = f.read()
        assert "metadata.json" in content, "entrypoint must sync clip metadata from S3"

    def test_entrypoint_starts_uvicorn(self):
        entrypoint = os.path.join(os.path.dirname(__file__), "..", "entrypoint.sh")
        with open(entrypoint) as f:
            content = f.read()
        assert "uvicorn" in content, "entrypoint must start uvicorn"
        assert "8000" in content, "entrypoint must use port 8000"


class TestDockerfile:
    """Test Dockerfile exists and has correct structure."""

    def test_dockerfile_exists(self):
        dockerfile = os.path.join(os.path.dirname(__file__), "..", "Dockerfile")
        assert os.path.exists(dockerfile), "Dockerfile must exist at project root"

    def test_dockerfile_exposes_port_8000(self):
        dockerfile = os.path.join(os.path.dirname(__file__), "..", "Dockerfile")
        with open(dockerfile) as f:
            content = f.read()
        assert "8000" in content, "Dockerfile must expose port 8000"

    def test_dockerfile_installs_rag_deps(self):
        dockerfile = os.path.join(os.path.dirname(__file__), "..", "Dockerfile")
        with open(dockerfile) as f:
            content = f.read()
        assert "rag" in content, "Dockerfile must install RAG dependencies"

    def test_dockerfile_installs_boto3(self):
        dockerfile = os.path.join(os.path.dirname(__file__), "..", "Dockerfile")
        with open(dockerfile) as f:
            content = f.read()
        assert "boto3" in content or "awscli" in content, "Dockerfile must include S3 tools"

    def test_dockerfile_uses_entrypoint(self):
        dockerfile = os.path.join(os.path.dirname(__file__), "..", "Dockerfile")
        with open(dockerfile) as f:
            content = f.read()
        assert "entrypoint" in content.lower(), "Dockerfile must use entrypoint.sh"


class TestDockerignore:
    """Test .dockerignore exists and excludes heavy directories."""

    def test_dockerignore_exists(self):
        dockerignore = os.path.join(os.path.dirname(__file__), "..", ".dockerignore")
        assert os.path.exists(dockerignore), ".dockerignore must exist"

    def test_dockerignore_excludes_output(self):
        dockerignore = os.path.join(os.path.dirname(__file__), "..", ".dockerignore")
        with open(dockerignore) as f:
            content = f.read()
        assert "lfucg_output" in content, ".dockerignore must exclude lfucg_output"

    def test_dockerignore_excludes_frontend(self):
        dockerignore = os.path.join(os.path.dirname(__file__), "..", ".dockerignore")
        with open(dockerignore) as f:
            content = f.read()
        assert "frontend" in content or "node_modules" in content, \
            ".dockerignore must exclude frontend/node_modules"


class TestServerStartupLogging:
    """Test that server logs collection count on startup."""

    def test_server_has_startup_event(self):
        server_path = os.path.join(os.path.dirname(__file__), "..", "rag", "server.py")
        with open(server_path) as f:
            content = f.read()
        assert "startup" in content or "lifespan" in content, \
            "server.py must have a startup event or lifespan for logging"
