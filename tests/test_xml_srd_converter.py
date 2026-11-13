import unittest
import xml.etree.ElementTree as ET
from zipfile import ZipFile
from io import BytesIO
import struct

from xml_srd_converter.converter import (
    ConversionSettings,
    convert_pair,
    convert_resource_pair,
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
TDS = b"""<?xml version="1.0"?>
<TextDataSource DefLang="1" OrgLang="1">
  <FileFormat>002A</FileFormat>
  <Folder Name="Main">
    <Folder Name="AnotherFolder">
      <Text>
        <ID>4</ID>
        <Description>ShowPlusSign</Description>
        <Used_by/>
        <String LanguageIndex="1" CodePage="1252" TextData="Value %+04d V"/>
        <String LanguageIndex="2" CodePage="1252" TextData="Vaerdi %+04d V"/>
      </Text>
    </Folder>
    <Text>
      <ID>1</ID>
      <Description>SupplyVoltage</Description>
      <Used_by/>
      <String LanguageIndex="1" CodePage="1252" TextData="Supply Voltage %4d V"/>
      <String LanguageIndex="2" CodePage="1252" TextData="Forsyningsspaending %4d V"/>
    </Text>
  </Folder>
  <Language><Name>English</Name><Index>1</Index><Hidden>0</Hidden><DefaultCodePage>1252</DefaultCodePage></Language>
  <Language><Name>Danish</Name><Index>2</Index><Hidden>0</Hidden><DefaultCodePage>1252</DefaultCodePage></Language>
</TextDataSource>"""
IDS = b"""<?xml version="1.0"?>
<ImageDataSource>
  <FileFormat>001E</FileFormat>
  <Folder Name="Main">
    <Image>
      <ID>2</ID><Description>TORTUGA</Description><SourceFileName>TORTUGA.BMP</SourceFileName>
      <OutputFormat>RLE</OutputFormat><ColorDepth>1</ColorDepth><PixelOrigo>0</PixelOrigo>
      <InsertionPoint X="0" Y="0"/><Transparent PaletteColor="0" X="0" Y="28" LowLeft="1"/>
    </Image>
  </Folder>
  <Image>
    <ID>1</ID><Description>LIEBRE</Description><SourceFileName>LIEBRE.BMP</SourceFileName>
    <OutputFormat>RLE</OutputFormat><ColorDepth>8</ColorDepth><PixelOrigo>0</PixelOrigo>
    <InsertionPoint X="0" Y="0"/><Transparent PaletteColor="0" X="0" Y="38" LowLeft="1"/>
  </Image>
</ImageDataSource>"""


def bmp(width: int, height: int) -> bytes:
    content = bytearray(54)
    content[:2] = b"BM"
    struct.pack_into("<I", content, 14, 40)
    struct.pack_into("<ii", content, 18, width, height)
    return bytes(content)


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

    def test_tds_ids_convert_to_screen_repository_resources(self):
        result = ET.fromstring(convert_resource_pair(
            TDS,
            IDS,
            {"tortuga.bmp": bmp(43, 39), "LIEBRE.BMP": bmp(53, 29)},
        ))
        self.assertEqual(result.tag, "ScreenRepository")
        self.assertEqual(result.findtext("FileFormat"), "006A")
        self.assertEqual(result.find("ScreenDefRepository").findall("ScreenDef"), [])

        library = result.find("ScreenLibrary")
        self.assertEqual(library.get("Name"), "Library")
        self.assertEqual(library.find("LanguageDefinition").get("DefLang"), "1")
        self.assertEqual(len(library.findall("LanguageDefinition/Language")), 2)

        nested_text = library.find("Folder/Folder[@Name='AnotherFolder']/Text")
        self.assertEqual(nested_text.find("String").get("TextData"), "Value %1$ V")
        fc = nested_text.find("Inputs/FC")
        self.assertEqual(fc.attrib, {
            "Type": "Signed",
            "Digits": "4",
            "Value": "0",
            "Prefix": "2",
            "Padding": "3",
            "Base": "1",
            "Alt": "false",
        })
        image = next(image for image in library.findall(".//Image") if image.findtext("Description") == "LIEBRE")
        self.assertEqual(image.findtext("Width"), "53")
        self.assertEqual(image.findtext("Height"), "29")
        self.assertEqual(image.findtext("ImageFormatName"), "256 Colors (8 bit)")

    def test_upload_converter_accepts_tds_ids_and_bmp_assets(self):
        result = convert_uploads({
            "TEXTDATA.TDS": TDS,
            "IMAGEDATA.IDS": IDS,
            "TORTUGA.BMP": bmp(43, 39),
            "LIEBRE.BMP": bmp(53, 29),
        })
        self.assertEqual(list(result), ["ScreenRepository.srd"])
        self.assertEqual(ET.fromstring(result["ScreenRepository.srd"]).tag, "ScreenRepository")

    def test_ids_referenced_bmp_is_required_for_dimensions(self):
        with self.assertRaisesRegex(ValueError, "upload the referenced image asset"):
            convert_resource_pair(TDS, IDS, {})

    def test_unsupported_text_format_is_reported(self):
        invalid_tds = TDS.replace(b"%4d", b"%4f")
        with self.assertRaisesRegex(ValueError, "supports %d, %i, %u, %x, and %X"):
            convert_resource_pair(
                invalid_tds,
                IDS,
                {"TORTUGA.BMP": bmp(43, 39), "LIEBRE.BMP": bmp(53, 29)},
            )


if __name__ == "__main__":
    unittest.main()
