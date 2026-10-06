import logging
import shutil
from pathlib import Path

from manga_translator.runtime_paths import get_application_dir


PACKAGE_DIR = Path(get_application_dir()) / "manga_translator"
SERVER_DIR = PACKAGE_DIR / "server"
SERVER_DATA_DIR = SERVER_DIR / "data"
USER_RESOURCES_DIR = SERVER_DATA_DIR / "user_resources"
USER_PROMPTS_DIR = USER_RESOURCES_DIR / "prompts"
USER_PROMPT_FILE = USER_PROMPTS_DIR / "prompt_user.yaml"
ADMIN_CONFIG_FILE = SERVER_DATA_DIR / "admin_config.json"

SERVER_RELATIVE_DIR = "manga_translator/server"
SERVER_DATA_RELATIVE_DIR = f"{SERVER_RELATIVE_DIR}/data"
USER_RESOURCES_RELATIVE_DIR = f"{SERVER_DATA_RELATIVE_DIR}/user_resources"

LEGACY_ADMIN_CONFIG_FILE = SERVER_DIR / "admin_config.json"
LEGACY_USER_RESOURCES_DIR = SERVER_DIR / "user_resources"
LEGACY_USER_RESOURCES_RELATIVE_DIR = f"{SERVER_RELATIVE_DIR}/user_resources"


SHIPPED_PROMPT_FILE = Path(get_application_dir()) / "dict" / "prompt_example.yaml"

logger = logging.getLogger(__name__)


def redirect_shipped_prompt_path(path: str | None) -> str | None:
    """Route glossary writes for the shipped prompt template into user storage.

    ``high_quality_prompt_path`` both seeds the custom prompt and receives
    newly extracted glossary terms.  When it points at the shipped template
    inside the checkout, the runtime dirties a tracked file and breaks any
    deployment that updates the checkout with ``git pull``.  Keep the
    template pristine and use an untracked user copy instead.
    """
    if not path:
        return path
    candidate = Path(str(path).replace("\\", "/"))
    if not candidate.is_absolute():
        candidate = Path(get_application_dir()) / candidate
    try:
        if candidate.resolve() != SHIPPED_PROMPT_FILE.resolve():
            return path
    except OSError:
        return path

    try:
        USER_PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
        if not USER_PROMPT_FILE.exists() and SHIPPED_PROMPT_FILE.exists():
            shutil.copyfile(SHIPPED_PROMPT_FILE, USER_PROMPT_FILE)
        logger.info("Redirected shipped prompt path %s to user copy %s", path, USER_PROMPT_FILE)
    except OSError as error:
        logger.warning("Unable to prepare user prompt copy %s: %s", USER_PROMPT_FILE, error)
    return USER_PROMPT_FILE.resolve().as_posix()



def normalize_server_resource_path(path: str) -> str:
    if not path:
        return path

    normalized = path.replace("\\", "/")
    legacy_relative_prefix = f"{LEGACY_USER_RESOURCES_RELATIVE_DIR}/"
    current_relative_prefix = f"{USER_RESOURCES_RELATIVE_DIR}/"

    if normalized == LEGACY_USER_RESOURCES_RELATIVE_DIR:
        return USER_RESOURCES_RELATIVE_DIR
    if normalized.startswith(legacy_relative_prefix):
        return normalized.replace(legacy_relative_prefix, current_relative_prefix, 1)

    legacy_absolute_prefix = LEGACY_USER_RESOURCES_DIR.resolve().as_posix()
    current_absolute_prefix = USER_RESOURCES_DIR.resolve().as_posix()

    if normalized == legacy_absolute_prefix:
        return current_absolute_prefix
    if normalized.startswith(f"{legacy_absolute_prefix}/"):
        return normalized.replace(f"{legacy_absolute_prefix}/", f"{current_absolute_prefix}/", 1)

    return normalized


def ensure_server_data_layout(logger: logging.Logger | None = None) -> None:
    target_logger = logger or logging.getLogger("manga_translator.server")

    SERVER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    USER_RESOURCES_DIR.mkdir(parents=True, exist_ok=True)
    (USER_RESOURCES_DIR / "fonts").mkdir(parents=True, exist_ok=True)
    (USER_RESOURCES_DIR / "prompts").mkdir(parents=True, exist_ok=True)

    _migrate_legacy_admin_config(target_logger)
    _migrate_legacy_user_resources(target_logger)


def _migrate_legacy_admin_config(logger: logging.Logger) -> None:
    if not LEGACY_ADMIN_CONFIG_FILE.exists():
        return

    ADMIN_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)

    if ADMIN_CONFIG_FILE.exists():
        logger.warning(
            "Legacy admin config still exists but the new path is already in use: %s",
            LEGACY_ADMIN_CONFIG_FILE,
        )
        return

    shutil.move(str(LEGACY_ADMIN_CONFIG_FILE), str(ADMIN_CONFIG_FILE))
    logger.info("Migrated admin config to %s", ADMIN_CONFIG_FILE)


def _migrate_legacy_user_resources(logger: logging.Logger) -> None:
    if not LEGACY_USER_RESOURCES_DIR.exists():
        return

    _merge_directory_contents(LEGACY_USER_RESOURCES_DIR, USER_RESOURCES_DIR, logger)

    try:
        LEGACY_USER_RESOURCES_DIR.rmdir()
        logger.info("Removed empty legacy user resource directory: %s", LEGACY_USER_RESOURCES_DIR)
    except OSError:
        logger.warning(
            "Legacy user resource directory still contains files after migration: %s",
            LEGACY_USER_RESOURCES_DIR,
        )


def _merge_directory_contents(source: Path, destination: Path, logger: logging.Logger) -> None:
    destination.mkdir(parents=True, exist_ok=True)

    for item in source.iterdir():
        target = destination / item.name
        if not target.exists():
            shutil.move(str(item), str(target))
            continue

        if item.is_dir() and target.is_dir():
            _merge_directory_contents(item, target, logger)
            try:
                item.rmdir()
            except OSError:
                logger.warning("Legacy directory still contains files after merge: %s", item)
            continue

        logger.warning("Skipped legacy path because target already exists: %s", target)
