from __future__ import annotations

import zlib
from pathlib import Path

from django.conf import settings
from django.utils import timezone

try:
    from PIL import Image
except ImportError:  # pragma: no cover - fallback keeps PDFs working without image backgrounds.
    Image = None


PDF_BACKGROUND_DIR = Path(settings.BASE_DIR) / "Dam_stores" / "static" / "stock_management" / "pdf_backgrounds"
FIRST_PAGE_BACKGROUND = PDF_BACKGROUND_DIR / "statement_first_page.png"
OTHER_PAGE_BACKGROUND = PDF_BACKGROUND_DIR / "statement_other_page.png"
IMAGE_CANDIDATE_SUFFIXES = (".png", ".jpg", ".jpeg")

# Manual layout controls. Increase these Y values to move content upward,
# decrease them to move content downward.
OWNER_INFO_Y = 680
TABLE_TOP_Y = 660
OTHER_PAGE_TABLE_TOP_Y = 790
TABLE_HEADER_HEIGHT = 20
TABLE_FIRST_ROW_GAP = 16
TABLE_BOTTOM_Y = 58
TIMESTAMP_X = 450
TIMESTAMP_Y = 785
TIMESTAMP_COLOR = (1, 1, 1)


def _background_path(path: Path) -> Path | None:
    if path.exists():
        return path
    for suffix in IMAGE_CANDIDATE_SUFFIXES:
        candidate = path.with_suffix(suffix)
        if candidate.exists():
            return candidate
    return None


def _load_pdf_image(path: Path | None) -> tuple[bytes, int, int] | None:
    if not path or Image is None:
        return None
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        return zlib.compress(rgb.tobytes()), rgb.width, rgb.height


def _pdf_escape(value) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    text = text.encode("latin-1", "replace").decode("latin-1")
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf_line(x: int, y: int, text, size: int = 9, bold: bool = False, color: tuple[float, float, float] = (0, 0, 0)) -> str:
    font = "F2" if bold else "F1"
    r, g, b = color
    return f"{r:.3f} {g:.3f} {b:.3f} rg BT /{font} {size} Tf {x} {y} Td ({_pdf_escape(text)}) Tj ET\n"


def _truncate_pdf_text(value, limit: int) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return f"{text[: max(limit - 3, 0)]}..."


def _pdf_stroke_color(color: tuple[float, float, float] = (0, 0, 0)) -> str:
    r, g, b = color
    return f"{r:.3f} {g:.3f} {b:.3f} RG\n"


def _draw_fallback_page_shell(width: int, height: int, page_number: int, total_pages: int) -> str:
    border_x = 28
    border_y = 28
    border_w = width - 56
    border_h = height - 56
    muted = (0.39, 0.43, 0.49)
    stream = _pdf_stroke_color()
    stream += f"1 w {border_x} {border_y} {border_w} {border_h} re S\n"
    stream += _pdf_line(TIMESTAMP_X, TIMESTAMP_Y, f"Generated: {timezone.localtime().strftime('%d %b %Y %I:%M %p')}", 8, True, TIMESTAMP_COLOR)
    stream += _pdf_line(478, 42, f"Page {page_number} of {total_pages}", 8, False, muted)
    return stream


