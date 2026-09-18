#!/usr/bin/env python3
"""T.M.O.S — Personal AI Desktop Assistant
Three AI backends (Groq, Gemini, Ollama) with tool calling, always-on
wake-word listening, web-animated orb, streaming responses, full command suite.

    python main.py              # normal start
    python main.py --minimized  # start in the tray (used by "Start with Windows")
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from functools import lru_cache

# Logs contain arrows/emoji; a legacy console code page would crash a worker thread on them.
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, 'reconfigure'):
        _stream.reconfigure(encoding='utf-8', errors='replace')

from PyQt6.QtCore import QPoint, QThread, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (
    QBrush, QColor, QDesktopServices, QIcon, QPainter, QPixmap, QPolygon,
)
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMainWindow, QMenu,
    QPushButton, QSystemTrayIcon, QVBoxLayout, QWidget,
)
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebChannel import QWebChannel

import commands
from bridge import Bridge
from commands import Reply
from modules import config, history, mic, stt, tools, tts

# ── Colour palette (title bar only — content is HTML) ────────────────────────
_DARK   = '#020b14'
_CYAN   = '#00e5ff'
_GREEN  = '#00ff88'
_ORANGE = '#ffaa00'
_RED    = '#ff4444'
_DIM    = '#7ab8cc'
_BORDER = '#0d3a52'

_INSTANCE_NAME = 'tmos-desktop-assistant'


# ══════════════════════════════════════════════════════════════════════════════
#  BACKGROUND WORKERS
# ══════════════════════════════════════════════════════════════════════════════

class AIStreamWorker(QThread):
    """Streaming AI request — emits tokens as they arrive. cancel() stops it."""
    token      = pyqtSignal(str)
    tool_used  = pyqtSignal(str)         # JSON for the chat's tool chips
    reply_done = pyqtSignal(str, bool)   # (full reply, cancelled)

    def __init__(self, prompt: str):
        super().__init__()
        self.prompt = prompt
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        from modules import ai
        reply = ''
        stream = ai.ask_stream(self.prompt, on_tool=self._on_tool,
                               should_stop=lambda: self._cancelled)
        try:
            for chunk in stream:
                if self._cancelled:
                    break
                reply += chunk
                self.token.emit(chunk)
        except Exception as e:
            msg = f'Something went wrong: {e}'
            reply += msg
            self.token.emit(msg)
        finally:
            stream.close()
        self.reply_done.emit(reply, self._cancelled)

    def _on_tool(self, name: str, args: dict, result: dict):
        from modules import actions
        detail = ' · '.join(str(v) for v in args.values() if isinstance(v, (str, int, float)))
        self.tool_used.emit(json.dumps({
            'name': name,
            'label': actions.LABELS.get(name, name),
            'detail': detail[:60],
            'ok': bool(result.get('ok', True)),
            'error': result.get('error') or (result.get('message') if not result.get('ok', True) else ''),
        }))


class TaskWorker(QThread):
    """Runs fn() off the UI thread and emits its return value."""
    result = pyqtSignal(object)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.result.emit(self.fn())
        except Exception as e:
            self.result.emit({'success': False, 'message': str(e)})


class StatsWorker(QThread):
    done = pyqtSignal(dict)

    def run(self):
        from modules import system_info
        self.done.emit(system_info.get_stats())


class VoiceWorker(QThread):
    """One-shot voice recognition (mic button)."""
    result = pyqtSignal(str)
    error  = pyqtSignal(str)

    def run(self):
        try:
            import speech_recognition as sr
            rec = sr.Recognizer()
            with sr.Microphone(device_index=mic.resolve()[0]) as src:
                rec.adjust_for_ambient_noise(src, duration=0.3)
                audio = rec.listen(src, timeout=6, phrase_time_limit=12)
            self.result.emit(stt.recognize(rec, audio, purpose='command'))
        except Exception as e:
            name = type(e).__name__
            self.error.emit({'WaitTimeoutError': 'No speech heard.',
                             'UnknownValueError': "Couldn't understand that."}.get(name, str(e) or name))


@lru_cache(maxsize=8)
def wake_pattern(word: str) -> re.Pattern:
    """Regex for the wake word that survives recognizer spelling: 'TMOS', 'T-Mos',
    'T.M.O.S.', 'team OS', 'temos'… optionally preceded by hey/ok."""
    word = (word or 'tmos').strip().lower()
    if word == 'tmos':
        # t-mos, T.M.O.S, temos, timos, Timo's, team OS, tea moss, teemo's…
        core = r"t(?:ee|ea|i|e)?[\s.\-']*m+[\s.\-']*o+[\s.\-']*s+"
    else:
        core = r'[\s.\-]*'.join(re.escape(c) for c in word.replace(' ', ''))
    return re.compile(rf'\b(?:(?:hey|hi|ok|okay)[\s,]+)?(?:{core})\b[\s,.!?:;-]*', re.IGNORECASE)


class WakeWordListener(QThread):
    """Continuous wake-word listener.
    Runs in a loop, listening for short audio clips, and emits the command
    text after the wake word is detected (e.g. "TMOS what time is it").
    While T.M.O.S itself is talking, it ignores everything except "stop",
    so it doesn't answer its own voice coming out of the speakers.
    """
    command    = pyqtSignal(str)   # text after wake word
    wake_fired = pyqtSignal()      # wake word detected (for UI animation)
    stop_heard = pyqtSignal()      # "stop" said while T.M.O.S was talking
    status_msg = pyqtSignal(str)   # status updates
    listening  = pyqtSignal(bool)  # capturing a command after the wake word

    def __init__(self):
        super().__init__()
        self._running = False

    def stop(self):
        self._running = False

    def _after_wake(self, text: str) -> str | None:
        """Command text after the wake word ('' if only the wake word), or None."""
        m = wake_pattern(config.get('wake_word')).search(text)
        if not m:
            return None
        return text[m.end():].strip()

    def run(self):
        try:
            import speech_recognition as sr
        except ImportError:
            self.status_msg.emit('speech_recognition not installed.')
            return

        rec = sr.Recognizer()
        rec.energy_threshold = 300
        rec.dynamic_energy_threshold = True
        rec.pause_threshold = 0.6

        self._running = True
        last_error = 0.0
        if stt.setting() in ('auto', 'local') and stt.vosk_installed() and not stt.vosk_ready():
            stt.download_vosk_model(log=self.status_msg.emit)
        device, device_name = mic.resolve()
        try:
            source = sr.Microphone(device_index=device)
            with source as src:
                rec.adjust_for_ambient_noise(src, duration=1.0)
        except Exception as e:
            self.status_msg.emit(f'Mic init error ({device_name}): {e}')
            return
        self.status_msg.emit(f'🎙 Listening on "{device_name}" — {stt.describe()}.')

        while self._running:
            try:
                started = time.time()
                with source as src:
                    audio = rec.listen(src, timeout=5, phrase_time_limit=8)
                if not self._running:
                    break
                # Did T.M.O.S talk while we were recording? Then it's probably its own voice.
                overlapped = tts.is_speaking() or tts.last_active() > started

                try:
                    text = stt.recognize(rec, audio, purpose='wake').strip()
                except sr.UnknownValueError:
                    continue
                except sr.RequestError as e:
                    # Say so (at most once a minute) instead of failing silently.
                    if time.time() - last_error > 60:
                        last_error = time.time()
                        self.status_msg.emit(f'⚠ {e}')
                    time.sleep(1)
                    continue
                if not text:
                    continue

                cmd = self._after_wake(text)
                if overlapped:
                    if commands.is_stop(cmd or text):
                        self.stop_heard.emit()
                    continue
                if cmd is None:
                    self.status_msg.emit(f'Heard (no wake word): "{text[:80]}"')
                    continue

                self.wake_fired.emit()
                if cmd:
                    # The offline recognizer found the wake word; get the exact
                    # words of the command from the more accurate engine.
                    if stt.wake_is_local():
                        try:
                            exact = self._after_wake(stt.recognize(rec, audio, purpose='command'))
                            cmd = exact or cmd
                        except (sr.UnknownValueError, sr.RequestError):
                            pass
                    self.command.emit(cmd)       # wake word + command in one breath
                    continue

                # Just the wake word — listen for the follow-up
                self.status_msg.emit('Wake word detected — listening for a command...')
                self.listening.emit(True)
                try:
                    with source as src:
                        audio2 = rec.listen(src, timeout=5, phrase_time_limit=12)
                    cmd2 = stt.recognize(rec, audio2, purpose='command').strip()
                    if cmd2:
                        self.command.emit(cmd2)
                except (sr.WaitTimeoutError, sr.UnknownValueError):
                    self.status_msg.emit('No command heard.')
                except sr.RequestError as e:
                    self.status_msg.emit(f'Speech recognition error: {e}')
                finally:
                    self.listening.emit(False)

            except sr.WaitTimeoutError:
                continue
            except Exception as e:
                self.status_msg.emit(f'Listener error: {e}')
                time.sleep(0.5)

        self.listening.emit(False)
        self.status_msg.emit('Wake-word listener stopped.')


class TmosPage(QWebEnginePage):
    """Prints UI errors to the console and opens clicked links in the real browser."""

    def javaScriptConsoleMessage(self, level, message, line, source):
        if level != QWebEnginePage.JavaScriptConsoleMessageLevel.InfoMessageLevel:
            print(f'[UI] {message} ({os.path.basename(source)}:{line})')

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if nav_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            if url.scheme() in ('http', 'https'):
                QDesktopServices.openUrl(url)
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN WINDOW
# ══════════════════════════════════════════════════════════════════════════════

class TmosWindow(QMainWindow):
    _reminder_signal = pyqtSignal(dict)
    _timer_signal    = pyqtSignal(dict)
    _speaking_signal = pyqtSignal(bool)
    _hotkey_signal   = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setMinimumSize(1100, 700)
        self.resize(1280, 800)
        self.setWindowTitle('T.M.O.S')
        self.setWindowIcon(self._make_tray_icon())

        self._drag_pos:       QPoint | None          = None
        self._stream_worker:  AIStreamWorker | None  = None
        self._stream_reply:   str                     = ''
        self._voice_worker:   VoiceWorker | None     = None
        self._stats_worker:   StatsWorker | None     = None
        self._wake_listener:  WakeWordListener | None = None
        self._threads:        set[QThread]           = set()   # keeps workers alive until they finish
        self._hotkey = None
        self.hotkey_error = ''
        self._told_about_tray = False
        self._warned: dict[str, float] = {}
        self._quitting = False

        # ── Build shell ───────────────────────────────────────────────────────
        root = QWidget()
        root.setObjectName('root')
        self.setCentralWidget(root)
        vb = QVBoxLayout(root)
        vb.setContentsMargins(0, 0, 0, 0)
        vb.setSpacing(0)

        vb.addWidget(self._make_titlebar())

        self._view = QWebEngineView()
        self._view.setPage(TmosPage(self._view))
        self._view.page().setBackgroundColor(QColor(_DARK))
        self._view.settings().setAttribute(       # lets the alarm chime play without a click first
            QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        self._view.setStyleSheet(f'background:{_DARK};')
        vb.addWidget(self._view, 1)

        # ── WebChannel bridge ─────────────────────────────────────────────────
        self._bridge  = Bridge(self)
        self._channel = QWebChannel()
        self._channel.registerObject('bridge', self._bridge)
        self._view.page().setWebChannel(self._channel)
        self._bridge.chat_pushed.connect(self._record_chat)

        self._ensure_qwebchannel_js()

        html_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), 'ui', 'index.html')
        )
        self._view.load(QUrl.fromLocalFile(html_path))

        self._apply_style()

        # ── Clock ─────────────────────────────────────────────────────────────
        self._clock_t = QTimer(self)
        self._clock_t.timeout.connect(self._tick_clock)
        self._clock_t.start(1000)
        self._tick_clock()

        # ── Stats ─────────────────────────────────────────────────────────────
        self._stats_t = QTimer(self)
        self._stats_t.timeout.connect(self._poll_stats)
        self._stats_t.start(3000)

        # ── Reminders, timers, speech state (callbacks arrive on other threads) ─
        self._reminder_signal.connect(self._on_reminder_fired)
        self._timer_signal.connect(self._on_timer_done)
        self._speaking_signal.connect(self._on_speaking)
        self._hotkey_signal.connect(self.summon)
        from modules import reminders as rem
        rem.init(callback=lambda r: self._reminder_signal.emit(r))
        tools.set_countdown_callback(lambda t: self._timer_signal.emit(t))
        tts.set_state_callback(lambda on: self._speaking_signal.emit(on))

        # ── System tray ───────────────────────────────────────────────────────
        self._setup_tray()

        # ── TTS preload ───────────────────────────────────────────────────────
        tts.preload()
        self._sync_mute_button()

        # ── Global hotkey ─────────────────────────────────────────────────────
        ok, msg = self.apply_hotkey(config.get('hotkey'))
        if not ok:
            print(f'[Hotkey] {msg}')

        # ── Start always-listen if enabled ────────────────────────────────────
        mic.list_inputs()      # scan microphones once, before any audio thread starts
        if config.get('always_listen'):
            QTimer.singleShot(1500, self._start_wake_listener)

    # ── qwebchannel.js injection ──────────────────────────────────────────────

    def _ensure_qwebchannel_js(self):
        dest = os.path.join(os.path.dirname(__file__), 'ui', 'qwebchannel.js')
        if os.path.exists(dest):
            return
        try:
            from PyQt6.QtCore import QFile, QIODevice
            f = QFile(':/qtwebchannel/qwebchannel.js')
            if f.open(QIODevice.OpenModeFlag.ReadOnly):
                content = bytes(f.readAll()).decode('utf-8')
                f.close()
                with open(dest, 'w', encoding='utf-8') as out:
                    out.write(content)
        except Exception:
            pass

    # ── Title bar ─────────────────────────────────────────────────────────────

    def _make_titlebar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName('titlebar')
        bar.setFixedHeight(52)
        bar.mousePressEvent   = self._tb_press
        bar.mouseMoveEvent    = self._tb_move
        bar.mouseReleaseEvent = lambda _: setattr(self, '_drag_pos', None)
        bar.mouseDoubleClickEvent = lambda _: self._toggle_max()

        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 0, 12, 0)

        logo = QLabel('◈  T.M.O.S')
        logo.setObjectName('logo')
        h.addWidget(logo)
        h.addStretch()

        col = QWidget()
        cv  = QVBoxLayout(col)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        self._clock_lbl = QLabel('00:00:00')
        self._clock_lbl.setObjectName('clock_lbl')
        self._date_lbl  = QLabel(datetime.now().strftime('%a, %d %b %Y'))
        self._date_lbl.setObjectName('date_lbl')
        self._date_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        cv.addWidget(self._clock_lbl)
        cv.addWidget(self._date_lbl)
        h.addWidget(col)
        h.addStretch()

        self._status_lbl = QLabel('●  STANDING BY')
        self._status_lbl.setObjectName('status_lbl')
        h.addWidget(self._status_lbl)
        h.addSpacing(20)

        self._mute_btn = QPushButton('🔊')
        self._mute_btn.setObjectName('btn_mute')
        self._mute_btn.setFixedSize(30, 30)
        self._mute_btn.setToolTip('Toggle voice (mute/unmute)')
        self._mute_btn.clicked.connect(self._toggle_mute)
        h.addWidget(self._mute_btn)
        h.addSpacing(6)

        for txt, slot, oid, tip in [('─', self.showMinimized, 'btn_min',   'Minimize'),
                                    ('□', self._toggle_max,  'btn_max',   'Maximize'),
                                    ('✕', self.close,        'btn_close', 'Close to tray')]:
            btn = QPushButton(txt)
            btn.setObjectName(oid)
            btn.setFixedSize(30, 30)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            h.addWidget(btn)
        return bar

    def _apply_style(self):
        self.setStyleSheet(f"""
            QMainWindow, #root {{ background: {_DARK}; }}
            #titlebar {{
                background: #030d19;
                border-bottom: 1px solid {_BORDER};
            }}
            #logo {{
                color: {_CYAN};
                font-family: Consolas, monospace;
                font-size: 15px;
                font-weight: bold;
                letter-spacing: 4px;
            }}
            #clock_lbl {{
                color: {_CYAN};
                font-family: Consolas, monospace;
                font-size: 18px;
                letter-spacing: 2px;
            }}
            #date_lbl {{
                color: {_DIM};
                font-family: Consolas, monospace;
                font-size: 9px;
                letter-spacing: 1px;
            }}
            #status_lbl {{
                color: {_GREEN};
                font-family: Consolas, monospace;
                font-size: 11px;
                letter-spacing: 1px;
            }}
            #btn_mute, #btn_min, #btn_max {{
                background: transparent; border: none;
                color: {_DIM}; font-size: 14px;
            }}
            #btn_mute:hover, #btn_min:hover, #btn_max:hover {{
                color: {_CYAN}; background: #0d2535; border-radius: 4px;
            }}
            #btn_close {{
                background: transparent; border: none;
                color: {_DIM}; font-size: 14px;
            }}
            #btn_close:hover {{ background: {_RED}; color: white; border-radius: 4px; }}
            QToolTip {{
                background: #071a28; color: #b0d8e8; border: 1px solid {_BORDER};
                font-family: Consolas, monospace; font-size: 11px; padding: 4px;
            }}
        """)

    # ── Drag ──────────────────────────────────────────────────────────────────

    def _tb_press(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _tb_move(self, e):
        if e.buttons() == Qt.MouseButton.LeftButton and self._drag_pos:
            if self.isMaximized():
                return
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def _toggle_max(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def _toggle_mute(self):
        muted = tts.toggle_mute()
        self._sync_mute_button()
        self.log('Voice muted' if muted else 'Voice unmuted', 'sys')
        self._bridge._push_config()

    def _sync_mute_button(self):
        self._mute_btn.setText('🔇' if tts.is_muted() else '🔊')

    def sync_voice(self):
        self._sync_mute_button()
        self._bridge._push_config()

    # ── System tray ───────────────────────────────────────────────────────────

    def _make_tray_icon(self) -> QIcon:
        pix = QPixmap(32, 32)
        pix.fill(QColor(0, 0, 0, 0))
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QBrush(QColor(_CYAN)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(QPolygon([
            QPoint(16, 1), QPoint(31, 16),
            QPoint(16, 31), QPoint(1, 16),
        ]))
        p.end()
        return QIcon(pix)

    def _setup_tray(self):
        self._tray = QSystemTrayIcon(self._make_tray_icon(), self)
        menu = QMenu()
        menu.addAction('Show T.M.O.S', self._show_and_raise)
        menu.addAction('Stop talking', self.stop_all)
        menu.addSeparator()
        menu.addAction('Quit', self.quit_app)
        self._tray.setContextMenu(menu)
        self._tray.setToolTip('T.M.O.S — AI Desktop Assistant')
        self._tray.activated.connect(
            lambda r: self._show_and_raise()
            if r in (QSystemTrayIcon.ActivationReason.DoubleClick,
                     QSystemTrayIcon.ActivationReason.Trigger) else None
        )
        self._tray.show()

    def _show_and_raise(self):
        self.showNormal() if not self.isMaximized() else self.showMaximized()
        self.activateWindow()
        self.raise_()

    def summon(self):
        """Global hotkey: bring T.M.O.S to the front with the cursor in the input box,
        or hide it again if it's already in front."""
        if self.isVisible() and self.isActiveWindow() and not self.isMinimized():
            self.hide()
            return
        self._show_and_raise()
        self._view.setFocus()
        self._bridge.focus_input.emit()

    def apply_hotkey(self, combo: str) -> tuple[bool, str]:
        from modules.hotkey import GlobalHotkey, pretty
        if self._hotkey:
            self._hotkey.stop()
            self._hotkey = None
        self.hotkey_error = ''
        if not combo:
            return True, 'Global hotkey turned off.'
        hk = GlobalHotkey(combo, lambda: self._hotkey_signal.emit())
        if hk.start():
            self._hotkey = hk
            return True, f'Global hotkey: {pretty(combo)}'
        self.hotkey_error = hk.error
        return False, hk.error

    def closeEvent(self, event):
        if self._quitting:
            event.accept()
            return
        # Closing the window keeps T.M.O.S running (and listening) in the tray.
        event.ignore()
        self.hide()
        if not self._told_about_tray:
            self._told_about_tray = True
            self._tray.showMessage(
                'T.M.O.S', 'Still running in the tray. Right-click the tray icon to quit.',
                QSystemTrayIcon.MessageIcon.Information, 3000
            )

    def quit_app(self):
        self._quitting = True
        self._stop_wake_listener()
        self._cancel_stream()
        tts.stop()
        if self._hotkey:
            self._hotkey.stop()
        self._tray.hide()
        QApplication.instance().quit()

    # ── Clock ─────────────────────────────────────────────────────────────────

    def _tick_clock(self):
        now = datetime.now()
        self._clock_lbl.setText(now.strftime('%H:%M:%S'))
        self._date_lbl.setText(now.strftime('%a, %d %b %Y'))

    def _set_status(self, text: str, color: str = _GREEN):
        self._status_lbl.setText(text)
        self._status_lbl.setStyleSheet(
            f'color:{color}; font-family:Consolas,monospace;'
            'font-size:11px; letter-spacing:1px;'
        )

    def _idle_status(self):
        if self._stream_worker is not None:
            self._set_status('●  PROCESSING...', _ORANGE)
        elif tts.is_speaking():
            self._set_status('●  SPEAKING', _CYAN)
        else:
            self._set_status('●  STANDING BY', _GREEN)

    # ── Threads ───────────────────────────────────────────────────────────────

    def _start_thread(self, t: QThread):
        self._threads.add(t)
        t.finished.connect(self._reap_thread)
        t.start()

    def _reap_thread(self):
        self._threads.discard(self.sender())

    def run_async(self, fn, on_done):
        """Run fn() in the background, then on_done(result) back on the UI thread."""
        w = TaskWorker(fn)
        w.result.connect(on_done)
        self._start_thread(w)

    # ── Stats ─────────────────────────────────────────────────────────────────

    def _poll_stats(self):
        if not self.isVisible():
            return
        if self._stats_worker is not None and self._stats_worker.isRunning():
            return
        self._stats_worker = StatsWorker()
        self._stats_worker.done.connect(self._on_stats)
        self._start_thread(self._stats_worker)

    def _on_stats(self, s: dict):
        self._bridge.stats_pushed.emit(json.dumps(s))
        # Warn once when a condition starts; re-arm only after it has clearly ended.
        cpu = s['cpu']['percent']
        self._warn_once('cpu', cpu > 85, cpu < 70, f'CPU high: {cpu}%')
        batt = s.get('battery')
        if batt:
            low = batt['percent'] < 15 and not batt.get('plugged')
            ok = batt['percent'] > 20 or batt.get('plugged')
            self._warn_once('battery', low, ok, f"Low battery: {batt['percent']}%")

    def _warn_once(self, key: str, active: bool, cleared: bool, text: str):
        if active and key not in self._warned:
            self._warned[key] = time.time()
            self.log(text, 'warn')
        elif cleared:
            self._warned.pop(key, None)

    # ── UI plumbing (used by commands.py and bridge.py) ───────────────────────

    def on_ui_ready(self):
        """The page finished loading — send it everything it needs."""
        from modules import ai
        b = self._bridge
        b._push_config()
        b._push_model_info()
        b._push_reminders()
        b._push_notes()
        b._push_timers()
        b.history_pushed.emit(json.dumps(history.get_chat(60)))
        self.refresh_models()
        self.refresh_location()
        self._poll_stats()
        if not ai.has_api_key():
            name = ai.BACKEND_NAMES[ai.get_backend()]
            b.notice_pushed.emit(
                f'👋 Welcome to T.M.O.S. To turn on the AI, open ⚙ **Settings** and paste a free '
                f'{name} API key. Built-in commands already work — type `help` to see them.')

    def log(self, text: str, level: str = 'sys'):
        self._bridge.log_pushed.emit(text, level)

    def reply(self, r: Reply | str, speak: str | None = None):
        if isinstance(r, str):
            r = Reply(r, speak=speak)
        if r.text:
            self._bridge.chat_pushed.emit(r.text, False)
        if r.level in ('warn', 'err') and r.text.strip():
            self.log(r.text.strip().splitlines()[0][:120], r.level)
        spoken = r.text if r.speak is None else r.speak
        if spoken:
            tts.speak(spoken)

    def refresh(self, *what: str):
        pushers = {'reminders': self._bridge._push_reminders, 'notes': self._bridge._push_notes,
                   'timers': self._bridge._push_timers, 'config': self._bridge._push_config}
        for w in what:
            pushers[w]()

    def clear_chat(self):
        history.clear_chat()
        self._bridge.chat_cleared.emit()
        self.log('Chat cleared', 'sys')

    def refresh_location(self, force: bool = False):
        """Look the location up in the background (so the AI knows it), then show it in Settings."""
        from modules import location
        if location.mode() != 'off':
            self.run_async(lambda: location.get(refresh=force), lambda _: self._bridge._push_config())

    def refresh_models(self, force: bool = False):
        from modules import ai
        backend = ai.get_backend()
        self.run_async(lambda: (backend, *ai.fetch_models(backend, refresh=force)), self._on_models)

    def _on_models(self, res):
        from modules import ai
        if not isinstance(res, tuple):
            return
        backend, models, err = res
        if backend != ai.get_backend():
            return
        old = ai.get_model(backend)
        new = ai.heal_model(backend)
        if new:
            self.log(f'{old} is no longer offered by {ai.BACKEND_NAMES[backend]} — switched to {new}', 'warn')
            self._bridge.model_pushed.emit(new)
        self._bridge.models_pushed.emit(json.dumps(
            {'backend': backend, 'current': ai.get_model(), 'models': models}))
        if err:
            self.log(err, 'warn')

    def _record_chat(self, text: str, is_user: bool):
        history.add_chat(text, is_user)

    # ── Reminder / timer callbacks ────────────────────────────────────────────

    def _alert(self, title: str, text: str, kind: str = 'alarm'):
        self._bridge.alert_pushed.emit(json.dumps({'title': title, 'text': text, 'kind': kind}))
        if not self.isVisible() or self.isMinimized() or not self.isActiveWindow():
            self._tray.showMessage(f'T.M.O.S — {title}', text,
                                   QSystemTrayIcon.MessageIcon.Information, 8000)

    def _on_reminder_fired(self, r: dict):
        title = 'Missed reminder' if r.get('missed') else 'Reminder'
        self._bridge.chat_pushed.emit(f"⏰ **{title}:** {r.get('text', '')}", False)
        self.log(f"REMINDER: {r.get('text', '')}", 'warn')
        self._alert(title, r.get('text', ''))
        tts.speak(f"Reminder: {r.get('text', '')}")
        self._bridge._push_reminders()

    def _on_timer_done(self, t: dict):
        label = t.get('label', '')
        self._bridge.chat_pushed.emit(f'⏱ **Time\'s up!** {label}', False)
        self.log(f'TIMER DONE: {label}', 'warn')
        self._alert("Time's up", label)
        tts.speak(f"Time's up. {label} timer finished.")
        self._bridge._push_timers()

    def _on_speaking(self, on: bool):
        self._bridge.speaking_pushed.emit(on)
        self._idle_status()

    # ══════════════════════════════════════════════════════════════════════════
    #  WAKE WORD LISTENER
    # ══════════════════════════════════════════════════════════════════════════

    def _start_wake_listener(self):
        if self._wake_listener and self._wake_listener.isRunning():
            return
        self._wake_listener = WakeWordListener()
        self._wake_listener.command.connect(self._on_wake_command)
        self._wake_listener.wake_fired.connect(self._on_wake_fired)
        self._wake_listener.stop_heard.connect(self.stop_all)
        self._wake_listener.status_msg.connect(lambda m: self.log(m, 'sys'))
        self._wake_listener.listening.connect(self._on_listening)
        self._start_thread(self._wake_listener)
        self.log('👂 Always-listen: ON', 'ok')

    def _stop_wake_listener(self, quiet: bool = False):
        # The old thread may still be inside a 5 s listen(); it exits on its own
        # (kept alive in self._threads), so a new listener can start right away.
        if self._wake_listener and self._wake_listener.isRunning():
            self._wake_listener.stop()
            if not quiet:
                self.log('Always-listen: OFF', 'sys')
        self._wake_listener = None

    def restart_listeners(self):
        """Pick up a new microphone choice."""
        if self._wake_listener is not None:
            self._stop_wake_listener(quiet=True)
            self._start_wake_listener()

    def _set_always_listen(self, enabled: bool):
        if enabled:
            self._start_wake_listener()
        else:
            self._stop_wake_listener()

    def _on_wake_fired(self):
        self._bridge.wake_detected.emit()
        self._set_status('●  LISTENING...', _CYAN)

    def _on_listening(self, on: bool):
        self._bridge.mic_state_pushed.emit(on)
        if not on:
            self._idle_status()

    def _on_wake_command(self, cmd: str):
        self._handle(cmd)

    # ══════════════════════════════════════════════════════════════════════════
    #  COMMAND HANDLER
    # ══════════════════════════════════════════════════════════════════════════

    def _handle(self, cmd: str):
        cmd = cmd.strip()
        if not cmd:
            return
        if commands.is_stop(cmd):
            self.stop_all()
            return

        self._bridge.chat_pushed.emit(cmd, True)
        self.log(f'CMD: {cmd}', 'sys')
        tts.stop()                         # a new request interrupts the old answer
        try:
            if commands.dispatch(self, cmd):
                return
        except Exception as e:
            self.reply(Reply(f'That command failed: {e}', speak='That command failed.', level='err'))
            return
        self._ask_ai(cmd)

    def stop_all(self):
        """Stop talking and cancel the answer in progress ("stop", Esc, Stop button)."""
        busy = self._stream_worker is not None
        spoke = tts.stop()
        self._cancel_stream()
        if busy or spoke:
            self.log('Stopped', 'sys')
        self._idle_status()

    # ── AI streaming ──────────────────────────────────────────────────────────

    def _ask_ai(self, prompt: str):
        self._cancel_stream()
        self._set_status('●  PROCESSING...', _ORANGE)
        self._bridge.thinking_on.emit()
        self._bridge.busy_pushed.emit(True)
        w = AIStreamWorker(prompt)
        w.token.connect(self._on_ai_token)
        w.tool_used.connect(self._on_ai_tool)
        w.reply_done.connect(self._on_ai_done)
        self._stream_worker = w
        self._stream_reply = ''
        self._start_thread(w)

    def _cancel_stream(self):
        w = self._stream_worker
        if w is None:
            return
        w.cancel()
        for sig in (w.token, w.tool_used, w.reply_done):
            try:
                sig.disconnect()
            except TypeError:
                pass
        if self._stream_reply.strip():
            history.add_chat(self._stream_reply.strip() + ' …', False)
        self._stream_worker = None
        self._stream_reply = ''
        self._bridge.ai_stream_done.emit()
        self._bridge.thinking_off.emit()
        self._bridge.busy_pushed.emit(False)

    def _on_ai_token(self, token: str):
        self._stream_reply += token
        self._bridge.ai_token.emit(token)

    def _on_ai_tool(self, payload: str):
        self._bridge.tool_pushed.emit(payload)
        info = json.loads(payload)
        self.log(f"🔧 {info['label']}{': ' + info['detail'] if info['detail'] else ''}",
                 'ok' if info['ok'] else 'err')
        refresh = {'set_reminder': 'reminders', 'add_note': 'notes', 'start_timer': 'timers',
                   'remember_fact': 'config', 'forget_fact': 'config', 'get_location': 'config'}
        if info['name'] in refresh:
            self.refresh(refresh[info['name']])

    def _on_ai_done(self, reply: str, cancelled: bool):
        self._stream_worker = None
        self._stream_reply = ''
        self._bridge.ai_stream_done.emit()
        self._bridge.thinking_off.emit()
        self._bridge.busy_pushed.emit(False)
        if not cancelled and reply.strip():
            history.add_chat(reply.strip(), False)
            self.log('AI response received', 'ok')
            tts.speak(reply)
        self._idle_status()

    # ── Voice (manual mic button) ─────────────────────────────────────────────

    def _toggle_mic(self):
        if self._voice_worker and self._voice_worker.isRunning():
            return
        tts.stop()
        self._bridge.mic_state_pushed.emit(True)
        self._set_status('●  LISTENING...', _CYAN)
        self._voice_worker = VoiceWorker()
        self._voice_worker.result.connect(self._on_voice)
        self._voice_worker.error.connect(lambda e: self.log(f'Voice: {e}', 'warn'))
        self._voice_worker.finished.connect(self._on_voice_done)
        self._start_thread(self._voice_worker)

    def _on_voice(self, text: str):
        self._handle(text)

    def _on_voice_done(self):
        self._bridge.mic_state_pushed.emit(False)
        self._idle_status()


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def _already_running() -> bool:
    """If T.M.O.S is already open, ask it to show itself and return True."""
    sock = QLocalSocket()
    sock.connectToServer(_INSTANCE_NAME)
    if sock.waitForConnected(300):
        sock.write(b'show')
        sock.flush()
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        return True
    return False


