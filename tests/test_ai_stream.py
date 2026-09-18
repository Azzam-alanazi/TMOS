"""The streaming + tool-calling loop of each backend, against fake HTTP responses."""

import json

import pytest

from modules import actions, ai, config


class FakeResponse:
    def __init__(self, lines=(), status=200, body=None):
        self.status_code = status
        self._lines = [l if isinstance(l, bytes) else l.encode() for l in lines]
        self._body = body
        self.text = json.dumps(body) if body is not None else ''

    def iter_lines(self):
        yield from self._lines

    def json(self):
        return self._body

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def sse(obj) -> str:
    return 'data: ' + json.dumps(obj)


@pytest.fixture
def env(monkeypatch):
    """Fresh history, fake tools, and a queue of fake HTTP responses."""
    ai._history.clear()
    calls, requests_made = [], []
    monkeypatch.setattr(actions, 'run', lambda name, args: calls.append((name, args)) or {'ok': True})
    monkeypatch.setattr(config, '_config', dict(config._config, groq_api_key='gsk_test',
                                                gemini_api_key='AIza_test', ai_tools=True))
    queue = []

    def fake_post(url, **kw):
        requests_made.append((url, kw.get('json')))
        return queue.pop(0)

    monkeypatch.setattr(ai.requests, 'post', fake_post)
    return queue, calls, requests_made


def run(prompt='open spotify'):
    return ''.join(ai.ask_stream(prompt))


def test_groq_tool_call_split_across_chunks(env):
    queue, calls, sent = env
    config.set_value('ai_backend', 'groq')
    queue.append(FakeResponse([
        sse({'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'c1', 'function': {'name': 'open_app', 'arguments': '{"na'}}]}}]}),
        sse({'choices': [{'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': 'me": "spotify"}'}}]}}]}),
        'data: [DONE]',
    ]))
    queue.append(FakeResponse([sse({'choices': [{'delta': {'content': 'Spotify is open.'}}]}), 'data: [DONE]']))

    assert run() == 'Spotify is open.'
    assert calls == [('open_app', {'name': 'spotify'})]
    second = sent[1][1]['messages']
    assert second[-2]['tool_calls'][0]['function']['arguments'] == '{"name": "spotify"}'
    assert second[-1] == {'role': 'tool', 'tool_call_id': 'c1', 'content': '{"ok": true}'}
    assert ai._history[-1] == {'role': 'assistant', 'content': 'Spotify is open.'}


def test_groq_retries_without_tools_after_bad_tool_call(env):
    queue, calls, sent = env
    config.set_value('ai_backend', 'groq')
    bad = {'error': {'message': 'Failed to call a function. tool_use_failed'}}
    queue += [FakeResponse(status=400, body=bad), FakeResponse(status=400, body=bad),
              FakeResponse([sse({'choices': [{'delta': {'content': 'Hi'}}]})])]
    assert run('hello') == 'Hi'
    assert 'tools' in sent[0][1] and 'tools' in sent[1][1] and 'tools' not in sent[2][1]


def test_groq_bad_key_is_explained(env):
    queue, _, _ = env
    config.set_value('ai_backend', 'groq')
    queue.append(FakeResponse(status=401, body={'error': {'message': 'Invalid API Key'}}))
    assert 'rejected the API key' in run('hi')
    assert ai._history == []


def test_gemini_function_call_keeps_thought_signature(env):
    queue, calls, sent = env
    config.set_value('ai_backend', 'gemini')
    fc_part = {'functionCall': {'name': 'set_reminder', 'args': {'text': 'stretch', 'time': '17:00'}},
               'thoughtSignature': 'sig123'}
    queue.append(FakeResponse([sse({'candidates': [{'content': {'parts': [fc_part]}}]})]))
    queue.append(FakeResponse([sse({'candidates': [{'content': {'parts': [{'text': 'Reminder set.'}]}}]})]))

    assert run('remind me at 5pm to stretch') == 'Reminder set.'
    assert calls == [('set_reminder', {'text': 'stretch', 'time': '17:00'})]
    contents = sent[1][1]['contents']
    assert contents[-2] == {'role': 'model', 'parts': [fc_part]}
    assert contents[-1]['parts'][0]['functionResponse']['name'] == 'set_reminder'
    decls = sent[0][1]['tools'][0]['functionDeclarations']
    no_args = next(d for d in decls if d['name'] == 'list_notes')
    assert 'parameters' not in no_args                      # Gemini rejects empty OBJECT schemas
    assert next(d for d in decls if d['name'] == 'open_app')['parameters']['type'] == 'OBJECT'


def test_ollama_missing_model_says_how_to_pull(env):
    queue, _, _ = env
    config.set_value('ai_backend', 'ollama')
    config.set_value('ollama_model', 'qwen2.5')
    queue.append(FakeResponse(status=404, body={'error': 'model "qwen2.5" not found, try pulling it first'}))
    assert 'ollama pull qwen2.5' in run('hi')


def test_ollama_model_without_tool_support_answers_plainly(env):
    queue, _, sent = env
    config.set_value('ai_backend', 'ollama')
    queue.append(FakeResponse(status=400, body={'error': 'registry.ollama.ai/library/gemma3 does not support tools'}))
    queue.append(FakeResponse([json.dumps({'message': {'content': 'Hello!'}, 'done': True})]))
    assert run('hi') == 'Hello!'
    assert 'tools' not in sent[1][1]


def test_ollama_tool_call_then_answer(env):
    queue, calls, _ = env
    config.set_value('ai_backend', 'ollama')
    queue.append(FakeResponse([json.dumps({'message': {'content': '', 'tool_calls': [
        {'function': {'name': 'start_timer', 'arguments': {'seconds': 300}}}]}, 'done': True})]))
    queue.append(FakeResponse([json.dumps({'message': {'content': 'Timer started.'}, 'done': True})]))
    assert run('5 minute timer please') == 'Timer started.'
    assert calls == [('start_timer', {'seconds': 300})]


def test_cancelled_answer_is_not_remembered(env):
    queue, _, _ = env
    config.set_value('ai_backend', 'groq')
    queue.append(FakeResponse([sse({'choices': [{'delta': {'content': 'part'}}]})]))
    out = ''.join(ai.ask_stream('hi', should_stop=lambda: True))
    assert out == ''
    assert ai._history == []


def test_think_filter_handles_split_tags():
    f = ai._ThinkFilter()
    out = ''.join(f.feed(t) for t in ['Hel', 'lo <thi', 'nk>secret</th', 'ink> world', '<'])
    assert out + f.flush() == 'Hello  world<'


def test_set_model_accepts_unique_partial_name(monkeypatch):
    monkeypatch.setattr(config, '_config', dict(config._config, ai_backend='groq'))
    monkeypatch.setattr(config, '_save', lambda: None)
    assert ai.set_model('120b')['model'] == 'openai/gpt-oss-120b'
    assert not ai.set_model('gpt-oss')['success']          # ambiguous: 120b and 20b


def test_retired_model_is_replaced(monkeypatch):
    monkeypatch.setattr(config, '_config', dict(config._config, groq_model='llama-3.3-70b-versatile'))
    monkeypatch.setattr(config, '_save', lambda: None)
    monkeypatch.setitem(ai._model_cache, 'groq', (0, [{'id': 'openai/gpt-oss-120b', 'name': 'x', 'desc': ''}]))
    assert ai.heal_model('groq') == 'openai/gpt-oss-120b'
    assert ai.heal_model('groq') is None                   # already fine
