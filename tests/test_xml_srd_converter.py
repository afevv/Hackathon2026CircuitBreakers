import unittest
import xml.etree.ElementTree as ET
from zipfile import ZipFile
from io import BytesIO

from xml_srd_converter.converter import (
    ConversionSettings,
    convert_pair,
    convert_uploads,
    create_download,
)


TEXT_XML = b"""<TextData>
  <Text id="1" x="20" y="40" font="Arial" size="16" color="255,255,255">Engine Speed</Text>
  <Text id="2" x="20" y="80" font="Arial" size="16" color="255,255,255">2500 RPM</Text>
</TextData>"""
IMAGE_XML = b"""<ImageData>
  <Image id="1" x="220" y="40" file="engine.png" />
</ImageData>"""


class ConverterTests(unittest.TestCase):
    def test_convert_pair_scales_positions_and_combines_elements(self):
        result = ET.fromstring(convert_pair(TEXT_XML, IMAGE_XML))
        self.assertEqual(result.tag, "Screen")
        self.assertEqual(result.attrib["width"], "800")
        text = result.findall("Text")
        image = result.find("Image")
        self.assertEqual(text[0].attrib["x"], "50")
        self.assertEqual(text[0].attrib["y"], "80")
        self.assertEqual(text[0].attrib["size"], "32")
        self.assertEqual(text[0].text, "Engine Speed")
        self.assertEqual(image.attrib["x"], "550")
        self.assertEqual(image.attrib["file"], "engine.png")

    def test_custom_resolution_and_layout_settings_are_used(self):
        settings = ConversionSettings(
            source_width=100,
            source_height=100,
            target_width=200,
            target_height=300,
            text_font="TestFont",
            text_size_scale=1.5,
            text_width=300,
            text_height=40,
        )
        result = ET.fromstring(convert_pair(TEXT_XML, IMAGE_XML, settings))
        text = result.find("Text")
        self.assertEqual((text.attrib["x"], text.attrib["y"]), ("40", "120"))
        self.assertEqual((text.attrib["font"], text.attrib["size"]), ("TestFont", "24"))
        self.assertEqual((text.attrib["width"], text.attrib["height"]), ("300", "40"))

    def test_convert_uploads_pairs_by_suffix_and_names_outputs(self):
        result = convert_uploads({
            "imgdata2.xml": IMAGE_XML,
            "textdata2.xml": TEXT_XML,
            "textdata1.xml": TEXT_XML,
            "imgdata1.xml": IMAGE_XML,
        })
        self.assertEqual(list(result), ["screen1.srd", "screen2.srd"])

    def test_convert_uploads_reports_missing_pair(self):
        with self.assertRaisesRegex(ValueError, "missing imgdata"):
            convert_uploads({"textdata1.xml": TEXT_XML})

    def test_invalid_xml_is_reported_with_filename(self):
        with self.assertRaisesRegex(ValueError, "textdata1.xml: invalid XML"):
            convert_uploads({"textdata1.xml": b"<TextData>", "imgdata1.xml": IMAGE_XML})

    def test_download_bundles_screens_and_assets(self):
        filename, mime_type, content = create_download(
            {"screen1.srd": b"<Screen />"},
            {"images/engine.png": b"image-data"},
        )
        self.assertEqual((filename, mime_type), ("converted_screens.zip", "application/zip"))
        with ZipFile(BytesIO(content)) as archive:
            self.assertEqual(set(archive.namelist()), {"screen1.srd", "engine.png"})


if __name__ == "__main__":
    unittest.main()
