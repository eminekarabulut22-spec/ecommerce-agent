from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ecommerce_agent.agent.orchestrator import ProductAgent
from ecommerce_agent.api.dependencies import get_db_session, get_product_agent
from ecommerce_agent.api.schemas import (
    ErrorResponse,
    HealthResponse,
    ProcessProductResponse,
    ProductListItem,
    ProductListResponse,
)
from ecommerce_agent.db import repository
from ecommerce_agent.llm.client import ImageMediaType, LLMClientError

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SAMPLE_IMAGES_DIR = _REPO_ROOT / "data" / "sample_images"
_FRONTEND_DIR = _REPO_ROOT / "frontend"

app = FastAPI(
    title="E-Commerce Agent API",
    description="Agentic product ingestion: image -> multimodal extraction -> LLM-driven tool use -> structured record.",
    version="0.1.0",
)

_ALLOWED_IMAGE_MEDIA_TYPES: frozenset[str] = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/gif"}
)
_MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB

# Serves the actual image bytes referenced by `source_image_url` / `image_url` - the agent
# itself never persists uploaded images anywhere, it only stores the filename/path string.
if _SAMPLE_IMAGES_DIR.is_dir():
    app.mount("/sample-images", StaticFiles(directory=str(_SAMPLE_IMAGES_DIR)), name="sample-images")


# --- Error handling -------------------------------------------------------------------------
#
# ProductAgent.run() is designed to never raise for extraction or agent-decision failures - it
# always returns a structured AgentRunResult (outcome=EXTRACTION_FAILED / AGENT_ERROR / ...).
# Those are reported as normal 200 responses whose body carries the outcome, since the endpoint
# did what was asked. The handlers below cover what's left: things that stop the agent from
# running at all (a bad upload, credentials/dependency setup, the database being unreachable).


@app.exception_handler(LLMClientError)
async def handle_llm_client_error(request: Request, exc: LLMClientError) -> JSONResponse:
    return JSONResponse(
        status_code=502,
        content=ErrorResponse(error="llm_error", detail=str(exc)).model_dump(),
    )


@app.exception_handler(SQLAlchemyError)
async def handle_database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content=ErrorResponse(error="database_error", detail=str(exc)).model_dump(),
    )


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(error="internal_error", detail=str(exc)).model_dump(),
    )


# --- Routes ----------------------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()


@app.get("/products", response_model=ProductListResponse)
def list_products(
    session: Annotated[Session, Depends(get_db_session)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> ProductListResponse:
    products = repository.list_products(session, limit=limit)
    items = [ProductListItem.from_product(p) for p in products]
    return ProductListResponse(products=items, count=len(items))


@app.post(
    "/products/process",
    response_model=ProcessProductResponse,
    responses={400: {"model": ErrorResponse}, 502: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def process_product(
    image: Annotated[UploadFile, File(description="Product photo (jpeg/png/webp/gif)")],
    agent: Annotated[ProductAgent, Depends(get_product_agent)],
) -> ProcessProductResponse:
    if image.content_type not in _ALLOWED_IMAGE_MEDIA_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported image type '{image.content_type}'. "
                f"Allowed: {sorted(_ALLOWED_IMAGE_MEDIA_TYPES)}."
            ),
        )

    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Uploaded image is empty.")
    if len(image_bytes) > _MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=400, detail=f"Image exceeds the {_MAX_IMAGE_BYTES}-byte limit."
        )

    media_type: ImageMediaType = image.content_type  # type: ignore[assignment]  # validated above

    result = agent.run(
        image_bytes=image_bytes,
        image_media_type=media_type,
        source_image_url=image.filename or "uploaded-image",
    )
    return ProcessProductResponse.from_agent_result(result)


# Serves the static catalog frontend. Mounted last, at "/", so it only ever catches requests
# the routes above did not already handle - it cannot shadow /health, /products, etc.
if _FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")
