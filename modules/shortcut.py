"""T.M.O.S — Desktop shortcut
Puts a "T.M.O.S" shortcut with the app's icon on the desktop. It starts
main.py with pythonw (no console window); if T.M.O.S is already running, the
open window comes to the front instead of a second copy starting.

    python main.py --create-shortcut      # or ⚙ Settings › System, or say "create a desktop shortcut"
"""

import os
import platform
import struct

from modules import autostart, config

NAME      = 'T.M.O.S'
ICON_FILE = os.path.join(config.DATA_DIR, 'tmos.ico')
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
_CYAN, _GLOW = '#00e5ff', '#6600e5ff'           # the tray icon's colour; glow is 40% alpha


def desktop_dir() -> str:
    """The real Desktop folder: Windows often moves it into OneDrive."""
    if platform.system() == 'Windows':
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0 and buf.value:
            return buf.value                             # 0x10 = CSIDL_DESKTOPDIRECTORY
    return os.path.join(os.path.expanduser('~'), 'Desktop')


def path() -> str:
    return os.path.join(desktop_dir(), NAME + '.lnk')


def exists() -> bool:
    return os.path.exists(path())


# WScript.Shell writes the .lnk; paths come in through environment variables so
# quotes or non-English characters in them can never be read as PowerShell code.
_CREATE_PS = r'''
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:TMOS_LNK)
$s.TargetPath = $env:TMOS_EXE
$s.Arguments = '"' + $env:TMOS_MAIN + '"'
$s.WorkingDirectory = $env:TMOS_DIR
$s.Description = 'T.M.O.S desktop AI assistant'
if ($env:TMOS_ICON) { $s.IconLocation = $env:TMOS_ICON + ',0' }
$s.Save()
'''


def create() -> dict:
    """Create (or refresh) the desktop shortcut."""
    if platform.system() != 'Windows':
        return {'success': False, 'message': 'Desktop shortcuts are only supported on Windows.'}
    from modules import tools
    exe, main = autostart.launcher()
    lnk = path()
    icon = write_icon()
    env = dict(os.environ, TMOS_LNK=lnk, TMOS_EXE=exe, TMOS_MAIN=main,
               TMOS_DIR=os.path.dirname(main), TMOS_ICON=icon)
    try:
        r = tools._powershell(_CREATE_PS, env=env, timeout=20)
    except Exception as e:
        return {'success': False, 'message': f'Could not create the shortcut: {e}'}
    if r.returncode != 0 or not os.path.exists(lnk):
        err = r.stderr.decode('utf-8', errors='replace').strip().splitlines()
        return {'success': False, 'message': 'Could not create the shortcut'
                                             + (f': {err[0][:200]}' if err else '.')}
    return {'success': True, 'path': lnk,
            'message': f'Added a "{NAME}" shortcut to your desktop. Double-click it to start T.M.O.S.'}


# ── Icon ─────────────────────────────────────────────────────────────────────

def write_icon() -> str:
    """Draw the app's cyan diamond into ~/.tmos/tmos.ico (every size Windows asks
    for) and return its path, or '' if it can't be drawn."""
    try:
        data = _ico([(s, _diamond_png(s)) for s in ICON_SIZES])
        with open(ICON_FILE, 'wb') as f:
            f.write(data)
        return ICON_FILE
    except Exception as e:                     # no icon is fine: Windows shows Python's
        print(f'[Shortcut] could not draw the icon: {e}')
        return ''


def _diamond_png(size: int) -> bytes:
    from PyQt6.QtCore import QBuffer, QIODevice, QPointF, Qt
    from PyQt6.QtGui import QColor, QImage, QPainter, QPolygonF

    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)

    def diamond(margin: float) -> QPolygonF:
        c, r = size / 2, size / 2 - margin
        return QPolygonF([QPointF(c, c - r), QPointF(c + r, c), QPointF(c, c + r), QPointF(c - r, c)])

    if size >= 48:                             # a soft glow, like the orb's
        p.setBrush(QColor(_GLOW))
        p.drawPolygon(diamond(0))
    p.setBrush(QColor(_CYAN))
    p.drawPolygon(diamond(size / 32 + (size / 14 if size >= 48 else 0)))
    p.end()

    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, 'PNG')
    return bytes(buf.data())


def _ico(images: list[tuple[int, bytes]]) -> bytes:
    """An .ico file holding PNG images (supported since Windows Vista)."""
    header = struct.pack('<HHH', 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, blobs = b'', b''
    for size, png in images:
        entries += struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32,
                               len(png), offset + len(blobs))      # 256 is written as 0
        blobs += png
    return header + entries + blobs
