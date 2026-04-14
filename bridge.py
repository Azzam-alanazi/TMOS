"""T.M.O.S — Bridge (Python ↔ JavaScript via QWebChannel)
Handles all bidirectional communication between the PyQt backend and the HTML UI.
"""

import json
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot


class Bridge(QObject):
    """Bidirectional Python ↔ JavaScript channel via QWebChannel."""

    # ── Signals: Python → JavaScript ─────────────────────────────────────────
    stats_pushed     = pyqtSignal(str)
    chat_pushed      = pyqtSignal(str, bool)
    log_pushed       = pyqtSignal(str, str)
    thinking_on      = pyqtSignal()
    thinking_off     = pyqtSignal()
    reminders_pushed = pyqtSignal(str)
    reminder_fired   = pyqtSignal(str)
    mic_state_pushed = pyqtSignal(bool)
    notes_pushed     = pyqtSignal(str)
    model_pushed     = pyqtSignal(str)
    ai_token         = pyqtSignal(str)
    ai_stream_done   = pyqtSignal()
    config_pushed    = pyqtSignal(str)        # full config JSON
    backend_pushed   = pyqtSignal(str)        # 'gemini' | 'ollama'
    wake_detected    = pyqtSignal()           # wake word detected — orb should activate
    speaking_pushed  = pyqtSignal(bool)       # TTS speaking (for web animation)

    def __init__(self, window):
        super().__init__()
        self._win = window

    # ── Slots: JavaScript → Python ────────────────────────────────────────────

    @pyqtSlot(str)
    def send_command(self, cmd: str):
        self._win._handle(cmd)

    @pyqtSlot(str, str)
    def add_reminder(self, time_str: str, text: str):
        from modules import reminders as rem
        rem.add({'time': time_str, 'text': text})
        self._push_reminders()

    @pyqtSlot(str)
    def del_reminder(self, rid: str):
        from modules import reminders as rem
        rem.remove(rid)
        self._push_reminders()

    @pyqtSlot()
    def toggle_mic(self):
        self._win._toggle_mic()

    @pyqtSlot(str)
    def add_note(self, text: str):
        from modules import notes
        notes.add(text)
        self._push_notes()

    @pyqtSlot(str)
    def del_note(self, nid: str):
        from modules import notes
        notes.remove(nid)
        self._push_notes()

    @pyqtSlot(str)
    def switch_model(self, model: str):
        from modules import ai
        result = ai.set_model(model)
        if result['success']:
            info = result['info']
            self.model_pushed.emit(model)
            self.log_pushed.emit(f'Model → {info["name"]}', 'ok')
            self.chat_pushed.emit(f'Switched to **{info["name"]}** — {info["desc"]}', False)
        else:
            self.chat_pushed.emit(result['message'], False)
            self.log_pushed.emit(result['message'], 'err')

    @pyqtSlot(str)
    def switch_backend(self, backend: str):
        from modules import ai
        result = ai.set_backend(backend)
        if result['success']:
            self.backend_pushed.emit(backend)
            self.model_pushed.emit(ai.get_model())
            self.log_pushed.emit(f'Backend → {backend.upper()}', 'ok')
            self.chat_pushed.emit(
                f'Switched to **{backend.upper()}** backend.',
                False,
            )
            self._push_config()
        else:
            self.chat_pushed.emit(result['message'], False)

    @pyqtSlot(str)
    def save_api_key(self, key: str):
        """Save key for the currently-active backend."""
        from modules import ai
        result = ai.set_api_key(key)
        if result['success']:
            self.log_pushed.emit(result['message'], 'ok')
            self.chat_pushed.emit(result['message'], False)
            self._push_config()
        else:
            self.chat_pushed.emit(result['message'], False)

    @pyqtSlot(str)
    def save_gemini_key(self, key: str):
        from modules import ai
        r = ai.set_gemini_key(key)
        self.log_pushed.emit(r['message'], 'ok' if r['success'] else 'err')
        self.chat_pushed.emit(r['message'], False)
        self._push_config()

    @pyqtSlot(str)
    def save_groq_key(self, key: str):
        from modules import ai
        r = ai.set_groq_key(key)
        self.log_pushed.emit(r['message'], 'ok' if r['success'] else 'err')
        self.chat_pushed.emit(r['message'], False)
        self._push_config()

    @pyqtSlot(bool)
    def toggle_always_listen(self, enabled: bool):
        from modules import config
        config.set_value('always_listen', enabled)
        self._win._set_always_listen(enabled)
        self.log_pushed.emit(f'Always-listen {"enabled" if enabled else "disabled"}', 'sys')
        self._push_config()

    @pyqtSlot(str)
    def set_wake_word(self, word: str):
        from modules import config
        word = word.strip().lower() or 'tmos'
        config.set_value('wake_word', word)
        self.log_pushed.emit(f'Wake word set to "{word}"', 'sys')
        self._push_config()

    @pyqtSlot()
    def clear_ai_history(self):
        from modules import ai
        ai.clear_history()
        self.log_pushed.emit('AI conversation history cleared', 'sys')
        self.chat_pushed.emit('Memory cleared. Starting fresh conversation.', False)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _push_reminders(self):
        from modules import reminders as rem
        self.reminders_pushed.emit(json.dumps(rem.get_all()))

    def _push_notes(self):
        from modules import notes
        self.notes_pushed.emit(json.dumps(notes.get_all()))

    def _push_model_info(self):
        from modules import ai
        self.model_pushed.emit(ai.get_model())
        self.backend_pushed.emit(ai.get_backend())

    def _push_config(self):
        from modules import config
        self.config_pushed.emit(json.dumps(config.get_all()))
