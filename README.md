# TDS/IDS to SRD Converter

A small template application for converting `.TDS` text catalogs and `.IDS`
image catalogs into the `ScreenRepository` XML format used by `.SRD` files.
Upload one matching TDS/IDS pair and the BMP assets referenced in the IDS.

## Run on Windows

Double-click `run_converter.bat`. On first use, it creates a local `.venv` and
installs Streamlit. The converter opens in your browser.

To start it manually:

```powershell
python -m pip install -r converter-requirements.txt
python -m streamlit run converter_app.py
```

## Input and output

- The `.TDS` root must be `TextDataSource`, and the `.IDS` root must be
  `ImageDataSource`. The converter preserves resource IDs, descriptions,
  folders, language strings, and image metadata supported by the SRD example.
- Integer format specifiers `%d`, `%i`, `%u`, `%x`, and `%X` are rewritten as
  SRD placeholders with matching `Inputs/FC` metadata. Other format specifiers
  are reported as unsupported rather than silently altered.
- Upload each BMP named by an IDS `<SourceFileName>` entry. BMP dimensions are
  needed to create the corresponding SRD image resource; the app includes the
  BMPs alongside the SRD in a ZIP download.
- Output is a `ScreenRepository` with a `ScreenLibrary`, language definition,
  and empty screen/application-definition repositories. TDS/IDS contain no
  positions or screen layouts, so the converter does not invent them. Add
  layouts in the target application after importing the resource library.
- The generated format version and element mappings are based on the supplied
  `SCREENREPO.SRD` sample. Validate the output in the target software before
  using it in production.

The earlier simple XML-to-screen demo remains available as a library function
for tests, but the browser UI is focused on the real TDS/IDS resource catalogs.

## Test

```powershell
python -m unittest discover -s tests
```
