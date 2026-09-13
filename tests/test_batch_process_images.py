from pathlib import Path
from typing import Any

import httpx
import pytest

from batch_process_images import (
    BatchResult,
    format_result,
    iter_supported_images,
    main,
    process_image,
    run_batch,
)

BASE_URL = "http://testserver"


def _write(path: Path, content: bytes = b"fake-bytes") -> Path:
    path.write_bytes(content)
    return path


@pytest.fixture()
def image_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "sample_images"
    directory.mkdir()
    _write(directory / "shoe.jpg")
    _write(directory / "bag.jpeg")
    _write(directory / "hat.png")
    _write(directory / "toy.webp")
    _write(directory / "README.md")  # must be skipped
    _write(directory / "notes.txt")  # unsupported, must be skipped
    (directory / "subdir").mkdir()  # not a file, must be skipped
    return directory


# --- iter_supported_images ---------------------------------------------------------------


def test_iter_supported_images_skips_readme_and_unsupported(image_dir: Path) -> None:
    found = [p.name for p in iter_supported_images(image_dir)]

    assert found == ["bag.jpeg", "hat.png", "shoe.jpg", "toy.webp"]
    assert "README.md" not in found
    assert "notes.txt" not in found
    assert "subdir" not in found


# --- process_image / format_result -------------------------------------------------------


def _client_with_handler(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_process_image_saved(tmp_path: Path) -> None:
    image_path = _write(tmp_path / "product.jpg")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == f"{BASE_URL}/products/process"
        return httpx.Response(
            200,
            json={
                "outcome": "saved",
                "reason": "Product passed validation and was saved.",
                "product": {"id": "abc-123", "title": "Widget"},
            },
        )

    with _client_with_handler(handler) as client:
        result = process_image(client, image_path, base_url=BASE_URL)

    assert result == BatchResult(
        filename="product.jpg", outcome="saved", product_id="abc-123", reason=None
    )
    assert format_result(result) == "product.jpg | outcome=saved | product_id=abc-123"


def test_process_image_needs_review(tmp_path: Path) -> None:
    image_path = _write(tmp_path / "product.jpg")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"outcome": "needs_review", "reason": "bad GTIN checksum", "product": None},
        )

    with _client_with_handler(handler) as client:
        result = process_image(client, image_path, base_url=BASE_URL)

    assert result.outcome == "needs_review"
    assert result.product_id is None
    assert result.reason == "bad GTIN checksum"
    assert "reason=bad GTIN checksum" in format_result(result)


def test_process_image_http_error_response(tmp_path: Path) -> None:
    image_path = _write(tmp_path / "product.jpg")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "bad_request", "detail": "Uploaded image is empty."})

    with _client_with_handler(handler) as client:
        result = process_image(client, image_path, base_url=BASE_URL)

    assert result.outcome is None
    assert "HTTP 400" in result.reason
    assert "Uploaded image is empty." in result.reason
    assert format_result(result) == f"product.jpg | outcome=error | reason={result.reason}"


def test_process_image_uses_generous_read_timeout_and_fast_connect_timeout(tmp_path: Path) -> None:
    """A full agent run (extraction + several iterations + sequential web searches) can take
    well over a minute; the read timeout must comfortably cover that. The connect timeout stays
    short so an unreachable server still fails fast."""
    image_path = _write(tmp_path / "product.jpg")
    seen_timeouts: list[httpx.Timeout] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_timeouts.append(request.extensions["timeout"])
        return httpx.Response(200, json={"outcome": "saved", "reason": "ok", "product": {"id": "p-1"}})

    with _client_with_handler(handler) as client:
        process_image(client, image_path, base_url=BASE_URL)

    assert len(seen_timeouts) == 1
    timeout = seen_timeouts[0]
    assert timeout == {"connect": 10.0, "read": 300.0, "write": 300.0, "pool": 300.0}


def test_process_image_network_error(tmp_path: Path) -> None:
    image_path = _write(tmp_path / "product.jpg")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with _client_with_handler(handler) as client:
        result = process_image(client, image_path, base_url=BASE_URL)

    assert result.outcome is None
    assert "Request failed" in result.reason


# --- run_batch: continues past a failing image --------------------------------------------


def test_run_batch_continues_after_failure(image_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    responses_by_name: dict[str, httpx.Response] = {
        "bag.jpeg": httpx.Response(500, json={"error": "internal_error", "detail": "boom"}),
        "hat.png": httpx.Response(
            200, json={"outcome": "saved", "reason": "ok", "product": {"id": "p-1"}}
        ),
        "shoe.jpg": httpx.Response(
            200, json={"outcome": "needs_review", "reason": "low confidence", "product": None}
        ),
        "toy.webp": httpx.Response(
            200, json={"outcome": "saved", "reason": "ok", "product": {"id": "p-2"}}
        ),
    }
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        # Identify which file this request carries by decoding the multipart body's filename.
        body = request.read().decode("utf-8", errors="ignore")
        for name in responses_by_name:
            if name in body:
                seen.append(name)
                return responses_by_name[name]
        raise AssertionError(f"unrecognized request body: {body!r}")

    with _client_with_handler(handler) as client:
        results = run_batch(image_dir, base_url=BASE_URL, client=client)

    assert sorted(seen) == ["bag.jpeg", "hat.png", "shoe.jpg", "toy.webp"]
    assert len(results) == 4

    by_name = {r.filename: r for r in results}
    assert by_name["bag.jpeg"].outcome is None
    assert by_name["hat.png"].outcome == "saved" and by_name["hat.png"].product_id == "p-1"
    assert by_name["shoe.jpg"].outcome == "needs_review"
    assert by_name["toy.webp"].outcome == "saved" and by_name["toy.webp"].product_id == "p-2"

    printed = capsys.readouterr().out
    assert "bag.jpeg" in printed
    assert "hat.png" in printed


# --- main(): CLI wiring, still no real network calls ---------------------------------------


def test_main_reports_directory_not_found(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "does-not-exist"

    exit_code = main(["--dir", str(missing)])

    assert exit_code == 1
    assert "No such directory" in capsys.readouterr().err


def test_main_returns_nonzero_when_any_image_fails(
    image_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_run_batch(directory: Path, *, base_url: str, client: Any = None) -> list[BatchResult]:
        return [
            BatchResult(filename="bag.jpeg", outcome=None, reason="HTTP 500: boom"),
            BatchResult(filename="hat.png", outcome="saved", product_id="p-1"),
        ]

    import batch_process_images

    monkeypatch.setattr(batch_process_images, "run_batch", fake_run_batch)

    exit_code = main(["--dir", str(image_dir)])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "Processed 2 image(s); 1 failed" in out


def test_main_returns_zero_when_all_saved(
    image_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_run_batch(directory: Path, *, base_url: str, client: Any = None) -> list[BatchResult]:
        return [BatchResult(filename="hat.png", outcome="saved", product_id="p-1")]

    import batch_process_images

    monkeypatch.setattr(batch_process_images, "run_batch", fake_run_batch)

    exit_code = main(["--dir", str(image_dir)])

    assert exit_code == 0
