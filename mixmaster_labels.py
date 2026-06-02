#!/usr/bin/env python3
import argparse
import io
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List
from urllib.parse import urljoin, urlparse

import qrcode
from bs4 import BeautifulSoup
from PIL import Image, ImageChops, ImageDraw, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

PART_PATTERN = re.compile(r"\b\d{3,}[A-Z]\d{1,}\b")
SIZE_PATTERN = re.compile(r"^(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(in|mm)$", re.IGNORECASE)
MCM_BASE = "https://www.mcmaster.com/"
_IMAGE_CACHE: dict[str, Image.Image | None] = {}
_FONT_CANDIDATES = [
    Path("C:/Windows/Fonts/arialbd.ttf"),
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("C:/Windows/Fonts/calibrib.ttf"),
    Path("C:/Windows/Fonts/calibri.ttf"),
]

# Code39 narrow/wide patterns (9 elements: bar/space alternating, starting with bar).
_CODE39_PATTERNS = {
    "0": "nnnwwnwnn",
    "1": "wnnwnnnnw",
    "2": "nnwwnnnnw",
    "3": "wnwwnnnnn",
    "4": "nnnwwnnnw",
    "5": "wnnwwnnnn",
    "6": "nnwwwnnnn",
    "7": "nnnwnnwnw",
    "8": "wnnwnnwnn",
    "9": "nnwwnnwnn",
    "A": "wnnnnwnnw",
    "B": "nnwnnwnnw",
    "C": "wnwnnwnnn",
    "D": "nnnnwwnnw",
    "E": "wnnnwwnnn",
    "F": "nnwnwwnnn",
    "G": "nnnnnwwnw",
    "H": "wnnnnwwnn",
    "I": "nnwnnwwnn",
    "J": "nnnnwwwnn",
    "K": "wnnnnnnww",
    "L": "nnwnnnnww",
    "M": "wnwnnnnwn",
    "N": "nnnnwnnww",
    "O": "wnnnwnnwn",
    "P": "nnwnwnnwn",
    "Q": "nnnnnnwww",
    "R": "wnnnnnwwn",
    "S": "nnwnnnwwn",
    "T": "nnnnwnwwn",
    "U": "wwnnnnnnw",
    "V": "nwwnnnnnw",
    "W": "wwwnnnnnn",
    "X": "nwnnwnnnw",
    "Y": "wwnnwnnnn",
    "Z": "nwwnwnnnn",
    "-": "nwnnnnwnw",
    ".": "wwnnnnwnn",
    " ": "nwwnnnwnn",
    "$": "nwnwnwnnn",
    "/": "nwnwnnnwn",
    "+": "nwnnnwnwn",
    "%": "nnnwnwnwn",
    "*": "nwnnwnwnn",
}


@dataclass
class OrderItem:
    part_number: str
    quantity: str
    description: str
    product_url: str
    image_url: str
    line_number: str = ""
    quantity_unit: str = ""
    price_each: str = ""
    subtotal: str = ""
    po_number: str = ""
    order_date: str = ""


def parse_label_size(value: str) -> tuple[float, float, str]:
    match = SIZE_PATTERN.match(value.strip())
    if not match:
        raise argparse.ArgumentTypeError(
            "Label size must be WIDTHxHEIGHTin or WIDTHxHEIGHTmm (example: 4x2in)"
        )
    w, h, unit = match.groups()
    return float(w), float(h), unit.lower()


def _find_first_part_token(text: str) -> str | None:
    match = PART_PATTERN.search(text)
    return match.group(0) if match else None


def _normalize_mcm_url(url: str) -> str:
    if not url:
        return ""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("//"):
        return f"https:{url}"
    return urljoin(MCM_BASE, url)


def _is_mcmaster_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "mcmaster.com" or host == "www.mcmaster.com" or host.endswith(".mcmaster.com")


def _row_text_from_html_row(row) -> list[str]:
    return [c.get_text(" ", strip=True) for c in row.find_all(["th", "td"])]


