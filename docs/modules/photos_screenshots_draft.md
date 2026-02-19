# Photos / Screenshots Module Research Draft

Status: **Draft** (Research Only — no implementation)
Last updated: 2026-02-19
Author: Research pass, butlers-962.3
Depends on: `src/butlers/modules/base.py`, `docs/connectors/interface.md`

---

## 1. Purpose

This document captures research into a Photos/Screenshots module for butlers.
The module adds visual context ingestion: photos sent via Telegram, screenshots
shared for tech support, receipts and documents captured by camera, and visual
journaling use cases.

Use cases in scope:

- **Screenshot sharing / tech support:** User sends a screenshot of an error,
  UI bug, or system state. Butler extracts text via OCR, optionally describes
  the image, and logs it for context in subsequent sessions.
- **Photo journaling:** User sends daily photo diary entries via Telegram.
  Butler captions, tags, and logs them in chronological order.
- **Receipt / document capture:** User photographs a receipt or invoice.
  Butler extracts vendor, total, date, and line items via OCR+LLM for expense
  tracking or memory.
- **Visual memory:** User sends an image they want the butler to remember.
  Butler stores a semantic caption, OCR text, and embedding for later recall.

This is a **research-only** deliverable. No implementation code accompanies this
doc. The goal is to identify the best library/API approaches, map data models to
existing butler conventions, surface hardware requirements, privacy considerations,
and storage strategies for a future implementation ticket.

---

## 2. Scope and Interaction Models

### 2.1 Telegram Photo / Screenshot Ingestion (Primary)

The user sends a photo or screenshot to their butler's Telegram bot. The butler:

1. Downloads the image from Telegram's file server.
2. Strips privacy-sensitive EXIF metadata (GPS, device fingerprint).
3. Runs OCR to extract any text.
4. Optionally generates a semantic caption via a vision model.
5. Stores a thumbnail and extracted metadata in PostgreSQL.
6. Injects a normalized text summary into the standard pipeline.

This is the primary target — it fits the existing Telegram module pattern
with no new infrastructure.

### 2.2 Dashboard Screenshot Upload (Secondary)

A user uploads a screenshot via the dashboard web UI. Flow is identical to
the Telegram path after the file arrives at the butler's API endpoint.
Requires an `POST /photos` endpoint on the butler's FastAPI dashboard router.

### 2.3 Out of Scope (v1)

- Real-time camera feed / video stream analysis
- Face recognition / face identification (GDPR biometric data category — highest
  protection tier, requires explicit consent framework; deferred to future scope)
- Bulk photo library import / historical backfill
- Automatic screenshot capture from the butler host screen

---

## 3. OCR: Library and API Landscape

### 3.1 Tesseract (Local, Rule-Based + LSTM)

**What it is:** Tesseract is the oldest and most widely deployed open-source OCR
engine. Originally rule-based; now uses LSTM neural networks (since v4.0).
Version 5.x (current: 5.5.2, released late 2025) continues LSTM improvements.

**Repository:** `github.com/tesseract-ocr/tesseract` (Apache 2.0)
**Python wrapper:** `pytesseract` on PyPI

**Accuracy:**
- Clean printed text: 90–95% character accuracy on ideal scans
- Handwriting: poor (15–40% depending on style)
- Scene text / smartphone photos: 60–80% (degrades heavily with skew, noise, blur)
- Receipts (printed): 80–90% with preprocessing

**Performance:**
- CPU-only, no GPU required
- ~100–500 ms per image on modern x86 (8.5 MP)
- Very low RAM: < 500 MB including model

**Preprocessing requirements:** Tesseract is sensitive to image quality.
Best results require: deskewing, binarization (Otsu thresholding), noise
removal, and scaling to ~300 DPI equivalent. Pillow and OpenCV provide
all necessary primitives.

**Language support:** 100+ languages via `tessdata` packages (LSTM models).

**Privacy:** Fully offline. No network egress.

**Verdict:** Adequate for clean text (screenshots, documents, receipts with clear
printing). Weak on degraded or camera-captured text. Best used as a fast, free
first-pass filter before escalating to a higher-accuracy option. Suitable as
the only OCR for screenshot text extraction where image quality is controlled.

---

### 3.2 EasyOCR (Local, Deep Learning)

**What it is:** A Python-native deep learning OCR library built on PyTorch.
Combines CRAFT text detector with CRNN text recognizer. Supports 80+ languages
and works natively on camera photos without extensive preprocessing.

**Repository:** `github.com/JaidedAI/EasyOCR` (Apache 2.0)

**Accuracy:**
- Printed text (documents/receipts): 88–93% character accuracy
- Scene text (photos): 75–85% — substantially better than Tesseract on noisy input
- Handwriting: 30–60% (much better than Tesseract)
- Multi-language (mixed script): strong — recognizes multiple languages per image

**Performance:**
- CPU inference: 2–8 s per image depending on size and text density
- GPU inference (CUDA): 0.3–1 s per image on RTX 3060
- RAM: 1.5–2 GB (model loaded in memory)
- Model size on disk: ~200–400 MB depending on languages selected

**Integration:** `pip install easyocr`. Pure Python; no native binary required.
GPU support via standard PyTorch CUDA installation.

**Privacy:** Fully offline. No network egress.

**Verdict:** Best open-source OCR for noisy/real-world photos (receipts, screens,
handwriting). Slower than Tesseract on CPU but significantly more accurate on
non-ideal inputs. Recommended as the primary OCR for camera-captured content.

---

### 3.3 PaddleOCR (Local, Deep Learning)

**What it is:** Baidu's open-source OCR framework built on PaddlePaddle. The
May 2025 release of PP-OCRv5 introduced a modular architecture with a reported
+13 percentage-point accuracy improvement over PP-OCRv4 on multi-scenario sets,
including handwriting, ancient texts, and Japanese characters. In January 2026,
Baidu released PaddleOCR-VL-1.5, achieving 94.5% accuracy on document parsing.

**Repository:** `github.com/PaddlePaddle/PaddleOCR` (Apache 2.0)

**Accuracy (PP-OCRv5):**
- Printed text (documents): 93–96% character accuracy — best open-source for structured documents
- Handwriting: 70–80%
- Scene text: 85–90%
- Tables and structured layouts: excellent — PP-Structure extracts table data

**Performance:**
- CPU inference: 1–5 s per image (v5 model)
- GPU inference: 0.2–0.8 s per image
- RAM: 1–2 GB
- Model sizes: 1 MB (lightweight server model) to 90 MB (accuracy model)

**Key differentiator:** PP-Structure provides layout analysis — column detection,
table extraction, reading order. Superior for multi-column documents, invoices,
and complex receipts.

