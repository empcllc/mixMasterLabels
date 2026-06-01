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
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import portrait
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

PART_PATTERN = re.compile(r"\b\d{3,}[A-Z]\d{1,}\b")
SIZE_PATTERN = re.compile(r"^(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(in|mm)$", re.IGNORECASE)
MCM_BASE = "https://www.mcmaster.com/"
_IMAGE_CACHE: dict[str, Image.Image | None] = {}


@dataclass
class OrderItem:
    part_number: str
    quantity: str
    description: str
    product_url: str
    image_url: str


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


def parse_order_items(html: str) -> List[OrderItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: List[OrderItem] = []

    for row in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in row.find_all(["th", "td"])]
        if not cells:
            continue

        joined = " ".join(cells)
        part = _find_first_part_token(joined)
        if not part:
            continue

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
        for cell in sorted(cells, key=len, reverse=True):
            if part in cell:
                continue
            if cell.strip().isdigit():
                continue
            description = cell
            break

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

        items.append(
            OrderItem(
                part_number=part,
                quantity=qty,
                description=description,
                product_url=product_url,
                image_url=image_url,
            )
        )

    if not items:
        raise ValueError(
            "No order lines found. Ensure the input file is a saved McMaster-Carr order webpage."
        )

    unique_items: List[OrderItem] = []
    seen = set()
    for item in items:
        key = (
            item.part_number,
            item.quantity,
            item.description,
            item.product_url,
            item.image_url,
        )
        if key not in seen:
            seen.add(key)
            unique_items.append(item)
    return unique_items


def _inches(value: float, unit: str) -> float:
    return value if unit == "in" else value / 25.4


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
        request = urllib.request.Request(
            image_url,
            headers={"User-Agent": "mixMasterLabels/1.0"},
        )
        with urllib.request.urlopen(request, timeout=5) as resp:
            data = resp.read()
        with Image.open(io.BytesIO(data)) as img:
            loaded = img.convert("RGB")
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


def _draw_label(image: Image.Image, item: OrderItem) -> None:
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, image.width - 1, image.height - 1), outline="black", width=2)

    title_font = ImageFont.load_default()
    body_font = ImageFont.load_default()

    margin = 12
    qr_size = max(56, min(120, image.height // 3))
    qr_x = image.width - margin - qr_size
    qr_y = margin

    qr_img = _qr_image(item.product_url, qr_size)
    image.paste(qr_img, (qr_x, qr_y))

    part_img_size = max(70, min(140, image.height // 2))
    part_img = _load_remote_image(item.image_url)
    part_x = image.width - margin - part_img_size
    part_y = qr_y + qr_size + 8
    if part_img:
        part_img.thumbnail((part_img_size, part_img_size))
        bg = Image.new("RGB", (part_img_size, part_img_size), "white")
        bg.paste(part_img, ((part_img_size - part_img.width) // 2, (part_img_size - part_img.height) // 2))
        image.paste(bg, (part_x, part_y))
        draw.rectangle((part_x, part_y, part_x + part_img_size, part_y + part_img_size), outline="black", width=1)
    else:
        draw.rectangle((part_x, part_y, part_x + part_img_size, part_y + part_img_size), outline="black", width=1)
        draw.text((part_x + 8, part_y + part_img_size // 2 - 6), "No image", font=body_font, fill="black")

    text_right = qr_x - 10
    y = margin
    draw.text((margin, y), f"Part: {item.part_number}", font=title_font, fill="black")
    y += 18
    draw.text((margin, y), f"Qty: {item.quantity}", font=title_font, fill="black")
    y += 22

    desc_lines = _wrap(draw, f"Description: {item.description or 'N/A'}", body_font, text_right - margin)
    for line in desc_lines[:8]:
        draw.text((margin, y), line, font=body_font, fill="black")
        y += 14

    url_lines = _wrap(draw, item.product_url, body_font, text_right - margin)
    if url_lines:
        draw.text((margin, image.height - 14), url_lines[0], font=body_font, fill="black")


def render_png(items: List[OrderItem], output: Path, width: float, height: float, unit: str, dpi: int) -> None:
    width_px = max(10, int(round(_inches(width, unit) * dpi)))
    height_px = max(10, int(round(_inches(height, unit) * dpi)))

    labels = []
    for item in items:
        label_img = Image.new("RGB", (width_px, height_px), "white")
        _draw_label(label_img, item)
        labels.append(label_img)

    canvas_img = Image.new("RGB", (width_px, height_px * len(labels)), "white")
    for idx, label in enumerate(labels):
        canvas_img.paste(label, (0, idx * height_px))

    canvas_img.save(output, format="PNG", dpi=(dpi, dpi))


def render_pdf(items: List[OrderItem], output: Path, width: float, height: float, unit: str, dpi: int) -> None:
    width_in = _inches(width, unit)
    height_in = _inches(height, unit)
    page_size = portrait((width_in * 72, height_in * 72))

    c = canvas.Canvas(str(output), pagesize=page_size)
    for item in items:
        temp = Image.new("RGB", (max(200, int(width_in * dpi)), max(120, int(height_in * dpi))), "white")
        _draw_label(temp, item)
        image_reader = ImageReader(temp)
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
    parser.add_argument("output", type=Path, help="Output file (.png or .pdf)")
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.input_html.exists():
        parser.error(f"Input file not found: {args.input_html}")

    fmt = infer_format(args.output, args.format)
    width, height, unit = args.label_size

    html = args.input_html.read_text(encoding="utf-8", errors="ignore")
    items = parse_order_items(html)

    args.output.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "png":
        render_png(items, args.output, width, height, unit, args.dpi)
    elif fmt == "pdf":
        render_pdf(items, args.output, width, height, unit, args.dpi)
    else:
        raise ValueError(f"Unsupported format: {fmt}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
