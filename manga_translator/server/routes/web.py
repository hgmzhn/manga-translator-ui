"""
Web routes module.

This module contains Web UI related endpoints for the manga translator server.
"""

import os
from datetime import timedelta

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from manga_translator.server.core.config_manager import admin_settings
from manga_translator.server.core.request_rate_limiter import SlidingWindowRateLimiter
from manga_translator.runtime_paths import get_application_dir

router = APIRouter(tags=["web"])
USER_LOGIN_WINDOW = timedelta(minutes=10)
USER_LOGIN_MAX_ATTEMPTS = 10
_user_login_rate_limiter = SlidingWindowRateLimiter()

# Static directory path
static_dir = os.path.join(get_application_dir(), "manga_translator", "server", "static")


# ============================================================================
# Web UI Page Endpoints
# ============================================================================

@router.get("/", response_class=HTMLResponse)
async def read_root():
    """Serve the Web UI index page (User mode)"""
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        with open(index_path, 'r', encoding='utf-8') as f:
            return f.read()
    return HTMLResponse("<h1>Web UI not installed</h1><p>Please ensure index.html exists in manga_translator/server/static/</p>")


@router.get("/admin", response_class=HTMLResponse)
async def read_admin():
    """Serve the Admin UI (new modular version)"""
    # 使用新的模块化管理界面
    admin_path = os.path.join(static_dir, "admin-new.html")
    if os.path.exists(admin_path):
        with open(admin_path, 'r', encoding='utf-8') as f:
            return f.read()
    return HTMLResponse("<h1>Admin UI not installed</h1>")





@router.get("/api")
async def api_info():
    """API server information"""
    return {
        "message": "Manga Translator API Server",
        "version": "2.0",
        "endpoints": {
            "translate": "/translate/image",
            "translate_stream": "/translate/with-form/image/stream",
            "batch": "/translate/batch/json",
            "docs": "/docs"
        }
    }

# ============================================================================
# User Login Endpoint
# ============================================================================

@router.post("/user/login")
async def user_login(req: Request, password: str = Form(...)):
    """User login"""
    user_access = admin_settings.get('user_access', {})
    
    # If no password required, allow access directly
    if not user_access.get('require_password', False):
        return {"success": True, "message": "No password required"}

    client_ip = req.client.host if req.client else "unknown"
    rate_limit_key = f"web:user-login:ip:{client_ip}"
    allowed, retry_after = _user_login_rate_limiter.check(
        rate_limit_key,
        USER_LOGIN_MAX_ATTEMPTS,
        USER_LOGIN_WINDOW,
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="尝试过于频繁，请稍后再试",
            headers={"Retry-After": str(retry_after)},
        )
    
    # Verify password
    if password == user_access.get('user_password', ''):
        _user_login_rate_limiter.reset(rate_limit_key)
        return {"success": True, "message": "Login successful"}

    _user_login_rate_limiter.record(rate_limit_key, USER_LOGIN_MAX_ATTEMPTS, USER_LOGIN_WINDOW)
    return {"success": False, "message": "Invalid password"}
