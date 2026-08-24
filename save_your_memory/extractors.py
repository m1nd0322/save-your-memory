from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree


TEXT_EXTENSIONS = {
    ".bat",
    ".c",
    ".cfg",
    ".cmd",
    ".conf",
    ".cpp",
    ".cs",
    ".css",
    ".csv",
    ".env",
    ".go",
    ".h",
    ".hpp",
    ".htm",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsonl",
    ".jsx",
    ".log",
    ".md",
    ".mdx",
    ".ps1",
    ".py",
    ".rb",
    ".rs",
    ".rst",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "cp949", "utf-16", "latin-1")
PP_SAVE_AS_OPEN_XML_PRESENTATION = 24


@dataclass(frozen=True)
class ExtractionResult:
    status: str
    content: str
    extractor: str
    error: str
    sha256: str


class ExtractionTooLarge(RuntimeError):
    pass


class ExtractionSecurityError(RuntimeError):
    pass


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _decode(raw: bytes) -> tuple[str, str]:
    for encoding in TEXT_ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("unknown", raw, 0, len(raw), "no supported encoding")


def _sniff_text(raw: bytes) -> tuple[str, str] | None:
    if not raw:
        return "", "utf-8-sig"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return raw.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            return None
    if b"\x00" in raw[:65_536]:
        return None
    try:
        decoded, encoding = _decode(raw)
    except UnicodeDecodeError:
        return None
    non_text = sum(
        1 for character in decoded if not character.isprintable() and character not in "\r\n\t\f\b"
    )
    if non_text / max(len(decoded), 1) > 0.01:
        return None
    return decoded, encoding


