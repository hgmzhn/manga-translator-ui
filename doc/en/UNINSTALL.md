# Uninstall Guide

## New version (portable, has `packaging\python` inside)

The new version is fully portable: Python, dependencies and caches all live inside this folder — nothing is written to the registry and no services are installed.

**To uninstall, simply delete the whole folder.** There are no other steps.

Optional cleanup (harmless leftovers, safe to ignore):

| Leftover | Location | How to clean |
|---|---|---|
| AI model caches | `C:\Users\<you>\.cache\huggingface`, `.cache\torch` | Delete the folders |
| git safe-directory entry | One line in `C:\Users\<you>\.gitconfig` | `git config --global --unset-all safe.directory` |

## Legacy version (Conda-based, has `Miniconda3` inside or installed via `步骤1-首次安装.bat`)

Do these two steps in order:

### 1. Uninstall Miniconda

- **If Miniconda3 was installed by the setup script** (inside this program's folder, or at a drive root such as `D:\Miniconda3`):
  open the `Miniconda3` folder and **double-click `Uninstall-Miniconda3.exe`**, then follow the prompts
  (it cleans up the registry, environment variables and Start Menu entries; the `manga-env` environment is removed with it).
  If the `Miniconda3` folder still has leftovers afterwards, just delete it.
- **If you installed Miniconda yourself and still need it**:
  do not uninstall it — only remove this program's environment from the command line:
  ```
  conda env remove -n manga-env -y
  ```

### 2. Delete the whole program folder

Once Miniconda is handled, delete the entire program folder (including `PortableGit`, code and scripts).
