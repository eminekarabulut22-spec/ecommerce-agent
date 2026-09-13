#!/usr/bin/env python3
"""Post every supported product image in a directory to a running ecommerce-agent
API's /products/process endpoint and print a concise result for each one.

Usage:
    uvicorn ecommerce_agent.api.main:app --reload           # in one terminal
    python scripts/batch_process_images.py                  # in another
    python scripts/batch_process_images.py --dir data/sample_images --url http://localhost:8000

This only talks to the endpoint over HTTP - it does not import or call the agent
directly, so it exercises exactly what a real client would.
"""

from __future__ import annotations

import argparse
import mimetypes
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

SUPPORTED_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp"})
ENDPOINT_PATH = "/products/process"


@dataclass
class BatchResult:
    filename: str
    outcome: str | None  # None means the request itself never got a response to read
    product_id: str | None = None
    reason: str | None = None


def iter_supported_images(directory: Path) -> list[Path]:
    """Every file directly under `directory` with a supported image extension, sorted by
    name. Unsupported files (including README.md) are silently skipped."""
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text
    return payload.get("detail") or payload.get("error") or str(payload)


def process_image(client: httpx.Client, image_path: Path, *, base_url: str) -> BatchResult:
    """Send one image to /products/process and turn the response into a BatchResult.

    Never raises: network errors and non-200 responses are captured in the result so the
    caller can keep processing the rest of the batch.
    """
    media_type = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"

    try:
        with image_path.open("rb") as image_file:
            response = client.post(
                f"{base_url}{ENDPOINT_PATH}",
                files={"image": (image_path.name, image_file, media_type)},
                timeout=httpx.Timeout(300.0, connect=10.0),
            )
    except httpx.HTTPError as exc:
        return BatchResult(filename=image_path.name, outcome=None, reason=f"Request failed: {exc}")

    if response.status_code != 200:
        return BatchResult(
            filename=image_path.name,
            outcome=None,
            reason=f"HTTP {response.status_code}: {_error_detail(response)}",
        )

    body = response.json()
    outcome = body.get("outcome")
    product = body.get("product")

    return BatchResult(
        filename=image_path.name,
        outcome=outcome,
        product_id=(product or {}).get("id"),
        reason=None if outcome == "saved" else body.get("reason"),
    )


def format_result(result: BatchResult) -> str:
    parts = [result.filename, f"outcome={result.outcome or 'error'}"]
    if result.product_id:
        parts.append(f"product_id={result.product_id}")
    if result.reason:
        parts.append(f"reason={result.reason}")
    return " | ".join(parts)


def run_batch(directory: Path, *, base_url: str, client: httpx.Client | None = None) -> list[BatchResult]:
    images = iter_supported_images(directory)
    owns_client = client is None
    client = client or httpx.Client()
    results: list[BatchResult] = []
    try:
        for image_path in images:
            result = process_image(client, image_path, base_url=base_url)
            results.append(result)
            print(format_result(result))
    finally:
        if owns_client:
            client.close()
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir",
        type=Path,
        default=Path("data/sample_images"),
        help="Directory of product images to process (default: data/sample_images)",
    )
    parser.add_argument("--url", default="http://localhost:8000", help="Base URL of the running API")
    args = parser.parse_args(argv)

    if not args.dir.is_dir():
        print(f"No such directory: {args.dir}", file=sys.stderr)
        return 1

    results = run_batch(args.dir, base_url=args.url)

    failed = sum(1 for r in results if r.outcome is None)
    print(f"\nProcessed {len(results)} image(s); {failed} failed to reach the agent.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