def _local_name(element: ElementTree.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _guard_zip_budget(
    archive: zipfile.ZipFile, names: list[str], max_bytes: int
) -> None:
    expanded_size = sum(archive.getinfo(name).file_size for name in names)
    if expanded_size > max_bytes:
        raise ExtractionTooLarge(
            f"OOXML decompressed content exceeds configured limit of {max_bytes} bytes"
        )


def _extract_docx(raw: bytes, max_bytes: int) -> str:
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        _guard_zip_budget(archive, ["word/document.xml"], max_bytes)
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    paragraphs: list[str] = []
    for paragraph in root.iter():
        if _local_name(paragraph) != "p":
            continue
        text = "".join(
            node.text or "" for node in paragraph.iter() if _local_name(node) == "t"
        ).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _numeric_suffix(name: str) -> int:
    match = re.search(r"(\d+)(?=\.xml$)", name)
    return int(match.group(1)) if match else 0


def _extract_pptx(raw: bytes, max_bytes: int) -> str:
    blocks: list[str] = []
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        slide_names = sorted(
            (
                name
                for name in archive.namelist()
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
            ),
            key=_numeric_suffix,
        )
        _guard_zip_budget(archive, slide_names, max_bytes)
        for name in slide_names:
            root = ElementTree.fromstring(archive.read(name))
            texts = [
                (node.text or "").strip()
                for node in root.iter()
                if _local_name(node) == "t" and (node.text or "").strip()
            ]
            number = _numeric_suffix(name)
            blocks.append("\n".join([f"Slide {number}", *texts]))
    return "\n\n".join(blocks)


def _extract_xlsx(raw: bytes, max_bytes: int) -> str:
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        shared_strings: list[str] = []
        archive_names = archive.namelist()
        shared_name = "xl/sharedStrings.xml"
        sheet_names = sorted(
            (
                name
                for name in archive_names
                if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
            ),
            key=_numeric_suffix,
        )
        relevant_names = ([shared_name] if shared_name in archive_names else []) + sheet_names
        _guard_zip_budget(archive, relevant_names, max_bytes)
        if shared_name in archive_names:
            shared_root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in shared_root.iter():
                if _local_name(item) == "si":
                    shared_strings.append(
                        "".join(
                            node.text or ""
                            for node in item.iter()
                            if _local_name(node) == "t"
                        )
                    )
        sheets: list[str] = []
        for sheet_name in sheet_names:
            root = ElementTree.fromstring(archive.read(sheet_name))
            lines = [f"Sheet {_numeric_suffix(sheet_name)}"]
            for cell in root.iter():
                if _local_name(cell) != "c":
                    continue
                reference = cell.attrib.get("r", "?")
                cell_type = cell.attrib.get("t", "")
                value = ""
                if cell_type == "inlineStr":
                    value = "".join(
                        node.text or ""
                        for node in cell.iter()
                        if _local_name(node) == "t"
                    )
                else:
                    value_node = next(
                        (node for node in cell if _local_name(node) == "v"), None
                    )
                    if value_node is not None:
                        value = value_node.text or ""
                    if cell_type == "s" and value:
                        index = int(value)
                        value = (
                            shared_strings[index]
                            if 0 <= index < len(shared_strings)
                            else value
                        )
                if value:
                    lines.append(f"{reference}: {value}")
            sheets.append("\n".join(lines))
    return "\n\n".join(sheets)


def _read_pdf_output(path: Path, max_bytes: int) -> bytes:
    if path.stat().st_size > max_bytes:
        raise ExtractionTooLarge(
            f"PDF extracted text exceeds configured limit of {max_bytes} bytes"
        )
    output = path.read_bytes()
    if len(output) > max_bytes:
        raise ExtractionTooLarge(
            f"PDF extracted text exceeds configured limit of {max_bytes} bytes"
        )
    return output


def _find_executable(*names: str) -> str | None:
    for name in names:
        executable = shutil.which(name)
        if executable is not None:
            return executable
    return None


def _program_files_roots() -> tuple[Path, ...]:
    roots: list[Path] = []
    for key in ("ProgramFiles", "ProgramFiles(x86)"):
        value = os.environ.get(key, "").strip()
        if value:
            candidate = Path(value)
            if candidate not in roots:
                roots.append(candidate)
    return tuple(roots)


def _find_libreoffice() -> str | None:
    executable = _find_executable("soffice", "soffice.com", "soffice.exe")
    if executable is not None:
        return executable
    for root in _program_files_roots():
        for name in ("soffice.com", "soffice.exe"):
            candidate = root / "LibreOffice" / "program" / name
            if candidate.is_file():
                return str(candidate)
    return None


def _find_powerpoint() -> str | None:
    executable = _find_executable("POWERPNT.EXE", "powerpnt.exe", "powerpnt")
    if executable is not None:
        return executable
    for root in _program_files_roots():
        for office_dir in ("Office16", "Office15", "Office14"):
            candidate = root / "Microsoft Office" / "root" / office_dir / "POWERPNT.EXE"
            if candidate.is_file():
                return str(candidate)
            legacy_candidate = root / "Microsoft Office" / office_dir / "POWERPNT.EXE"
            if legacy_candidate.is_file():
                return str(legacy_candidate)
    return None


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            if completed.returncode != 0 and process.poll() is None:
                process.kill()
        except (OSError, subprocess.SubprocessError):
            process.kill()
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        process.kill()


def _run_supervised(
    command: list[str],
    *,
    timeout: int,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        start_new_session=(os.name != "nt"),
        creationflags=(
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            if os.name == "nt"
            else 0
        ),
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
        raise subprocess.TimeoutExpired(
            command,
            timeout,
            output=stdout if stdout is not None else exc.output,
            stderr=stderr if stderr is not None else exc.stderr,
        ) from exc
    return subprocess.CompletedProcess(
        command,
        process.returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _completed_detail(completed: subprocess.CompletedProcess[str]) -> str:
    return ((completed.stderr or completed.stdout) or "").strip()


def _convert_ppt_with_libreoffice(input_path: Path, output_dir: Path) -> Path:
    executable = _find_libreoffice()
    if executable is None:
        raise FileNotFoundError("LibreOffice soffice was not found")
    profile = output_dir / "libreoffice-profile"
    profile.mkdir(exist_ok=True)
    completed = _run_supervised(
        [
            executable,
            f"-env:UserInstallation={profile.resolve().as_uri()}",
            "--headless",
            "--nologo",
            "--nofirststartwizard",
            "--convert-to",
            "pptx",
            "--outdir",
            str(output_dir),
            str(input_path),
        ],
        timeout=120,
    )
    if completed.returncode != 0:
        detail = _completed_detail(completed)
        raise RuntimeError(
            f"LibreOffice conversion failed for {input_path.name}: {detail}"
        )
    converted = output_dir / f"{input_path.stem}.pptx"
    if not converted.exists():
        raise RuntimeError(
            f"LibreOffice did not create the expected output file: {converted}"
        )
    return converted


def _convert_ppt_with_powerpoint(input_path: Path, output_dir: Path) -> Path:
    if os.name != "nt":
        raise FileNotFoundError("PowerPoint COM automation is only available on Windows")
    if _find_powerpoint() is None:
        raise FileNotFoundError("Microsoft PowerPoint was not found")
    powershell = _find_executable(
        "powershell.exe", "powershell", "pwsh.exe", "pwsh"
    )
    if powershell is None:
        raise FileNotFoundError("PowerShell was not found on PATH")
    output_path = output_dir / f"{input_path.stem}.pptx"
    script = "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            "$ppt = $null",
            "$presentation = $null",
            "$originalAutomationSecurity = $null",
            "try {",
            "  $ppt = New-Object -ComObject PowerPoint.Application",
            "  $originalAutomationSecurity = $ppt.AutomationSecurity",
            "  $ppt.AutomationSecurity = 3",
            "  $ppt.DisplayAlerts = 1",
            "  $presentation = $ppt.Presentations.Open($env:SAVE_YOUR_MEMORY_PPT_INPUT, -1, 0, 0)",
            f"  $presentation.SaveCopyAs($env:SAVE_YOUR_MEMORY_PPT_OUTPUT, {PP_SAVE_AS_OPEN_XML_PRESENTATION}, 0)",
            "} finally {",
            "  if ($presentation -ne $null) {",
            "    try { $presentation.Close() } catch {}",
            "    try { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($presentation) } catch {}",
            "  }",
            "  if ($ppt -ne $null) {",
            "    if ($originalAutomationSecurity -ne $null) {",
            "      try { $ppt.AutomationSecurity = $originalAutomationSecurity } catch {}",
            "    }",
            "    try { $ppt.Quit() } catch {}",
            "    try { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($ppt) } catch {}",
            "  }",
            "  [GC]::Collect()",
            "  [GC]::WaitForPendingFinalizers()",
            "}",
        ]
    )
    process_env = dict(os.environ)
    process_env.update(
        {
            "SAVE_YOUR_MEMORY_PPT_INPUT": str(input_path),
            "SAVE_YOUR_MEMORY_PPT_OUTPUT": str(output_path),
        }
    )
    completed = _run_supervised(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        env=process_env,
        timeout=120,
    )
    if completed.returncode != 0:
        detail = _completed_detail(completed)
        raise RuntimeError(f"PowerPoint conversion failed for {input_path.name}: {detail}")
    if not output_path.exists():
        raise RuntimeError(
            f"PowerPoint did not create the expected output file: {output_path}"
        )
    return output_path


def _convert_legacy_ppt(input_path: Path, output_dir: Path) -> tuple[Path, str]:
    attempts: list[tuple[str, Callable[[Path, Path], Path]]] = [
        ("libreoffice", _convert_ppt_with_libreoffice)
    ]
    if os.name == "nt":
        attempts.append(("powerpoint", _convert_ppt_with_powerpoint))

    errors: list[str] = []
    for label, converter in attempts:
        try:
            return converter(input_path, output_dir), label
        except FileNotFoundError as exc:
            errors.append(f"{label}: {exc}")
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            errors.append(f"{label}: {exc}")
    message = "; ".join(errors) if errors else "No supported .ppt converter was found"
    raise RuntimeError(message)


def _extract_ppt(raw: bytes, max_bytes: int) -> tuple[str, str]:
    with tempfile.TemporaryDirectory() as temp:
        temp_root = Path(temp)
        input_path = temp_root / "input.ppt"
        input_path.write_bytes(raw)
        converted_path, converter = _convert_legacy_ppt(input_path, temp_root)
        if converted_path.stat().st_size > max_bytes:
            raise ExtractionTooLarge(
                f"Converted PPTX exceeds configured limit of {max_bytes} bytes"
            )
        return (
            _extract_pptx(converted_path.read_bytes(), max_bytes),
            f"ppt:{converter}->pptx",
        )


def _extract_pdf(raw: bytes, max_bytes: int) -> tuple[str, str]:
    executable = shutil.which("pdftotext")
    with tempfile.TemporaryDirectory() as temp:
        temp_root = Path(temp)
        input_path = temp_root / "input.pdf"
        output_path = temp_root / "output.txt"
        input_path.write_bytes(raw)
        if executable is not None:
            completed = subprocess.run(
                [executable, str(input_path), str(output_path)],
                check=False,
                capture_output=True,
                timeout=120,
            )
            if completed.returncode != 0:
                detail = completed.stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"pdftotext failed: {detail}")
            text, encoding = _decode(_read_pdf_output(output_path, max_bytes))
            return text.strip(), f"pdftotext:{encoding}"

        worker_path = Path(__file__).with_name("pdf_worker.py")
        completed = subprocess.run(
            [
                sys.executable,
                str(worker_path),
                str(input_path),
                str(output_path),
                str(max_bytes),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        if completed.returncode == 3:
            raise ExtractionTooLarge(completed.stderr.strip())
        if completed.returncode != 0:
            raise RuntimeError(
                f"PyMuPDF extraction failed: {completed.stderr.strip()}"
            )
        text = _read_pdf_output(output_path, max_bytes).decode("utf-8")
        return text.strip(), "pymupdf"


def _is_reparse_point(file_stat: os.stat_result) -> bool:
    attributes = getattr(file_stat, "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(flag and attributes & flag)


def _same_file(before: os.stat_result, after: os.stat_result) -> bool:
    return (
        before.st_dev == after.st_dev
        and before.st_ino == after.st_ino
        and before.st_size == after.st_size
        and before.st_mtime_ns == after.st_mtime_ns
    )


def _read_stable_bytes(
    path: Path,
    max_bytes: int,
    expected_size: int | None,
    expected_mtime_ns: int | None,
) -> bytes:
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode) or _is_reparse_point(before):
        raise ExtractionSecurityError("Refusing to read a link or reparse point")
    if not stat.S_ISREG(before.st_mode):
        raise ExtractionSecurityError("Refusing to read a non-regular file")
    if expected_size is not None and before.st_size != expected_size:
        raise ExtractionSecurityError("File size changed after scanning")
    if expected_mtime_ns is not None and before.st_mtime_ns != expected_mtime_ns:
        raise ExtractionSecurityError("File modification time changed after scanning")
    if before.st_size > max_bytes:
        raise ExtractionTooLarge(
            f"File exceeds configured limit of {max_bytes} bytes"
        )

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not _same_file(before, opened):
            raise ExtractionSecurityError("File identity changed while opening")
        chunks: list[bytes] = []
        total = 0
        while total <= max_bytes:
            chunk = os.read(descriptor, min(1024 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        after = os.fstat(descriptor)
        if not _same_file(opened, after):
            raise ExtractionSecurityError("File changed while being read")
        if total > max_bytes:
            raise ExtractionTooLarge(
                f"File exceeds configured limit of {max_bytes} bytes"
            )
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def extract_content(
    path: Path,
    max_bytes: int,
    *,
    expected_size: int | None = None,
    expected_mtime_ns: int | None = None,
) -> ExtractionResult:

    suffix = path.suffix.casefold()
    try:
        raw = _read_stable_bytes(path, max_bytes, expected_size, expected_mtime_ns)
        digest = _sha256(raw)
        if suffix in TEXT_EXTENSIONS:
            content, encoding = _decode(raw)
            return ExtractionResult(
                "extracted", content, f"text:{encoding}", "", digest
            )
        if suffix == ".docx":
            content = _extract_docx(raw, max_bytes)
            return ExtractionResult("extracted", content, "ooxml:docx", "", digest)
        if suffix == ".pptx":
            content = _extract_pptx(raw, max_bytes)
            return ExtractionResult("extracted", content, "ooxml:pptx", "", digest)
        if suffix == ".ppt":
            content, extractor = _extract_ppt(raw, max_bytes)
            return ExtractionResult("extracted", content, extractor, "", digest)
        if suffix == ".xlsx":
            content = _extract_xlsx(raw, max_bytes)
            return ExtractionResult("extracted", content, "ooxml:xlsx", "", digest)
        if suffix == ".pdf":
            content, extractor = _extract_pdf(raw, max_bytes)
            return ExtractionResult("extracted", content, extractor, "", digest)
        sniffed = _sniff_text(raw)
        if sniffed is not None:
            content, encoding = sniffed
            return ExtractionResult(
                "extracted", content, f"text-sniffed:{encoding}", "", digest
            )
        return ExtractionResult(
            "unsupported",
            "",
            "",
            f"Unsupported file type: {suffix or '(no extension)'}",
            "",
        )
    except ExtractionTooLarge as exc:
        return ExtractionResult("too_large", "", "", str(exc), "")
    except (
        OSError,
        RuntimeError,
        ExtractionSecurityError,
        subprocess.SubprocessError,
        zipfile.BadZipFile,
    ) as exc:
        return ExtractionResult("error", "", "", str(exc), "")
    except (ElementTree.ParseError, KeyError, ValueError, UnicodeDecodeError) as exc:
        return ExtractionResult("error", "", "", f"Extraction failed: {exc}", "")
