"""SMTP transport with optional HTTP CONNECT proxy tunnelling."""

import base64
import smtplib
import socket
import ssl
from urllib.parse import unquote, urlparse

from source.settings import get_proxy_config

EMAIL_TIMEOUT_SECONDS = 30

def _resolve_smtp_proxy_url(proxy_config=None):
    """返回 SMTP 发送要使用的代理 URL；HTTPS 代理配置优先。"""
    proxy = proxy_config if proxy_config is not None else get_proxy_config()
    if not proxy.get("enabled"):
        return ""
    return str(proxy.get("https") or proxy.get("http") or "").strip()


def _parse_proxy_url(proxy_url):
    parsed = urlparse(proxy_url)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("报告邮件 SMTP 代理仅支持 HTTP CONNECT 代理")
    if not parsed.hostname:
        raise ValueError("报告邮件 SMTP 代理地址缺少主机名")
    return parsed


def _create_proxy_tunnel(host, port, timeout, proxy_url):
    """通过 HTTP CONNECT 创建到 SMTP 服务器的隧道 socket。"""
    parsed = _parse_proxy_url(proxy_url)
    proxy_port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    raw_sock = socket.create_connection((parsed.hostname, proxy_port), timeout=timeout)
    sock = raw_sock
    try:
        if parsed.scheme.lower() == "https":
            context = ssl.create_default_context()
            sock = context.wrap_socket(raw_sock, server_hostname=parsed.hostname)

        target = f"{host}:{int(port)}"
        headers = [
            f"CONNECT {target} HTTP/1.1",
            f"Host: {target}",
            "Proxy-Connection: Keep-Alive",
        ]
        if parsed.username:
            username = unquote(parsed.username)
            password = unquote(parsed.password or "")
            token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
            headers.append(f"Proxy-Authorization: Basic {token}")
        request = "\r\n".join(headers) + "\r\n\r\n"
        sock.sendall(request.encode("ascii"))

        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
            if len(response) > 65536:
                raise ConnectionError("SMTP 代理 CONNECT 响应过大")

        response_text = response.decode("iso-8859-1", errors="replace")
        status_line = response_text.splitlines()[0] if response_text.splitlines() else "无响应"
        parts = status_line.split()
        status_code = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0
        if status_code != 200:
            raise ConnectionError(f"SMTP 代理 CONNECT 失败：{status_line}")
        return sock
    except Exception:
        try:
            sock.close()
        finally:
            if sock is not raw_sock:
                raw_sock.close()
        raise


class _ProxySMTP(smtplib.SMTP):
    """使用 HTTP CONNECT 代理隧道的 SMTP 客户端。"""

    def __init__(self, *args, proxy_url="", **kwargs):
        self._smtp_proxy_url = proxy_url
        super().__init__(*args, **kwargs)

    def _get_socket(self, host, port, timeout):
        if self._smtp_proxy_url:
            return _create_proxy_tunnel(host, port, timeout, self._smtp_proxy_url)
        return super()._get_socket(host, port, timeout)


class _ProxySMTP_SSL(smtplib.SMTP_SSL):
    """使用 HTTP CONNECT 代理隧道的 SMTP over SSL 客户端。"""

    def __init__(self, *args, proxy_url="", **kwargs):
        self._smtp_proxy_url = proxy_url
        super().__init__(*args, **kwargs)

    def _get_socket(self, host, port, timeout):
        if self._smtp_proxy_url:
            sock = _create_proxy_tunnel(host, port, timeout, self._smtp_proxy_url)
            return self.context.wrap_socket(sock, server_hostname=host)
        return super()._get_socket(host, port, timeout)


def _send_message(config, message):
    context = ssl.create_default_context()
    security = config.get("security", "starttls")
    host = config["smtp_host"]
    port = int(config.get("smtp_port") or (465 if security == "ssl" else 587))
    proxy_url = _resolve_smtp_proxy_url()
    if security == "ssl":
        smtp_cls = _ProxySMTP_SSL if proxy_url else smtplib.SMTP_SSL
        kwargs = {"timeout": EMAIL_TIMEOUT_SECONDS, "context": context}
        if proxy_url:
            kwargs["proxy_url"] = proxy_url
        with smtp_cls(host, port, **kwargs) as smtp:
            if config.get("username"):
                smtp.login(config["username"], config.get("password", ""))
            smtp.send_message(message)
        return

    smtp_cls = _ProxySMTP if proxy_url else smtplib.SMTP
    kwargs = {"timeout": EMAIL_TIMEOUT_SECONDS}
    if proxy_url:
        kwargs["proxy_url"] = proxy_url
    with smtp_cls(host, port, **kwargs) as smtp:
        if security == "starttls":
            smtp.starttls(context=context)
        if config.get("username"):
            smtp.login(config["username"], config.get("password", ""))
        smtp.send_message(message)
