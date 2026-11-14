# Project A.S.S.E.R.T.

A FastAPI-based prototype for migrating legacy Danfoss DP600 TEXTDATA/TDS and IMAGEDATA/IDS catalogs into a modern SCREENREPO.SRD representation.

## Run

```bash
cd project-assert
python -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000.

## Features

- Legacy XML parsing into a universal intermediate representation
- Deterministic rule dictionary and self-healing LLM fallback workflow
- Math-gate trust scoring and Red/Amber/Green routing
- Generated SRD XML preview and dictionary inspector
- Dark-mode dashboard for demo and safety-hazard simulation
- Legacy-to-SRD bitmap preview: run a migration, select an IDS image, then load its bundled sample BMP or choose a local BMP. The selected file is rendered in-browser and is not uploaded.
- Custom per-image target aspect ratios update the generated SRD dimensions and preview; the generated width is kept and the height is adjusted to match.
