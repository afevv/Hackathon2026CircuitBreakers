from __future__ import annotations

import streamlit as st

from xml_srd_converter import convert_uploads
from xml_srd_converter.converter import create_download


st.set_page_config(page_title="TDS/IDS to SRD converter", page_icon="🔄", layout="centered")
st.title("TDS/IDS to SRD converter")
st.write(
    "Upload a `.TDS` text catalog, its matching `.IDS` image catalog, "
    "and the BMP files referenced by the `.IDS`."
)
st.caption(
    "The source catalogs contain resources, not screen positions. "
    "The generated SRD includes the resource library; add screen layouts in the target application."
)

uploads = st.file_uploader(
    "Choose the .TDS, .IDS, and referenced .BMP files",
    type=["tds", "ids", "bmp"],
    accept_multiple_files=True,
)

if uploads:
    input_files = {upload.name: upload.getvalue() for upload in uploads}
    st.write("Files selected:", ", ".join(input_files))
    if st.button("Convert to SRD", type="primary"):
        try:
            srds = convert_uploads(input_files)
            image_assets = {
                name: content for name, content in input_files.items()
                if name.lower().endswith(".bmp")
            }
            filename, mime_type, content = create_download(srds, image_assets)
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.success("Created an SRD resource library.")
            for name in srds:
                st.write(f"- `{name}`")
            if image_assets:
                st.info("The download includes the referenced BMP image assets.")
            st.download_button(
                "Download SRD package",
                data=content,
                file_name=filename,
                mime=mime_type,
                type="primary",
            )
else:
    st.info("Upload one .TDS file, one matching .IDS file, and all BMPs referenced by the .IDS.")
