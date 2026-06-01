import tempfile
import unittest
from pathlib import Path

from mixmaster_labels import infer_format, main, parse_label_size, parse_order_items


SAMPLE_HTML = """
<html><body>
<table>
  <tr><th>Qty</th><th>Part</th><th>Description</th></tr>
  <tr><td>3</td><td><a href="/91251A148">91251A148</a></td><td>18-8 Stainless Steel Hex Nut</td><td><img src="/images/91251A148p1s.png" /></td></tr>
  <tr><td>1</td><td><a href="https://www.mcmaster.com/92141A033">92141A033</a></td><td>Alloy Steel Socket Head Screw</td><td><img src="https://www.mcmaster.com/images/92141A033p1s.png" /></td></tr>
</table>
</body></html>
"""


SAMPLE_HTML_NO_IMAGES = """
<html><body>
<table>
  <tr><th>Qty</th><th>Part</th><th>Description</th></tr>
  <tr><td>3</td><td><a href="/91251A148">91251A148</a></td><td>18-8 Stainless Steel Hex Nut</td></tr>
  <tr><td>1</td><td><a href="https://www.mcmaster.com/92141A033">92141A033</a></td><td>Alloy Steel Socket Head Screw</td></tr>
</table>
</body></html>
"""


class MixMasterLabelsTests(unittest.TestCase):
    def test_parse_label_size(self):
        self.assertEqual(parse_label_size("4x2in"), (4.0, 2.0, "in"))
        self.assertEqual(parse_label_size("100x50mm"), (100.0, 50.0, "mm"))

    def test_parse_order_items(self):
        items = parse_order_items(SAMPLE_HTML)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].part_number, "91251A148")
        self.assertEqual(items[0].quantity, "3")
        self.assertEqual(items[0].product_url, "https://www.mcmaster.com/91251A148")
        self.assertEqual(items[0].image_url, "https://www.mcmaster.com/images/91251A148p1s.png")

    def test_infer_format_from_extension(self):
        self.assertEqual(infer_format(Path("labels.png"), None), "png")
        self.assertEqual(infer_format(Path("labels.pdf"), None), "pdf")

    def test_main_generates_png_and_pdf(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            html_path = td_path / "order.html"
            html_path.write_text(SAMPLE_HTML_NO_IMAGES, encoding="utf-8")

            png_path = td_path / "labels.png"
            pdf_path = td_path / "labels.pdf"

            self.assertEqual(main([str(html_path), str(png_path), "--label-size", "4x2in", "--dpi", "96"]), 0)
            self.assertTrue(png_path.exists())
            self.assertGreater(png_path.stat().st_size, 0)

            self.assertEqual(main([str(html_path), str(pdf_path), "--label-size", "100x50mm", "--dpi", "96"]), 0)
            self.assertTrue(pdf_path.exists())
            self.assertGreater(pdf_path.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
