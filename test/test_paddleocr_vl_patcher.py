import _bootstrap  # noqa: F401

from manga_translator.ocr.paddleocr_vl_patcher import patch_transformers_paddleocr_vl_docs


def test_patch_transformers_paddleocr_vl_docs_adds_missing_fields(tmp_path):
    module_file = tmp_path / "image_processing_paddleocr_vl.py"
    module_file.write_text(
        "class PaddleOCRVLImageProcessorKwargs:\n"
        "    merge_size (`int`, *optional*, defaults to 2):\n"
        "        The merge size of the vision encoder to llm encoder.\n",
        encoding="utf-8",
    )

    assert patch_transformers_paddleocr_vl_docs(str(module_file)) is True
    content = module_file.read_text(encoding="utf-8")
    assert "min_pixels (`int`, *optional*, defaults to 147456):" in content
    assert "max_pixels (`int`, *optional*, defaults to 2359296):" in content
    assert patch_transformers_paddleocr_vl_docs(str(module_file)) is False