def _parse_table_row(row) -> OrderItem | None:
    cells = _row_text_from_html_row(row)
    if not cells:
        return None

    joined = " ".join(cells)
    part = _find_first_part_token(joined)
    if not part:
        return None

    qty = "1"
    for cell in cells:
        stripped = cell.strip()
        if stripped.isdigit():
            qty = stripped
            break
        qty_match = re.search(r"\bQty\b\s*:?\s*(\d+)", stripped, flags=re.IGNORECASE)
        if qty_match:
            qty = qty_match.group(1)
            break

    description = ""
    for cell in cells:
        stripped = cell.strip()
        if not stripped or part in stripped or stripped.isdigit():
            continue
        if re.search(r"\b(Quantity|Each|Add to Order|Qty)\b", stripped, flags=re.IGNORECASE):
            continue
        if len(stripped) > len(description):
            description = stripped

    product_url = ""
    for link in row.find_all("a", href=True):
        href = _normalize_mcm_url(link["href"].strip())
        if _is_mcmaster_url(href) or part in link.get_text(" ", strip=True):
            product_url = href
            break
    if not product_url:
        product_url = _normalize_mcm_url(part)

    image_url = ""
    img = row.find("img", src=True)
    if img:
        image_url = _normalize_mcm_url(img["src"].strip())

    return OrderItem(
        part_number=part,
        quantity=qty,
        description=description,
        product_url=product_url,
        image_url=image_url,
    )


def _extract_order_meta(soup: BeautifulSoup) -> tuple[str, str]:
    po_number = ""
    po_node = soup.select_one("input.order-dtl-po[value]")
    if po_node and po_node.has_attr("value"):
        po_number = po_node["value"].strip()

    order_date = ""
    date_node = soup.select_one(".order-dtl-date")
    if date_node:
        order_date = date_node.get_text(" ", strip=True)

    return po_number, order_date


def _parse_detail_row(row, source_path: Path | None) -> OrderItem | None:
    part = ""
    specs = row.select_one(".dtl-row-specs")
    if specs:
        part = _find_first_part_token(specs.get_text(" ", strip=True)) or ""
    if not part:
        part = _find_first_part_token(row.get_text(" ", strip=True)) or ""
    if not part:
        return None

    qty_node = row.select_one(".dtl-row-quantity")
    qty = qty_node.get_text(" ", strip=True) if qty_node else "1"
    line_node = row.select_one(".dtl-row-nbr")
    line_number = line_node.get_text(" ", strip=True) if line_node else ""

    qty_unit_node = row.select_one(".dtl-row-qu .dtl-row-unit")
    quantity_unit = qty_unit_node.get_text(" ", strip=True) if qty_unit_node else ""

    price_each_node = row.select_one(".dtl-row-ppu .dtl-row-priceper")
    price_each = price_each_node.get_text(" ", strip=True) if price_each_node else ""

    subtotal_node = row.select_one(".dtl-row-pricetot")
    subtotal = subtotal_node.get_text(" ", strip=True) if subtotal_node else ""

    desc_node = row.select_one(".dtl-row-copy p")
    description = desc_node.get_text(" ", strip=True) if desc_node else ""

    product_url = ""
    link = row.select_one("a.title-dtl-link[href], a.dtl-row-lnk[href]")
    if link and link.has_attr("href"):
        product_url = _normalize_mcm_url(link["href"].strip())
    if not product_url:
        product_url = _normalize_mcm_url(part)

    image_url = ""
    img = row.select_one("img[src]")
    if img:
        src = img["src"].strip()
        if source_path and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", src):
            image_path = (source_path.parent / src).resolve()
            image_url = str(image_path)
        else:
            image_url = _normalize_mcm_url(src)

    return OrderItem(
        part_number=part,
        quantity=qty,
        description=description,
        product_url=product_url,
        image_url=image_url,
        line_number=line_number,
        quantity_unit=quantity_unit,
        price_each=price_each,
        subtotal=subtotal,
    )


