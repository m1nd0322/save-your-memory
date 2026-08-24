from __future__ import annotations

import sys
from pathlib import Path


OUTPUT_TOO_LARGE = 3


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: pdf_worker.py INPUT OUTPUT MAX_BYTES", file=sys.stderr)
        return 2
    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    max_bytes = int(sys.argv[3])
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf
        except ImportError:
            print("PyMuPDF is not installed", file=sys.stderr)
            return 2

    document = pymupdf.open(input_path)
    total_bytes = 0
    try:
        with output_path.open("wb") as output:
            for page_number, page in enumerate(document):
                text = page.get_text("text", sort=True)
                prefix = "" if page_number == 0 else "\n\f\n"
                encoded = (prefix + text).encode("utf-8")
                total_bytes += len(encoded)
                if total_bytes > max_bytes:
                    print(
                        f"PDF extracted text exceeds configured limit of {max_bytes} bytes",
                        file=sys.stderr,
                    )
                    return OUTPUT_TOO_LARGE
                output.write(encoded)
    finally:
        document.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
