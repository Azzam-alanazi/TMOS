"""Point T.M.O.S at a throwaway home folder before any module is imported,
so tests never read or write the real ~/.tmos data."""

import json
import os
import sys
import tempfile

_HOME = tempfile.mkdtemp(prefix='tmos-test-home-')
os.environ['USERPROFILE'] = _HOME
os.environ['HOME'] = _HOME

# No background location lookups: tests that need a location switch it on themselves.
os.makedirs(os.path.join(_HOME, '.tmos'), exist_ok=True)
with open(os.path.join(_HOME, '.tmos', 'config.json'), 'w', encoding='utf-8') as f:
    json.dump({'location_mode': 'off'}, f)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
