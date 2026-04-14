"""T.M.O.S — File Management Module
Handles directory listing, file creation/deletion, and file info.
Includes path validation to prevent unsafe operations.
"""

import os
import shutil
import stat
from datetime import datetime
from pathlib import Path


# ── Safety ───────────────────────────────────────────────────────────────────

# Directories that should NEVER be deleted/modified by T.M.O.S
_PROTECTED_PATHS = {
    'C:\\Windows', 'C:\\Program Files', 'C:\\Program Files (x86)',
    '/bin', '/sbin', '/usr', '/etc', '/boot', '/dev', '/proc', '/sys',
}


def _is_safe_path(path: str) -> bool:
    """Check if the path is safe to operate on (not a protected system dir)."""
    resolved = os.path.realpath(os.path.expanduser(path))
    for protected in _PROTECTED_PATHS:
        if resolved.lower().startswith(protected.lower()):
            return False
    return True


def _format_size(size_bytes: int) -> str:
    """Human-readable file size."""
    for unit in ('B', 'KB', 'MB', 'GB'):
        if abs(size_bytes) < 1024:
            return f'{size_bytes:.1f} {unit}'
        size_bytes /= 1024
    return f'{size_bytes:.1f} TB'


# ── Directory listing ────────────────────────────────────────────────────────

def list_dir(dir_path: str | None = None) -> dict:
    """List directory contents with metadata."""
    if dir_path is None:
        dir_path = str(Path.home())

    dir_path = os.path.expanduser(dir_path)

    if not os.path.isdir(dir_path):
        return {'success': False, 'message': f'Not a directory: {dir_path}', 'items': []}

    try:
        items = []
        for entry in os.scandir(dir_path):
            try:
                st = entry.stat(follow_symlinks=False)
                items.append({
                    'name': entry.name,
                    'is_folder': entry.is_dir(follow_symlinks=False),
                    'full_path': entry.path,
                    'size': st.st_size if not entry.is_dir() else 0,
                    'size_human': _format_size(st.st_size) if not entry.is_dir() else '—',
                    'modified': datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M'),
                })
            except (PermissionError, OSError):
                items.append({
                    'name': entry.name,
                    'is_folder': entry.is_dir(),
                    'full_path': entry.path,
                    'size': 0,
                    'size_human': '—',
                    'modified': '—',
                })
        # Sort: folders first, then alphabetical
        items.sort(key=lambda x: (not x['is_folder'], x['name'].lower()))
        return {'success': True, 'path': dir_path, 'items': items}
    except PermissionError:
        return {'success': False, 'message': f'Permission denied: {dir_path}', 'items': []}
    except Exception as e:
        return {'success': False, 'message': str(e), 'items': []}


# ── File/folder operations ───────────────────────────────────────────────────

def delete_file(file_path: str) -> dict:
    """Delete a file or folder (with safety checks)."""
    if not file_path or not file_path.strip():
        return {'success': False, 'message': 'No path provided.'}

    file_path = os.path.expanduser(file_path.strip())

    if not os.path.exists(file_path):
        return {'success': False, 'message': f'Path does not exist: {file_path}'}

    if not _is_safe_path(file_path):
        return {'success': False, 'message': 'Cannot delete protected system paths.'}

    try:
        if os.path.isdir(file_path):
            shutil.rmtree(file_path)
        else:
            os.remove(file_path)
        return {'success': True, 'message': f'Deleted: {file_path}'}
    except PermissionError:
        return {'success': False, 'message': f'Permission denied: {file_path}'}
    except Exception as e:
        return {'success': False, 'message': str(e)}


def create_item(dir_path: str, name: str, is_folder: bool = False) -> dict:
    """Create a new file or folder."""
    if not dir_path or not name:
        return {'success': False, 'message': 'Directory path and name are required.'}

    dir_path = os.path.expanduser(dir_path.strip())
    name = name.strip()

    # Basic name validation
    invalid_chars = '<>:"|?*' if os.name == 'nt' else '/'
    if any(c in name for c in invalid_chars):
        return {'success': False, 'message': f'Invalid characters in name: {name}'}

    target = os.path.join(dir_path, name)

    if os.path.exists(target):
        return {'success': False, 'message': f'Already exists: {target}'}

    try:
        if is_folder:
            os.makedirs(target, exist_ok=True)
        else:
            # Ensure parent directory exists
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, 'w', encoding='utf-8') as f:
                pass
        return {'success': True, 'path': target}
    except PermissionError:
        return {'success': False, 'message': f'Permission denied: {target}'}
    except Exception as e:
        return {'success': False, 'message': str(e)}


def get_file_info(file_path: str) -> dict:
    """Get detailed info about a file or folder."""
    file_path = os.path.expanduser(file_path.strip())

    if not os.path.exists(file_path):
        return {'success': False, 'message': f'Path does not exist: {file_path}'}

    try:
        st = os.stat(file_path)
        return {
            'success': True,
            'name': os.path.basename(file_path),
            'path': file_path,
            'is_folder': os.path.isdir(file_path),
            'size': st.st_size,
            'size_human': _format_size(st.st_size),
            'created': datetime.fromtimestamp(st.st_ctime).strftime('%Y-%m-%d %H:%M'),
            'modified': datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M'),
            'readable': os.access(file_path, os.R_OK),
            'writable': os.access(file_path, os.W_OK),
        }
    except Exception as e:
        return {'success': False, 'message': str(e)}
