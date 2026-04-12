"""Tenant management CLI and TenantManager facade."""

import argparse
import os
import subprocess
import sys

from dotenv import load_dotenv

from api.auth import PLAN_LIMITS, TenantStore, VALID_PLANS


class TenantManager:
    """High-level facade over TenantStore for application use."""

    def __init__(self, store: TenantStore):
        self._store = store

    # -- queries -------------------------------------------------------------

    def get(self, tenant_id: str):
        return self._store.get_by_id(tenant_id)

    def get_by_key(self, api_key: str):
        return self._store.get_by_api_key(api_key)

    def list(self):
        return self._store.list_all()

    # -- mutations -----------------------------------------------------------

    def create(
        self,
        tenant_id: str,
        name: str,
        granicus_host: str,
        granicus_view_id: str = "",
        plan: str = "starter",
    ):
        return self._store.create(tenant_id, name, granicus_host, granicus_view_id, plan)

    def delete(self, tenant_id: str) -> bool:
        return self._store.delete(tenant_id)

    def rotate_key(self, tenant_id: str):
        return self._store.rotate_key(tenant_id)

    def update_plan(self, tenant_id: str, plan: str) -> bool:
        return self._store.update_plan(tenant_id, plan)

    @staticmethod
    def plan_definitions() -> dict:
        return {
            plan: {"queries_per_month": limit if limit else "unlimited"}
            for plan, limit in PLAN_LIMITS.items()
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _get_store(output_dir: str) -> TenantStore:
    db_path = os.path.join(output_dir, "tenants.db")
    return TenantStore(db_path)


def cmd_create(args, store: TenantStore):
    try:
        tenant = store.create(
            tenant_id=args.id,
            name=args.name,
            granicus_host=args.granicus_host,
            granicus_view_id=args.granicus_view_id or "",
            plan=args.plan,
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Created tenant: {tenant.id}")
    print(f"  Name:           {tenant.name}")
    print(f"  Granicus host:  {tenant.granicus_host}")
    print(f"  Granicus view:  {tenant.granicus_view_id}")
    print(f"  Plan:           {tenant.plan}")
    print(f"  API key:        {tenant.api_key}")
    print()
    print("Store this API key securely -- it cannot be retrieved later.")


def cmd_list(args, store: TenantStore):
    tenants = store.list_all()
    if not tenants:
        print("No tenants found.")
        return
    # Column widths
    id_w = max(len(t.id) for t in tenants)
    name_w = max(len(t.name) for t in tenants)
    plan_w = max(len(t.plan) for t in tenants)
    print(f"{'ID':<{id_w}}  {'Name':<{name_w}}  {'Plan':<{plan_w}}  {'Host':<30}  Created")
    print("-" * (id_w + name_w + plan_w + 50))
    for t in tenants:
        created = t.created_at[:10] if t.created_at else "?"
        print(f"{t.id:<{id_w}}  {t.name:<{name_w}}  {t.plan:<{plan_w}}  {t.granicus_host:<30}  {created}")


def cmd_delete(args, store: TenantStore):
    if store.delete(args.id):
        print(f"Deleted tenant: {args.id}")
    else:
        print(f"Tenant not found: {args.id}", file=sys.stderr)
        sys.exit(1)


def cmd_rotate_key(args, store: TenantStore):
    new_key = store.rotate_key(args.id)
    if new_key:
        print(f"Rotated API key for tenant: {args.id}")
        print(f"  New API key: {new_key}")
        print()
        print("Store this API key securely -- it cannot be retrieved later.")
    else:
        print(f"Tenant not found: {args.id}", file=sys.stderr)
        sys.exit(1)


def cmd_update_plan(args, store: TenantStore):
    try:
        if store.update_plan(args.id, args.plan):
            print(f"Updated tenant {args.id} to plan: {args.plan}")
        else:
            print(f"Tenant not found: {args.id}", file=sys.stderr)
            sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_plans(args, store: TenantStore):
    print("Available plans:")
    for plan, limit in PLAN_LIMITS.items():
        limit_str = f"{limit} queries/month" if limit else "unlimited"
        print(f"  {plan:<12} {limit_str}")


def cmd_process(args, store: TenantStore):
    """Run the meeting pipeline for a specific tenant, then ingest into RAG."""
    tenant = store.get_by_id(args.id)
    if tenant is None:
        print(f"Tenant not found: {args.id}", file=sys.stderr)
        sys.exit(1)

    print(f"Processing tenant: {tenant.name} ({tenant.id})")
    print(f"  Granicus host:    {tenant.granicus_host}")
    print(f"  Granicus view ID: {tenant.granicus_view_id}")
    print(f"  Max clips:        {args.max}")

    # Build the main.py command with tenant context
    cmd = [
        sys.executable, "-m", "main" if __package__ else os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"
        ),
        "--tenant-id", tenant.id,
        "--output-dir", args.output_dir,
        "--scrape",
        "--max", str(args.max),
        "--rag",
    ]

    print(f"\nRunning: {' '.join(cmd)}\n")

    result = subprocess.run(cmd, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.exit(result.returncode)


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(
        prog="python -m api.tenants",
        description="Manage tenants for the Meeting API",
    )
    parser.add_argument(
        "--output-dir",
        default=os.environ.get("MEETINGS_OUTPUT_DIR", "./meetings_output"),
        help="Output directory containing tenants.db",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # create
    p_create = sub.add_parser("create", help="Create a new tenant")
    p_create.add_argument("--id", required=True, help="Unique tenant identifier (e.g. 'lexington-ky')")
    p_create.add_argument("--name", required=True, help="Display name")
    p_create.add_argument("--granicus-host", required=True, help="Granicus hostname (e.g. lexington.granicus.com)")
    p_create.add_argument("--granicus-view-id", default="", help="Granicus view ID")
    p_create.add_argument("--plan", default="starter", choices=sorted(VALID_PLANS), help="Pricing plan")

    # list
    sub.add_parser("list", help="List all tenants")

    # delete
    p_delete = sub.add_parser("delete", help="Delete a tenant")
    p_delete.add_argument("--id", required=True, help="Tenant ID to delete")

    # rotate-key
    p_rotate = sub.add_parser("rotate-key", help="Rotate a tenant's API key")
    p_rotate.add_argument("--id", required=True, help="Tenant ID")

    # update-plan
    p_plan = sub.add_parser("update-plan", help="Change a tenant's plan")
    p_plan.add_argument("--id", required=True, help="Tenant ID")
    p_plan.add_argument("--plan", required=True, choices=sorted(VALID_PLANS), help="New plan")

    # plans
    sub.add_parser("plans", help="Show available plans and limits")

    # process
    p_process = sub.add_parser("process", help="Run meeting pipeline for a tenant (scrape + ingest)")
    p_process.add_argument("--id", required=True, help="Tenant ID to process")
    p_process.add_argument("--max", type=int, default=10, help="Maximum clips to process (default: 10)")

    args = parser.parse_args()
    store = _get_store(args.output_dir)

    commands = {
        "create": cmd_create,
        "list": cmd_list,
        "delete": cmd_delete,
        "rotate-key": cmd_rotate_key,
        "update-plan": cmd_update_plan,
        "plans": cmd_plans,
        "process": cmd_process,
    }

    try:
        commands[args.command](args, store)
    finally:
        store.close()


if __name__ == "__main__":
    main()
