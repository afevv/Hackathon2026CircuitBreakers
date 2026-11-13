from __future__ import annotations

import streamlit as st

from xml_srd_converter import ConversionSettings, convert_uploads
from xml_srd_converter.converter import create_download


st.set_page_config(page_title="DP600 XML to SRD converter", page_icon="🔄", layout="centered")
st.title("DP600 XML to DM700 SRD converter")
st.write("Upload matching `textdata*.xml` and `imgdata*.xml` files to create one `.srd` screen per pair.")

with st.sidebar:
    st.header("Display and layout")
    st.caption("Defaults use the example displays. Change these if your screens use different resolutions.")
    source_width = st.number_input("Source width (DP600)", min_value=1, value=320)
    source_height = st.number_input("Source height (DP600)", min_value=1, value=240)
    target_width = st.number_input("Target width (DM700)", min_value=1, value=800)
    target_height = st.number_input("Target height (DM700)", min_value=1, value=480)
    font = st.text_input("Target font", value="Roboto")
    font_scale = st.number_input("Font-size scale", min_value=0.1, value=2.0, step=0.1)
    text_width = st.number_input("Text box width", min_value=1, value=500)
    text_height = st.number_input("Text box height", min_value=1, value=60)
    image_width = st.number_input("Image width", min_value=1, value=96)
    image_height = st.number_input("Image height", min_value=1, value=96)
    background = st.text_input("Screen background (RGBA)", value="0,0,0,0")

uploads = st.file_uploader(
    "Choose XML files and, optionally, image assets",
    type=["xml", "png", "jpg", "jpeg", "bmp", "gif"],
    accept_multiple_files=True,
)

if uploads:
    input_files = {upload.name: upload.getvalue() for upload in uploads}
    xml_files = {
        name: content for name, content in input_files.items()
        if name.lower().endswith(".xml")
    }
    image_assets = {
        name: content for name, content in input_files.items()
        if not name.lower().endswith(".xml")
    }
    st.write("Files selected:", ", ".join(input_files))
    if st.button("Convert to SRD", type="primary"):
        try:
            settings = ConversionSettings(
                source_width=source_width,
                source_height=source_height,
                target_width=target_width,
                target_height=target_height,
                text_font=font.strip() or "Roboto",
                text_size_scale=font_scale,
                text_width=text_width,
                text_height=text_height,
                image_width=image_width,
                image_height=image_height,
                background=background.strip(),
            )
            srds = convert_uploads(xml_files, settings)
            filename, mime_type, content = create_download(srds, image_assets)
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.success(f"Created {len(srds)} SRD screen(s).")
            for name in srds:
                st.write(f"- `{name}`")
            if image_assets:
                st.info("The download includes the uploaded image assets alongside the SRD file(s).")
            st.download_button(
                "Download converted file(s)",
                data=content,
                file_name=filename,
                mime=mime_type,
                type="primary",
            )
else:
    st.info("Upload at least one matching TextData and ImageData XML pair to begin.")
