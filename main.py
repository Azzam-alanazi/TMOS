#!/usr/bin/env python3
"""T.M.O.S — Personal AI Desktop Assistant
Dual AI backend (Gemini + Ollama), always-on wake-word listening,
web-animated orb, streaming responses, full command suite.
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from urllib.parse import quote_plus

from PyQt6.QtCore import QPoint, QThread, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (
    QBrush, QColor, QIcon, QPainter, QPen, QPixmap, QPolygon,
)
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMainWindow, QMenu,
    QPushButton, QSystemTrayIcon, QVBoxLayout, QWidget,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebChannel import QWebChannel

from bridge import Bridge
from modules import tts, config

# ── Colour palette (title bar only — content is HTML) ────────────────────────
_DARK   = '#020b14'
_CYAN   = '#00e5ff'
_GREEN  = '#00ff88'
_ORANGE = '#ffaa00'
_RED    = '#ff4444'
_DIM    = '#7ab8cc'
_BORDER = '#0d3a52'


# ══════════════════════════════════════════════════════════════════════════════
#  BACKGROUND WORKERS
# ══════════════════════════════════════════════════════════════════════════════

class AIStreamWorker(QThread):
    """Streaming AI request — emits tokens as they arrive."""
    token = pyqtSignal(str)
    done  = pyqtSignal()

    def __init__(self, prompt: str):
        super().__init__()
        self.prompt = prompt

    def run(self):
        from modules import ai
        for chunk in ai.ask_stream(self.prompt):
            self.token.emit(chunk)
        self.done.emit()


class StatsWorker(QThread):
    done = pyqtSignal(dict)

    def run(self):
        from modules import system_info
        self.done.emit(system_info.get_stats())


class VoiceWorker(QThread):
    """One-shot voice recognition (legacy, for mic button)."""
    result = pyqtSignal(str)
    error  = pyqtSignal(str)

    def run(self):
        try:
            import speech_recognition as sr
            rec = sr.Recognizer()
            with sr.Microphone() as src:
                rec.adjust_for_ambient_noise(src, duration=0.3)
                audio = rec.listen(src, timeout=6, phrase_time_limit=12)
            self.result.emit(rec.recognize_google(audio))
        except Exception as e:
            self.error.emit(str(e))


class WakeWordListener(QThread):
    """Continuous wake-word listener.
    Runs in a loop, listening for short audio clips, and emits the command
    text after the wake word is detected (e.g. "TMOS what time is it").
    """
    command       = pyqtSignal(str)   # text after wake word
    wake_fired    = pyqtSignal()      # wake word detected (for UI animation)
    status_msg    = pyqtSignal(str)   # status updates
    listening     = pyqtSignal(bool)  # listening state

    def __init__(self):
        super().__init__()
        self._running = False

    def stop(self):
        self._running = False

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
        self.status_msg.emit('Wake-word listener started.')

        try:
            mic = sr.Microphone()
            with mic as src:
                rec.adjust_for_ambient_noise(src, duration=1.0)
        except Exception as e:
            self.status_msg.emit(f'Mic init error: {e}')
            return

        while self._running:
            try:
                with mic as src:
                    self.listening.emit(True)
                    audio = rec.listen(src, timeout=5, phrase_time_limit=8)
                    self.listening.emit(False)

                try:
                    text = rec.recognize_google(audio).lower().strip()
                except sr.UnknownValueError:
                    continue
                except sr.RequestError:
                    time.sleep(1)
                    continue

                if not text:
                    continue

                # Check for wake word
                wake = config.get('wake_word', 'tmos').lower()
                # Allow variants: "tmos", "hey tmos", "t mos", "temos"
                variants = [wake, f'hey {wake}', 't mos', 't.m.o.s', 'temos', 'team os']

                matched = None
                for v in variants:
                    if v in text:
                        matched = v
                        break

                if matched:
                    self.wake_fired.emit()
                    # Extract command after wake word
                    idx = text.find(matched)
                    cmd = text[idx + len(matched):].strip()
                    cmd = cmd.lstrip(',.!? ').strip()

                    if cmd:
                        # Wake word + command in same utterance
                        self.command.emit(cmd)
                    else:
                        # Just the wake word — listen for follow-up
                        self.status_msg.emit('Wake detected. Listening for command...')
                        try:
                            with mic as src:
                                audio2 = rec.listen(src, timeout=4, phrase_time_limit=10)
                            try:
                                cmd2 = rec.recognize_google(audio2).strip()
                                if cmd2:
                                    self.command.emit(cmd2)
                            except sr.UnknownValueError:
                                pass
                        except sr.WaitTimeoutError:
                            pass

            except sr.WaitTimeoutError:
                continue
            except Exception as e:
                self.status_msg.emit(f'Listener error: {e}')
                time.sleep(0.5)

        self.listening.emit(False)
        self.status_msg.emit('Wake-word listener stopped.')


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN WINDOW
# ══════════════════════════════════════════════════════════════════════════════

class TmosWindow(QMainWindow):
    _reminder_signal = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setMinimumSize(1100, 700)
        self.resize(1280, 800)
        self.setWindowTitle('T.M.O.S')

        self._drag_pos:       QPoint | None          = None
        self._stream_worker:  AIStreamWorker | None  = None
        self._stream_reply:   str                     = ''
        self._voice_worker:   VoiceWorker | None     = None
        self._stats_worker:   StatsWorker | None     = None
        self._wake_listener:  WakeWordListener | None = None

        # ── Build shell ───────────────────────────────────────────────────────
        root = QWidget()
        root.setObjectName('root')
        self.setCentralWidget(root)
        vb = QVBoxLayout(root)
        vb.setContentsMargins(0, 0, 0, 0)
        vb.setSpacing(0)

        vb.addWidget(self._make_titlebar())

        self._view = QWebEngineView()
        self._view.setStyleSheet(f'background:{_DARK};')
        vb.addWidget(self._view, 1)

        # ── WebChannel bridge ─────────────────────────────────────────────────
        self._bridge  = Bridge(self)
        self._channel = QWebChannel()
        self._channel.registerObject('bridge', self._bridge)
        self._view.page().setWebChannel(self._channel)

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
        QTimer.singleShot(2500, self._poll_stats)

        # ── Reminders ─────────────────────────────────────────────────────────
        self._reminder_signal.connect(self._on_reminder_fired)
        from modules import reminders as rem
        rem.init(callback=lambda r: self._reminder_signal.emit(r))
        QTimer.singleShot(2500, self._bridge._push_reminders)

        # ── Notes / config ────────────────────────────────────────────────────
        QTimer.singleShot(2600, self._bridge._push_notes)
        QTimer.singleShot(2700, self._bridge._push_model_info)
        QTimer.singleShot(2800, self._bridge._push_config)

        # ── System tray ───────────────────────────────────────────────────────
        self._setup_tray()

        # ── TTS preload ───────────────────────────────────────────────────────
        tts.preload()

        # ── Start always-listen if enabled ────────────────────────────────────
        if config.get('always_listen'):
            QTimer.singleShot(3000, self._start_wake_listener)

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

        for txt, slot, oid in [('─', self.showMinimized, 'btn_min'),
                                ('□', self._toggle_max,  'btn_max'),
                                ('✕', self.close,        'btn_close')]:
            btn = QPushButton(txt)
            btn.setObjectName(oid)
            btn.setFixedSize(30, 30)
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
        """)

    # ── Drag ──────────────────────────────────────────────────────────────────

    def _tb_press(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _tb_move(self, e):
        if e.buttons() == Qt.MouseButton.LeftButton and self._drag_pos:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def _toggle_max(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def _toggle_mute(self):
        muted = tts.toggle_mute()
        self._mute_btn.setText('🔇' if muted else '🔊')
        self._bridge.log_pushed.emit('Voice muted' if muted else 'Voice unmuted', 'sys')

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
        menu.addSeparator()
        menu.addAction('Quit', QApplication.instance().quit)
        self._tray.setContextMenu(menu)
        self._tray.setToolTip('T.M.O.S — AI Desktop Assistant')
        self._tray.activated.connect(
            lambda r: self._show_and_raise()
            if r == QSystemTrayIcon.ActivationReason.DoubleClick else None
        )
        self._tray.show()

    def _show_and_raise(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def closeEvent(self, event):
        # Stop wake listener cleanly
        if self._wake_listener and self._wake_listener.isRunning():
            self._wake_listener.stop()
            self._wake_listener.wait(2000)
        event.ignore()
        self.hide()
        self._tray.showMessage(
            'T.M.O.S', 'Running in the background.',
            QSystemTrayIcon.MessageIcon.Information, 2000
        )

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

    # ── Stats ─────────────────────────────────────────────────────────────────

    def _poll_stats(self):
        self._stats_worker = StatsWorker()
        self._stats_worker.done.connect(self._on_stats)
        self._stats_worker.start()

    def _on_stats(self, s: dict):
        self._bridge.stats_pushed.emit(json.dumps(s))
        if s['cpu']['percent'] > 85:
            self._bridge.log_pushed.emit(f"CPU spike: {s['cpu']['percent']}%", 'warn')
        batt = s.get('battery')
        if batt and batt['percent'] < 15 and not batt.get('plugged'):
            self._bridge.log_pushed.emit(f"Low battery: {batt['percent']}%", 'warn')

    # ── Reminder callback ─────────────────────────────────────────────────────

    def _on_reminder_fired(self, r: dict):
        self._bridge.reminder_fired.emit(json.dumps(r))
        tts.speak(f"Reminder: {r.get('text', '')}")

    # ══════════════════════════════════════════════════════════════════════════
    #  WAKE WORD LISTENER
    # ══════════════════════════════════════════════════════════════════════════

    def _start_wake_listener(self):
        if self._wake_listener and self._wake_listener.isRunning():
            return
        self._wake_listener = WakeWordListener()
        self._wake_listener.command.connect(self._on_wake_command)
        self._wake_listener.wake_fired.connect(self._on_wake_fired)
        self._wake_listener.status_msg.connect(
            lambda m: self._bridge.log_pushed.emit(m, 'sys')
        )
        self._wake_listener.listening.connect(
            lambda on: self._bridge.mic_state_pushed.emit(on)
        )
        self._wake_listener.start()
        self._bridge.log_pushed.emit('👂 Always-listen: ON', 'ok')

    def _stop_wake_listener(self):
        if self._wake_listener and self._wake_listener.isRunning():
            self._wake_listener.stop()
            self._wake_listener.wait(2000)
            self._bridge.log_pushed.emit('Always-listen: OFF', 'sys')

    def _set_always_listen(self, enabled: bool):
        if enabled:
            self._start_wake_listener()
        else:
            self._stop_wake_listener()

    def _on_wake_fired(self):
        self._bridge.wake_detected.emit()
        self._set_status('●  LISTENING...', _CYAN)

    def _on_wake_command(self, cmd: str):
        js = f"document.getElementById('cmd-input').value={json.dumps(cmd)};"
        self._view.page().runJavaScript(js)
        self._handle(cmd)

    # ══════════════════════════════════════════════════════════════════════════
    #  COMMAND HANDLER
    # ══════════════════════════════════════════════════════════════════════════

    def _handle(self, cmd: str):
        lower = cmd.lower().strip()
        self._bridge.chat_pushed.emit(cmd, True)
        self._bridge.log_pushed.emit(f'CMD: {cmd}', 'sys')

        # ── "open chrome and search [for] X" ─────────────────────────────
        m = re.match(r'open\s+\w+\s+and\s+search(?:\s+for)?\s+(.+)', lower)
        if m:
            query = m.group(1).strip()
            from modules import apps
            apps.open_browser(f'https://www.google.com/search?q={quote_plus(query)}')
            reply = f'Searching Google for: {query}'
            self._bridge.chat_pushed.emit(reply, False)
            tts.speak(reply)
            return

        # ── "search [for] X" ─────────────────────────────────────────────
        m = re.match(r'search(?:\s+for)?\s+(.+)', lower)
        if m:
            query = m.group(1).strip()
            from modules import apps
            apps.open_browser(f'https://www.google.com/search?q={quote_plus(query)}')
            reply = f'Searching Google for: {query}'
            self._bridge.chat_pushed.emit(reply, False)
            tts.speak(reply)
            return

        # ── YouTube search ───────────────────────────────────────────────
        m = re.match(r'(?:youtube|yt)\s+(.+)', lower)
        if m:
            query = m.group(1).strip()
            from modules import apps
            apps.open_browser(f'https://www.youtube.com/results?search_query={quote_plus(query)}')
            reply = f'Searching YouTube for: {query}'
            self._bridge.chat_pushed.emit(reply, False)
            tts.speak(reply)
            return

        # ── open app ─────────────────────────────────────────────────────
        if lower.startswith('open '):
            name = lower[5:].strip()
            from modules import apps
            r = apps.open_app(name)
            self._bridge.chat_pushed.emit(r['message'], False)
            self._bridge.log_pushed.emit(r['message'], 'ok' if r['success'] else 'err')
            tts.speak(r['message'])

        # ── browse URL ───────────────────────────────────────────────────
        elif lower.startswith('browse ') or lower.startswith('goto '):
            url = lower.split(' ', 1)[1].strip()
            from modules import apps
            apps.open_browser(url)
            self._bridge.chat_pushed.emit(f'Opening {url}...', False)
            tts.speak(f'Opening {url}')

        # ── calculator ───────────────────────────────────────────────────
        elif lower.startswith('calc ') or lower.startswith('calculate '):
            expr = cmd.split(' ', 1)[1].strip()
            from modules.tools import calculate
            r = calculate(expr)
            reply = f'**{expr}** = `{r["result"]}`' if r['success'] else r['message']
            self._bridge.chat_pushed.emit(reply, False)
            tts.speak(f'{expr} equals {r["result"]}' if r['success'] else reply)

        # ── timer / stopwatch ────────────────────────────────────────────
        elif lower.startswith('timer ') or lower.startswith('stopwatch'):
            self._handle_timer(lower)

        # ── screenshot ───────────────────────────────────────────────────
        elif lower in ('screenshot', 'ss', 'screen capture'):
            from modules.tools import take_screenshot
            r = take_screenshot()
            reply = r.get('message', 'Screenshot failed.')
            self._bridge.chat_pushed.emit(reply, False)
            tts.speak('Screenshot taken.' if r['success'] else 'Screenshot failed.')

        # ── clipboard ────────────────────────────────────────────────────
        elif lower in ('clipboard', 'paste', 'get clipboard'):
            from modules.tools import get_clipboard
            r = get_clipboard()
            if r['success']:
                text = r['text'][:200] + ('...' if len(r.get('text', '')) > 200 else '')
                reply = f'Clipboard: {text}' if text else 'Clipboard is empty.'
            else:
                reply = r['message']
            self._bridge.chat_pushed.emit(reply, False)

        elif lower.startswith('copy '):
            text = cmd[5:].strip()
            from modules.tools import set_clipboard
            r = set_clipboard(text)
            self._bridge.chat_pushed.emit(r['message'], False)
            tts.speak(r['message'])

        # ── notes ────────────────────────────────────────────────────────
        elif lower.startswith('note ') or lower.startswith('add note '):
            text = re.sub(r'^(add\s+)?note\s+', '', cmd, flags=re.IGNORECASE).strip()
            from modules import notes
            r = notes.add(text)
            reply = f'Note saved: {text[:60]}' if r['success'] else r['message']
            self._bridge.chat_pushed.emit(reply, False)
            self._bridge._push_notes()
            tts.speak('Note saved.')

        elif lower in ('notes', 'list notes', 'show notes'):
            from modules import notes
            all_notes = notes.get_all()
            if all_notes:
                lines = [f'• **{n["title"]}** ({n["created"]})' for n in all_notes[-5:]]
                reply = 'Recent notes:\n' + '\n'.join(lines)
            else:
                reply = 'No notes yet. Use `note [text]` to save one.'
            self._bridge.chat_pushed.emit(reply, False)

        # ── system power commands ────────────────────────────────────────
        elif lower in ('lock', 'lock pc', 'lock computer'):
            from modules.tools import system_command
            r = system_command('lock')
            self._bridge.chat_pushed.emit(r['message'], False)

        elif lower in ('sleep', 'sleep pc'):
            from modules.tools import system_command
            r = system_command('sleep')
            self._bridge.chat_pushed.emit(r['message'], False)

        elif lower.startswith('shutdown'):
            from modules.tools import system_command
            r = system_command('shutdown')
            self._bridge.chat_pushed.emit(r['message'], False)
            tts.speak(r['message'])

        elif lower.startswith('restart'):
            from modules.tools import system_command
            r = system_command('restart')
            self._bridge.chat_pushed.emit(r['message'], False)
            tts.speak(r['message'])

        elif lower in ('cancel shutdown', 'cancel restart'):
            from modules.tools import system_command
            r = system_command('cancel')
            self._bridge.chat_pushed.emit(r['message'], False)

        # ── model switching ──────────────────────────────────────────────
        elif lower.startswith('model '):
            model = lower.split(' ', 1)[1].strip()
            if model == 'list':
                from modules import ai
                models = ai.get_available_models()
                current = ai.get_model()
                lines = []
                for k, v in models.items():
                    marker = ' ◄' if k == current else ''
                    lines.append(f'• `{k}` — {v["name"]} ({v["desc"]}){marker}')
                self._bridge.chat_pushed.emit(f'**{ai.get_backend().upper()} models:**\n' + '\n'.join(lines), False)
            else:
                self._bridge.switch_model(model)

        # ── backend switching ────────────────────────────────────────────
        elif lower in ('use gemini', 'switch to gemini'):
            self._bridge.switch_backend('gemini')
        elif lower in ('use ollama', 'switch to ollama', 'use local'):
            self._bridge.switch_backend('ollama')

        # ── clear history ────────────────────────────────────────────────
        elif lower in ('clear memory', 'clear history', 'forget', 'reset'):
            self._bridge.clear_ai_history()

        # ── help ─────────────────────────────────────────────────────────
        elif lower in ('help', 'commands'):
            reply = (
                '**Commands:**\n'
                '`open [app]` — Chrome, VSCode, Spotify, Discord…\n'
                '`search X` — Google search  |  `youtube X` — YouTube search\n'
                '`browse [url]` — open a website\n'
                '`stats` / `files` — system info / home directory\n'
                '`calc [expr]` — calculator (e.g. `calc 2^10`)\n'
                '`screenshot` / `clipboard` / `copy [text]`\n'
                '`note [text]` / `notes`\n'
                '`timer start/stop/check`\n'
                '`remind me at HH:MM to X`\n'
                '`use gemini` / `use ollama` — switch AI backend\n'
                '`model [name]` / `model list`\n'
                '`clear memory` — reset AI conversation\n'
                '`lock` / `sleep` / `shutdown` / `restart`\n'
                '`weather [city]` / `clear` / `help`'
            )
            self._bridge.chat_pushed.emit(reply, False)
            tts.speak('Here are the available commands.')

        # ── clear chat ───────────────────────────────────────────────────
        elif lower == 'clear':
            self._view.page().runJavaScript(
                "document.getElementById('chat').innerHTML='';"
            )
            self._bridge.log_pushed.emit('Chat cleared', 'sys')

        # ── weather ──────────────────────────────────────────────────────
        elif lower.startswith('weather'):
            city = lower.split(' ', 1)[1].strip() if ' ' in lower else ''
            from modules import apps
            apps.open_browser(f'https://wttr.in/{city}' if city else 'https://wttr.in/')
            reply = f'Opening weather{"for " + city if city else ""}.'
            self._bridge.chat_pushed.emit(reply, False)
            tts.speak(reply)

        # ── stats ────────────────────────────────────────────────────────
        elif lower == 'stats':
            from modules import system_info
            s = system_info.get_stats()
            c, r, d = s['cpu'], s['ram'], s['disk']
            batt = s.get('battery')
            freq = c.get('freq_mhz')
            freq_str = f' @ {freq}MHz' if freq else ''
            display = (
                f"**CPU** {c['percent']}% ({c['cores']}C/{c['threads']}T{freq_str})\n"
                f"**RAM** {r['used']}/{r['total']} GB ({r['percent']}%)\n"
                f"**Disk** {d['used']}/{d['total']} GB ({d['percent']}%)"
            )
            if batt:
                plug = '⚡' if batt['plugged'] else '🔋'
                display += f"\n{plug} **Battery** {batt['percent']}%"
            self._bridge.chat_pushed.emit(display, False)
            tts.speak(f"CPU {c['percent']} percent. RAM {r['used']} of {r['total']} gigabytes.")

        # ── files ────────────────────────────────────────────────────────
        elif lower == 'files':
            from modules import files
            r = files.list_dir()
            items = r.get('items', [])
            names = ', '.join(i['name'] for i in items[:10])
            suffix = f'... ({len(items)} total)' if len(items) > 10 else ''
            self._bridge.chat_pushed.emit(f'**Home:** {names}{suffix}', False)

        # ── reminder (voice/text) ─────────────────────────────────────────
        elif 'remind me' in lower:
            m = re.search(r'at\s+(\d{1,2}:\d{2})\s+to\s+(.+)', cmd, re.IGNORECASE)
            if m:
                t, text = m.group(1), m.group(2).strip()
                from modules import reminders as rem
                result = rem.add({'time': t, 'text': text})
                if result['success']:
                    reply = f'Reminder set for **{t}**: {text}'
                    self._bridge.chat_pushed.emit(reply, False)
                    self._bridge._push_reminders()
                    tts.speak(f'Reminder set for {t}: {text}')
                else:
                    self._bridge.chat_pushed.emit(result['message'], False)
            else:
                self._bridge.chat_pushed.emit('Usage: `remind me at HH:MM to [task]`', False)

        # ── AI fallback (streaming) ──────────────────────────────────────
        else:
            self._set_status('●  PROCESSING...', _ORANGE)
            self._bridge.thinking_on.emit()
            self._stream_worker = AIStreamWorker(cmd)
            self._stream_reply = ''
            self._stream_worker.token.connect(self._on_ai_token)
            self._stream_worker.done.connect(self._on_ai_stream_done)
            self._stream_worker.start()

    # ── Timer handler ─────────────────────────────────────────────────────────

    def _handle_timer(self, lower: str):
        from modules.tools import start_timer, stop_timer, check_timer
        parts = lower.split()
        action = parts[1] if len(parts) > 1 else 'start'
        name = parts[2] if len(parts) > 2 else 'default'

        if action == 'start':
            r = start_timer(name)
        elif action in ('stop', 'end'):
            r = stop_timer(name)
            if r['success']:
                r['message'] = f'Timer "{name}" stopped at {r["elapsed"]:.1f}s'
        elif action in ('check', 'status'):
            r = check_timer(name)
            if r['success']:
                r['message'] = f'Timer "{name}": {r["elapsed"]:.1f}s elapsed'
                if 'remaining' in r:
                    r['message'] += f', {r["remaining"]:.1f}s remaining'
        else:
            r = {'success': False, 'message': 'Usage: `timer start/stop/check [name]`'}

        self._bridge.chat_pushed.emit(r.get('message', 'Timer error.'), False)

    # ── AI callbacks ──────────────────────────────────────────────────────────

    def _on_ai_token(self, token: str):
        self._stream_reply += token
        self._bridge.ai_token.emit(token)

    def _on_ai_stream_done(self):
        self._bridge.ai_stream_done.emit()
        self._bridge.thinking_off.emit()
        self._bridge.log_pushed.emit('AI response received', 'ok')
        self._set_status('●  STANDING BY', _GREEN)
        # Signal "speaking" for orb animation
        self._bridge.speaking_pushed.emit(True)
        tts.speak(self._stream_reply)
        # Estimate speaking duration to toggle off
        dur_ms = max(2000, len(self._stream_reply) * 55)
        QTimer.singleShot(dur_ms, lambda: self._bridge.speaking_pushed.emit(False))
        self._stream_reply = ''

    # ── Voice (manual mic button — legacy fallback) ───────────────────────────

    def _toggle_mic(self):
        if self._voice_worker and self._voice_worker.isRunning():
            return
        self._bridge.mic_state_pushed.emit(True)
        self._set_status('●  LISTENING...', _CYAN)
        self._voice_worker = VoiceWorker()
        self._voice_worker.result.connect(self._on_voice)
        self._voice_worker.error.connect(
            lambda e: self._bridge.log_pushed.emit(f'Voice: {e}', 'warn')
        )
        self._voice_worker.finished.connect(self._on_voice_done)
        self._voice_worker.start()

    def _on_voice(self, text: str):
        js = f"document.getElementById('cmd-input').value={json.dumps(text)};"
        self._view.page().runJavaScript(js)
        self._handle(text)

    def _on_voice_done(self):
        self._bridge.mic_state_pushed.emit(False)
        self._set_status('●  STANDING BY', _GREEN)


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    app = QApplication(sys.argv)
    app.setApplicationName('T.M.O.S')
    app.setStyle('Fusion')

    from PyQt6.QtGui import QPalette
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window,     QColor(_DARK))
    pal.setColor(QPalette.ColorRole.WindowText, QColor('#b0d8e8'))
    pal.setColor(QPalette.ColorRole.Base,       QColor('#040f1e'))
    pal.setColor(QPalette.ColorRole.Text,       QColor('#b0d8e8'))
    app.setPalette(pal)

    win = TmosWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
