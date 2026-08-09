"""OpenAI-compatible client construction and explicit proxy routing."""

from urllib.parse import urlparse

from openai import DefaultHttpxClient, OpenAI

from source.settings import get_ai_config, get_proxy_config


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


def get_openai_client(cfg=None):
    """Create an OpenAI-compatible client from one resolved task configuration."""
    cfg = cfg or get_ai_config()
    return OpenAI(
        api_key=cfg["api_key"],
        base_url=cfg["base_url"],
        http_client=_build_openai_http_client(cfg),
    )


__all__ = ["get_openai_client"]
