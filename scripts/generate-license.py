from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.license import make_license_key  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an Enprato license key.")
    parser.add_argument("--plan", choices=["monthly", "lifetime"], required=True)
    parser.add_argument("--email", default="")
    parser.add_argument("--order-id", default="")
    parser.add_argument("--days", type=int, default=None, help="Monthly license validity days, default 31.")
    args = parser.parse_args()
    print(make_license_key(plan=args.plan, email=args.email, order_id=args.order_id, days=args.days))


if __name__ == "__main__":
    main()