**Privacy:** Fully offline. Uses PaddlePaddle framework (not PyTorch).

**Verdict:** Best open-source OCR for structured documents and tables. Highly
accurate for receipts and invoices. The PaddlePaddle dependency adds some
operational weight (separate from the project's existing PyTorch ecosystem).
Recommended specifically when table/form extraction is a priority use case.

---

### 3.4 docTR (Local, Deep Learning, Document-Focused)

**What it is:** A document-focused OCR library by Mindee that uses a two-stage
pipeline: text detection (DBNet or FAST) and text recognition (CRNN). Wraps
multiple backends (TensorFlow and PyTorch) with a unified Python API. Designed
specifically for document OCR — scanned PDFs, multi-column layouts, forms.

**Repository:** `github.com/mindee/doctr` (Apache 2.0)

**Accuracy:**
- Printed document OCR: 91–95% (competitive with PaddleOCR on FUNSD/CORD benchmarks)
- Does not provide structural analysis beyond bounding boxes
- Scene text / photos: weaker than EasyOCR (document-tuned models)
- Tables: no table extraction (PaddleOCR's PP-Structure is better for this)

**Performance:**
- CPU inference: 1–3 s per page
- GPU: 0.2–0.5 s per page
- RAM: 1–1.5 GB
- Model size: 30–80 MB

**Key differentiator vs. PaddleOCR:** Pure PyTorch or TensorFlow backend — no
PaddlePaddle dependency. Cleaner Python API. Better integrated with the Hugging
Face ecosystem. However, lacks table extraction and structural analysis.

**Privacy:** Fully offline.

**Verdict:** Strong for document/form OCR in a PyTorch-native codebase. Inferior to
PaddleOCR for table extraction and to EasyOCR for noisy/photo OCR. Best fit for
screenshot OCR (clean printed text) with fewer dependencies than PaddleOCR.

---

### 3.5 Google Cloud Vision API (Cloud, Free Tier)

**What it is:** Google's managed Vision API exposing OCR, label detection, face
detection, object detection, and landmark recognition via REST/gRPC.

**Free tier (as of 2026):** First 1,000 units per month free. Unit = one image
per feature. OCR (text detection) on 1,000 images/month costs $0.
Units 1,001–5,000,000: $1.50 per 1,000 units (OCR feature).

**Accuracy:**
- Printed text: 96–99% — best-in-class OCR accuracy
- Handwriting: 85–95% (Chirp model)
- Scene text: 90–95%
- Document layout: supported via Document AI (separate product, higher cost)

**Privacy:** Images are sent to Google's servers. Not compliant with tailnet
isolation unless the user explicitly accepts cloud image processing.

**Rate limits:** 1,000 free units/month; then pay-per-use.

**Verdict:** Excellent accuracy. Free tier adequate for low-volume personal use
(< 1,000 photos/month). However, cloud privacy concern is a hard architectural
constraint — Google Vision must be an explicit opt-in only. Document clearly
that enabling it sends image data to Google. Suitable as an opt-in premium
backend for users who need maximum OCR accuracy on important receipts/documents.

---

### 3.6 OCR Comparison Matrix

| Criterion | Tesseract | EasyOCR | PaddleOCR | docTR | Google Vision |
|---|---|---|---|---|---|
| Accuracy (printed) | 90–95% | 88–93% | 93–96% | 91–95% | 96–99% |
| Accuracy (scene/photo) | 60–80% | 75–85% | 85–90% | 75–85% | 90–95% |
| Accuracy (handwriting) | 15–40% | 30–60% | 70–80% | 40–60% | 85–95% |
| Table / structure extraction | No | No | Yes (PP-Structure) | No | Partial |
| Local / offline | Yes | Yes | Yes | Yes | No (cloud) |
| Privacy | Full | Full | Full | Full | Cloud |
| GPU required | No | No (optional) | No (optional) | No (optional) | N/A |
| Min RAM | < 500 MB | ~1.5 GB | ~1 GB | ~1 GB | N/A |
| CPU speed (per image) | 100–500 ms | 2–8 s | 1–5 s | 1–3 s | Network RTT |
| Framework | C++ (subprocess) | PyTorch | PaddlePaddle | PyTorch/TF | REST API |
| Languages | 100+ | 80+ | 80+ | 20+ | 200+ |
| License | Apache 2.0 | Apache 2.0 | Apache 2.0 | Apache 2.0 | Proprietary |
| Cost | Free | Free | Free | Free | Free ≤1K/mo then $1.50/1K |

**Recommended combination:**
- Screenshots and clean text: Tesseract (fast, adequate quality)
- Camera photos (receipts, documents): EasyOCR (handles real-world noise)
- Structured receipts/invoices (if table extraction needed): PaddleOCR PP-Structure
- User opt-in maximum accuracy: Google Vision API (cloud, explicit consent required)

---

## 4. Image Understanding: Vision Models

### 4.1 Claude Vision API (Anthropic)

**What it is:** All Claude 3.x and 4.x models (including the model powering this
butler runtime) support multimodal image inputs. Images are passed as base64
or URL references in the API request alongside text.

**Formats:** PNG, JPEG, GIF, WebP. Up to 100 images per API request.

**Token cost for images:** Approximately `(width × height) / 750` input tokens.
A typical smartphone photo at 1568×1568 pixels consumes ~3,277 input tokens.
At Claude Sonnet 4.5 pricing (~$3/MTok input), a 3K-token image costs ~$0.009
per image analyzed.

**Capabilities relevant to butler use cases:**
- Screenshot text extraction and description: excellent (outperforms pure OCR
  for understanding context around UI elements)
- Receipt/document extraction: structured field extraction (merchant, total, date,
  line items) with a single prompt, no separate OCR step
- Visual memory captioning: describe what is in the photo in natural language
- Error diagnosis: understand UI errors, stack traces in screenshots

**Privacy:** Images are sent to Anthropic's API. Same cloud data constraint as
Google Vision. Must be opt-in if the butler's default is local-only processing.

**Latency:** 1–5 s per image depending on model and content complexity.

**Verdict:** Best-in-class image understanding for the butlers project — the same
API already used for text reasoning. For users comfortable with Anthropic data
processing, Claude Vision is the recommended default for image understanding
(captioning, receipt extraction) because it eliminates the need for a separate
vision model and provides richer semantic output than pure OCR. However, it
is a cloud API and requires explicit opt-in for privacy-sensitive images.

**Butler-specific advantage:** Since the butler runtime already calls the Claude
API for LLM reasoning, adding image inputs to an existing session costs zero
additional integration complexity. The butler can pass an image to its own
ephemeral LLM CLI instance.

---

### 4.2 Moondream2 (Local, 2B Parameters)

**What it is:** Moondream2 is a tiny vision-language model (2 billion parameters)
designed to run efficiently on constrained hardware. Architecture: SigLIP visual
encoder + Phi-1.5 language model. As of the June 2025 release, RL fine-tuning
was applied across 55 vision-language tasks with roadmap to expand to ~120 tasks.

**Repository:** `github.com/vikhyat/moondream` (Apache 2.0)
**HuggingFace:** `vikhyatk/moondream2`

**Capabilities:**
- Image captioning (short/normal)
- Visual question answering
- Object detection (improved COCO score: 30.5 → 51.2 in 2025 release)
- OCR text reading (OCRBench score: 58.3 → 61.2)
- Streaming generation support

**Performance:**
- 4-bit quantized model: 2,450 MB VRAM, 184 tokens/s on RTX 3090
- CPU inference: feasible on any modern x86_64 with 3–4 GB RAM
- 4-bit quantized: 42% memory reduction, only 0.6% accuracy drop
- Model weights: ~1.86 GB full, ~900 MB 4-bit quantized

**Use case fit for butlers:**
- Photo captioning for visual memory
- Screenshot description
- Basic receipt field extraction (less accurate than Claude Vision or PaddleOCR)

**Privacy:** Fully offline. No network egress. Runs inside the butler container.

**Verdict:** Best local vision model for a CPU-constrained butler deployment.
Apache 2.0 license. Runs on hardware where larger models cannot. Quality
is meaningfully below Claude Vision but suitable for captioning and simple VQA
on a local-only deployment. Recommended as the local vision backend when the
user opts out of cloud image processing.

---

### 4.3 LLaVA / LLaVA-NeXT (Local, 7B–110B Parameters)

**What it is:** LLaVA (Large Language-and-Vision Assistant) connects a vision
encoder (CLIP ViT) to an LLM via a projection matrix. LLaVA-NeXT (released
May 2024) added LLaMA-3 (8B) and Qwen-1.5 (72B/110B) backends and increased
input resolution to 4× more pixels.

**Repository:** `github.com/haotian-liu/LLaVA` (Apache 2.0)
**Local deployment:** Via Ollama (`ollama pull llava`) or LocalAI

**Hardware requirements:**
- LLaVA-7B: 8 GB RAM (CPU), ~5–6 GB VRAM (GPU)
- LLaVA-13B: ~14 GB RAM or 10 GB VRAM
- CPU inference with Ollama: LLaVA-7B runs on a Raspberry Pi 4 (8 GB)
  with adequate response time for async batch captioning

**Capabilities:**
- Image captioning: good quality at 7B; excellent at 13B
- Visual QA: conversational image understanding
- Receipt / document extraction: adequate at 7B; requires structured prompt engineering
- Screenshot analysis: good understanding of UI elements and layout

**Privacy:** Fully offline. Via Ollama, runs as a local HTTP server accessible
on the tailnet.

**Verdict:** Stronger than Moondream2 on reasoning-heavy tasks (13B+ models)
but requires a GPU for interactive use. On a CPU-only butler, LLaVA-7B is
~3–10× slower than Moondream2 for the same task. Best suited for butler
deployments that already run an Ollama server on the tailnet for other LLM
needs. If an Ollama server is present, LLaVA-7B is a viable local vision backend
superior to Moondream2.

---

### 4.4 GPT-4o Vision (Cloud, OpenAI)

**What it is:** OpenAI's GPT-4o supports multimodal inputs including images.
Quality is comparable to Claude Vision for most tasks; performance on structured
document understanding (forms, tables) is competitive.

**Pricing (2026):** Per token, with image tokens computed as 85 tokens per 512×512
tile (high-detail mode). A standard 1568×1568 photo = ~6 tiles = ~510 image tokens.

**Privacy:** Same cloud constraint as Claude Vision. Images sent to OpenAI's servers.

**Verdict:** Viable alternative to Claude Vision if the user has an existing
OpenAI API key and prefers GPT-4o. Not recommended as the primary option for
a butler that already uses the Anthropic API — integrating a second cloud vision
provider adds cost, complexity, and a second set of privacy disclosures. Document
as a configurable backend option.

---

### 4.5 Vision Model Comparison Matrix

| Criterion | Claude Vision | Moondream2 (2B) | LLaVA-7B | GPT-4o Vision |
|---|---|---|---|---|
| Quality (captioning) | Excellent | Good | Very Good | Excellent |
| Quality (receipt extraction) | Excellent | Fair | Good | Excellent |
| Quality (screenshot analysis) | Excellent | Good | Good | Excellent |
| Local / offline | No (cloud) | Yes | Yes (Ollama) | No (cloud) |
| Privacy | Cloud (Anthropic) | Full | Full | Cloud (OpenAI) |
| GPU required | No | No (slower) | No (slower) | N/A |
| Min RAM (local) | N/A | ~2–3 GB | ~8 GB | N/A |
| Latency | 1–5 s | 2–10 s (CPU) | 5–30 s (CPU) | 1–5 s |
| Cost per image | ~$0.009 | Free | Free | ~$0.005–0.015 |
| Integration complexity | Low (Anthropic SDK) | Medium (HF/PyPI) | Medium (Ollama) | Low (OpenAI SDK) |
| License | Proprietary | Apache 2.0 | Apache 2.0 | Proprietary |

---

## 5. Image Processing: Pillow, OpenCV, and EXIF

### 5.1 Pillow (PIL Fork)

**What it is:** The standard Python image processing library. Handles format
conversion, resizing, thumbnail generation, basic filtering, and EXIF metadata
access. Used by most Python image pipelines.

**PyPI:** `pillow` — MIT license, pure Python with C extensions.

**Key operations for the Photos module:**

```python
from PIL import Image, ExifTags

# Open and convert
img = Image.open(file_path)
img = img.convert("RGB")  # Normalize for JPEG output

# Generate thumbnail (preserves aspect ratio, does not enlarge)
img.thumbnail((320, 320), Image.LANCZOS)

# Extract EXIF
exif_data = img.getexif()  # Returns IFD mapping
exif_readable = {ExifTags.TAGS.get(k, k): v for k, v in exif_data.items()}

# Strip all EXIF (privacy-safe save)
clean_img = Image.new(img.mode, img.size)
clean_img.putdata(list(img.getdata()))
clean_img.save(output_path, "JPEG", quality=85)
```

**EXIF stripping:** Pillow does not expose a direct "delete EXIF" method.
The standard approach is to save a new image without EXIF, or use `piexif` to
selectively strip GPS tags:

```python
import piexif

# Load EXIF, remove GPS IFD, re-embed
exif_dict = piexif.load(image_bytes)
del exif_dict["GPS"]  # Remove GPS block entirely
clean_exif = piexif.dump(exif_dict)
img.save(output_path, exif=clean_exif)
```

**Alternatively** (simplest and most complete EXIF strip): save a clean copy
using `Image.open()` → `data = list(img.getdata())` → reconstruct without
metadata. This strips all EXIF, ICC profiles, and thumbnails embedded in the
source EXIF block.

**Thumbnail generation:** `Image.thumbnail()` is the correct method; it resizes
in-place without enlarging and preserves aspect ratio. For WebP output (smallest
file size): `img.save(thumb_path, "WEBP", quality=75, method=6)`.

---

### 5.2 OpenCV (cv2)

**What it is:** Open-source computer vision library. In the Photos module
context, OpenCV adds value for preprocessing before OCR: deskewing, binarization,
noise removal, and deblurring. These steps improve OCR accuracy significantly
on camera-captured photos.

**PyPI:** `opencv-python` (Apache 2.0, prebuilt wheels available)

**Relevant preprocessing operations for OCR:**

```python
import cv2
import numpy as np

img = cv2.imread(file_path)
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

# Adaptive thresholding (Otsu) — improves OCR on varying illumination
_, binarized = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

# Deskew: detect rotation angle from Hough lines, then warp
# (requires line detection → angle estimation → affine transform)

# Noise removal via morphological operations
kernel = np.ones((2, 2), np.uint8)
denoised = cv2.morphologyEx(binarized, cv2.MORPH_CLOSE, kernel)
```

**When to use OpenCV vs. Pillow:**
- Pillow: format conversion, EXIF handling, thumbnail generation (simpler API)
- OpenCV: OCR preprocessing, deskewing, binarization, contour detection

Both can coexist in the same pipeline. For the Photos module, the recommended
pattern is:
1. Load with Pillow → strip EXIF → save clean version
2. Load cleaned image with OpenCV → preprocess → pass to OCR

---

### 5.3 EXIF Extraction for Structured Metadata

Beyond GPS (stripped for privacy), EXIF metadata provides useful butler context:

| EXIF Field | EXIF Tag ID | Value type | Butler use |
|---|---|---|---|
| `DateTimeOriginal` | 36867 | String | When the photo was taken |
| `Make` / `Model` | 271 / 272 | String | Device identification |
| `Software` | 305 | String | Screenshot → OS/app source |
| `ImageWidth` / `ImageLength` | 256 / 257 | Integer | Resolution |
| `Orientation` | 274 | Integer | Auto-rotate correction |
| `Flash` | 37385 | Integer | Flash used (indoor/outdoor indicator) |
| `ExposureTime` | 33434 | Rational | Shutter speed |
| `ISOSpeedRatings` | 34855 | Integer | Lighting conditions |
| `GPSInfo` | 34853 | IFD | **MUST BE STRIPPED before storage** |

For screenshots (no camera EXIF), the `Software` tag often identifies the
capturing app (e.g., "macOS Sequoia", "Windows 11").

---

## 6. Privacy: EXIF GPS Stripping and Face Detection

### 6.1 EXIF GPS Stripping (Mandatory)

GPS coordinates embedded in smartphone photos constitute precise location data
under GDPR and all major privacy regulations. The butler **must** strip GPS EXIF
before storing any image.

**Implementation rule:** GPS stripping is non-optional and must occur before
any disk or database write. Apply at ingest time, before OCR or thumbnail
generation.

**Recommended implementation:**

```python
import piexif
from PIL import Image
import io

def strip_exif_gps(image_bytes: bytes) -> bytes:
    """Strip GPS from EXIF and return clean image bytes."""
    img = Image.open(io.BytesIO(image_bytes))
    try:
        exif_dict = piexif.load(img.info.get("exif", b""))
        exif_dict.pop("GPS", None)  # Remove GPS IFD entirely
        clean_exif = piexif.dump(exif_dict)
        out = io.BytesIO()
        img.save(out, format=img.format or "JPEG", exif=clean_exif)
        return out.getvalue()
    except Exception:
        # Fallback: save without any EXIF if piexif fails
        out = io.BytesIO()
        clean = Image.new(img.mode, img.size)
        clean.putdata(list(img.getdata()))
        clean.save(out, format=img.format or "JPEG")
        return out.getvalue()
```

### 6.2 Face Detection (Privacy Flag, Not Recognition)

Face **detection** (detecting that a face is present in an image, without
identifying who it is) can be used as a privacy flag: if a photo contains
faces, the butler may apply stricter retention policies or require explicit
confirmation before logging.

This is distinct from face **recognition** (identifying specific people), which
constitutes biometric data processing under GDPR and is out of scope for v1.

**Face detection options:**

**OpenCV Haar cascade (included in `opencv-python`):**
- CPU-only, < 100 ms per image
- Acceptable accuracy for frontal faces; poor for profile or occluded faces
- Zero additional dependencies
- Use: `cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")`

**MediaPipe Face Detection (Google, Apache 2.0):**
- `pip install mediapipe`
- Deep learning model, runs locally
- ~80–95% detection accuracy including non-frontal faces
- ~50–200 ms per image on CPU
- More accurate than Haar; runs on the same hardware without GPU

**Recommended:** MediaPipe for detection accuracy. OpenCV Haar cascade as a
zero-dependency fallback. Both run fully offline.

**Face detection workflow:**
1. Detect faces in the image.
2. If faces detected: log `has_faces=True` in the DB, skip captioning by default
   (captions naming individuals could become personal data under GDPR).
3. Present a dashboard indicator showing the image was flagged; user can
   explicitly approve captioning/storage.
4. Configurable: `[modules.photos] face_detection = true / false`.

### 6.3 Data Classification and Minimization

Under GDPR Article 5(1)(c), personal data must be limited to what is necessary:

- **Photos sent to the butler are personal data.** The user is both controller
  and processor in a self-hosted deployment, which simplifies compliance — but
  the butler must still apply data minimization by default.
- **GPS coordinates** are sensitive location data: stripped before storage (§6.1).
- **Faces** are potentially biometric data if recognition is performed: detection
  only, no recognition, no embeddings stored (§6.2).
- **Captions and OCR text** inherit the sensitivity of the image content.

**Default minimization policy:**
1. Store thumbnails (lossy, stripped of EXIF, reduced resolution) not originals,
   unless the user explicitly enables original storage.
2. Retain raw image originals for a configurable window (default: 30 days), then
   auto-purge while retaining the extracted metadata.
3. Provide `bot_photos_delete_image` MCP tool for user-initiated removal.
4. Do not log face detection embeddings — only a boolean flag `has_faces`.

---

## 7. Storage Strategy

### 7.1 Storage Tiers

Three storage tiers for photo data:

| Tier | Content | Storage | Retention |
|---|---|---|---|
| **Thumbnail** | 320×320 px max, EXIF-stripped, WebP/JPEG | PostgreSQL BYTEA (< 50 KB) | Permanent (until user deletes) |
| **Metadata** | OCR text, caption, EXIF fields, tags, face flag | PostgreSQL JSONB | Permanent |
| **Original** | EXIF-stripped original file | Docker volume (filesystem) or S3-compatible | 30 days default, configurable |

### 7.2 Size Estimates

**Typical image sizes:**
- Smartphone photo (JPEG, 12–24 MP): 3–8 MB original; 5–15% of original after
  WebP compression to 1568×1568 → ~200–500 KB
- Screenshot (PNG, 1080p–4K): 0.5–5 MB original; WebP 80% quality → 50–300 KB
- Thumbnail (320px, WebP quality 75): 10–50 KB

**Storage budget estimates per user per year:**

| Photo type | Frequency | Original | Thumbnail | Metadata |
|---|---|---|---|---|
| Screenshot (tech support) | 2/day | 730 × 1 MB = 730 MB | 730 × 25 KB = 18 MB | ~10 MB |
| Receipt capture | 5/week | 260 × 3 MB = 780 MB | 260 × 30 KB = 8 MB | ~5 MB |
| Photo journal | 1/day | 365 × 5 MB = 1.8 GB | 365 × 40 KB = 15 MB | ~10 MB |
| **Total (all three)** | | **~3.3 GB originals** | **~41 MB thumbnails** | **~25 MB** |

**Recommendation:**
- Thumbnails and metadata → PostgreSQL BYTEA/JSONB (< 100 MB/year typical)
- Originals → Docker volume bind-mount (local disk); configurable to S3-compatible
  object storage (MinIO or AWS S3) for users with large volumes
- PostgreSQL BYTEA for originals is **not recommended** above 1 MB per image:
  performance degrades significantly at scale and bloats WAL

### 7.3 Original Storage: Filesystem vs. Object Storage

**Filesystem (Docker volume):**
- Zero additional infrastructure
- Suitable for personal butler with moderate photo volume (< 5 GB)
- Requires volume backup in user's existing backup strategy
- Path: `{BUTLER_DATA_DIR}/photos/originals/{year}/{month}/{image_id}.jpg`

**S3-compatible object storage:**
- MinIO (self-hosted, `AGPL-3.0`): deploys as a Docker container, S3-compatible API
- AWS S3: pay-as-you-go, ~$0.023/GB/month storage
- Recommended when: photo volume > 10 GB, multi-host tailnet, or user has existing S3 infra
- The butler module uses `boto3` regardless of backend (MinIO or AWS both use S3 API)

**Recommendation for v1:**
Default to local filesystem volume. Document S3 as an opt-in configuration with
MinIO as the self-hosted path. Design the storage interface as an abstract backend
from the start so switching between filesystem and S3 requires only config changes.

### 7.4 Thumbnail Strategy

All thumbnails should be:
- Maximum 320×320 pixels (preserving aspect ratio)
- WebP format at quality=75 (best size/quality tradeoff; ~25–35% smaller than JPEG)
- EXIF-stripped
- Stored as `BYTEA` in PostgreSQL for fast retrieval (typical size: 10–50 KB)

For dashboard display, thumbnails are served directly from the DB via the API
route without a separate file lookup — simpler than serving files from disk.

---

## 8. Data Model and Butler Integration

### 8.1 Photos Module in Butler Architecture

The Photos module implements the `Module` ABC:

```python
class PhotosModule(Module):
    name = "photos"
    dependencies = []  # or ["pipeline"] if Telegram integration is wired

    async def register_tools(self, mcp, config, db) -> None:
        # MCP tools exposed to the LLM CLI instance:
        # - bot_photos_ingest_image (ingest raw bytes or file path)
        # - bot_photos_get_image_metadata (retrieve extracted metadata)
        # - bot_photos_search_by_text (full-text search across OCR/captions)
        # - bot_photos_delete_image (user-initiated deletion)
        # - bot_photos_list_recent (list recent ingested images)

    async def migrations(self) -> list[Migration]: ...
    async def on_startup(self, config, db) -> None: ...
    async def on_shutdown(self) -> None: ...
```

### 8.2 Telegram Photo Ingestion Pipeline

When a Telegram bot receives a photo message (`message.photo`):

1. Download the highest-resolution photo variant from Telegram's file server
   (Telegram provides multiple sizes; use `photo[-1]` for the largest).
2. Strip GPS EXIF (mandatory — see §6.1).
3. Run OCR (Tesseract for screenshots; EasyOCR for photos — configurable).
4. Generate thumbnail (320px WebP).
5. Optionally caption with vision model (Moondream2 local; Claude Vision cloud opt-in).
6. Detect faces (flag `has_faces` boolean).
7. Store thumbnail + metadata in PostgreSQL.
8. Store original on filesystem/S3 (if originals retention is enabled).
9. Compose a text summary of the image and inject into the standard pipeline:
   ```
   [Photo received]
   OCR text: "<extracted text>"
   Caption: "<model caption>"
   EXIF: taken 2026-02-19 14:32 | iPhone 15 Pro
   Tags: receipt, food, landscape (semantic)
   ```
10. This text summary becomes the `payload.normalized_text` in the `ingest.v1`
    envelope submitted to Switchboard.

**File format handling:**
- Telegram photos: JPEG (compressed by Telegram)
- Telegram documents (`message.document` with image MIME): PNG, GIF, WebP, HEIC
- HEIC (Apple format): requires `pillow-heif` plugin (`pip install pillow-heif`)
  for Pillow to decode; convert to JPEG/WebP on ingest

### 8.3 Semantic Tagging with CLIP

CLIP (Contrastive Language-Image Pre-training by OpenAI) maps images and text
into the same embedding space. This enables:

1. **Zero-shot image classification / tagging:** Compare the image embedding
   against a set of text label embeddings to assign semantic tags (e.g., "receipt",
   "screenshot", "landscape", "food", "document").
2. **Image similarity search:** Store image embeddings and find visually similar
   images using cosine similarity or FAISS.
3. **Text-to-image search:** Convert a text query to a CLIP embedding and find
   the most relevant stored images.

**Implementation:**

```python
import clip  # pip install git+https://github.com/openai/CLIP.git
import torch
from PIL import Image

model, preprocess = clip.load("ViT-B/32", device="cpu")

# Tag an image
image = preprocess(Image.open(path)).unsqueeze(0)
labels = ["receipt", "screenshot", "landscape", "food", "document", "code"]
text = clip.tokenize(labels)

with torch.no_grad():
    image_features = model.encode_image(image)
    text_features = model.encode_text(text)
    similarity = (image_features @ text_features.T).softmax(dim=-1)

top_tag = labels[similarity.argmax()]
```

**Resources:**
- `ViT-B/32` model: ~350 MB, runs on CPU in 0.5–2 s per image
- `ViT-L/14` model: ~880 MB, higher quality, ~2–5 s on CPU
- Storage per embedding: 512 floats (ViT-B/32) = 2 KB per image

**Privacy:** CLIP runs fully offline. No network egress.

**Recommendation:** Include CLIP tagging in the default local pipeline. Store
the embedding vector in a PostgreSQL `vector` column (via `pgvector` extension)
or serialize as JSON/BYTEA for later similarity search. Tag 5–10 top-scoring
labels per image. This enables the butler to respond to queries like "find the
receipt I sent last week" or "show me food photos from this month."

---

## 9. Database Schema

```sql
-- Core image registry
CREATE TABLE photos_images (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_channel    TEXT NOT NULL,         -- 'telegram', 'upload', 'dashboard'
    source_message_id TEXT,                  -- External message ID (Telegram file_id)
    ingested_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    captured_at       TIMESTAMPTZ,           -- From EXIF DateTimeOriginal
    device_make       TEXT,                  -- EXIF Make (e.g., 'Apple')
    device_model      TEXT,                  -- EXIF Model (e.g., 'iPhone 15 Pro')
    image_width       INTEGER,
    image_height      INTEGER,
    original_format   TEXT NOT NULL,         -- 'jpeg', 'png', 'webp', 'heic'
    original_size_bytes INTEGER,
    original_path     TEXT,                  -- Filesystem path (NULL if not stored)
    original_s3_key   TEXT,                  -- S3 key (NULL if not stored)
    thumbnail_data    BYTEA,                 -- 320px WebP thumbnail (10–50 KB)
    has_faces         BOOLEAN NOT NULL DEFAULT false,
    ocr_text          TEXT,                  -- Raw extracted text (nullable)
    ocr_backend       TEXT,                  -- 'tesseract', 'easyocr', 'paddleocr', 'google-vision'
    caption           TEXT,                  -- Vision model caption (nullable)
    caption_backend   TEXT,                  -- 'moondream2', 'claude-vision', 'llava'
    semantic_tags     TEXT[],                -- CLIP-derived tags
    clip_embedding    BYTEA,                 -- 512-float vector, serialized
    pipeline_request_id UUID                 -- FK to pipeline if wired
);

-- Indexes
CREATE INDEX photos_images_ingested_at_idx ON photos_images (ingested_at DESC);
CREATE INDEX photos_images_source_channel_idx ON photos_images (source_channel);
CREATE INDEX photos_images_ocr_text_idx ON photos_images USING gin(to_tsvector('english', ocr_text))
    WHERE ocr_text IS NOT NULL;
CREATE INDEX photos_images_tags_idx ON photos_images USING gin(semantic_tags);

-- Structured receipt / document extraction results
CREATE TABLE photos_extractions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    image_id    UUID NOT NULL REFERENCES photos_images(id) ON DELETE CASCADE,
    schema_type TEXT NOT NULL,          -- 'receipt', 'invoice', 'document', 'screenshot'
    extracted   JSONB NOT NULL,         -- Structured fields (merchant, total, date, etc.)
    extractor   TEXT NOT NULL,          -- 'claude-vision', 'paddleocr-structure', 'llm-post-ocr'
    extracted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX photos_extractions_image_id_idx ON photos_extractions (image_id);
CREATE INDEX photos_extractions_schema_idx ON photos_extractions (schema_type);
CREATE INDEX photos_extractions_extracted_gin ON photos_extractions USING gin(extracted);
```

Alembic migration: `photos_001_create_photos_tables.py` under
`alembic/versions/photos/`.

---

## 10. Integration Path with Telegram Media Connector

The existing Telegram module already handles `message.photo` and `message.document`
events. The Photos module integrates at the message handling layer:

**Current Telegram flow:**
```
Telegram update → TelegramModule.handle_message()
    → message.text → pipeline injection
    → message.photo → [currently: log raw file_id, no processing]
    → message.voice → VoiceModule.transcribe() (if Voice module enabled)
```

**Extended flow with Photos module enabled:**
```
Telegram update → TelegramModule.handle_message()
    → message.photo → PhotosModule.ingest_telegram_photo()
        → download_file(file_id)
        → strip_exif_gps()
        → generate_thumbnail()
        → run_ocr()  [configurable backend]
        → detect_faces()
        → generate_caption()  [configurable backend]
        → compute_clip_tags()
        → store_to_db()
        → return normalized_text_summary
    → pipeline injection with normalized_text_summary
```

**Interface between modules:**
- PhotosModule exposes `ingest_telegram_photo(bot, message) -> str` coroutine
  returning a text summary for pipeline injection.
- TelegramModule calls this if PhotosModule is present in the resolved module graph.
- No modification to TelegramModule internals required — the module resolution
  and dependency graph already handles this pattern.

**Telegram API photo fields:**
```python
# python-telegram-bot pattern for photo ingestion
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]  # largest resolution variant
    file = await context.bot.get_file(photo.file_id)
    image_bytes = await file.download_as_bytearray()
    # → pass to PhotosModule.ingest(image_bytes, source="telegram",
    #       source_message_id=photo.file_id)
```

**Supported Telegram media types:**
- `message.photo`: JPEG compressed by Telegram (lossy; original not preserved)
- `message.document` with MIME `image/*`: uncompressed original (PNG, WebP, HEIC)
- `message.sticker`: WebP/TGS; out of scope for Photos module (use Stickers module if needed)

---

## 11. Privacy-Preserving Pipeline Summary

The complete privacy-preserving pipeline for an ingested photo:

```
1. RECEIVE: Telegram bot receives photo / dashboard upload
2. STRIP EXIF GPS: Remove GPS IFD from EXIF (mandatory, no exceptions)
   → Retain: DateTimeOriginal, Make, Model, Orientation
   → Delete: GPSInfo, all GPS sub-fields
3. DETECT FACES: MediaPipe / OpenCV Haar → flag has_faces (boolean only)
   → If has_faces=True: suppress auto-captioning, log warning, require user confirmation
4. GENERATE THUMBNAIL: 320px WebP, quality=75, no EXIF → store in PostgreSQL BYTEA
5. STORE ORIGINAL: EXIF-stripped original on filesystem/S3 (configurable, default: 30-day TTL)
6. RUN OCR: Configurable backend; fully local by default (Tesseract / EasyOCR)
7. CAPTION: Configurable; local by default (Moondream2 if installed, else skip)
   → Cloud vision (Claude / GPT-4o) only if user has enabled `cloud_allowed = true`
8. CLIP TAG: Local CLIP embeddings for semantic tags (offline)
9. STORE METADATA: OCR text, caption, tags, EXIF subset → PostgreSQL
10. INJECT PIPELINE: Normalized text summary → ingest.v1 → Switchboard
11. AUTO-PURGE ORIGINALS: Background task purges originals older than retention_days
```

**Configuration flags (butler.toml):**
```toml
[modules.photos]
ocr_backend = "easyocr"         # tesseract | easyocr | paddleocr | google-vision
vision_backend = "none"         # none | moondream2 | llava | claude-vision | gpt4o-vision
clip_tagging = true             # Enable local CLIP semantic tags
face_detection = true           # Enable face presence detection
cloud_allowed = false           # Must be true to enable cloud OCR or cloud vision
retain_originals_days = 30      # 0 = never store originals; -1 = keep forever
thumbnail_max_px = 320          # Max thumbnail dimension
```

---

## 12. Hardware Requirements

### 12.1 Minimum Configuration (Screenshots, CPU-only)

Target: Screenshot OCR + Tesseract + no vision captioning

| Component | Minimum | Notes |
|---|---|---|
| CPU | Any modern x86/ARM | Tesseract is single-threaded |
| RAM | 1 GB available | Tesseract < 500 MB |
| Disk | 500 MB | Tesseract models + thumbnail storage |
| GPU | None required | |

### 12.2 Recommended Configuration (Photos + OCR + Local Vision)

Target: EasyOCR + Moondream2 + CLIP tagging

| Component | Recommended | Notes |
|---|---|---|
| CPU | 4-core x86_64 at 3 GHz+ (AVX2) | AVX2 required for Moondream2 4-bit ONNX |
| RAM | 6 GB available | EasyOCR ~1.5 GB + Moondream2 ~2.5 GB + CLIP ~0.5 GB |
| Disk | 5 GB | Models + thumbnail storage + 30-day originals cache |
| GPU | None required (optional) | GPU accelerates EasyOCR / Moondream2 significantly |

### 12.3 Hardware by Use Case

| Use case | OCR backend | Vision backend | RAM | GPU |
|---|---|---|---|---|
| Screenshot text extraction | Tesseract | None | < 1 GB | No |
| Receipt capture (basic) | EasyOCR | None | ~2 GB | No |
| Receipt capture (structured) | PaddleOCR PP-Structure | None | ~2.5 GB | No |
| Full visual memory (local) | EasyOCR | Moondream2 | ~4.5 GB | Optional |
| Full visual memory (GPU) | EasyOCR | LLaVA-7B / Claude Vision | 6 GB VRAM | Yes / N/A |
| Max accuracy (cloud opt-in) | Google Vision | Claude Vision | ~2 GB | No |

---

## 13. Recommended Pipeline by Use Case

### Primary Recommendation: Local-Only Pipeline

**OCR: Tesseract for screenshots + EasyOCR for camera photos**
- Tesseract is adequate for controlled-quality screenshots (no preprocessing needed)
- EasyOCR handles noisy real-world photos (receipts, documents on camera)
- Both run offline, Apache 2.0 licenses

**Vision captioning: Moondream2 (2B, 4-bit)**
- Local, Apache 2.0, runs on CPU with 2.5 GB RAM
- Quality sufficient for captioning (not for precise receipt extraction)
- Enable only if the user has enough RAM (`vision_backend = "moondream2"`)

**Semantic tagging: CLIP ViT-B/32**
- Local, MIT license
- 350 MB model, 0.5–2 s per image on CPU
- Enables text-to-image search in butler memory

**Default if RAM-constrained:** Tesseract only (< 1 GB RAM) — still useful for
screenshot text extraction without any deep learning dependency.

### Optional Cloud Upgrade

**Cloud vision: Claude Vision API** (enable with `cloud_allowed = true`)
- Best-in-class understanding, already integrated in the butler's existing Anthropic SDK usage
- Eliminates need for separate local vision model
- Costs ~$0.009/image for 3K-token photos
- Privacy: images sent to Anthropic — must be user-confirmed opt-in

**Cloud OCR: Google Vision API** (enable separately with `cloud_allowed = true`)
- Best OCR accuracy (96–99%) for critical receipts/documents
- Free for ≤ 1,000 images/month; $1.50/1,000 beyond that
- Privacy: images sent to Google — must be user-confirmed opt-in

---

## 14. Open Questions for Implementation

1. **Receipt extraction schema:** What structured fields should be extracted from
   receipts? (merchant, total, date, line items, currency, payment method). Should
   the butler auto-create expense entries in a linked expense tracker module?

2. **CLIP embedding storage:** Should CLIP embeddings be stored as PostgreSQL BYTEA
   (JSON-serialized) or via the `pgvector` extension? `pgvector` enables native
   vector similarity queries (`ORDER BY embedding <-> query_embedding`) but requires
   an extension install. BYTEA + Python-side similarity is simpler to deploy.

3. **Moondream2 startup time:** Moondream2 takes 5–15 s to load on first use.
   Should the model be loaded once at `on_startup()` and held in memory (higher
   idle RAM) or loaded on demand per image (per-image latency spike)?

4. **Photo deduplication:** Users may send the same screenshot twice. Should the
   butler detect duplicates via perceptual hash (pHash) and skip re-processing?
   This requires `imagehash` PyPI package + a DB index on the hash.

5. **HEIC support:** Apple HEIC files require `pillow-heif`. Should this be
   an optional dependency (fails gracefully if not installed) or required?

6. **OCR language detection:** EasyOCR supports 80+ languages. Should the butler
   auto-detect the language of OCR text and store it? Or always run in the user's
   configured language?

7. **Sticker handling:** Telegram stickers arrive as `message.sticker` (WebP/TGS).
   Should the Photos module skip them or treat them as regular images?

8. **Dashboard screenshot upload size limit:** What is the maximum accepted upload
   size? Recommend 20 MB matching Telegram's `document` limit; configurable.

9. **Face detection threshold:** MediaPipe and OpenCV Haar have configurable
   sensitivity thresholds. False positives (flagging non-face images as having
   faces) should not block captioning unnecessarily. Need to tune confidence
   threshold during implementation testing.

10. **pgvector availability:** The `pgvector` extension may not be available in
    all PostgreSQL deployments. The DB schema above uses `BYTEA` for embeddings.
    If `pgvector` is available, a future migration can add a typed `vector(512)`
    column and a native HNSW index for fast ANN search.

---

## 15. Implementation Checklist (for future ticket)

When the implementation ticket is created:

1. Add `pytesseract`, `easyocr`, `pillow`, `piexif`, `opencv-python` to
   `pyproject.toml` dependencies.
2. Add `clip` (CLIP ViT-B/32) and `imagehash` as optional extras.
3. Add `pillow-heif` as optional extra for HEIC support.
4. Add Moondream2 as optional extra (`moondream` PyPI package).
5. Create `src/butlers/modules/photos.py` implementing `PhotosModule`.
6. Write Alembic migration `photos_001_create_photos_tables.py`.
7. Implement `strip_exif_gps()` utility in `src/butlers/utils/image.py`.
8. Implement `generate_thumbnail()` utility.
9. Implement `run_ocr(image_bytes, backend) -> str` dispatcher.
10. Implement `detect_faces(image_bytes) -> bool` using MediaPipe.
11. Implement `compute_clip_tags(image_bytes, labels) -> list[str]`.
12. Wire Telegram photo handler to call `PhotosModule.ingest_telegram_photo()`.
13. Expose MCP tools: `bot_photos_ingest_image`, `bot_photos_search_by_text`,
    `bot_photos_delete_image`, `bot_photos_list_recent`.
14. Add `[modules.photos]` config section to butler TOML schema.
15. Add `POST /photos` endpoint to butler dashboard API router.
16. Write unit tests: EXIF stripping, thumbnail generation, OCR dispatch,
    face detection flag, pipeline injection.
17. Document privacy-preserving pipeline in this file (§11) and link from
    `docs/connectors/interface.md`.

---

## 16. References

**OCR**
- [Technical Analysis of Modern Non-LLM OCR Engines — IntuitionLabs](https://intuitionlabs.ai/articles/non-llm-ocr-technologies)
- [8 Top Open-Source OCR Models Compared — Modal](https://modal.com/blog/8-top-open-source-ocr-models-compared)
- [Comparing the Best Open Source OCR Tools in 2025 — Unstract](https://unstract.com/blog/best-opensource-ocr-tools-in-2025/)
- [PaddleOCR vs Tesseract — Koncile AI](https://www.koncile.ai/en/ressources/paddleocr-analyse-avantages-alternatives-open-source)
- [PaddleOCR GitHub (Baidu)](https://github.com/PaddlePaddle/PaddleOCR)
- [docTR GitHub (Mindee)](https://github.com/mindee/doctr)
- [EasyOCR GitHub (JaidedAI)](https://github.com/JaidedAI/EasyOCR)
- [DeepSeek-OCR vs GPT-4-Vision vs PaddleOCR 2025 — Skywork AI](https://skywork.ai/blog/ai-agent/deepseek-ocr-vs-gpt-4-vision-vs-paddleocr-2025-comparison/)
- [OCR Ranking 2025 — Pragmile](https://pragmile.com/ocr-ranking-2025-comparison-of-the-best-text-recognition-and-document-structure-software/)
- [Google Cloud Vision API Pricing](https://cloud.google.com/vision/pricing)
- [Receipt OCR with Python 2025 — Tabscanner](https://tabscanner.com/receipt-ocr-using-python/)

**Image Understanding / Vision Models**
- [Claude Vision API Docs — Anthropic](https://docs.claude.com/en/docs/build-with-claude/vision)
- [LLM API Pricing Comparison 2025 — IntuitionLabs](https://intuitionlabs.ai/articles/llm-api-pricing-comparison-2025)
- [Moondream GitHub (vikhyat)](https://github.com/vikhyat/moondream)
- [Moondream2 on HuggingFace](https://huggingface.co/vikhyatk/moondream2)
- [LLaVA GitHub (haotian-liu)](https://github.com/haotian-liu/LLaVA)
- [LLaVA: Open-Source Alternative to GPT-4V — Towards Data Science](https://towardsdatascience.com/llava-an-open-source-alternative-to-gpt-4v-ision-b06f88ce8efa/)
- [LocalAI GPT Vision (LLaVA via LocalAI)](https://localai.io/features/gpt-vision/)

**Image Processing and EXIF**
- [Pillow ExifTags Documentation](https://pillow.readthedocs.io/en/stable/reference/ExifTags.html)
- [How to Remove EXIF Data Using Python — MetaRemover](https://metaremover.com/articles/en/exif-remove-python)
- [Extracting GPS Location from Image Metadata — Medium](https://medium.com/@hemant.ramphul/extracting-gps-location-from-image-metadata-using-python-881ff442c641)
- [OpenCV Image Resizing — OpenCV Blog](https://opencv.org/blog/resizing-and-rescaling-images-with-opencv/)
- [Python Image Resize with Pillow and OpenCV — Cloudinary](https://cloudinary.com/guides/bulk-image-resize/python-image-resize-with-pillow-and-opencv)

**Semantic Tagging and Embeddings**
- [Building an Image Similarity Search Engine with FAISS and CLIP — Towards Data Science](https://towardsdatascience.com/building-an-image-similarity-search-engine-with-faiss-and-clip-2211126d08fa/)
- [CLIP Embeddings for Multimodal RAG — OpenAI Cookbook](https://cookbook.openai.com/examples/custom_image_embedding_search)
- [Building an AI Home Security System with CLIP and Raspberry Pi — Jamie Maguire](https://jamiemaguire.net/index.php/2025/05/11/building-an-ai-home-security-system-using-net-python-clip-semantic-kernel-telegram-and-raspberry-pi-4-part-2/)

**Face Detection and Privacy**
- [DeepFace GitHub (serengil)](https://github.com/serengil/deepface)
- [GDPR for Images — GDPR Local](https://gdprlocal.com/gdpr-for-images/)
- [GDPR for Self-Hosted Apps — WeHaveServers](https://wehaveservers.com/blog/compliance-privacy/gdpr-for-self-hosted-apps-logs-backups-and-data-retention/)
- [Data Minimization and Retention Policies — SecurePrivacy](https://secureprivacy.ai/blog/data-minimization-retention-policies)

**Storage**
- [Optimizing Image Storage in PostgreSQL — Medium](https://medium.com/@ajaymaurya73130/optimizing-image-storage-in-postgresql-tips-for-performance-scalability-fd4d575a6624)
- [WebP Compression Study — Google for Developers](https://developers.google.com/speed/webp/docs/webp_study)
- [Ente Photos Self-Hosted Setup with Tailscale](https://koller.ninja/2025/12/26/ente-photos-self-hosted-setup-with-tailscale-https.html)

**Telegram Integration**
- [python-telegram-bot on PyPI](https://pypi.org/project/python-telegram-bot/)
- [Telegram Bot API Files Documentation](https://core.telegram.org/api/files)
