"""Backward-compatible runtime settings interface.

Canonical implementations live in :mod:`source.settings`.
"""

from source.settings import *
from source.settings import __all__
from source.settings.defaults import DB_DIR
from source.settings.normalize import _normalize_fetch_config, _normalize_prompt_profiles
