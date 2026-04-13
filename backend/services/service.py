import asyncio
import base64
import io
import threading
from concurrent.futures import ThreadPoolExecutor
from logging import getLogger
from typing import Any, cast

from PIL import Image
from rembg import new_session, remove

from utils.helpers import (
    validate_image,
    detect_bg_brightness,
    get_matting_params,
    clean_alpha_edges,
    decontaminate_dark_edges,
    expand_mask_into_product,
    soften_alpha_edges,
    remove_small_components,
    hex_to_rgb,
)

logger = getLogger(__name__)


MODEL_REGISTRY: dict[str, str] = {
    "product": "isnet-general-use",  # Schärfste Kanten, schnell Standard für Produktfotos
    "high-quality": "birefnet-general",  # Beste Qualität, deutlich langsamer
    "person": "u2net_human_seg",  # Optimiert für Personen / Körper
    "general": "u2netp",  # Sehr schnell, gut bei hohem Kontrast
}
DEFAULT_HINT = "high-quality"  # birefnet-general: das Beste für Produktfotos

# Jeder Thread hat sein eigenes Session-Dict (thread-local).
# _model_load_lock verhindert dass mehrere Threads dasselbe Modell
# gleichzeitig laden — das würde OOM / Hänger verursachen.
_thread_local = threading.local()
_model_load_lock = threading.Lock()
executor = ThreadPoolExecutor(max_workers=2)  # 2 weniger RAM, stabiler


def get_session(hint: str = DEFAULT_HINT) -> Any:
    """Gibt die thread-lokale rembg-Session zurück; serialisiert Model-Loading."""
    model_name = MODEL_REGISTRY.get(hint, MODEL_REGISTRY[DEFAULT_HINT])
    sessions: dict[str, Any] = getattr(_thread_local, "sessions", None)  # type: ignore[assignment]
    if sessions is None:
        sessions = {}
        _thread_local.sessions = sessions
    if model_name not in sessions:
        with _model_load_lock:
            # Nochmal prüfen — anderer Thread könnte inzwischen geladen haben
            if model_name not in sessions:
                logger.info(
                    f"Lade rembg-Modell: {model_name} [{threading.current_thread().name}]"
                )
                sessions[model_name] = new_session(model_name)
    return sessions[model_name]


def _resolve_model_hint(hint: str, bg: str) -> str:
    """
    Wählt bei dunklem Hintergrund automatisch ein besseres Modell:

    isnet-general-use (hint='product') ist auf helle Hintergründe optimiert.
    Auf schwarzem/dunklem HG schneidet es bei niedrigem Kontrast zu aggressiv
    aus (graue Handschuhe auf schwarz, dunkle Hose auf schwarz).
    birefnet-general hat deutlich besseres semantisches Verständnis und
    kommt mit niedrigem Kontrast wesentlich besser zurecht.
    """
    if bg == "dark" and hint == "product":
        return "high-quality"  # birefnet-general
    return hint


# --- Kern-Verarbeitung ---
def _cropping_to_buffer(
    img_bytes: bytes,
    *,
    output_format: str = "jpeg",
    quality: int = 90,
    bg_color: str = "white",
    force_opaque_foreground: bool = False,
    alpha_threshold: int = 160,
    model_hint: str = DEFAULT_HINT,
) -> tuple[io.BytesIO, str]:
    validate_image(img_bytes)

    quality = max(1, min(100, int(quality)))
    alpha_threshold = max(1, min(255, int(alpha_threshold)))

    bg = detect_bg_brightness(img_bytes)
    resolved_hint = _resolve_model_hint(model_hint, bg)
    matting_params = get_matting_params()

    logger.info(
        f"Segmentierung: bg={bg} hint={model_hint} model={MODEL_REGISTRY.get(resolved_hint, resolved_hint)}"
    )

    result_bytes = cast(
        bytes,
        remove(
            img_bytes,
            session=get_session(resolved_hint),
            **matting_params,
        ),
    )
    result_img = Image.open(io.BytesIO(result_bytes)).convert("RGBA")
    result_img = clean_alpha_edges(result_img, bg)
    if bg == "dark":
        result_img = decontaminate_dark_edges(result_img)
    else:
        result_img = expand_mask_into_product(result_img)
    result_img = remove_small_components(result_img)
    result_img = soften_alpha_edges(result_img)

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
        background.save(output_buffer, format="JPEG", quality=quality, optimize=True)
        mime = "image/jpeg"

    return output_buffer, mime


def _buffer_to_data_url(buf: io.BytesIO, mime: str) -> tuple[str, float]:
    raw = buf.getvalue()
    result_kb = round(len(raw) / 1024, 1)
    result_b64 = base64.b64encode(raw).decode("utf-8")
    return f"data:{mime};base64,{result_b64}", result_kb


# --- Async-Wrappers (für FastAPI-Endpunkte) ---
async def cropping_to_buffer_async(
    img_bytes: bytes,
    *,
    output_format: str = "jpeg",
    quality: int = 90,
    bg_color: str = "white",
    force_opaque_foreground: bool = False,
    alpha_threshold: int = 160,
    model_hint: str = DEFAULT_HINT,
) -> tuple[io.BytesIO, str]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        executor,
        lambda: _cropping_to_buffer(
            img_bytes,
            output_format=output_format,
            quality=quality,
            bg_color=bg_color,
            force_opaque_foreground=force_opaque_foreground,
            alpha_threshold=alpha_threshold,
            model_hint=model_hint,
        ),
    )


async def process_image_bytes_async(
    img_bytes: bytes,
    output_format: str = "jpeg",
    quality: int = 90,
    bg_color: str = "white",
    force_opaque_foreground: bool = False,
    alpha_threshold: int = 160,
    model_hint: str = DEFAULT_HINT,
) -> tuple[str, float]:
    buf, mime = await cropping_to_buffer_async(
        img_bytes,
        output_format=output_format,
        quality=quality,
        bg_color=bg_color,
        force_opaque_foreground=force_opaque_foreground,
        alpha_threshold=alpha_threshold,
        model_hint=model_hint,
    )
    return _buffer_to_data_url(buf, mime)


async def process_image_to_raw_bytes_async(
    img_bytes: bytes,
    *,
    output_format: str = "png",
    quality: int = 90,
    bg_color: str = "white",
    force_opaque_foreground: bool = False,
    alpha_threshold: int = 160,
    model_hint: str = DEFAULT_HINT,
) -> tuple[bytes, str]:
    buf, mime = await cropping_to_buffer_async(
        img_bytes,
        output_format=output_format,
        quality=quality,
        bg_color=bg_color,
        force_opaque_foreground=force_opaque_foreground,
        alpha_threshold=alpha_threshold,
        model_hint=model_hint,
    )
    return buf.getvalue(), mime
