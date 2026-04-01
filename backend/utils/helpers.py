import io
from typing import Any

from PIL import Image, ImageFilter

MAX_FILE_SIZE_MB = 20


def hex_to_rgb(hex_color: str) -> tuple:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


def validate_image(img_bytes: bytes) -> None:
    if len(img_bytes) > MAX_FILE_SIZE_MB * 1024 * 1024:
        raise ValueError(f"Bild zu groß (max {MAX_FILE_SIZE_MB} MB)")
    try:
        img = Image.open(io.BytesIO(img_bytes))
        img.verify()
    except Exception:
        raise ValueError("Ungültiges Bildformat")


# --- Adaptive Hintergrund-Erkennung ---
def detect_bg_brightness(img_bytes: bytes) -> str:
    """
    Samplet einen schmalen Randstreifen (robuster als 4 Ecken)
    und gibt 'light' oder 'dark' zurück.
    """
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    w, h = img.size
    if w < 10 or h < 10:
        return "light"

    inset = max(1, min(8, w // 15, h // 15))
    step_x = max(1, w // 16)
    step_y = max(1, h // 16)

    pixels: list[Any] = []
    for x in range(0, w, step_x):
        pixels.append(img.getpixel((x, inset)))
        pixels.append(img.getpixel((x, h - 1 - inset)))
    for y in range(0, h, step_y):
        pixels.append(img.getpixel((inset, y)))
        pixels.append(img.getpixel((w - 1 - inset, y)))

    avg = sum(sum(c) / 3 for c in pixels) / len(pixels)
    return "light" if avg > 128 else "dark"


def get_matting_params() -> dict[str, Any]:
    """
    rembg-Basisparameter für alle Bilder.

    post_process_mask=False: rembg's internes post_process macht
    gaussian_filter(sigma=2) + threshold@127, was niedrig-konfidente Pixel
    (z. B. graue Sohle auf weißem HG, alpha ~30–80) auf 0 setzt.
    Das eigene Cleanup in clean_alpha_edges arbeitet mit viel niedrigerem
    Threshold und ist schonender.

    alpha_matting=False: PyMatting ist zu langsam für Batch-Betrieb und
    bringt bei birefnet-general keinen messbaren Qualitätsvorteil.
    """
    return dict(alpha_matting=False, post_process_mask=False)


def clean_alpha_edges(img: Image.Image, bg: str) -> Image.Image:
    """
    Heller HG — selektives Cleanup:
        Entfernt nur Pixel mit NIEDRIGEM Alpha (nach Blur < 2) UND DUNKLER
        Farbe (brightness < 50).
        • Bewahrt: niedrig-alpha HELLE Pixel  → graue Gummisohle (~140 brightness)
        • Entfernt: niedrig-alpha DUNKLE Pixel → Schatten/Vignette (~15 brightness)

    Dunkler HG — minimales Smoothing:
        threshold=2 entfernt nur einzelne Rausch-Pixel.
        Tiefere Bereinigung übernimmt decontaminate_dark_edges.
    """
    import numpy as np

    if bg == "dark":
        r, g, b, a = img.split()
        a_smooth = a.filter(ImageFilter.GaussianBlur(radius=0.3))
        a_clean = a_smooth.point(lambda v: 0 if v < 2 else v)
        return Image.merge("RGBA", (r, g, b, a_clean))

    # Heller HG: Numpy-basiertes selektives Cleanup
    arr = np.array(img, dtype=np.uint8)
    a_ch = Image.fromarray(arr[..., 3])
    a_smooth = np.array(a_ch.filter(ImageFilter.GaussianBlur(radius=0.3)), dtype=np.uint16)
    brightness = (
        arr[..., 0].astype(np.uint16)
        + arr[..., 1].astype(np.uint16)
        + arr[..., 2].astype(np.uint16)
    ) // 3
    # Entfernen: sehr niedrige Konfidenz (blurred_alpha < 2) UND dunkle Farbe
    to_remove = (a_smooth < 2) & (brightness < 50)
    arr[to_remove, 3] = 0
    return Image.fromarray(arr)


def decontaminate_dark_edges(img: Image.Image) -> Image.Image:
    """
    Entfernt dunklen Hintergrund-Schmutz bei dunklem HG in zwei Zonen:

    Zone 1 – Randbereich (innerhalb 3 px vom Maskenrand):
        Alle dunklen Pixel (Helligkeit < 40) werden entfernt, unabhängig von alpha.
        Damit werden hochkonfidente schwarze Randpixel erfasst, die birefnet
        fälschlicherweise als Vordergrund klassifiziert (alpha > 220).

    Zone 2 – überall im Bild:
        Semi-transparente dunkle Pixel (alpha 5–220, Helligkeit < 40) werden
        entfernt. Betrifft Übergangs-Pixel an Kanten.

    Innen liegende dunkle Pixel (z. B. schwarzer Text auf Handschuhen)
    liegen tiefer als 3 px im Inneren und werden durch Zone 1 nicht berührt.
    """
    import numpy as np
    from scipy.ndimage import binary_erosion

    arr = np.array(img, dtype=np.uint16)
    r, g, b, a = arr[..., 0], arr[..., 1], arr[..., 2], arr[..., 3]
    brightness = (r + g + b) // 3

    fg = (a > 10).astype(bool)

    # Zone 1: Randbereich (5 px tief) — dunkle Pixel unabhängig von alpha entfernen
    interior = binary_erosion(fg, iterations=5)
    edge_zone = fg & ~interior
    arr[edge_zone & (brightness < 40), 3] = 0

    # Zone 2: Semi-transparente dunkle Pixel überall
    arr[(a >= 5) & (a <= 220) & (brightness < 40), 3] = 0

    return Image.fromarray(arr.astype(np.uint8))


def expand_mask_into_product(img: Image.Image, expand_px: int = 4) -> Image.Image:
    """
    Erweitert die Vordergrund-Maske in direkt angrenzende nicht-weiße Produktpixel.

    Problem: birefnet setzt niedrig-kontrast Produktteile (z. B. graue Gummisohle,
    brightness ~130–155) auf alpha=0 obwohl sie Teil des Produkts sind.

    Lösung: Sicher erkannte Produktpixel (alpha > 127) als Seed verwenden und
    expand_px Pixel in Richtung dunklerer Pixel erweitern.

    Schwellenwert brightness < 175:
        • Graue Sohle (~130–155)  → wird hinzugefügt        ✓
        • Produktschatten (~185+) → wird NICHT hinzugefügt  ✓  (verhindert schwarzen Rand)
        • Weißer HG (~250+)       → wird NICHT hinzugefügt  ✓
    """
    import numpy as np
    from scipy.ndimage import binary_dilation

    arr = np.array(img, dtype=np.uint8)
    r, g, b, a = arr[..., 0], arr[..., 1], arr[..., 2], arr[..., 3]
    brightness = (r.astype(np.uint16) + g.astype(np.uint16) + b.astype(np.uint16)) // 3

    fg_confident = (a > 127).astype(bool)

    # Nur mittlere Helligkeiten:
    #   ≥ 80  → dunkle Schuh-Randpixel (brightness ~10–70) werden NICHT hinzugefügt
    #   < 180 → helle Schatten (brightness ~185+) werden NICHT hinzugefügt
    #   Graue Sohle liegt bei ~120–160 → liegt genau im Fenster ✓
    candidates = (a == 0) & (brightness >= 80) & (brightness < 180)

    expanded = binary_dilation(fg_confident, iterations=expand_px)
    new_pixels = expanded & candidates
    arr[new_pixels, 3] = 255

    return Image.fromarray(arr)


def soften_alpha_edges(img: Image.Image, radius: float = 0.6) -> Image.Image:
    """
    Wendet einen leichten Gaussian Blur nur auf den Alpha-Kanal an.

    Verhindert harte, pixelige Kanten die entstehen wenn neu hinzugefügte
    Pixel (expand_mask_into_product) direkt auf alpha=255 gesetzt werden.
    Der Blur erzeugt einen natürlichen, weichen Übergang am Objektrand.

    radius=0.6: subtil genug um keine Details zu verlieren,
    stark genug um den Kantensprung zu glätten.
    """
    r, g, b, a = img.split()
    a_soft = a.filter(ImageFilter.GaussianBlur(radius=radius))
    return Image.merge("RGBA", (r, g, b, a_soft))


def remove_small_components(
    img: Image.Image, *, min_fraction: float = 0.70
) -> Image.Image:
    """
    Entfernt isolierte Fremdkörper neben dem Hauptprodukt (z. B. Logo-Schild).

    Behält nur Regionen, die >= min_fraction (70 %) der größten Region sind.
    Kalibriert auf echten Produktfotos:
      • Zwei gleiche Produkte (z. B. Handschuh-Paar): ~97–99 %  → beide behalten
      • Unverwandtes Logo-Schild neben der Weste:     ~59 %    → wird entfernt
      • Einzelprodukt:                                 1 Region → keine Änderung
    """
    import numpy as np
    from scipy.ndimage import label as ndlabel

    arr = np.array(img, dtype=np.uint8)
    alpha = arr[..., 3]
    fg = alpha > 10

    labeled, n = ndlabel(fg)
    if n <= 1:
        return img

    sizes = np.bincount(labeled.ravel())[1:]  # Index 0 = Hintergrund
    max_size = sizes.max()
    min_size = max_size * min_fraction

    for comp_idx, size in enumerate(sizes, start=1):
        if size < min_size:
            arr[labeled == comp_idx, 3] = 0

    return Image.fromarray(arr)
