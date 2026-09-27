#!/usr/bin/env python3
"""Post a product image to a running ecommerce-agent API and print the result.

Usage:
    uvicorn ecommerce_agent.api.main:app --reload   # in one terminal
    python scripts/run_demo.py path/to/photo.jpg    # in another
"""

import argparse
import json
import mimetypes
import sys
from pathlib import Path

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image_path", type=Path, help="Path to a product image file")
    parser.add_argument(
        "--url", default="http://localhost:8000", help="Base URL of the running API"
    )
    parser.add_argument("--price", type=float, required=True, help="Business-provided selling price")
    parser.add_argument(
        "--currency",
        default="USD",
        help="Business-provided 3-letter currency code (default: USD)",
    )
    args = parser.parse_args()

    if not args.image_path.is_file():
        print(f"No such file: {args.image_path}", file=sys.stderr)
        return 1

    media_type = mimetypes.guess_type(args.image_path.name)[0] or "application/octet-stream"

    with args.image_path.open("rb") as image_file:
        response = httpx.post(
            f"{args.url}/products/process",
            files={"image": (args.image_path.name, image_file, media_type)},
            data={"price": str(args.price), "currency": args.currency},
            timeout=120.0,
        )

    print(f"HTTP {response.status_code}")
    print(json.dumps(response.json(), indent=2, default=str))
    return 0 if response.is_success else 1


if __name__ == "__main__":
    raise SystemExit(main())
