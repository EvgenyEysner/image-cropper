import io
import base64
import logging
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from rembg import remove, new_session
from PIL import Image

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Freistellung Service", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Modelle: u2net (Standard), u2net_human_seg, isnet-general-use (bestes Ergebnis)
session = new_session("isnet-general-use")
logger.info("rembg Modell geladen: isnet-general-use")


class FreistellungRequest(BaseModel):
    image_base64: str          # Base64-kodiertes Bild (ohne data:image/... Prefix)
    format: str = "jpeg"       # "jpeg" oder "png"
    quality: int = 90          # JPEG-Qualität 1-100
    bg_color: str = "white"    # Hintergrundfarbe für JPEG: "white", "black" oder "#rrggbb"
    force_opaque_foreground: bool = False
    alpha_threshold: int = 160


class FreistellungResponse(BaseModel):
    success: bool
    image_base64: str          # Freigestelltes Bild als Base64
    format: str
    original_size_kb: float
    result_size_kb: float
    message: str = ""


class UploadFreistellungResult(BaseModel):
    filename: str
    success: bool
    original_image_base64: str = ""
    result_image_base64: str = ""
    format: str = ""
    original_size_kb: float = 0.0
    result_size_kb: float = 0.0
    message: str = ""


def hex_to_rgb(hex_color: str) -> tuple:
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def process_image_bytes(
    img_bytes: bytes,
    output_format: str = "jpeg",
    quality: int = 90,
    bg_color: str = "white",
    force_opaque_foreground: bool = False,
    alpha_threshold: int = 160,
) -> tuple[str, float]:
    """
    Stellt das Bild frei und gibt zurück data-url image + size in KB.
    """
    quality = max(1, min(100, int(quality)))
    alpha_threshold = max(1, min(255, int(alpha_threshold)))
    result_bytes = remove(img_bytes, session=session, post_process_mask=True)
    result_img = Image.open(io.BytesIO(result_bytes)).convert("RGBA")

    if force_opaque_foreground:
        r, g, b, a = result_img.split()
        hard_alpha = a.point(lambda pixel: 255 if pixel >= alpha_threshold else 0)
        result_img = Image.merge("RGBA", (r, g, b, hard_alpha))

    output_buffer = io.BytesIO()
    if output_format.lower() == "png":
        result_img.save(output_buffer, format="PNG", optimize=True)
        mime = "image/png"
    else:
        if bg_color.lower() == "white":
            bg_rgb = (255, 255, 255)
        elif bg_color.lower() == "black":
            bg_rgb = (0, 0, 0)
        else:
            try:
                bg_rgb = hex_to_rgb(bg_color)
            except Exception:
                bg_rgb = (255, 255, 255)

        background = Image.new("RGB", result_img.size, bg_rgb)
        background.paste(result_img, mask=result_img.split()[3])
        background.save(
            output_buffer,
            format="JPEG",
            quality=quality,
            optimize=True,
        )
        mime = "image/jpeg"

    result_kb = round(len(output_buffer.getvalue()) / 1024, 1)
    result_b64 = base64.b64encode(output_buffer.getvalue()).decode("utf-8")
    return f"data:{mime};base64,{result_b64}", result_kb


@app.get("/health")
def health():
    return {"status": "ok", "model": "isnet-general-use"}


@app.post("/freistellung", response_model=FreistellungResponse)
async def freistellung(req: FreistellungRequest):
    try:
        # --- Base64 to Bytes
        clean_b64 = req.image_base64.split(",")[-1]  # data:image/... entfernen falls vorhanden
        img_bytes = base64.b64decode(clean_b64)
        original_kb = round(len(img_bytes) / 1024, 1)
        logger.info(f"Bild empfangen: {original_kb} KB")

        result_data_url, result_kb = process_image_bytes(
            img_bytes=img_bytes,
            output_format=req.format,
            quality=req.quality,
            bg_color=req.bg_color,
            force_opaque_foreground=req.force_opaque_foreground,
            alpha_threshold=req.alpha_threshold,
        )

        logger.info(f"Freistellung abgeschlossen: {original_kb} KB → {result_kb} KB")

        return FreistellungResponse(
            success=True,
            image_base64=result_data_url,
            format=req.format.lower(),
            original_size_kb=original_kb,
            result_size_kb=result_kb,
            message=f"Erfolgreich freigestellt: {original_kb} KB → {result_kb} KB",
        )

    except Exception as e:
        logger.error(f"Fehler bei Freistellung: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/freistellung/batch")
async def freistellung_batch(requests: list[FreistellungRequest]):
    """Mehrere Bilder auf einmal freistellen"""
    results = []
    for i, req in enumerate(requests):
        try:
            result = await freistellung(req)
            results.append(result.dict())
        except Exception as e:
            results.append({"success": False, "message": str(e), "index": i})
    return {"results": results, "total": len(requests)}


@app.post("/freistellung/upload-batch")
@app.post("/upload-batch")
async def freistellung_upload_batch(
    files: list[UploadFile] = File(...),
    format: str = Form("jpeg"),
    quality: int = Form(90),
    bg_color: str = Form("white"),
    force_opaque_foreground: bool = Form(True),
    alpha_threshold: int = Form(160),
):
    """
    Mehrteiliger Endpunkt für lokale UI-Tests mit dem Hochladen mehrerer Dateien.
    Gibt das Originalbild und das bearbeitete Bild als Daten-URLs zurück.
    """
    results: list[UploadFreistellungResult] = []

    for upload in files:
        filename = upload.filename or "unknown"
        try:
            content = await upload.read()
            if not content:
                results.append(
                    UploadFreistellungResult(
                        filename=filename,
                        success=False,
                        message="Leere Datei",
                    )
                )
                continue

            original_kb = round(len(content) / 1024, 1)
            original_b64 = base64.b64encode(content).decode("utf-8")
            content_type = upload.content_type or "application/octet-stream"
            original_data_url = f"data:{content_type};base64,{original_b64}"

            result_data_url, result_kb = process_image_bytes(
                img_bytes=content,
                output_format=format,
                quality=quality,
                bg_color=bg_color,
                force_opaque_foreground=force_opaque_foreground,
                alpha_threshold=alpha_threshold,
            )

            results.append(
                UploadFreistellungResult(
                    filename=filename,
                    success=True,
                    original_image_base64=original_data_url,
                    result_image_base64=result_data_url,
                    format=format.lower(),
                    original_size_kb=original_kb,
                    result_size_kb=result_kb,
                    message=f"Erfolgreich freigestellt: {original_kb} KB → {result_kb} KB",
                )
            )
        except Exception as e:
            logger.error(f"Fehler bei Datei {filename}: {e}")
            results.append(
                UploadFreistellungResult(
                    filename=filename,
                    success=False,
                    message=str(e),
                )
            )

    return {"total": len(results), "results": [item.model_dump() for item in results]}