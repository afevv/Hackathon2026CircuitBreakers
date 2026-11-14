# Project A.S.S.E.R.T.

A line-oriented pipeline for migrating legacy Danfoss DP600 TEXTDATA/TDS and IMAGEDATA/IDS resources into a modern SCREENREPO.SRD plus a line-flag audit report.

## Run

```bash
cd project-assert
python -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000.

## Features

- Parses the legacy XML inputs into a UIR and assigns a shared `Line_###` ID to each resource
- Uses the dynamic dictionary for deterministic hits and a one-pass LLM fallback for unknown lines
- Optional local Ollama Vision/OCR infers normalized screen boxes from an uploaded coordinate-less screenshot
- Scales inferred geometry to the DM710 canvas and resizes available bitmap assets to PNG with Pillow LANCZOS
- Applies per-line boolean checks for syntax, machine safety, layout, and traceability; failed lines are marked for human review without LLM reassessment
- Produces `SCREENREPO.SRD`, a downloadable line-flag audit JSON, and a dictionary update
- Dashboard previews the migration, audit flags, image assets, and ROI estimates
- Legacy-to-SRD bitmap preview: run a migration, select an IDS image, then load its bundled sample BMP or choose a local BMP. The selected file is rendered in-browser and is not uploaded.
- Custom per-image target aspect ratios update the generated SRD dimensions and preview; the generated width is kept and the height is adjusted to match.

## Local models

The default mock fallback does not require Ollama. Enable the LLM toggle to use a local text model. Uploading a screen image invokes local Ollama Vision for OCR and geometry inference.

- `OLLAMA_URL` defaults to `http://localhost:11434`
- `OLLAMA_LLM_MODEL` defaults to `llama3.2`
- `OLLAMA_VISION_MODEL` defaults to `llava`

Set these variables to models installed in your local Ollama instance. Screen captures and uploaded bitmap assets are sent only to that local Ollama endpoint.
Bitmap resizing is deterministic LANCZOS interpolation, not learned super-resolution.
