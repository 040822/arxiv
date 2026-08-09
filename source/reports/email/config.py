"""Runtime email configuration normalization shared by rendering and sending."""


class _SafeFormatDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _sender_from_config(config):
    return (config.get("sender") or config.get("username") or "").strip()


def _normalize_recipients(value):
    if isinstance(value, str):
        import re
        parts = re.split(r"[,;\n\r]+", value)
    elif isinstance(value, (list, tuple)):
        parts = value
    else:
        parts = []
    return [str(part or "").strip() for part in parts if str(part or "").strip()]


def _normalize_runtime_config(config):
    config = dict(config or {})
    config["smtp_host"] = str(config.get("smtp_host") or "").strip()
    config["username"] = str(config.get("username") or "").strip()
    config["password"] = str(config.get("password") or "")
    config["sender"] = str(config.get("sender") or "").strip()
    config["recipients"] = _normalize_recipients(config.get("recipients"))
    config["security"] = str(config.get("security") or "starttls").strip().lower()
    if config["security"] not in {"starttls", "ssl", "none"}:
        config["security"] = "starttls"
    try:
        config["smtp_port"] = int(config.get("smtp_port") or (465 if config["security"] == "ssl" else 587))
    except (TypeError, ValueError):
        config["smtp_port"] = 465 if config["security"] == "ssl" else 587
    config["site_url"] = str(config.get("site_url") or "").strip().rstrip("/")
    return config
