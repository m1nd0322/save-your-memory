import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from save_your_memory.extractors import _run_supervised, extract_content


def _write_test_pptx(path: Path, text: str) -> None:
    slide = f"""<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
      xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
      <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>
    </p:sld>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ppt/slides/slide1.xml", slide)


class ExtractorTests(unittest.TestCase):
    def test_supervised_converter_replaces_non_utf8_process_output(self) -> None:
        completed = _run_supervised(
            [
                sys.executable,
                "-c",
                "import os; os.write(1, b'\\xff'); raise SystemExit(7)",
            ],
            timeout=10,
        )

        self.assertEqual(completed.returncode, 7)
        self.assertIsInstance(completed.stdout, str)
        self.assertIn("\ufffd", completed.stdout)

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

    def test_extracts_legacy_ppt_via_libreoffice_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "legacy-route-map.ppt"
            path.write_bytes(b"legacy ppt payload")
            slide_template = """<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
              xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
              <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>
            </p:sld>"""

            def fake_run(command, **kwargs):
                if "--convert-to" in command:
                    outdir = Path(command[command.index("--outdir") + 1])
                    source_path = Path(command[-1])
                    converted = outdir / f"{source_path.stem}.pptx"
                    with zipfile.ZipFile(converted, "w") as archive:
                        archive.writestr(
                            "ppt/slides/slide1.xml",
                            slide_template.format(text="Legacy route map"),
                        )
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with patch(
                "save_your_memory.extractors.shutil.which",
                return_value="soffice",
            ), patch(
                "save_your_memory.extractors._run_supervised",
                side_effect=fake_run,
            ):
                result = extract_content(path, max_bytes=100_000)

            self.assertEqual(result.status, "extracted")
            self.assertEqual(result.extractor, "ppt:libreoffice->pptx")
            self.assertIn("Legacy route map", result.content)

    def test_discovers_standard_libreoffice_and_isolates_its_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            program_files = root / "Program Files"
            soffice = program_files / "LibreOffice/program/soffice.exe"
            soffice.parent.mkdir(parents=True)
            soffice.write_bytes(b"")
            source = root / "route-map.ppt"
            original = b"legacy ppt payload"
            source.write_bytes(original)
            commands: list[list[str]] = []

            def fake_run(command, **kwargs):
                commands.append(command)
                outdir = Path(command[command.index("--outdir") + 1])
                _write_test_pptx(outdir / "input.pptx", "Route map evidence")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with patch.dict(
                os.environ,
                {
                    "ProgramFiles": str(program_files),
                    "ProgramFiles(x86)": str(root / "Program Files (x86)"),
                },
            ), patch(
                "save_your_memory.extractors.shutil.which", return_value=None
            ), patch(
                "save_your_memory.extractors._run_supervised", side_effect=fake_run
            ):
                result = extract_content(source, max_bytes=100_000)

            self.assertEqual(result.status, "extracted")
            self.assertEqual(result.extractor, "ppt:libreoffice->pptx")
            self.assertEqual(commands[0][0], str(soffice))
            self.assertTrue(
                any(argument.startswith("-env:UserInstallation=") for argument in commands[0])
            )
            self.assertEqual(source.read_bytes(), original)

    def test_uses_readonly_powerpoint_fallback_when_libreoffice_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            program_files = root / "Program Files"
            power_point = program_files / "Microsoft Office/root/Office16/POWERPNT.EXE"
            power_point.parent.mkdir(parents=True)
            power_point.write_bytes(b"")
            source = root / "route-map.ppt"
            source.write_bytes(b"legacy ppt payload")
            captured: dict[str, object] = {}

            def fake_which(name: str):
                return "powershell.exe" if name in {"powershell", "powershell.exe"} else None

            def fake_run(command, **kwargs):
                captured["command"] = command
                captured["env"] = kwargs["env"]
                output = Path(kwargs["env"]["SAVE_YOUR_MEMORY_PPT_OUTPUT"])
                _write_test_pptx(output, "PowerPoint fallback evidence")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with patch.dict(
                os.environ,
                {
                    "ProgramFiles": str(program_files),
                    "ProgramFiles(x86)": str(root / "Program Files (x86)"),
                },
            ), patch(
                "save_your_memory.extractors.shutil.which", side_effect=fake_which
            ), patch(
                "save_your_memory.extractors._run_supervised", side_effect=fake_run
            ):
                result = extract_content(source, max_bytes=100_000)

            script = captured["command"][-1]
            self.assertEqual(result.status, "extracted")
            self.assertEqual(result.extractor, "ppt:powerpoint->pptx")
            self.assertIn("AutomationSecurity = 3", script)
            self.assertIn("Presentations.Open", script)
            self.assertIn("SaveCopyAs", script)

    def test_legacy_ppt_timeout_terminates_the_converter_process_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "route-map.ppt"
            source.write_bytes(b"legacy ppt payload")
            process = MagicMock()
            process.pid = 4242
            process.returncode = 1
            process.poll.return_value = None
            process.communicate.side_effect = [
                subprocess.TimeoutExpired(["soffice"], 120),
                ("", "terminated"),
            ]
            cleanup = subprocess.CompletedProcess(
                ["taskkill", "/PID", "4242", "/T", "/F"],
                1,
                stdout="",
                stderr="",
            )

            with patch(
                "save_your_memory.extractors.shutil.which", return_value="soffice"
            ), patch(
                "save_your_memory.extractors._find_powerpoint", return_value=None
            ), patch(
                "save_your_memory.extractors.subprocess.Popen", return_value=process
            ) as popen, patch(
                "save_your_memory.extractors.subprocess.run", return_value=cleanup
            ) as run:
                result = extract_content(source, max_bytes=100_000)

            self.assertEqual(result.status, "error")
            popen.assert_called()
            self.assertTrue(
                any(
                    call.args[0] == ["taskkill", "/PID", "4242", "/T", "/F"]
                    for call in run.call_args_list
                )
            )
            process.kill.assert_called()

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