def build_statement_pdf(
    owner_label: str,
    owner_name: str,
    remaining_label: str,
    remaining_value: str,
    columns: list[str],
    rows: list[list[str]],
    totals: tuple[str, str],
    amount_colors: tuple[tuple[float, float, float], tuple[float, float, float]],
) -> bytes:
    width = 595
    height = 842
    first_background = _load_pdf_image(_background_path(FIRST_PAGE_BACKGROUND))
    other_background = _load_pdf_image(_background_path(OTHER_PAGE_BACKGROUND))
    page_streams = []
    rows_with_total = [*rows, ["", "Total", totals[0], totals[1]]]
    row_height = 16
    first_page_rows = max(1, int((TABLE_TOP_Y - TABLE_HEADER_HEIGHT - TABLE_FIRST_ROW_GAP - TABLE_BOTTOM_Y) / row_height))
    other_page_rows = max(1, int((OTHER_PAGE_TABLE_TOP_Y - TABLE_HEADER_HEIGHT - TABLE_FIRST_ROW_GAP - TABLE_BOTTOM_Y) / row_height))
    row_pages = [rows_with_total[:first_page_rows]]
    remaining_rows = rows_with_total[first_page_rows:]
    while remaining_rows:
        row_pages.append(remaining_rows[:other_page_rows])
        remaining_rows = remaining_rows[other_page_rows:]
    total_pages = len(row_pages)
    table_bottom = TABLE_BOTTOM_Y
    col_edges = [42, 190, 300, 420, 553]
    text_x = [48, 196, 306, 426]
    red = (0.961, 0.341, 0.251)
    muted = (0.39, 0.43, 0.49)

    for page_number, page_rows in enumerate(row_pages, start=1):
        table_top = TABLE_TOP_Y if page_number == 1 else OTHER_PAGE_TABLE_TOP_Y
        header_bottom = table_top - TABLE_HEADER_HEIGHT
        first_row_y = header_bottom - TABLE_FIRST_ROW_GAP
        background_name = "BG1" if page_number == 1 and first_background else "BG2" if page_number > 1 and other_background else None
        stream = ""
        if background_name:
            stream += f"q {width} 0 0 {height} 0 0 cm /{background_name} Do Q\n"
        else:
            stream += _draw_fallback_page_shell(width, height, page_number, total_pages)
        if page_number == 1:
            stream += _pdf_line(42, OWNER_INFO_Y, f"{owner_label}: {_truncate_pdf_text(owner_name, 32)}", 9, True)
            stream += _pdf_line(335, OWNER_INFO_Y, f"{remaining_label}: {remaining_value}", 9, True, red)
        if background_name:
            stream += _pdf_line(TIMESTAMP_X, TIMESTAMP_Y, f"Generated: {timezone.localtime().strftime('%d %b %Y %I:%M %p')}", 8, True, TIMESTAMP_COLOR)
            stream += _pdf_line(478, 42, f"Page {page_number} of {total_pages}", 8, False, muted)

        stream += _pdf_stroke_color((0.12, 0.16, 0.22))
        stream += f"0.8 w {col_edges[0]} {table_top} m {col_edges[-1]} {table_top} l S\n"
        stream += f"0.8 w {col_edges[0]} {header_bottom} m {col_edges[-1]} {header_bottom} l S\n"
        stream += f"0.8 w {col_edges[0]} {table_bottom} m {col_edges[-1]} {table_bottom} l S\n"
        for edge in col_edges:
            stream += f"0.8 w {edge} {table_bottom} m {edge} {table_top} l S\n"
        for index, label in enumerate(columns):
            stream += _pdf_line(text_x[index], table_top - 13, label, 8, True)

        y = first_row_y
        for row in page_rows:
            is_total = row[1] == "Total"
            if is_total:
                stream += _pdf_stroke_color((0.12, 0.16, 0.22))
                stream += f"0.8 w {col_edges[0]} {y + 10} m {col_edges[-1]} {y + 10} l S\n"
            stream += _pdf_line(text_x[0], y, _truncate_pdf_text(row[0], 26), 7, is_total)
            stream += _pdf_line(text_x[1], y, _truncate_pdf_text(row[1], 18), 7, is_total, red if row[1] and row[1] != "Total" else (0, 0, 0))
            stream += _pdf_line(text_x[2], y, _truncate_pdf_text(row[2], 18), 7, is_total, amount_colors[0])
            stream += _pdf_line(text_x[3], y, _truncate_pdf_text(row[3], 18), 7, is_total, amount_colors[1])
            if not is_total:
                stream += _pdf_stroke_color((0.82, 0.85, 0.89))
                stream += f"0.25 w {col_edges[0]} {y - 5} m {col_edges[-1]} {y - 5} l S\n"
            y -= row_height

        if not rows and page_number == 1:
            stream += _pdf_line(48, 672, "No entries found for this report.", 9, False, muted)

        page_streams.append((stream.encode("latin-1", "replace"), background_name))

    font_regular = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    font_bold = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>"
    objects = [
        (1, b"<< /Type /Catalog /Pages 2 0 R >>"),
        (3, font_regular),
        (4, font_bold),
    ]

    image_objects = {}
    next_obj_id = 5
    for name, image_data in (("BG1", first_background), ("BG2", other_background)):
        if not image_data:
            continue
        payload, image_width, image_height = image_data
        image_objects[name] = next_obj_id
        objects.append(
            (
                next_obj_id,
                (
                    f"<< /Type /XObject /Subtype /Image /Width {image_width} /Height {image_height} "
                    f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode /Length {len(payload)} >>\nstream\n"
                ).encode("ascii")
                + payload
                + b"\nendstream",
            )
        )
        next_obj_id += 1

    page_objects = []
    for stream, background_name in page_streams:
        page_obj = next_obj_id
        content_obj = page_obj + 1
        xobjects = ""
        if background_name and background_name in image_objects:
            xobjects = f"/XObject << /{background_name} {image_objects[background_name]} 0 R >>"
        resources = f"<< /Font << /F1 3 0 R /F2 4 0 R >> {xobjects} >>"
        page_objects.append(
            (
                page_obj,
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] /Resources {resources} /Contents {content_obj} 0 R >>".encode("ascii"),
            )
        )
        page_objects.append((content_obj, b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream"))
        next_obj_id += 2

    kids = " ".join(f"{obj_id} 0 R" for obj_id, _payload in page_objects if obj_id % 2 == next_obj_id % 2)
    # Page object ids are every other object in insertion order; build kids from that list directly.
    kids = " ".join(f"{obj_id} 0 R" for obj_id, _payload in page_objects[::2])
    objects.insert(1, (2, f"<< /Type /Pages /Kids [{kids}] /Count {len(page_streams)} >>".encode("ascii")))
    objects.extend(page_objects)

    pdf = b"%PDF-1.4\n"
    offsets = []
    for obj_id, payload in objects:
        offsets.append(len(pdf))
        pdf += f"{obj_id} 0 obj\n".encode("ascii") + payload + b"\nendobj\n"

    xref_offset = len(pdf)
    pdf += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    pdf += b"0000000000 65535 f \n"
    for offset in offsets:
        pdf += f"{offset:010d} 00000 n \n".encode("ascii")
    pdf += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    return pdf
