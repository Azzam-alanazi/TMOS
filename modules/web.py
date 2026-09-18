"""T.M.O.S — Web Search & Reading
Lets the AI look things up instead of guessing:
  - search()    → result titles, links and snippets from the ddgs meta-search
                  package (no API key), or Wikipedia when that isn't available
  - read_page() → the readable text of a web page

Everything fetched here is untrusted text from the internet. The AI is told to
treat it as information, never as instructions, and read_page() refuses local
and private network addresses so a page can't point it at your router or PC.
"""

import html
import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/140.0 Safari/537.36',
      'Accept-Language': 'en-US,en;q=0.9'}

MAX_RESULTS    = 5
SNIPPET_CHARS  = 260
PAGE_CHARS     = 3500        # keeps a page within the free Groq tier's request size
MAX_DOWNLOAD   = 2_000_000
MAX_REDIRECTS  = 5


# ══════════════════════════════════════════════════════════════════════════════
#  SEARCH
# ══════════════════════════════════════════════════════════════════════════════

def search(query: str, news: bool = False, max_results: int = MAX_RESULTS) -> dict:
    """{'success', 'query', 'results': [{'title', 'url', 'snippet', 'date'?, 'source'?}]}"""
    query = ' '.join((query or '').split())
    if not query:
        return {'success': False, 'message': 'No search query given.'}
    max_results = max(1, min(int(max_results or MAX_RESULTS), 8))
    results, engine = _ddgs(query, news, max_results), 'web'
    if not results and not news:
        results, engine = _wikipedia(query, max_results), 'wikipedia'
    if not results:
        return {'success': False, 'message': f'No results for "{query}" (offline, or the search '
                                             'service is busy — try again in a moment).'}
    return {'success': True, 'query': query, 'engine': engine, 'results': results}


def _ddgs(query: str, news: bool, n: int) -> list[dict]:
    try:
        from ddgs import DDGS
    except ImportError:
        print('[Web] ddgs is not installed (pip install ddgs); using Wikipedia instead')
        return []
    try:
        client = DDGS(timeout=8)
        raw = client.news(query, max_results=n) if news else client.text(query, max_results=n)
    except Exception as e:                  # ddgs raises its own errors for rate limits / no results
        print(f'[Web] search failed: {type(e).__name__}: {str(e)[:120]}')
        return []
    out = []
    for r in raw or []:
        item = {'title': _clean(r.get('title', '')),
                'url': r.get('href') or r.get('url') or '',
                'snippet': _clip(_clean(r.get('body', '')), SNIPPET_CHARS)}
        if news:
            item['date'] = (r.get('date') or '')[:10]
            item['source'] = r.get('source', '')
        if item['url']:
            out.append(item)
    return out


def _wikipedia(query: str, n: int) -> list[dict]:
    lang = 'ar' if re.search(r'[\u0600-\u06FF]', query) else 'en'
    try:
        data = requests.get(f'https://{lang}.wikipedia.org/w/api.php', params={
            'action': 'query', 'format': 'json', 'generator': 'search', 'gsrsearch': query,
            'gsrlimit': n, 'prop': 'extracts|info', 'exintro': 1, 'explaintext': 1,
            'exsentences': 3, 'exlimit': n, 'inprop': 'url',
        }, headers={'User-Agent': 'TMOS-Desktop-Assistant/1.0'}, timeout=8).json()
    except (requests.RequestException, ValueError):
        return []
    pages = sorted((data.get('query') or {}).get('pages', {}).values(), key=lambda p: p.get('index', 0))
    return [{'title': p.get('title', ''), 'url': p.get('fullurl', ''),
             'snippet': _clip(p.get('extract', ''), SNIPPET_CHARS)} for p in pages if p.get('fullurl')]


# ══════════════════════════════════════════════════════════════════════════════
#  READ A PAGE
# ══════════════════════════════════════════════════════════════════════════════

def read_page(url: str, max_chars: int = PAGE_CHARS) -> dict:
    """{'success', 'url', 'title', 'text', 'truncated'} for an http(s) page."""
    url = (url or '').strip()
    if not re.match(r'[a-z][a-z0-9+.-]*://', url, re.IGNORECASE):
        url = 'https://' + url                      # "github.com" → https://github.com
    try:
        resp = _fetch(url)
    except ValueError as e:
        return {'success': False, 'message': str(e)}
    except requests.exceptions.Timeout:
        return {'success': False, 'message': 'The page took too long to load.'}
    except requests.RequestException as e:
        return {'success': False, 'message': f'Could not open the page: {type(e).__name__}.'}

    with resp:
        if resp.status_code >= 400:
            return {'success': False, 'message': f'The page returned HTTP {resp.status_code}.'}
        ctype = resp.headers.get('Content-Type', '').lower()
        if ctype and not any(t in ctype for t in ('text/', 'html', 'xml', 'json')):
            return {'success': False, 'message': f"Can't read this kind of file ({ctype.split(';')[0]})."}
        body = b''
        for chunk in resp.iter_content(65536):
            body += chunk
            if len(body) >= MAX_DOWNLOAD:
                break
        final_url = resp.url

    raw = _decode(body, ctype)
    if 'html' in ctype or raw.lstrip()[:15].lower().startswith(('<!doctype', '<html')):
        title, text = _html_to_text(raw)
    else:
        title, text = '', raw
    text = _tidy(text)
    if not text:
        return {'success': False, 'message': 'The page has no readable text (it may need JavaScript).'}
    return {'success': True, 'url': final_url, 'title': title, 'text': text[:max_chars],
            'truncated': len(text) > max_chars}


