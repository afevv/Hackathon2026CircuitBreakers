# DP600 XML to DM700 SRD Converter

A small template application for converting DP600-style `TextData` and
`ImageData` XML files into DM700-style `.srd` screen XML files. Upload one or
more matching pairs in the browser UI; each pair produces a separate screen.

## Run on Windows

Double-click `run_converter.bat`. On first use, it creates a local `.venv` and
installs Streamlit. The converter opens in your browser.

To start it manually:

```powershell
python -m pip install -r converter-requirements.txt
python -m streamlit run converter_app.py
```

## Input and output

- Pair files by suffix: `textdata1.xml` with `imgdata1.xml`,
  `textdata2.xml` with `imgdata2.xml`, and so on.
- A single pair with other filenames is accepted if the XML roots are
  `TextData` and `ImageData`.
- Text elements need `id`, `x`, `y`, `size`, and `color` attributes and text
  content. Image elements need `id`, `x`, `y`, and `file` attributes.
- The output root is `<Screen>`. Text and image positions are scaled from the
  source resolution to the target resolution; font size, target font, text box
  size, image size, background, and both resolutions can be changed in the
  sidebar.
- One converted screen downloads as `.srd`; multiple screens download as a ZIP.
  Optionally upload image assets too; they will be included in the ZIP next to
  the screen file(s). The output image references use just the asset filename.

The default sizes and mappings are starting values based on the provided
illustration, not a vendor-validated SRD specification. Adjust the settings or
the converter mapping for your actual device schema. The generated XML is
intended as a template and should be tested on a copy of the target device files.

## Test

```powershell
python -m unittest discover -s tests
```
