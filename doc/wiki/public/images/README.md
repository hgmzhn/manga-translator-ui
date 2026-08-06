# Wiki image assets

This directory is reserved for public VitePress image assets. Files are served from the site root as `/images/<module>/<file>`.

## Module directories

| Module | Purpose |
| --- | --- |
| `desktop/navigation` | Desktop navigation and language-switching views |
| `desktop/translation` | Translation workspace views and states |
| `desktop/settings` | Settings views and controls |
| `desktop/api` | API-management views and states |
| `desktop/prompts` | Prompt-management views and dialogs |
| `desktop/rules` | Replacement and rich-text rule views |
| `desktop/batch` | Batch-management views and dialogs |
| `desktop/editor` | Editor views, tools, and panels |
| `web/login` | Web login and session views |
| `web/workspace` | Web workspace views and states |
| `web/history` | Web history views |
| `web/admin` | Web administration views |
| `cli/install` | Installation terminal captures |
| `cli/commands` | CLI command captures |
| `cli/debug` | CLI debugging captures |
| `diagrams/pipeline` | Pipeline diagrams |
| `diagrams/api` | API-selection and rotation diagrams |
| `diagrams/batch` | Batch-concurrency diagrams |
| `diagrams/web` | Web task and authentication diagrams |

## Naming convention

- Use lowercase ASCII kebab-case filenames and an appropriate web image extension: `.png`, `.webp`, `.svg`, or `.jpg`.
- Name an asset `<subject>-<state>-<locale>-<variant>.<ext>`; omit only trailing segments that do not apply. For example, `language-switch-zh-cn-default.png` and `api-rotation-failover.svg`.
- Use `zh-cn` and `en-us` for localized screenshots. Diagrams without localized text omit the locale segment.
- Keep each asset in the narrowest module directory above; do not create a catch-all image directory.
- Image content, alt text, and captions are added only with the page that uses them. Captures must be sanitized and meet the bilingual metadata requirements in `BLUEPRINT.md`.

No image asset is added by this directory-structure task.