def _fetch(url: str) -> requests.Response:
    """GET url, following redirects by hand so every hop is checked by _check_public."""
    for _ in range(MAX_REDIRECTS + 1):
        _check_public(url)
        resp = requests.get(url, headers=UA, timeout=(5, 12), stream=True, allow_redirects=False)
        if resp.is_redirect and resp.headers.get('Location'):
            url = urljoin(url, resp.headers['Location'])
            resp.close()
            continue
        return resp
    raise ValueError('The page redirects too many times.')


def _check_public(url: str) -> None:
    """Raise ValueError unless url is http(s) on a public internet address."""
    p = urlparse(url)
    if p.scheme not in ('http', 'https') or not p.hostname:
        raise ValueError('Only http:// and https:// web pages can be read.')
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == 'https' else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ValueError(f'Could not find the site {p.hostname}.')
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split('%')[0])
        if not ip.is_global:
            raise ValueError("That address is on this PC or the local network, which T.M.O.S won't read.")


def _decode(body: bytes, ctype: str) -> str:
    m = re.search(r'charset=([\w-]+)', ctype) or re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', body[:4096], re.I)
    enc = m.group(1) if m else 'utf-8'
    if isinstance(enc, bytes):
        enc = enc.decode('ascii', errors='ignore')
    try:
        return body.decode(enc, errors='replace')
    except LookupError:
        return body.decode('utf-8', errors='replace')


class _TextExtractor(HTMLParser):
    """The readable text of a page: skips scripts, menus, headers and footers."""
    SKIP_TAGS  = {'script', 'style', 'noscript', 'svg', 'nav', 'footer', 'header', 'aside',
                  'form', 'button', 'iframe', 'template', 'select', 'head'}
    SKIP_ROLES = {'navigation', 'banner', 'contentinfo', 'search', 'menu', 'menubar',
                  'complementary', 'dialog'}
    BLOCK = {'p', 'div', 'li', 'tr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'section',
             'article', 'blockquote', 'pre', 'table', 'ul', 'ol', 'dd', 'dt', 'main'}
    VOID  = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta',
             'source', 'track', 'wbr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ''
        self._in_title = False
        self._skip_tag = ''          # the element being skipped, and how deeply it's nested
        self._skip_depth = 0
        self._pre = 0                # inside <pre>, line breaks are real

    def handle_starttag(self, tag, attrs):
        if tag == 'title' and not self.title:         # the page's, not an SVG icon's <title>
            self._in_title = True
        if tag in self.VOID:
            if not self._skip_tag and tag in ('br', 'hr'):
                self.parts.append('\n')
            return
        if self._skip_tag:
            self._skip_depth += tag == self._skip_tag
            return
        a = dict(attrs)
        if tag in self.SKIP_TAGS or a.get('role') in self.SKIP_ROLES or a.get('aria-hidden') == 'true':
            self._skip_tag, self._skip_depth = tag, 1
        elif tag in self.BLOCK:
            self.parts.append('\n## ' if tag in ('h1', 'h2', 'h3') else '\n')
            self._pre += tag == 'pre'

    def handle_endtag(self, tag):
        if tag == 'title':
            self._in_title = False
        if self._skip_tag:
            if tag == self._skip_tag:
                self._skip_depth -= 1
                if not self._skip_depth:
                    self._skip_tag = ''
        elif tag in self.BLOCK:
            self.parts.append('\n')
            if tag == 'pre' and self._pre:
                self._pre -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip_tag:
            self.parts.append(data if self._pre else re.sub(r'\s+', ' ', data))


def _html_to_text(raw: str) -> tuple[str, str]:
    parser = _TextExtractor()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        pass
    return ' '.join(parser.title.split()), ''.join(parser.parts)


def _tidy(text: str) -> str:
    """One line per block, no empty or punctuation-only lines."""
    lines = (' '.join(line.split()) for line in text.splitlines())
    return '\n'.join(l for l in lines if re.search(r'\w', l) and l != '##')


# ── Helpers ──────────────────────────────────────────────────────────────────

def _clean(s: str) -> str:
    return ' '.join(html.unescape(re.sub(r'<[^>]+>', '', s or '')).split())


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n].rsplit(' ', 1)[0] + '…'
