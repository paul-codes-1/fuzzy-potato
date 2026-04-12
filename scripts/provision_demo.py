#!/usr/bin/env python3
"""Provision a CivicLens demo for a prospective city customer.

Automates tenant creation, meeting scraping, index generation, and RAG
ingestion so you can go from zero to a working demo in one command.

Usage:
    uv run python scripts/provision_demo.py \
        --host elkgrove.granicus.com \
        --name "Elk Grove, CA" \
        --clips 10

    uv run python scripts/provision_demo.py \
        --host elkgrove.granicus.com \
        --name "Elk Grove, CA" \
        --tenant-id elk-grove-ca \
        --clips 20 \
        --output-dir ./meetings_output
"""

import argparse
import os
import re
import subprocess
import sys
import time

# Ensure the project root is on sys.path so api imports work.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


def slugify(name: str) -> str:
    """Turn a display name like 'Elk Grove, CA' into 'elk-grove-ca'."""
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def step_header(num: int, total: int, description: str) -> None:
    print(f"\n{'='*60}")
    print(f"  Step {num}/{total}: {description}")
    print(f"{'='*60}\n")


def run_command(cmd: list[str], description: str) -> None:
    """Run a subprocess command, streaming output. Raises on failure."""
    print(f"  Running: {' '.join(cmd)}\n")
    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        raise RuntimeError(
            f"{description} failed (exit code {result.returncode}).\n"
            f"  Command: {' '.join(cmd)}\n"
            f"  You can retry this step manually."
        )


def create_tenant(tenant_id: str, name: str, host: str, output_dir: str) -> str:
    """Create the tenant via TenantManager and return the API key."""
    from dotenv import load_dotenv
    load_dotenv()

    from api.auth import TenantStore
    from api.tenants import TenantManager

    db_path = os.path.join(output_dir, "tenants.db")
    store = TenantStore(db_path)
    manager = TenantManager(store)

    # Check if tenant already exists
    existing = manager.get(tenant_id)
    if existing:
        print(f"  Tenant '{tenant_id}' already exists. Reusing it.")
        print(f"  (API key is hidden -- rotate with: uv run python -m api.tenants rotate-key --id {tenant_id})")
        return existing.api_key

    tenant = manager.create(
        tenant_id=tenant_id,
        name=name,
        granicus_host=host,
        plan="pro",
    )
    print(f"  Created tenant: {tenant.id}")
    print(f"  Plan:           {tenant.plan}")
    print(f"  API key:        {tenant.api_key}")
    return tenant.api_key


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Provision a CivicLens demo for a prospective city customer.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  uv run python scripts/provision_demo.py \\\n"
            '    --host elkgrove.granicus.com --name "Elk Grove, CA" --clips 10'
        ),
    )
    parser.add_argument("--host", required=True, help="Granicus hostname (e.g. elkgrove.granicus.com)")
    parser.add_argument("--name", required=True, help="City display name (e.g. 'Elk Grove, CA')")
    parser.add_argument("--tenant-id", default=None, help="Tenant ID (auto-generated from --name if omitted)")
    parser.add_argument("--clips", type=int, default=10, help="Number of clips to process (default: 10)")
    parser.add_argument("--output-dir", default="./meetings_output", help="Output directory (default: ./meetings_output)")

    args = parser.parse_args()
    tenant_id = args.tenant_id or slugify(args.name)
    output_dir = os.path.abspath(args.output_dir)
    total_steps = 5

    print("\nCivicLens Demo Provisioning")
    print(f"  Tenant:   {tenant_id}")
    print(f"  City:     {args.name}")
    print(f"  Host:     {args.host}")
    print(f"  Clips:    {args.clips}")
    print(f"  Output:   {output_dir}")

    os.makedirs(output_dir, exist_ok=True)
    start = time.time()

    # -- Step 1: Create tenant -------------------------------------------------
    step_header(1, total_steps, "Create tenant")
    try:
        api_key = create_tenant(tenant_id, args.name, args.host, output_dir)
    except Exception as e:
        print(f"  ERROR: {e}", file=sys.stderr)
        print(f"\n  To retry: uv run python -m api.tenants create --id {tenant_id} "
              f'--name "{args.name}" --granicus-host {args.host} --plan pro')
        sys.exit(1)

    # -- Step 2: Scrape and process meetings -----------------------------------
    step_header(2, total_steps, f"Scrape and process {args.clips} meetings")
    try:
        run_command(
            ["uv", "run", "python", "main.py",
             "--tenant-id", tenant_id,
             "--scrape", "--max", str(args.clips)],
            "Meeting pipeline",
        )
    except RuntimeError as e:
        print(f"  ERROR: {e}", file=sys.stderr)
        print(f"\n  To retry: uv run python main.py --tenant-id {tenant_id} --scrape --max {args.clips}")
        sys.exit(1)

    # -- Step 3: Generate search index -----------------------------------------
    step_header(3, total_steps, "Generate search index")
    try:
        run_command(
            ["uv", "run", "python", "main.py",
             "--tenant-id", tenant_id,
             "--generate-index"],
            "Index generation",
        )
    except RuntimeError as e:
        print(f"  ERROR: {e}", file=sys.stderr)
        print(f"\n  To retry: uv run python main.py --tenant-id {tenant_id} --generate-index")
        sys.exit(1)

    # -- Step 4: Ingest into RAG -----------------------------------------------
    step_header(4, total_steps, "Ingest into RAG vector store")
    try:
        run_command(
            ["uv", "run", "python", "-m", "api.ingest", "--all"],
            "RAG ingestion",
        )
    except RuntimeError as e:
        print(f"  ERROR: {e}", file=sys.stderr)
        print("\n  To retry: uv run python -m api.ingest --all")
        sys.exit(1)

    # -- Step 5: Summary -------------------------------------------------------
    elapsed = time.time() - start
    minutes = int(elapsed // 60)
    seconds = int(elapsed % 60)

    step_header(5, total_steps, "Done!")
    print(f"  Provisioning completed in {minutes}m {seconds}s.\n")
    print(f"  Tenant ID:  {tenant_id}")
    print(f"  API Key:    {api_key}")
    print()
    print("  Start the servers:")
    print("    API:       uv run uvicorn api.server:app --reload --port 8000")
    print("    Frontend:  cd frontend && npm run dev")
    print()
    print("  Try it out:")
    print(f'    curl -H "X-API-Key: {api_key}" http://localhost:8000/api/v1/ask \\')
    print('      -d \'{"question": "What were the main topics discussed?"}\' \\')
    print('      -H "Content-Type: application/json"')
    print()
    print("  Frontend:    http://localhost:5173")
    print("  API docs:    http://localhost:8000/docs")
    print()


if __name__ == "__main__":
    main()
