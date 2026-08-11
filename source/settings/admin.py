"""Administrator credential storage and bootstrap helpers."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import tempfile
import threading

from werkzeug.security import check_password_hash, generate_password_hash

from .defaults import DEFAULT_SETTINGS
from . import store


ADMIN_PASSWORD_MIN_LENGTH = 12
_LEGACY_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")
_IN_PROCESS_LOCK = threading.Lock()


class AdminCredentialError(RuntimeError):
    """Credential initialization could not proceed without risking config loss."""


@dataclass(frozen=True)
class AdminCredentialResult:
    password_hash: str
    generated_password: str | None
    password_change_recommended: bool


def _new_password():
    return secrets.token_urlsafe(24)


@contextmanager
def _credential_lock():
    """Serialize credential read-modify-write operations across workers."""
    directory = os.path.dirname(store.SETTINGS_PATH)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    lock_path = os.path.join(directory, ".admin-credentials.lock")
    with _IN_PROCESS_LOCK:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.chmod(lock_path, 0o600)
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)


def _read_settings_strict():
    path = store.SETTINGS_PATH
    if not os.path.exists(path):
        return json.loads(json.dumps(DEFAULT_SETTINGS)), False
    try:
        with open(path, "r", encoding="utf-8") as handle:
            settings = json.load(handle)
    except (OSError, ValueError) as exc:
        raise AdminCredentialError(f"无法安全读取 {path}: {exc}") from exc
    if not isinstance(settings, dict):
        raise AdminCredentialError(f"无法安全读取 {path}: 根节点必须是 JSON 对象")
    return settings, True


def _backup_settings(path):
    backup_dir = os.path.join(os.path.dirname(path), "settings-backup")
    os.makedirs(backup_dir, mode=0o700, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    backup_path = os.path.join(backup_dir, f"settings-before-admin-{stamp}.json")
    shutil.copy2(path, backup_path)
    os.chmod(backup_path, 0o600)
    return backup_path


def _write_settings_atomic(settings, *, backup_existing=False):
    path = store.SETTINGS_PATH
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    if backup_existing and os.path.exists(path):
        _backup_settings(path)

    file_descriptor, temp_path = tempfile.mkstemp(
        prefix=".settings-admin-", suffix=".tmp", dir=directory, text=True
    )
    try:
        os.chmod(temp_path, 0o600)
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
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
        raise AdminCredentialError(f"无法安全保存 {path}: {exc}") from exc


def _store_password(settings, password, *, generated, backup_existing=False):
    settings = dict(settings)
    settings["admin_password"] = generate_password_hash(password, method="scrypt")
    settings["admin_password_change_recommended"] = bool(generated)
    _write_settings_atomic(settings, backup_existing=backup_existing)
    return AdminCredentialResult(
        password_hash=settings["admin_password"],
        generated_password=password if generated else None,
        password_change_recommended=bool(generated),
    )


def ensure_admin_password():
    """Ensure startup has a credential, returning plaintext only when generated."""
    with _credential_lock():
        settings, existed = _read_settings_strict()
        password_hash = str(settings.get("admin_password") or "").strip()
        if password_hash:
            return AdminCredentialResult(
                password_hash=password_hash,
                generated_password=None,
                password_change_recommended=bool(
                    settings.get("admin_password_change_recommended", False)
                ),
            )
        return _store_password(
            settings, _new_password(), generated=True, backup_existing=existed
        )


def get_admin_password():
    return str(store.load_settings().get("admin_password") or "")


def has_admin_password():
    return bool(get_admin_password())


def admin_password_change_recommended():
    return bool(store.load_settings().get("admin_password_change_recommended", False))


def set_admin_password(password):
    password = str(password or "")
    if len(password) < ADMIN_PASSWORD_MIN_LENGTH:
        raise ValueError(f"管理密码至少需要 {ADMIN_PASSWORD_MIN_LENGTH} 个字符")
    with _credential_lock():
        settings, _ = _read_settings_strict()
        return _store_password(settings, password, generated=False).password_hash


def _upgrade_legacy_password(expected_hash, password):
    """Upgrade a verified legacy hash without applying new-password policy."""
    with _credential_lock():
        settings, _ = _read_settings_strict()
        current_hash = str(settings.get("admin_password") or "").strip()
        if not hmac.compare_digest(current_hash.lower(), expected_hash.lower()):
            return
        _store_password(settings, password, generated=False)


def verify_admin_password(password):
    password = str(password or "")
    stored = get_admin_password()
    if not stored:
        return False
    if _LEGACY_SHA256_RE.fullmatch(stored):
        candidate = hashlib.sha256(password.encode("utf-8")).hexdigest()
        valid = hmac.compare_digest(candidate.lower(), stored.lower())
        if valid:
            _upgrade_legacy_password(stored, password)
        return valid
    try:
        return check_password_hash(stored, password)
    except (TypeError, ValueError):
        return False


def reset_admin_password():
    """Generate a replacement credential and preserve a pre-reset backup."""
    with _credential_lock():
        settings, existed = _read_settings_strict()
        return _store_password(
            settings, _new_password(), generated=True, backup_existing=existed
        )
