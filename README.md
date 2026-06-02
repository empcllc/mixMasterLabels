# mixMasterLabels

Command-line Python tool to generate labels from a **saved McMaster-Carr order webpage**.

For each order item, it generates one label file containing:
- McMaster part number
- Description
- QR code pointing to the product page
- Product image from McMaster (if available from the saved page)

Use `--qty` if you also want the quantity printed on each label.

## Install

```bash
python -m pip install -r requirements.txt
```

## Usage

```bash
python mixmaster_labels.py /path/to/saved-order.html /path/to/output-dir --format pdf --label-size 4x2in
python mixmaster_labels.py /path/to/saved-order.html /path/to/output-dir --format png --label-size 100x50mm --dpi 300 --qty
python mixmaster_labels.py /path/to/saved-order.html /path/to/output-dir --format png --order-info
python mixmaster_labels.py /path/to/saved-order.html /path/to/output-dir --format png --image-fill
```

### Arguments

- `input_html`: saved McMaster-Carr order webpage HTML file
- `output`: output directory, or a file path whose `.png`/`.pdf` extension selects the format
- `--format`: optional explicit format (`png` or `pdf`), otherwise inferred from extension
- `--label-size`: label dimensions (`WIDTHxHEIGHTin` or `WIDTHxHEIGHTmm`), default `4x2in` (4"x2")
- `--dpi`: rendered label DPI (default `300`)
- `--order-info`: include line number, qty, price, subtotal, PO, and order date
- `--image-fill`: zoom and center-crop the product image to fill the image box

Each label is written as its own file named `partnumber.png` or `partnumber.pdf`.

PDF and PNG output both create one file per order item.