def parse_order_items(html: str, source_path: Path | None = None) -> List[OrderItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: List[OrderItem] = []
    po_number, order_date = _extract_order_meta(soup)

    for row in soup.select("div.dtl-row"):
        item = _parse_detail_row(row, source_path)
        if item:
            items.append(item)

    if not items:
        for row in soup.find_all("tr"):
            item = _parse_table_row(row)
            if item:
                items.append(item)

    if not items:
        raise ValueError(
            "No order lines found. Ensure the input file is a saved McMaster-Carr order webpage."
        )

    unique_items: List[OrderItem] = []
    seen = set()
    for item in items:
        item.po_number = po_number
        item.order_date = order_date
        key = (
            item.part_number,
            item.quantity,
            item.description,
            item.product_url,
            item.image_url,
            item.line_number,
            item.quantity_unit,
            item.price_each,
            item.subtotal,
        )
        if key not in seen:
            seen.add(key)
            unique_items.append(item)
    return unique_items


def _inches(value: float, unit: str) -> float:
    return value if unit == "in" else value / 25.4


def _load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = _FONT_CANDIDATES[:2] if bold else _FONT_CANDIDATES[1:]
    for font_path in candidates:
        if font_path.exists():
            try:
                return ImageFont.truetype(str(font_path), size=size)
            except OSError:
                continue
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", size=size)
    except OSError:
        return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> Iterable[str]:
    words = text.split()
    if not words:
        return []
    lines = []
    line = words[0]
    for word in words[1:]:
        candidate = f"{line} {word}"
        if draw.textlength(candidate, font=font) <= max_width:
            line = candidate
        else:
            lines.append(line)
            line = word
    lines.append(line)
    return lines


def _load_remote_image(image_url: str) -> Image.Image | None:
    if not image_url:
        return None
    if image_url in _IMAGE_CACHE:
        return _IMAGE_CACHE[image_url].copy() if _IMAGE_CACHE[image_url] else None

    try:
        image_path = Path(image_url)
        if image_path.exists():
            with Image.open(image_path) as img:
                loaded = img.convert("RGBA")
            _IMAGE_CACHE[image_url] = loaded
            return loaded.copy()

        request = urllib.request.Request(
            image_url,
            headers={"User-Agent": "mixMasterLabels/1.0"},
        )
        with urllib.request.urlopen(request, timeout=5) as resp:
            data = resp.read()
        with Image.open(io.BytesIO(data)) as img:
            loaded = img.convert("RGBA")
        _IMAGE_CACHE[image_url] = loaded
        return loaded.copy()
    except (urllib.error.URLError, ValueError, OSError):
        _IMAGE_CACHE[image_url] = None
        return None


def _qr_image(url: str, size: int) -> Image.Image:
    qr = qrcode.QRCode(border=1, box_size=3)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    return img.resize((size, size))


def _trim_image_margins(img: Image.Image) -> Image.Image:
    # Remove transparent and background-color padding so product art fills the frame.
    rgba = img.convert("RGBA")
    alpha_bbox = rgba.getchannel("A").getbbox()
    if not alpha_bbox:
        return img

    content_rgba = rgba.crop(alpha_bbox)
    # Composite onto white so transparent regions don't turn black in output.
    white_rgba = Image.new("RGBA", content_rgba.size, (255, 255, 255, 255))
    content = Image.alpha_composite(white_rgba, content_rgba).convert("RGB")

    # First pass: trim using the average corner color as background.
    corners = [
        content.getpixel((0, 0)),
        content.getpixel((max(0, content.width - 1), 0)),
        content.getpixel((0, max(0, content.height - 1))),
        content.getpixel((max(0, content.width - 1), max(0, content.height - 1))),
    ]
    bg_color = tuple(sum(px[i] for px in corners) // len(corners) for i in range(3))
    bg = Image.new("RGB", content.size, bg_color)
    diff = ImageChops.difference(content, bg)
    # Increase sensitivity so near-bg compression artifacts still trim correctly.
    diff = ImageChops.add(diff, diff, 2.0, -12)
    bbox = diff.getbbox()
    if bbox:
        content = content.crop(bbox)

    # Second pass fallback: trim near-white if present.
    white_bg = Image.new("RGB", content.size, "white")
    white_diff = ImageChops.difference(content, white_bg)
    white_diff = ImageChops.add(white_diff, white_diff, 2.0, -20)
    white_bbox = white_diff.getbbox()
    if white_bbox:
        content = content.crop(white_bbox)

    return content


def _draw_code39(draw: ImageDraw.ImageDraw, value: str, x: int, y: int, width: int, height: int) -> None:
    encoded = "*" + value.upper() + "*"
    if any(ch not in _CODE39_PATTERNS for ch in encoded):
        return

    # 3 wide + 6 narrow modules per symbol, plus one narrow inter-character gap.
    total_modules = len(encoded) * 15 - 1
    narrow = max(1, width // total_modules)
    wide = narrow * 3
    total_px = len(encoded) * (6 * narrow + 3 * wide + narrow) - narrow
    cursor = x + max(0, (width - total_px) // 2)

    for idx, ch in enumerate(encoded):
        pattern = _CODE39_PATTERNS[ch]
        for i, token in enumerate(pattern):
            span = wide if token == "w" else narrow
            if i % 2 == 0:
                draw.rectangle((cursor, y, cursor + span - 1, y + height), fill="black")
            cursor += span
        if idx != len(encoded) - 1:
            cursor += narrow


def _draw_label(
    image: Image.Image,
    item: OrderItem,
    include_qty: bool,
    include_order_info: bool,
    image_fill: bool = False,
) -> None:
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, image.width - 1, image.height - 1), outline="black", width=2)

    title_font = _load_font(max(24, min(40, image.height // 11)), bold=True)
    body_font = _load_font(max(16, min(28, image.height // 18)))
    meta_font = _load_font(max(14, min(22, image.height // 24)))

    margin = max(10, image.height // 50)
    gap = max(8, image.height // 70)

    left_w = max(180, int(image.width * 0.34))
    left_x = margin
    left_inner_w = left_w - margin

    max_image_h = max(48, image.height // 3)
    img_x = left_x
    img_y = margin

    part_img = _load_remote_image(item.image_url)
    display_img = _trim_image_margins(part_img) if part_img else None
    image_h = max_image_h
    if display_img and display_img.height > 0:
        aspect = display_img.width / display_img.height
        aspect_h = int(left_inner_w / max(aspect, 0.01))
        image_h = max(32, min(max_image_h, aspect_h))
        if image_fill:
            image_h = max_image_h

    draw.rectangle((img_x, img_y, img_x + left_inner_w, img_y + image_h), outline="black", width=1)
    if display_img:
        target_w = max(1, left_inner_w - 4)
        target_h = max(1, image_h - 4)
        if image_fill:
            src_w, src_h = display_img.size
            if src_w > 0 and src_h > 0:
                cover_scale = max(target_w / src_w, target_h / src_h)
                contain_scale = min(target_w / src_w, target_h / src_h)
                # Keep fill mode less aggressive so long parts don't get heavily cropped.
                scale = min(cover_scale, contain_scale * 1.12)
                resized_w = max(1, int(round(src_w * scale)))
                resized_h = max(1, int(round(src_h * scale)))
                resized = display_img.resize((resized_w, resized_h), Image.Resampling.LANCZOS)
                # Bias crop toward the right side, which often contains key distinguishing features.
                overflow_w = max(0, resized_w - target_w)
                left = int(round(overflow_w * 0.95))
                top = max(0, (resized_h - target_h) // 2)
                fitted = resized.crop((left, top, left + target_w, top + target_h))
            else:
                fitted = display_img.copy()
                fitted.thumbnail((target_w, target_h))
        else:
            fitted = display_img.copy()
            fitted.thumbnail((target_w, target_h))

        paste_x = img_x + (left_inner_w - fitted.width) // 2
        paste_y = img_y + (image_h - fitted.height) // 2
        image.paste(fitted, (paste_x, paste_y))
    else:
        draw.text((img_x + 8, img_y + image_h // 2 - 8), "No image", font=meta_font, fill="black")

    qr_y = img_y + image_h + gap
    qr_size = max(80, min(left_inner_w, image.height - qr_y - margin))
    qr_x = left_x + max(0, (left_inner_w - qr_size) // 2)
    qr_img = _qr_image(item.product_url, qr_size)
    image.paste(qr_img, (qr_x, qr_y))
    draw.rectangle((qr_x, qr_y, qr_x + qr_size, qr_y + qr_size), outline="black", width=1)

    right_x = left_x + left_w + gap
    right_w = image.width - right_x - margin
    y = margin

    draw.text((right_x, y), f"McMaster Part: {item.part_number}", font=title_font, fill="black")
    y += max(34, image.height // 8)

    barcode_h = max(42, image.height // 7)
    _draw_code39(draw, item.part_number, right_x, y, right_w, barcode_h)
    y += barcode_h + gap

    if include_qty:
        qty_value = item.quantity if not item.quantity_unit else f"{item.quantity} {item.quantity_unit}"
        draw.text((right_x, y), f"Qty: {qty_value}", font=body_font, fill="black")
        y += max(22, image.height // 14)

    desc_lines = _wrap(draw, f"Description: {item.description or 'N/A'}", body_font, right_w)
    for line in desc_lines[:6]:
        draw.text((right_x, y), line, font=body_font, fill="black")
        y += max(20, image.height // 16)

    if include_order_info:
        details = [
            f"PO: {item.po_number or 'N/A'}",
            f"Order Date: {item.order_date or 'N/A'}",
            f"Line #: {item.line_number or 'N/A'}",
            (
                f"Qty: {(item.quantity + (' ' + item.quantity_unit if item.quantity_unit else '')).strip() or 'N/A'}"
                f"\tPrice: {item.price_each or 'N/A'}"
                f"\tSubtotal: {item.subtotal or 'N/A'}"
            ),
        ]
        for detail in details:
            if y >= image.height - margin - 16:
                break
            draw.text((right_x, y), detail, font=meta_font, fill="black")
            y += max(16, image.height // 22)


def _resolve_output_dir(output: Path) -> Path:
    if output.suffix.lower() in {".png", ".pdf"}:
        return output.parent
    return output


def render_png(
    items: List[OrderItem],
    output: Path,
    width: float,
    height: float,
    unit: str,
    dpi: int,
    include_qty: bool,
    include_order_info: bool,
    image_fill: bool,
) -> None:
    width_px = max(10, int(round(_inches(width, unit) * dpi)))
    height_px = max(10, int(round(_inches(height, unit) * dpi)))
    output_dir = _resolve_output_dir(output)
    output_dir.mkdir(parents=True, exist_ok=True)

    for item in items:
        label_img = Image.new("RGB", (width_px, height_px), "white")
        _draw_label(label_img, item, include_qty, include_order_info, image_fill=image_fill)
        label_img.save(output_dir / f"{item.part_number}.png", format="PNG", dpi=(dpi, dpi))


def render_pdf(
    items: List[OrderItem],
    output: Path,
    width: float,
    height: float,
    unit: str,
    dpi: int,
    include_qty: bool,
    include_order_info: bool,
    image_fill: bool,
) -> None:
    width_in = _inches(width, unit)
    height_in = _inches(height, unit)
    page_size = (width_in * 72, height_in * 72)
    output_dir = _resolve_output_dir(output)
    output_dir.mkdir(parents=True, exist_ok=True)

    for item in items:
        temp = Image.new("RGB", (max(200, int(width_in * dpi)), max(120, int(height_in * dpi))), "white")
        _draw_label(temp, item, include_qty, include_order_info, image_fill=image_fill)
        image_reader = ImageReader(temp)
        item_output = output_dir / f"{item.part_number}.pdf"
        c = canvas.Canvas(str(item_output), pagesize=page_size)
        c.drawImage(image_reader, 0, 0, width=page_size[0], height=page_size[1])
        c.showPage()
        c.save()


def infer_format(output: Path, explicit: str | None) -> str:
    if explicit:
        return explicit.lower()
    suffix = output.suffix.lower()
    if suffix == ".png":
        return "png"
    if suffix == ".pdf":
        return "pdf"
    raise ValueError("Output format must be specified with --format or .png/.pdf file extension")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate one label per order item from a saved McMaster-Carr order webpage."
    )
    parser.add_argument("input_html", type=Path, help="Path to saved McMaster-Carr order HTML file")
    parser.add_argument("output", type=Path, help="Output directory or file path used to choose .png/.pdf output")
    parser.add_argument(
        "--format",
        choices=["png", "pdf"],
        help="Output format. If omitted, inferred from output extension.",
    )
    parser.add_argument(
        "--label-size",
        default="4x2in",
        type=parse_label_size,
        help="Label size as WIDTHxHEIGHTin or WIDTHxHEIGHTmm (default: 4x2in)",
    )
    parser.add_argument("--dpi", type=int, default=300, help="DPI for rendered labels (default: 300)")
    parser.add_argument("--qty", action="store_true", help="Include quantity on each label")
    parser.add_argument(
        "--order-info",
        action="store_true",
        help="Include line/qty/price/subtotal and PO/order-date fields on each label",
    )
    parser.add_argument(
        "--image-fill",
        action="store_true",
        help="Fill the part image box by zooming/cropping (useful for thin or wide parts)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.input_html.exists():
        parser.error(f"Input file not found: {args.input_html}")

    fmt = infer_format(args.output, args.format)
    width, height, unit = args.label_size
    output_dir = _resolve_output_dir(args.output)

    html = args.input_html.read_text(encoding="utf-8", errors="ignore")
    items = parse_order_items(html, args.input_html)

    output_dir.mkdir(parents=True, exist_ok=True)

    if fmt == "png":
        render_png(items, args.output, width, height, unit, args.dpi, args.qty, args.order_info, args.image_fill)
    elif fmt == "pdf":
        render_pdf(items, args.output, width, height, unit, args.dpi, args.qty, args.order_info, args.image_fill)
    else:
        raise ValueError(f"Unsupported format: {fmt}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
