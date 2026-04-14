#!/usr/bin/env python3
"""T.M.O.S — REST API Server (remote control from phone/browser)
   Run:  python server.py
   Default port: 3000

Endpoints:
   GET  /                     — health check
   POST /ai/ask               — ask AI (with conversation history)
   POST /ai/model             — switch model
   GET  /ai/models            — list available models
   POST /ai/clear             — clear conversation history
   GET  /system/stats         — system metrics
   POST /apps/open            — open an app
   POST /apps/browse          — open a URL
   GET  /files/list           — list directory
   POST /files/create         — create file/folder
   DELETE /files/delete        — delete file/folder
   GET  /reminders            — list reminders
   POST /reminders            — add reminder
   DELETE /reminders/<rid>     — delete reminder
   GET  /notes                — list notes
   POST /notes                — add note
   DELETE /notes/<nid>         — delete note
   POST /tools/calc           — calculator
   POST /tools/screenshot     — take screenshot
"""

import socket
from flask import Flask, jsonify, request
from flask_cors import CORS

from modules import ai, apps, files, notes
from modules import reminders, system_info
from modules.tools import calculate, take_screenshot

app = Flask(__name__)
CORS(app)

reminders.init()  # load saved reminders (no GUI callback in server mode)


# ── Health ───────────────────────────────────────────────────────────────────

@app.get('/')
def health():
    return jsonify({
        'status': 'ok',
        'name': 'T.M.O.S',
        'model': ai.get_model(),
        'ollama': ai.is_ollama_running(),
    })


# ── AI ───────────────────────────────────────────────────────────────────────

@app.post('/ai/ask')
def ai_ask():
    data  = request.get_json(silent=True) or {}
    reply = ai.ask(data.get('prompt', ''))
    return jsonify({'reply': reply, 'model': ai.get_model()})


@app.post('/ai/model')
def ai_model():
    data = request.get_json(silent=True) or {}
    return jsonify(ai.set_model(data.get('model', '')))


@app.get('/ai/models')
def ai_models():
    return jsonify({
        'available': ai.get_available_models(),
        'installed': ai.get_installed_models(),
        'current': ai.get_model(),
    })


@app.post('/ai/clear')
def ai_clear():
    ai.clear_history()
    return jsonify({'success': True, 'message': 'History cleared.'})


# ── System ───────────────────────────────────────────────────────────────────

@app.get('/system/stats')
def sys_stats():
    return jsonify(system_info.get_stats())


# ── Apps ─────────────────────────────────────────────────────────────────────

@app.post('/apps/open')
def apps_open():
    data = request.get_json(silent=True) or {}
    return jsonify(apps.open_app(data.get('name', '')))


@app.post('/apps/browse')
def apps_browse():
    data = request.get_json(silent=True) or {}
    return jsonify(apps.open_browser(data.get('url', '')))


# ── Files ────────────────────────────────────────────────────────────────────

@app.get('/files/list')
def files_list():
    path = request.args.get('path')
    return jsonify(files.list_dir(path))


@app.post('/files/create')
def files_create():
    data = request.get_json(silent=True) or {}
    return jsonify(files.create_item(
        data.get('dirPath', ''),
        data.get('name',    ''),
        data.get('isFolder', False),
    ))


@app.delete('/files/delete')
def files_delete():
    data = request.get_json(silent=True) or {}
    return jsonify(files.delete_file(data.get('path', '')))


# ── Reminders ────────────────────────────────────────────────────────────────

@app.get('/reminders')
def rem_list():
    return jsonify(reminders.get_all())


@app.post('/reminders')
def rem_add():
    data = request.get_json(silent=True) or {}
    return jsonify(reminders.add({
        'time': data.get('time', ''),
        'text': data.get('text', ''),
    }))


@app.delete('/reminders/<rid>')
def rem_delete(rid: str):
    return jsonify(reminders.remove(rid))


# ── Notes ────────────────────────────────────────────────────────────────────

@app.get('/notes')
def notes_list():
    return jsonify(notes.get_all())


@app.post('/notes')
def notes_add():
    data = request.get_json(silent=True) or {}
    return jsonify(notes.add(
        text=data.get('text', ''),
        title=data.get('title', ''),
    ))


@app.delete('/notes/<nid>')
def notes_delete(nid: str):
    return jsonify(notes.remove(nid))


# ── Tools ────────────────────────────────────────────────────────────────────

@app.post('/tools/calc')
def tools_calc():
    data = request.get_json(silent=True) or {}
    return jsonify(calculate(data.get('expression', '')))


@app.post('/tools/screenshot')
def tools_screenshot():
    return jsonify(take_screenshot())


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    local_ip = socket.gethostbyname(socket.gethostname())
    print(f'\n  T.M.O.S API server running')
    print(f'  Local:   http://127.0.0.1:3000')
    print(f'  Network: http://{local_ip}:3000\n')
    app.run(host='0.0.0.0', port=3000, debug=False)
