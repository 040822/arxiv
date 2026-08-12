"""Strict, atomic runtime-settings I/O used by security-sensitive migrations."""

from datetime import datetime, timezone
import json
import os
import shutil
import tempfile

from .defaults import DEFAULT_SETTINGS
from . import store


class SettingsPersistenceError(RuntimeError):
    """Settings could not be read or written without risking data loss."""


def read_settings_strict():
    path = store.SETTINGS_PATH
    if not os.path.exists(path):
        return json.loads(json.dumps(DEFAULT_SETTINGS)), False
    try:
        with open(path, "r", encoding="utf-8") as handle:
            settings = json.load(handle)
    except (OSError, ValueError) as exc:
        raise SettingsPersistenceError(f"无法安全读取 {path}: {exc}") from exc
    if not isinstance(settings, dict):
        raise SettingsPersistenceError(f"无法安全读取 {path}: 根节点必须是 JSON 对象")
    return settings, True


def _backup_settings(path):
    backup_dir = os.path.join(os.path.dirname(path), "settings-backup")
    os.makedirs(backup_dir, mode=0o700, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    backup_path = os.path.join(backup_dir, f"settings-before-user-migration-{stamp}.json")
    shutil.copy2(path, backup_path)
    os.chmod(backup_path, 0o600)
    return backup_path


def write_settings_atomic(settings, *, backup_existing=False):
    path = store.SETTINGS_PATH
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    if backup_existing and os.path.exists(path):
        _backup_settings(path)
    descriptor, temp_path = tempfile.mkstemp(
        prefix=".settings-migration-", suffix=".tmp", dir=directory, text=True
    )
    try:
        os.chmod(temp_path, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(settings, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        os.chmod(path, 0o600)
    except Exception as exc:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise SettingsPersistenceError(f"无法安全保存 {path}: {exc}") from exc

