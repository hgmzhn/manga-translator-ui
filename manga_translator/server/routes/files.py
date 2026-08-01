"""
Files routes module.

This module contains file management endpoints for the manga translator server.
"""

import os
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from manga_translator.server.core.config_manager import FONTS_DIR, admin_settings
from manga_translator.server.core.download_ticket_service import resolve_path_within
from manga_translator.server.core.middleware import require_admin
from manga_translator.server.core.models import Session
from manga_translator.utils import BASE_PATH

router = APIRouter(tags=["files"])
PROMPTS_DIR = Path(BASE_PATH) / 'dict'


def _resolve_managed_path(directory: str | Path, filename: str) -> Path:
    if not filename or '..' in filename or '/' in filename or '\\' in filename:
        raise HTTPException(400, detail="Invalid filename")

    try:
        return resolve_path_within(directory, os.path.join(directory, filename))
    except ValueError:
        raise HTTPException(400, detail="Invalid filename")


# ============================================================================
# Font Management Endpoints
# ============================================================================

@router.post("/upload/font")
async def upload_font(
    file: UploadFile = File(...),
    session: Session = Depends(require_admin)
):
    """Upload a font file to server (admin only)"""
    # Check permissions
    if not admin_settings.get('permissions', {}).get('can_upload_fonts', True):
        raise HTTPException(403, detail="Font upload is disabled")

    filename = file.filename or ''
    if not filename.lower().endswith(('.ttf', '.otf', '.ttc')):
        raise HTTPException(400, detail="Invalid font file format")

    os.makedirs(FONTS_DIR, exist_ok=True)

    file_path = _resolve_managed_path(FONTS_DIR, filename)
    with open(file_path, 'wb') as f:
        content = await file.read()
        f.write(content)

    return {"success": True, "filename": filename}


@router.delete("/fonts/{filename}")
async def delete_font(
    filename: str,
    session: Session = Depends(require_admin)
):
    """Delete a font file (admin only)"""
    # Check permissions
    if not admin_settings.get('permissions', {}).get('can_delete_fonts', True):
        raise HTTPException(403, detail="Font deletion is disabled")
    
    # Find in fonts directory
    file_path = _resolve_managed_path(FONTS_DIR, filename)
    
    if not os.path.exists(file_path):
        raise HTTPException(404, detail="Font file not found")
    
    try:
        os.remove(file_path)
        return {"success": True, "message": f"Deleted {filename}"}
    except Exception as e:
        raise HTTPException(500, detail=f"Failed to delete file: {str(e)}")


# ============================================================================
# Prompt Management Endpoints
# ============================================================================

@router.post("/upload/prompt")
async def upload_prompt(
    file: UploadFile = File(...),
    session: Session = Depends(require_admin)
):
    """Upload a high-quality translation prompt file to server (admin only)"""
    # Check permissions
    if not admin_settings.get('permissions', {}).get('can_upload_prompts', True):
        raise HTTPException(403, detail="Prompt upload is disabled")
    
    filename = file.filename or ''
    if not filename.lower().endswith(('.json', '.yaml', '.yml')):
        raise HTTPException(400, detail="Invalid prompt file format (must be .json, .yaml, or .yml)")

    # Prohibit uploading system prompt filenames
    SYSTEM_PROMPT_FILES = {
        'system_prompt_hq.json', 'system_prompt_hq.yaml', 'system_prompt_hq.yml',
        'system_prompt_hq_format.json', 'system_prompt_hq_format.yaml', 'system_prompt_hq_format.yml',
        'system_prompt_line_break.json', 'system_prompt_line_break.yaml', 'system_prompt_line_break.yml',
        'glossary_extraction_prompt.json', 'glossary_extraction_prompt.yaml', 'glossary_extraction_prompt.yml',
    }
    if filename in SYSTEM_PROMPT_FILES:
        raise HTTPException(403, detail="Cannot overwrite system prompt files")

    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)

    file_path = _resolve_managed_path(PROMPTS_DIR, filename)
    with open(file_path, 'wb') as f:
        content = await file.read()
        f.write(content)

    return {"success": True, "filename": filename}


@router.get("/prompts")
async def list_prompts(session: Session = Depends(require_admin)):
    """List available prompt files (excluding system prompts)"""
    try:
        prompts = []

        if PROMPTS_DIR.exists():
            files = os.listdir(PROMPTS_DIR)
            SYSTEM_PROMPT_BASES = {
                'system_prompt_hq', 'system_prompt_hq_format', 'system_prompt_line_break', 'glossary_extraction_prompt',
            }
            PROMPT_EXTENSIONS = ('.json', '.yaml', '.yml')
            for f in files:
                if not f.lower().endswith(PROMPT_EXTENSIONS):
                    continue
                # 排除系统提示词文件
                name_without_ext = os.path.splitext(f)[0]
                if name_without_ext in SYSTEM_PROMPT_BASES:
                    continue
                prompts.append(f)

        return sorted(prompts)
    except Exception as e:
        raise HTTPException(500, detail=f"Failed to list prompts: {str(e)}")


@router.get("/prompts/{filename}")
async def get_prompt(
    filename: str,
    session: Session = Depends(require_admin)
):
    """Get prompt file content (admin only)"""
    file_path = _resolve_managed_path(PROMPTS_DIR, filename)

    if not file_path.exists():
        raise HTTPException(404, detail="Prompt file not found")

    with file_path.open('r', encoding='utf-8') as f:
        content = f.read()

    return {"filename": filename, "content": content}


@router.delete("/prompts/{filename}")
async def delete_prompt(
    filename: str,
    session: Session = Depends(require_admin)
):
    """Delete a prompt file (admin only)"""
    # Check permissions
    if not admin_settings.get('permissions', {}).get('can_delete_prompts', True):
        raise HTTPException(403, detail="Prompt deletion is disabled")
    
    # Prohibit deleting system prompts
    SYSTEM_PROMPT_BASES = {
        'system_prompt_hq', 'system_prompt_hq_format', 'system_prompt_line_break', 'glossary_extraction_prompt',
    }
    name_without_ext = os.path.splitext(filename)[0]
    if name_without_ext in SYSTEM_PROMPT_BASES:
        raise HTTPException(403, detail="Cannot delete system prompt files")

    file_path = _resolve_managed_path(PROMPTS_DIR, filename)

    if not file_path.exists():
        raise HTTPException(404, detail="Prompt file not found")

    try:
        os.remove(file_path)
        return {"success": True, "message": f"Deleted {filename}"}
    except Exception as e:
        raise HTTPException(500, detail=f"Failed to delete file: {str(e)}")
