from typing import Literal

from pydantic import BaseModel

ModelHint = Literal["product", "high-quality", "person", "general"]


class CroppingRequest(BaseModel):
    image_base64: str  # Base64-kodiertes Bild
    format: str = "jpeg"  # "jpeg" oder "png"
    quality: int = 90  # JPEG-Qualität 1-100
    bg_color: str = (
        "white"  # Hintergrundfarbe für JPEG: "white", "black" oder "#rrggbb"
    )
    force_opaque_foreground: bool = False
    alpha_threshold: int = 160
    # Modell-Hint: product (Standard, schnell), high-quality, person, general
    model_hint: ModelHint = "product"


class CroppingResponse(BaseModel):
    success: bool
    image_base64: str  # Freigestelltes Bild als Base64
    format: str
    original_size_kb: float
    result_size_kb: float
    message: str = ""


class UploadCroppingResult(BaseModel):
    filename: str
    success: bool
    original_image_base64: str = ""
    result_image_base64: str = ""
    format: str = ""
    original_size_kb: float = 0.0
    result_size_kb: float = 0.0
    message: str = ""
