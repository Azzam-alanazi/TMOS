"""T.M.O.S — Bridge (Python ↔ JavaScript via QWebChannel)
Handles all bidirectional communication between the PyQt backend and the HTML UI.
"""

import json
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot


class Bridge(QObject):
    """Bidirectional Python ↔ JavaScript channel via QWebChannel."""

    # ── Signals: Python → JavaScript ─────────────────────────────────────────
    stats_pushed     = pyqtSignal(str)
    chat_pushed      = pyqtSignal(str, bool)   # (markdown text, is_user)
    notice_pushed    = pyqtSignal(str)         # one-off system hint (not saved to history)
    history_pushed   = pyqtSignal(str)         # previous session's chat, on startup
    chat_cleared     = pyqtSignal()
    log_pushed       = pyqtSignal(str, str)
    thinking_on      = pyqtSignal()
    thinking_off     = pyqtSignal()
    busy_pushed      = pyqtSignal(bool)        # AI is answering (Send ↔ Stop)
    reminders_pushed = pyqtSignal(str)
    timers_pushed    = pyqtSignal(str)
    alert_pushed     = pyqtSignal(str)         # {"title", "text", "kind"} toast + chime
    mic_state_pushed = pyqtSignal(bool)
    notes_pushed     = pyqtSignal(str)
    model_pushed     = pyqtSignal(str)
    models_pushed    = pyqtSignal(str)         # {"backend", "current", "models": [...]}
    ai_token         = pyqtSignal(str)
    ai_stream_done   = pyqtSignal()
    tool_pushed      = pyqtSignal(str)         # {"label", "detail", "ok"} — AI used a tool
    config_pushed    = pyqtSignal(str)         # full config JSON (secrets masked)
    backend_pushed   = pyqtSignal(str)         # 'groq' | 'gemini' | 'ollama'
    wake_detected    = pyqtSignal()            # wake word detected — orb should activate
    speaking_pushed  = pyqtSignal(bool)        # TTS really speaking (for web animation)
    focus_input      = pyqtSignal()

    def __init__(self, window):
        super().__init__()
        self._win = window

    # ── Slots: JavaScript → Python ────────────────────────────────────────────

    @pyqtSlot()
    def ui_ready(self):
        self._win.on_ui_ready()

    @pyqtSlot(str)
    def send_command(self, cmd: str):
        self._win._handle(cmd)

    @pyqtSlot()
    def stop(self):
        self._win.stop_all()

    @pyqtSlot(str, str, bool)
    def add_reminder(self, time_str: str, text: str, daily: bool):
        from modules import reminders as rem
        r = rem.add({'time': time_str, 'text': text, 'repeat': 'daily' if daily else 'once'})
        if r['success']:
            rm = r['reminder']
            self.log_pushed.emit(f"Reminder set: {rm['time']} {rm['text']}", 'ok')
        else:
            self.log_pushed.emit(r['message'], 'err')
            self.alert_pushed.emit(json.dumps({'title': 'Reminder not set', 'text': r['message'],
                                               'kind': 'error'}))
        self._push_reminders()

    @pyqtSlot(str)
    def del_reminder(self, rid: str):
        from modules import reminders as rem
        rem.remove(rid)
        self._push_reminders()

    @pyqtSlot(str)
    def cancel_timer(self, tid: str):
        from modules import tools
        tools.cancel_countdown(tid)
        self._push_timers()

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
            self.model_pushed.emit(result['model'])
            self.log_pushed.emit(f'Model → {info["name"]}', 'ok')
            desc = f' — {info["desc"]}' if info['desc'] else ''
            self.chat_pushed.emit(f'Switched to **{info["name"]}**{desc}', False)
        else:
            self.chat_pushed.emit(result['message'], False)
            self.log_pushed.emit(result['message'], 'err')
            self.model_pushed.emit(ai.get_model())

    @pyqtSlot(str)
    def switch_backend(self, backend: str):
        from modules import ai
        result = ai.set_backend(backend)
        if result['success']:
            b = result['backend']
            self.backend_pushed.emit(b)
            self.model_pushed.emit(ai.get_model())
            self.log_pushed.emit(f'Backend → {b.upper()}', 'ok')
            msg = f'Switched to **{ai.BACKEND_NAMES[b]}** · `{ai.get_model()}`.'
            if not ai.has_api_key(b):
                msg += ' No API key yet — open ⚙ **Settings** to add one.'
            self.chat_pushed.emit(msg, False)
            self._push_config()
            self._win.refresh_models()
        else:
            self.chat_pushed.emit(result['message'], False)

    @pyqtSlot()
    def refresh_models(self):
        self._win.refresh_models(force=True)

    @pyqtSlot(str)
    def save_settings(self, payload: str):
        """Apply everything from the Settings dialog in one go."""
        from modules import ai, autostart, config, tts
        try:
            s = json.loads(payload)
        except json.JSONDecodeError:
            return
        keys_changed = False
        if s.get('groq_key', '').strip():
            ai.set_groq_key(s['groq_key'])
            keys_changed = True
        if s.get('gemini_key', '').strip():
            ai.set_gemini_key(s['gemini_key'])
            keys_changed = True

        config.update({
            'wake_word':  (s.get('wake_word') or 'tmos').strip().lower(),
            'tts_voice':  s.get('tts_voice') or config.get('tts_voice'),
            'stt_engine': s.get('stt_engine') if s.get('stt_engine') in ('google', 'groq') else 'google',
            'ai_tools':   bool(s.get('ai_tools', True)),
        })

        mic_device = (s.get('mic_device') or '').strip()
        if mic_device != (config.get('mic_device') or ''):
            config.set_value('mic_device', mic_device)
            self._win.restart_listeners()

        voice = bool(s.get('voice_enabled', True))
        if voice == tts.is_muted():
            tts.set_muted(not voice)
            self._win._sync_mute_button()

        listen = bool(s.get('always_listen'))
        if listen != bool(config.get('always_listen')):
            config.set_value('always_listen', listen)
            self._win._set_always_listen(listen)

        startup = bool(s.get('start_with_windows'))
        if startup != autostart.is_enabled():
            r = autostart.set_enabled(startup)
            self.log_pushed.emit(r['message'], 'ok' if r['success'] else 'err')
            config.set_value('start_with_windows', startup and r['success'])

        hotkey = (s.get('hotkey') or '').strip().lower()
        if hotkey != config.get('hotkey'):
            ok, msg = self._win.apply_hotkey(hotkey)
            if ok:
                config.set_value('hotkey', hotkey)
            self.log_pushed.emit(msg, 'ok' if ok else 'err')

        self.log_pushed.emit('Settings saved', 'ok')
        self.alert_pushed.emit(json.dumps({'title': 'Settings saved', 'text': '', 'kind': 'ok'}))
        self._push_config()
        if keys_changed:
            self._win.refresh_models(force=True)

    @pyqtSlot(bool)
    def toggle_always_listen(self, enabled: bool):
        from modules import config
        config.set_value('always_listen', enabled)
        self._win._set_always_listen(enabled)
        self._push_config()

    @pyqtSlot()
    def clear_ai_history(self):
        from modules import ai
        ai.clear_history()
        self.log_pushed.emit('AI conversation memory cleared', 'sys')
        self.chat_pushed.emit('Memory cleared. Starting a fresh conversation.', False)

    @pyqtSlot()
    def clear_chat(self):
        self._win.clear_chat()

    @pyqtSlot(str)
    def copy_text(self, text: str):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(text)
        self.log_pushed.emit('Copied to clipboard', 'ok')

    @pyqtSlot(str)
    def open_url(self, url: str):
        if url.startswith(('http://', 'https://')):
            from modules import apps
            apps.open_browser(url)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _push_reminders(self):
        from modules import reminders as rem
        self.reminders_pushed.emit(json.dumps(rem.get_all()))

    def _push_timers(self):
        from modules import tools
        self.timers_pushed.emit(json.dumps(tools.list_countdowns()))

    def _push_notes(self):
        from modules import notes
        self.notes_pushed.emit(json.dumps(notes.get_all()))

    def _push_model_info(self):
        from modules import ai
        self.backend_pushed.emit(ai.get_backend())
        self.model_pushed.emit(ai.get_model())

    def _push_config(self):
        from modules import autostart, config, hotkey, mic, tts
        c = config.get_all()
        c['mics'] = [m['name'] for m in mic.list_inputs()]
        c['mic_active'] = mic.resolve()[1]
        c['start_with_windows'] = autostart.is_enabled()
        c['voices'] = tts.VOICES
        c['hotkey_pretty'] = hotkey.pretty(c.get('hotkey') or '') if c.get('hotkey') else ''
        c['hotkey_error'] = self._win.hotkey_error
        c['muted'] = tts.is_muted()
        self.config_pushed.emit(json.dumps(c))