def main():
    # If Qt/Chromium ever crashes hard, leave a Python traceback of every thread behind.
    import faulthandler
    global _crash_log
    _crash_log = open(os.path.join(config.DATA_DIR, 'crash.log'), 'a', encoding='utf-8')
    _crash_log.write(f'\n=== T.M.O.S started {datetime.now():%Y-%m-%d %H:%M:%S} ===\n')
    _crash_log.flush()
    faulthandler.enable(_crash_log, all_threads=True)

    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('tmos.desktop.assistant')

    app = QApplication(sys.argv)
    app.setApplicationName('T.M.O.S')
    app.setStyle('Fusion')
    app.setQuitOnLastWindowClosed(False)      # closing the window hides it to the tray

    if _already_running():
        return 0

    from PyQt6.QtGui import QPalette
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window,     QColor(_DARK))
    pal.setColor(QPalette.ColorRole.WindowText, QColor('#b0d8e8'))
    pal.setColor(QPalette.ColorRole.Base,       QColor('#040f1e'))
    pal.setColor(QPalette.ColorRole.Text,       QColor('#b0d8e8'))
    app.setPalette(pal)

    win = TmosWindow()

    server = QLocalServer()
    QLocalServer.removeServer(_INSTANCE_NAME)
    server.listen(_INSTANCE_NAME)
    server.newConnection.connect(lambda: (server.nextPendingConnection(), win._show_and_raise()))

    if '--minimized' in sys.argv:
        win._tray.showMessage('T.M.O.S', 'Running in the tray.',
                              QSystemTrayIcon.MessageIcon.Information, 2500)
    else:
        win.show()
    code = app.exec()
    # Mic/network threads can be blocked in a read; don't wait for them on exit.
    for s in (sys.stdout, sys.stderr):
        if s is not None:
            s.flush()
    os._exit(code)


if __name__ == '__main__':
    sys.exit(main())
