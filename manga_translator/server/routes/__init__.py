"""
Routes module for manga_translator server.

This module contains all API route definitions organized by functionality.
"""

from manga_translator.server.routes.admin import router as admin_router
from manga_translator.server.routes.audit import router as audit_router
from manga_translator.server.routes.auth import init_auth_services
from manga_translator.server.routes.auth import router as auth_router
from manga_translator.server.routes.config import router as config_router
from manga_translator.server.routes.files import router as files_router
from manga_translator.server.routes.groups import router as groups_router
from manga_translator.server.routes.history import init_history_routes
from manga_translator.server.routes.history import router as history_router
from manga_translator.server.routes.quota import init_quota_routes
from manga_translator.server.routes.quota import router as quota_router
from manga_translator.server.routes.resources import init_resource_routes
from manga_translator.server.routes.resources import router as resources_router
from manga_translator.server.routes.source import router as source_router
from manga_translator.server.routes.translation import router as translation_router
from manga_translator.server.routes.users import router as users_router
from manga_translator.server.routes.web import router as web_router
from manga_translator.server.routes.config_management import (
    router as config_management_router,
)
from manga_translator.server.routes.logs import logs_router

# Import sessions router
from manga_translator.server.routes.sessions import router as sessions_router

__all__ = [
    'translation_router',
    'admin_router',
    'config_router',
    'files_router',
    'web_router',
    'users_router',
    'audit_router',
    'auth_router',
    'init_auth_services',
    'groups_router',
    'resources_router',
    'source_router',
    'init_resource_routes',
    'history_router',
    'init_history_routes',
    'quota_router',
    'init_quota_routes',
    'config_management_router',
    'logs_router',
    'sessions_router',
]
