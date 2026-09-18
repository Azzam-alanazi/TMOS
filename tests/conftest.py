"""Point T.M.O.S at a throwaway home folder before any module is imported,
so tests never read or write the real ~/.tmos data."""

import os
import sys
import tempfile

_HOME = tempfile.mkdtemp(prefix='tmos-test-home-')
os.environ['USERPROFILE'] = _HOME
os.environ['HOME'] = _HOME

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
