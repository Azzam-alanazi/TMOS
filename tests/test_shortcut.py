"""Desktop shortcut: the icon file, the PowerShell hand-off and the command."""

import os
import struct

import pytest

import commands
from modules import autostart, shortcut, tools


def test_icon_is_a_valid_multi_size_ico():
    data = shortcut._ico([(s, shortcut._diamond_png(s)) for s in shortcut.ICON_SIZES])
    reserved, kind, count = struct.unpack_from('<HHH', data)
    assert (reserved, kind, count) == (0, 1, len(shortcut.ICON_SIZES))
    sizes = []
    for i in range(count):
        w, h, _, _, planes, bpp, length, offset = struct.unpack_from('<BBBBHHII', data, 6 + 16 * i)
        assert data[offset:offset + 8] == b'\x89PNG\r\n\x1a\n' and offset + length <= len(data)
        sizes.append(w or 256)
    assert sizes == list(shortcut.ICON_SIZES)


@pytest.fixture
def fake_ps(monkeypatch, tmp_path):
    """Stand-in for PowerShell: records the environment and writes the .lnk."""
    monkeypatch.setattr(shortcut, 'desktop_dir', lambda: str(tmp_path))
    monkeypatch.setattr(shortcut.platform, 'system', lambda: 'Windows')
    seen = {}

    class Done:
        returncode, stderr = 0, b''

    def run(script, env=None, timeout=5):
        seen.update(env)
        open(env['TMOS_LNK'], 'wb').close()
        return Done()

    monkeypatch.setattr(tools, '_powershell', run)
    return seen, tmp_path


def test_create_points_the_shortcut_at_main_py(fake_ps):
    seen, desk = fake_ps
    assert not shortcut.exists()
    r = shortcut.create()
    exe, main = autostart.launcher()
    assert r['success'] and r['path'] == os.path.join(str(desk), 'T.M.O.S.lnk') and shortcut.exists()
    assert (seen['TMOS_EXE'], seen['TMOS_MAIN']) == (exe, main)
    assert seen['TMOS_DIR'] == os.path.dirname(main) and main.endswith('main.py')
    assert seen['TMOS_ICON'] == shortcut.ICON_FILE and os.path.getsize(shortcut.ICON_FILE) > 1000


def test_create_reports_a_powershell_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(shortcut, 'desktop_dir', lambda: str(tmp_path))
    monkeypatch.setattr(shortcut.platform, 'system', lambda: 'Windows')

    class Failed:
        returncode, stderr = 1, b'Access is denied.\r\nmore detail'

    monkeypatch.setattr(tools, '_powershell', lambda *a, **k: Failed())
    r = shortcut.create()
    assert not r['success'] and r['message'] == 'Could not create the shortcut: Access is denied.'


def test_autostart_command_is_unchanged():
    exe, main = autostart.launcher()
    assert autostart.command() == f'"{exe}" "{main}" --minimized'


class Ctx:
    def __init__(self):
        self.async_calls = []

    def run_async(self, fn, done):
        self.async_calls.append(fn)


@pytest.mark.parametrize('text', ['create a desktop shortcut', 'Add a shortcut to my desktop.',
                                  'make a shortcut for yourself', 'desktop shortcut'])
def test_shortcut_command(text):
    ctx = Ctx()
    assert commands.dispatch(ctx, text)
    assert ctx.async_calls == [shortcut.create]


def test_other_shortcuts_go_to_the_ai():
    assert not commands.dispatch(Ctx(), 'create a shortcut for chrome')
