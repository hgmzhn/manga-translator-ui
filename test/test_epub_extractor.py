import io
import os
import tempfile
import zipfile
import pytest

from desktop_qt_ui.utils.archive_extractor import extract_images_from_epub


def _create_sample_epub(include_text_page: bool = True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('mimetype', 'application/epub+zip')
        zf.writestr('META-INF/container.xml', '''<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
   <rootfiles>
      <rootfile full-path="item/standard.opf" media-type="application/oebps-package+xml"/>
   </rootfiles>
</container>''')

        dummy_png = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82'
        dummy_jpg = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9'

        # Intentionally write files into zip in reversed order
        zf.writestr('item/image/moe-017805.jpg', dummy_jpg)
        zf.writestr('item/xhtml/p2.xhtml', '''<html xmlns="http://www.w3.org/1999/xhtml"><body><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><image href="../image/moe-017805.jpg"/></svg></body></html>''')
        zf.writestr('item/image/moe-017804.jpg', dummy_png)
        zf.writestr('item/xhtml/p1.xhtml', '''<html xmlns="http://www.w3.org/1999/xhtml"><body><img src="../image/moe-017804.jpg"/></body></html>''')
        if include_text_page:
            zf.writestr('item/xhtml/p3.xhtml', '''<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Text only</p></body></html>''')

        spine_items = '<itemref idref="p1"/><itemref idref="p2"/>'
        manifest_items = '''
    <item id="p1" href="xhtml/p1.xhtml" media-type="application/xhtml+xml"/>
    <item id="p2" href="xhtml/p2.xhtml" media-type="application/xhtml+xml"/>
    <item id="img1" href="image/moe-017804.jpg" media-type="image/jpeg"/>
    <item id="img2" href="image/moe-017805.jpg" media-type="image/jpeg"/>'''
        if include_text_page:
            spine_items += '<itemref idref="p3"/>'
            manifest_items += '\n    <item id="p3" href="xhtml/p3.xhtml" media-type="application/xhtml+xml"/>'

        zf.writestr('item/standard.opf', f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="pub-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Test Manga</dc:title></metadata>
  <manifest>{manifest_items}
  </manifest>
  <spine>{spine_items}
  </spine>
</package>''')

    return buf.getvalue()


def test_extract_images_from_epub_spine_order_and_naming():
    epub_bytes = _create_sample_epub(include_text_page=True)
    with tempfile.TemporaryDirectory() as tmp_dir:
        epub_path = os.path.join(tmp_dir, 'sample.epub')
        with open(epub_path, 'wb') as f:
            f.write(epub_bytes)

        out_dir = os.path.join(tmp_dir, 'extracted')
        extracted = extract_images_from_epub(epub_path, out_dir)

        # There should be 3 pages extracted
        assert len(extracted) == 3

        # Check naming convention: page_0001, page_0002, page_0003
        filenames = [os.path.basename(p) for p in extracted]
        assert filenames[0].startswith("page_0001")
        assert filenames[1].startswith("page_0002")
        assert filenames[2].startswith("page_0003")

        # Check content order:
        # page 1 was moe-017804 (dummy_png)
        # page 2 was moe-017805 (dummy_jpg)
        assert os.path.getsize(extracted[0]) == 67
        assert os.path.getsize(extracted[1]) == 149


def test_extract_images_from_epub_without_opf_fallback():
    # Non-standard zip with only images and no container.xml / OPF
    buf = io.BytesIO()
    dummy_png = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82'
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('img_02.png', dummy_png)
        zf.writestr('img_01.png', dummy_png)

    with tempfile.TemporaryDirectory() as tmp_dir:
        epub_path = os.path.join(tmp_dir, 'broken.epub')
        with open(epub_path, 'wb') as f:
            f.write(buf.getvalue())

        out_dir = os.path.join(tmp_dir, 'extracted')
        extracted = extract_images_from_epub(epub_path, out_dir)

        assert len(extracted) == 2
        assert os.path.basename(extracted[0]) == "page_0001.png"
        assert os.path.basename(extracted[1]) == "page_0002.png"
