#!/usr/bin/env python3
"""T.M.O.S — REST API Server (remote control from phone/browser)
   Run:  python server.py            # this PC only (127.0.0.1)
         python server.py --lan      # also reachable from other devices on your network
   Default port: 3000

Every request (except GET /) needs the access token printed at startup, sent as
    Authorization: Bearer <token>      or      X-TMOS-Token: <token>
The token is stored in ~/.tmos/config.json ("server_token"); delete it there to
generate a new one.

Endpoints:
   GET  /                     — health check (no token needed)
   POST /ai/ask               — ask AI (with conversation history)
   POST /ai/model             — switch model
   GET  /ai/models            — list available models
   POST /ai/clear             — clear conversation history
   GET  /system/stats         — system metrics
   POST /apps/open            — open an app
   POST /apps/browse          — open a URL
   GET  /files/list           — list directory
   POST /files/create         — create file/folder
   DELETE /files/delete        — delete file/folder (inside your home folder only)
   GET  /reminders            — list reminders
   POST /reminders            — add reminder
   DELETE /reminders/<rid>     — delete reminder
   GET  /notes                — list notes
   POST /notes                — add note
   DELETE /notes/<nid>         — delete note
   POST /tools/calc           — calculator
   POST /tools/screenshot     — take screenshot
"""

import argparse
import hmac
import os
import secrets
import socket
import sys

from flask import Flask, jsonify, request
from flask_cors import CORS

from modules import ai, apps, config, files, notes
from modules import reminders, system_info
from modules.tools import calculate, take_screenshot

for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, 'reconfigure'):
        _stream.reconfigure(encoding='utf-8', errors='replace')

app = Flask(__name__)
CORS(app, allow_headers=['Authorization', 'X-TMOS-Token', 'Content-Type'])

reminders.init()  # load saved reminders (the desktop app is the one that fires them)


def _token() -> str:
    token = config.get('server_token')
    if not token:
        token = secrets.token_urlsafe(24)
        config.set_value('server_token', token)
    return token


TOKEN = _token()


@app.before_request
def _require_token():
    if request.method == 'OPTIONS' or (request.path == '/' and request.method == 'GET'):
        return None
    auth = request.headers.get('Authorization', '')
    given = auth[7:].strip() if auth.lower().startswith('bearer ') else request.headers.get('X-TMOS-Token', '')
    if not given or not hmac.compare_digest(given.encode(), TOKEN.encode()):
        return jsonify({'success': False, 'message': 'Missing or wrong access token.'}), 401
    return None


# ── Health ───────────────────────────────────────────────────────────────────

@app.get('/')
def health():
    return jsonify({'status': 'ok', 'name': 'T.M.O.S'})


# ── AI ───────────────────────────────────────────────────────────────────────

@app.post('/ai/ask')
def ai_ask():
    data  = request.get_json(silent=True) or {}
    reply = ai.ask(data.get('prompt', ''))
    return jsonify({'reply': reply, 'backend': ai.get_backend(), 'model': ai.get_model()})


@app.post('/ai/model')
def ai_model():
    data = request.get_json(silent=True) or {}
    return jsonify(ai.set_model(data.get('model', '')))


@app.get('/ai/models')
def ai_models():
    models, error = ai.fetch_models()
    return jsonify({
        'backend': ai.get_backend(),
        'available': models,
        'installed': ai.get_installed_ollama_models() if ai.get_backend() == 'ollama' else [],
        'current': ai.get_model(),
        'error': error,
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
    path = os.path.realpath(os.path.expanduser(data.get('path', '')))
    home = os.path.realpath(os.path.expanduser('~'))
    if os.path.commonpath([path, home]) != home or path == home:
        return jsonify({'success': False, 'message': 'Only files inside your home folder can be deleted.'}), 403
    return jsonify(files.delete_file(path))


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
        'repeat': data.get('repeat', 'once'),
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
    parser = argparse.ArgumentParser(description='T.M.O.S REST API server')
    parser.add_argument('--lan', action='store_true',
                        help='listen on all network interfaces (other devices can connect)')
    parser.add_argument('--port', type=int, default=3000)
    args = parser.parse_args()

    host = '0.0.0.0' if args.lan else '127.0.0.1'
    print(f'\n  T.M.O.S API server running')
    print(f'  Local:   http://127.0.0.1:{args.port}')
    if args.lan:
        local_ip = socket.gethostbyname(socket.gethostname())
        print(f'  Network: http://{local_ip}:{args.port}')
    else:
        print('  (this PC only — add --lan to allow other devices)')
    print(f'  Token:   {TOKEN}')
    print('  Send it as "Authorization: Bearer <token>" with every request.\n')
    app.run(host=host, port=args.port, debug=False)
