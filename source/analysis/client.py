"""OpenAI-compatible client construction and explicit proxy routing."""

import uuid
from urllib.parse import urlparse

from openai import DefaultHttpxClient, OpenAI

from source.settings import get_ai_config, get_proxy_config

from .usage import get_usage_user_id


def _select_proxy_for_base_url(base_url, proxy_config=None):
    """Choose the global proxy matching the provider URL scheme."""
    proxy = proxy_config if proxy_config is not None else get_proxy_config()
    if not proxy.get("enabled"):
        return ""
    scheme = urlparse(str(base_url or "")).scheme.lower()
    if scheme == "http":
        return str(proxy.get("http") or proxy.get("https") or "").strip()
    return str(proxy.get("https") or proxy.get("http") or "").strip()


def _build_openai_http_client(cfg):
    """Create an OpenAI HTTP client that never reads proxy environment variables."""
    kwargs = {"trust_env": False}
    proxy_url = _select_proxy_for_base_url(cfg.get("base_url", ""))
    if proxy_url:
        kwargs["proxy"] = proxy_url
    return DefaultHttpxClient(**kwargs)


def _is_opencode_gateway(base_url):
    """判断是否为 opencode 官方网关（覆盖 opencode.ai 与 api.opencode.ai）。"""
    return "opencode.ai" in str(base_url or "").lower()


def conversation_session_id(task_key, paper_data=None):
    """稳定会话 id：(task_key, user, paper) 视为同一个对话，跨轮复用。"""
    user_id = get_usage_user_id() or "system"
    paper_data = paper_data or {}
    paper_key = (
        paper_data.get("paper_key")
        or paper_data.get("arxiv_id")
        or str(paper_data.get("paper_id") or paper_data.get("id") or "unknown")
    )
    return f"arxiv-{task_key}-{user_id}-{paper_key}"


def get_openai_client(cfg=None, session_id=None):
    """Create an OpenAI-compatible client from one resolved task configuration."""
    cfg = cfg or get_ai_config()
    default_headers = None
    if _is_opencode_gateway(cfg.get("base_url")):
        default_headers = {"x-opencode-session": session_id or uuid.uuid4().hex}
    return OpenAI(
        api_key=cfg["api_key"],
        base_url=cfg["base_url"],
        http_client=_build_openai_http_client(cfg),
        default_headers=default_headers,
    )


__all__ = ["get_openai_client"]
