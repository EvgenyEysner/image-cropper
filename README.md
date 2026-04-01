# Image Cropper — Freistellungs-Service

Automatische Hintergrundentfernung für Produktfotos. Backend: FastAPI + rembg (birefnet). Frontend: React + Vite. Deployment: Docker Compose.

---

## Inhaltsverzeichnis

1. [Lokale Entwicklung](#1-lokale-entwicklung)
2. [Docker Deployment](#2-docker-deployment)
3. [Frontend (UI)](#3-frontend-ui)
4. [REST API — Endpunkte](#4-rest-api--endpunkte)
5. [n8n Integration](#5-n8n-integration)
6. [Modelle](#6-modelle)
7. [Fehlerbehebung](#7-fehlerbehebung)

---

## 1. Lokale Entwicklung

### Voraussetzungen

| Tool | Version | Zweck |
|------|---------|-------|
| Python | ≥ 3.12 | Backend |
| [uv](https://docs.astral.sh/uv/) | aktuell | Package Manager |
| Node.js | ≥ 18 | Frontend |
| npm | ≥ 9 | Frontend Packages |

### Backend starten

```bash
cd backend

# Abhängigkeiten installieren (einmalig)
uv sync

# Server starten — überwacht ALLE Unterordner auf Änderungen
.venv/bin/python -m uvicorn main:app \
  --host 0.0.0.0 \
  --port 8010 \
  --reload \
  --reload-dir .
```

**Wichtig:** `--reload-dir .` ist notwendig, damit Änderungen in `utils/` und `services/` erkannt werden. Ohne diesen Parameter werden Unterordner nicht überwacht.

Server läuft auf: `http://127.0.0.1:8010`  
Swagger UI: `http://127.0.0.1:8010/docs`

### Frontend starten

```bash
cd frontend

# Abhängigkeiten installieren (einmalig)
npm install

# Dev-Server starten
npm run dev
```

Frontend läuft auf: `http://localhost:5173`

Beim ersten Start mit birefnet-Modell: **das erste Bild dauert 30–60 Sekunden** — das Modell (~170 MB) wird geladen und gecacht. Alle weiteren Bilder sind deutlich schneller.

---

## 2. Docker Deployment

### Voraussetzungen

- Docker Desktop läuft

### Starten

```bash
cd /Users/evgenyeysner/PycharmProjects/image-cropper

docker compose up --build
```

| Service | URL | Beschreibung |
|---------|-----|--------------|
| Frontend | `http://localhost:8080` | React UI + Nginx |
| Backend | `http://localhost:8010` | FastAPI direkt |

### Stoppen

```bash
docker compose down
```

### Nur neu bauen (nach Code-Änderungen)

```bash
docker compose up --build backend   # nur Backend
docker compose up --build frontend  # nur Frontend
```

### Logs anschauen

```bash
docker compose logs -f backend
docker compose logs -f frontend
```

---

## 3. Frontend (UI)

Aufruf: `http://localhost:5173` (lokal) oder `http://localhost:8080` (Docker)

### Bedienung

1. **Bilder auswählen** — mehrere Dateien gleichzeitig möglich (PNG, JPEG, WEBP, AVIF)
2. **Format wählen**
   - `JPEG` — weißer Hintergrund, kleinere Dateigröße
   - `PNG` — transparenter Hintergrund
3. **BG Color** — Hintergrundfarbe für JPEG: `white`, `black`, `#ff0000`
4. **Modell wählen** (siehe [Modelle](#6-modelle))
5. **Qualität** — nur für JPEG, 1–100 (Standard: 90)
6. **Freistellen starten** klicken

### Ergebnis

Für jedes Bild wird angezeigt:
- Originalgröße → Ergebnisgröße in KB
- Vorher/Nachher Vorschau
- Erfolg / Fehlermeldung

---

## 4. REST API — Endpunkte

Basis-URL (lokal): `http://127.0.0.1:8010`  
Basis-URL (Docker): `http://localhost:8010`

### GET /health

Statusprüfung und verfügbare Modelle.

```bash
curl http://127.0.0.1:8010/health
```

```json
{
  "status": "ok",
  "default_model": "high-quality",
  "available_models": ["product", "high-quality", "person", "general"]
}
```

---

### POST /cropping-image

Einzelbild freistellen. Bild wird als Base64 übergeben.

**Request Body:**

```json
{
  "image_base64": "data:image/jpeg;base64,/9j/4AAQ...",
  "format": "jpeg",
  "quality": 90,
  "bg_color": "white",
  "model_hint": "high-quality"
}
```

| Feld | Typ | Standard | Beschreibung |
|------|-----|---------|--------------|
| `image_base64` | string | — | Base64-Bild, mit oder ohne Data-URL-Prefix |
| `format` | `jpeg` \| `png` | `jpeg` | Ausgabeformat |
| `quality` | 1–100 | `90` | JPEG-Qualität |
| `bg_color` | string | `white` | Hintergrund für JPEG: `white`, `black`, `#rrggbb` |
| `model_hint` | string | `product` | Segmentierungsmodell |

**Response:**

```json
{
  "success": true,
  "image_base64": "data:image/jpeg;base64,/9j/...",
  "format": "jpeg",
  "original_size_kb": 264.3,
  "result_size_kb": 45.7,
  "message": "Erfolgreich freigestellt: 264.3 KB > 45.7 KB"
}
```

**Beispiel mit curl:**

```bash
# Bild als Base64 kodieren und senden
IMAGE_B64=$(base64 -i mein_bild.jpg)

curl -X POST http://127.0.0.1:8010/cropping-image \
  -H "Content-Type: application/json" \
  -d "{\"image_base64\": \"$IMAGE_B64\", \"format\": \"png\", \"model_hint\": \"high-quality\"}"
```

---

### POST /cropping-image/batch

Mehrere Bilder parallel freistellen (Base64-Array).

**Request Body:**

```json
[
  {"image_base64": "data:image/jpeg;base64,...", "format": "jpeg"},
  {"image_base64": "data:image/png;base64,...",  "format": "png", "model_hint": "high-quality"}
]
```

**Response:**

```json
{
  "total": 2,
  "results": [
    {"success": true, "image_base64": "...", ...},
    {"success": true, "image_base64": "...", ...}
  ]
}
```

---

### POST /cropping-image/upload-batch

Multipart-Upload (Dateien direkt hochladen, kein Base64 nötig). Wird vom Frontend genutzt.

```bash
curl -X POST http://127.0.0.1:8010/cropping-image/upload-batch \
  -F "files=@schuh.jpg" \
  -F "files=@handschuh.jpeg" \
  -F "format=jpeg" \
  -F "quality=90" \
  -F "bg_color=white" \
  -F "model_hint=high-quality"
```

---

### POST /n8n/freistellung

Speziell für n8n-Automation. Akzeptiert Binärdaten, gibt Bilddatei zurück.

**Query-Parameter:**

| Parameter | Standard | Beschreibung |
|-----------|---------|--------------|
| `output_format` | `png` | `png` (Transparenz) oder `jpeg` (weißer HG) |
| `quality` | `90` | JPEG-Qualität |
| `bg_color` | `white` | Hintergrundfarbe für JPEG |
| `model_hint` | `high-quality` | Segmentierungsmodell |

**Multipart-Felder** (eines davon muss gesetzt sein):
- `file` — bevorzugter Feldname
- `image` — alternativer Feldname  
- `data` — wie Binary-Property nach OpenAI-Node

**Beispiel curl:**

```bash
curl -X POST "http://127.0.0.1:8010/n8n/freistellung?output_format=png&model_hint=high-quality" \
  -F "file=@produkt.jpg" \
  --output freigestellt.png
```

---

## 5. n8n Integration

### HTTP Request Node konfigurieren

| Einstellung | Wert |
|-------------|------|
| Methode | `POST` |
| URL | `http://<server>:8010/n8n/freistellung` |
| Body | `Form Data` (Multipart) |
| Feldname | `file` |
| Body Content Type | `Binary` |
| Query-Parameter | `output_format=png&model_hint=high-quality` |

### Response verarbeiten

Die Antwort ist eine direkte Bilddatei (Binary). In n8n:
- **Response Format**: `File`
- **Binary Property**: `data`

Das Ergebnis-Bild kann dann direkt an den nächsten Node weitergegeben oder gespeichert werden.

---

## 6. Modelle

| `model_hint` | Modell | Geschwindigkeit | Beste Anwendung |
|-------------|--------|----------------|-----------------|
| `high-quality` | birefnet-general | ~15–25 Sek | **Standard** — Produktfotos, dunkle Hintergründe, komplexe Formen |
| `product` | isnet-general-use | ~5–10 Sek | Einfache Produkte auf weißem HG |
| `person` | u2net_human_seg | ~5 Sek | Personen / Porträts |
| `general` | u2netp | ~2–3 Sek | Schnelle Vorschau, einfache Motive |

**Automatische Modell-Anpassung:**  
Das Backend erkennt den Hintergrund automatisch (`detect_bg_brightness`). Bei dunklem Hintergrund wird immer auf `high-quality` (birefnet) hochgestuft, unabhängig vom gewählten `model_hint`.

**Erster Start:**  
Beim allerersten Aufruf eines Modells wird es heruntergeladen und gecacht. birefnet (~170 MB) kann 30–90 Sekunden dauern. Alle weiteren Anfragen nutzen den Cache.

---

## 7. Fehlerbehebung

### Server reagiert nicht nach Code-Änderungen

`--reload` ohne `--reload-dir .` überwacht nur das Hauptverzeichnis:

```bash
# Falsch:
uvicorn main:app --reload

# Richtig:
uvicorn main:app --reload --reload-dir .
```

### Erstes Bild dauert sehr lang (>60 Sek)

Normal — birefnet wird geladen. Warten bis der Server meldet:
```
INFO: rembg Session erzeugt: birefnet-general [ThreadPoolExecutor-0_0]
```

### 422 Fehler: model_hint ungültig

Erlaubte Werte: `product`, `high-quality`, `person`, `general`

```bash
# Falsch:
"model_hint": "highquality"

# Richtig:
"model_hint": "high-quality"
```

### Docker: Port bereits belegt

```bash
# Prozess auf Port 8010 finden und beenden
lsof -ti :8010 | xargs kill -9
```

### Schwarzer Rand um freigestelltes Bild

Passiert wenn `decontaminate_dark_edges` zu aggressiv greift. Überprüfen ob das Bild korrekt als `bg=light` oder `bg=dark` erkannt wird:

```bash
curl http://127.0.0.1:8010/health
```

Wechsel zum Modell `high-quality` verbessert die Kantenerkennung meistens.
