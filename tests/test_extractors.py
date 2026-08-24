import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from save_your_memory.extractors import extract_content


class ExtractorTests(unittest.TestCase):
    def test_extracts_utf8_and_cp949_text_with_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            utf8 = root / "note.md"
            cp949 = root / "legacy.txt"
            utf8.write_text("기억할 내용", encoding="utf-8")
            cp949.write_bytes("오래된 문서".encode("cp949"))

            utf8_result = extract_content(utf8, max_bytes=1024)
            cp949_result = extract_content(cp949, max_bytes=1024)

            self.assertEqual(utf8_result.status, "extracted")
            self.assertEqual(utf8_result.content, "기억할 내용")
            self.assertEqual(utf8_result.extractor, "text:utf-8-sig")
            self.assertEqual(len(utf8_result.sha256), 64)
            self.assertEqual(cp949_result.status, "extracted")
            self.assertEqual(cp949_result.content, "오래된 문서")
            self.assertEqual(cp949_result.extractor, "text:cp949")

    def test_marks_unknown_binary_and_too_large_files_without_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            binary = root / "archive.bin"
            large = root / "large.txt"
            binary.write_bytes(b"\x00\x01\x02\xff")
            large.write_text("12345", encoding="utf-8")

            unsupported = extract_content(binary, max_bytes=1024)
            too_large = extract_content(large, max_bytes=4)

            self.assertEqual(unsupported.status, "unsupported")
            self.assertEqual(unsupported.content, "")
            self.assertIn("Unsupported", unsupported.error)
            self.assertEqual(too_large.status, "too_large")
            self.assertEqual(too_large.content, "")
            self.assertIn("4 bytes", too_large.error)

    def test_content_sniffing_extracts_text_with_unknown_or_missing_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            typed = root / "module.pyi"
            extensionless = root / "Makefile"
            typed.write_text("def calculate(value: int) -> int: ...", encoding="utf-8")
            extensionless.write_text("build:\n\tpython -m compileall .", encoding="utf-8")

            typed_result = extract_content(typed, max_bytes=10_000)
            extensionless_result = extract_content(extensionless, max_bytes=10_000)

            self.assertEqual(typed_result.status, "extracted")
            self.assertEqual(typed_result.content, "def calculate(value: int) -> int: ...")
            self.assertEqual(typed_result.extractor, "text-sniffed:utf-8-sig")
            self.assertEqual(extensionless_result.status, "extracted")
            self.assertIn("python -m compileall", extensionless_result.content)

    def test_uses_pymupdf_fallback_when_pdftotext_is_unavailable(self) -> None:
        if importlib.util.find_spec("fitz") is None:
            self.skipTest("PyMuPDF is not installed")
        import fitz

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sample.pdf"
            document = fitz.open()
            page = document.new_page()
            page.insert_text((72, 72), "Aurora PDF evidence")
            path.write_bytes(document.tobytes())
            document.close()

            with patch("save_your_memory.extractors.shutil.which", return_value=None):
                result = extract_content(path, max_bytes=100_000)

            self.assertEqual(result.status, "extracted")
            self.assertEqual(result.extractor, "pymupdf")
            self.assertIn("Aurora PDF evidence", result.content)

    def test_caps_pdf_extracted_text_size(self) -> None:
        if importlib.util.find_spec("fitz") is None:
            self.skipTest("PyMuPDF is not installed")
        import fitz

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "expanded.pdf"
            document = fitz.open()
            page = document.new_page(width=10_000, height=100)
            page.insert_text((20, 40), "A" * 10_000, fontsize=1)
            path.write_bytes(document.tobytes(deflate=True))
            document.close()
            preview = fitz.open(path)
            extracted_size = sum(
                len(page.get_text("text", sort=True).encode("utf-8"))
                for page in preview
            )
            preview.close()
            budget = path.stat().st_size + 1_024
            self.assertGreater(extracted_size, budget)

            with patch("save_your_memory.extractors.shutil.which", return_value=None):
                result = extract_content(path, max_bytes=budget)

            self.assertEqual(result.status, "too_large")
            self.assertEqual(result.content, "")
            self.assertIn("PDF extracted text exceeds", result.error)

    def test_extracts_docx_paragraphs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sample.docx"
            xml = """<?xml version="1.0" encoding="UTF-8"?>
            <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
              <w:body>
                <w:p><w:r><w:t>Hello </w:t></w:r><w:r><w:t>world</w:t></w:r></w:p>
                <w:p><w:r><w:t>Second line</w:t></w:r></w:p>
              </w:body>
            </w:document>"""
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("word/document.xml", xml)

            result = extract_content(path, max_bytes=100_000)

            self.assertEqual(result.status, "extracted")
            self.assertEqual(result.extractor, "ooxml:docx")
            self.assertEqual(result.content, "Hello world\nSecond line")

    def test_extracts_pptx_slides_in_numeric_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sample.pptx"
            slide_template = """<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
              xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
              <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>
            </p:sld>"""
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(
                    "ppt/slides/slide10.xml", slide_template.format(text="Tenth")
                )
                archive.writestr(
                    "ppt/slides/slide2.xml", slide_template.format(text="Second")
                )

            result = extract_content(path, max_bytes=100_000)

            self.assertEqual(result.status, "extracted")
            self.assertEqual(result.extractor, "ooxml:pptx")
            self.assertEqual(result.content, "Slide 2\nSecond\n\nSlide 10\nTenth")

    def test_extracts_xlsx_shared_inline_and_numeric_cells(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sample.xlsx"
            shared = """<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <si><t>Shared value</t></si>
            </sst>"""
            sheet = """<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <sheetData><row r="1">
                <c r="A1" t="s"><v>0</v></c>
                <c r="B1" t="inlineStr"><is><t>Inline value</t></is></c>
                <c r="C1"><v>42</v></c>
              </row></sheetData>
            </worksheet>"""
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("xl/sharedStrings.xml", shared)
                archive.writestr("xl/worksheets/sheet1.xml", sheet)

            result = extract_content(path, max_bytes=100_000)

            self.assertEqual(result.status, "extracted")
            self.assertEqual(result.extractor, "ooxml:xlsx")
            self.assertEqual(
                result.content,
                "Sheet 1\nA1: Shared value\nB1: Inline value\nC1: 42",
            )

    def test_rejects_ooxml_when_relevant_xml_expands_past_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "compressed.docx"
            xml = (
                '<w:document xmlns:w="http://schemas.openxmlformats.org/'
                'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'
                + ("A" * 20_000)
                + "</w:t></w:r></w:p></w:body></w:document>"
            )
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("word/document.xml", xml)
            self.assertLess(path.stat().st_size, 2_000)

            result = extract_content(path, max_bytes=2_000)

            self.assertEqual(result.status, "too_large")
            self.assertEqual(result.content, "")
            self.assertIn("decompressed", result.error)


if __name__ == "__main__":
    unittest.main()
