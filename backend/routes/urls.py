import asyncio
import base64
import logging
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response

from schema.models import (
    CroppingRequest,
    CroppingResponse,
    ModelHint,
    UploadCroppingResult,
)
from services.service import (
    DEFAULT_HINT,
    MODEL_REGISTRY,
    process_image_bytes_async,
    process_image_to_raw_bytes_async,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()

_MODEL_HINT_CHOICES = list(MODEL_REGISTRY.keys())


@router.get("/health")
def health():
    return {
        "status": "ok",
        "default_model": DEFAULT_HINT,
        "available_models": _MODEL_HINT_CHOICES,
    }


async def _cropping_from_request(req: CroppingRequest) -> CroppingResponse:
    clean_b64 = req.image_base64.split(",")[-1]
    try:
        img_bytes = base64.b64decode(clean_b64)
    except Exception:
        raise HTTPException(status_code=400, detail="Ungültiger Base64-String")

    original_kb = round(len(img_bytes) / 1024, 1)
    logger.info(f"Bild empfangen: {original_kb}KB (model_hint={req.model_hint})")

    result_data_url, result_kb = await process_image_bytes_async(
        img_bytes,
        output_format=req.format,
        quality=req.quality,
        bg_color=req.bg_color,
        force_opaque_foreground=req.force_opaque_foreground,
        alpha_threshold=req.alpha_threshold,
        model_hint=req.model_hint,
    )

    logger.info(f"Freistellung abgeschlossen: {original_kb}KB > {result_kb}KB")

    return CroppingResponse(
        success=True,
        image_base64=result_data_url,
        format=req.format.lower(),
        original_size_kb=original_kb,
        result_size_kb=result_kb,
        message=f"Erfolgreich freigestellt: {original_kb} KB > {result_kb} KB",
    )


@router.post("/cropping-image", response_model=CroppingResponse)
async def handle_image(req: CroppingRequest):
    try:
        return await _cropping_from_request(req)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Fehler bei Freistellung: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/cropping-image/batch")
async def handle_image_batch(requests: list[CroppingRequest]):
    """Mehrere Bilder parallel freistellen (ThreadPoolExecutor + asyncio.gather)."""

    async def one(i: int, req: CroppingRequest) -> dict:
        try:
            r = await _cropping_from_request(req)
            return r.model_dump()
        except HTTPException as e:
            detail = e.detail
            return {
                "success": False,
                "message": detail if isinstance(detail, str) else str(detail),
                "index": i,
            }
        except Exception as e:
            logger.error(f"Batch Index {i}: {e}")
            return {"success": False, "message": str(e), "index": i}

    results = await asyncio.gather(*(one(i, r) for i, r in enumerate(requests)))
    return {"results": list(results), "total": len(requests)}


@router.post("/cropping-image/upload-batch")
async def handle_upload_batch(
    files: list[UploadFile] = File(...),
    format: str = Form("jpeg"),
    quality: int = Form(90),
    bg_color: str = Form("white"),
    force_opaque_foreground: bool = Form(True),
    alpha_threshold: int = Form(160),
    model_hint: ModelHint = Form("product"),
):
    """
    Mehrteiliger Endpunkt für lokale UI-Tests.
    Dateien werden parallel im Worker-Pool verarbeitet.
    """
    prepared: list[tuple[str, bytes, str]] = []
    for upload in files:
        filename = upload.filename or "unknown"
        content = await upload.read()
        content_type = upload.content_type or "application/octet-stream"
        prepared.append((filename, content, content_type))

    async def process_one(
        filename: str, content: bytes, content_type: str
    ) -> UploadCroppingResult:
        if not content:
            return UploadCroppingResult(
                filename=filename, success=False, message="Leere Datei"
            )
        try:
            original_kb = round(len(content) / 1024, 1)
            original_b64 = base64.b64encode(content).decode("utf-8")
            original_data_url = f"data:{content_type};base64,{original_b64}"

            result_data_url, result_kb = await process_image_bytes_async(
                content,
                output_format=format,
                quality=quality,
                bg_color=bg_color,
                force_opaque_foreground=force_opaque_foreground,
                alpha_threshold=alpha_threshold,
                model_hint=model_hint,
            )

            return UploadCroppingResult(
                filename=filename,
                success=True,
                original_image_base64=original_data_url,
                result_image_base64=result_data_url,
                format=format.lower(),
                original_size_kb=original_kb,
                result_size_kb=result_kb,
                message=f"Erfolgreich freigestellt: {original_kb} KB → {result_kb} KB",
            )
        except Exception as e:
            logger.error(f"Fehler bei Datei {filename}: {e}")
            return UploadCroppingResult(
                filename=filename, success=False, message=str(e)
            )

    results = await asyncio.gather(*(process_one(fn, c, ct) for fn, c, ct in prepared))
    return {"total": len(results), "results": [item.model_dump() for item in results]}


@router.post(
    "/n8n/freistellung",
    summary="Freistellung für n8n (rohe Bilddaten)",
    description=(
        "In n8n: HTTP Request, Methode POST, Body Binary, Feldname file/image/data. "
        "Response als Datei (Binary Property z. B. 'data')."
    ),
    response_class=Response,
)
async def n8n_freistellung(
    file: UploadFile | None = File(None, description="Bevorzugter Feldname"),
    image: UploadFile | None = File(None, description="Alternativer Feldname"),
    data: UploadFile | None = File(None, description="Wie Binary-Property nach OpenAI"),
    output_format: Literal["png", "jpeg"] = Query(
        "png", description="png = Transparenz; jpeg = weißer Hintergrund"
    ),
    quality: int = Query(90, ge=1, le=100),
    bg_color: str = Query("white"),
    force_opaque_foreground: bool = Query(False, description="Nur für JPEG sinnvoll"),
    alpha_threshold: int = Query(160, ge=1, le=255),
    model_hint: str = Query(
        DEFAULT_HINT, description=f"Modell: {', '.join(_MODEL_HINT_CHOICES)}"
    ),
):
    upload = file or image or data
    if upload is None:
        raise HTTPException(
            status_code=422,
            detail="Multipart-Feld erwartet: file, image oder data.",
        )
    content = await upload.read()
    if not content:
        raise HTTPException(status_code=400, detail="Leere Datei.")

    if model_hint not in MODEL_REGISTRY:
        model_hint = DEFAULT_HINT

    try:
        raw, mime = await process_image_to_raw_bytes_async(
            content,
            output_format=output_format,
            quality=quality,
            bg_color=bg_color,
            force_opaque_foreground=force_opaque_foreground,
            alpha_threshold=alpha_threshold,
            model_hint=model_hint,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"n8n Freistellung: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e

    ext = "png" if output_format == "png" else "jpg"
    return Response(
        content=raw,
        media_type=mime,
        headers={"Content-Disposition": f'inline; filename="freistellung.{ext}"'},
    )
