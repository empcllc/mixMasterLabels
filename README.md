# mixMasterLabels

Command-line Python tool to generate labels from a **saved McMaster-Carr order webpage**.

For each order item, it generates one label containing:
- McMaster part number
- Description
- Quantity
- QR code pointing to the product page
- Product image from McMaster (if available from the saved page)

## Install

```bash
python -m pip install -r requirements.txt
```

## Usage

```bash
python mixmaster_labels.py /path/to/saved-order.html /path/to/labels.pdf --label-size 4x2in
python mixmaster_labels.py /path/to/saved-order.html /path/to/labels.png --label-size 100x50mm --dpi 300
```

### Arguments

- `input_html`: saved McMaster-Carr order webpage HTML file
- `output`: output file path (`.png` or `.pdf`)
- `--format`: optional explicit format (`png` or `pdf`), otherwise inferred from extension
- `--label-size`: label dimensions (`WIDTHxHEIGHTin` or `WIDTHxHEIGHTmm`)
- `--dpi`: rendered label DPI (default `300`)

PDF output creates one label per page. PNG output stacks labels vertically in a single image.
