"""Validate and orchestrate one report-email send."""

from email.message import EmailMessage

from source.settings import get_email_report_config, update_email_report_status

from .config import _normalize_runtime_config, _sender_from_config
from .content import (
    build_report_email_data,
    build_report_email_html,
    build_report_email_subject,
    build_report_email_text,
)
from .transport import _send_message

def _validate_send_config(config):
    if not config.get("smtp_host"):
        raise ValueError("请先填写 SMTP 服务器地址")
    if not config.get("recipients"):
        raise ValueError("请至少填写一个收件人邮箱")
    if not _sender_from_config(config):
        raise ValueError("请填写发件人邮箱或 SMTP 用户名")


def send_report_email(report, config=None, force=False, record_status=True, ai_summary=None, ai_summary_error=""):
    """
    发送单份报告邮件。

    force=True 用于手动测试：即使 enabled=false，只要 SMTP 配置完整也会执行。
    """
    runtime_config = _normalize_runtime_config(config or get_email_report_config(mask_password=False))
    if not runtime_config.get("enabled") and not force:
        return {"status": "skipped", "message": "报告邮件发送未启用"}

    report_date = str(report.get("report_date") or "")
    try:
        _validate_send_config(runtime_config)
        subject = build_report_email_subject(report, runtime_config)
        email_data = build_report_email_data(
            report,
            runtime_config,
            ai_summary=ai_summary,
            ai_summary_error=ai_summary_error,
        )
        text_content = build_report_email_text(report, runtime_config, email_data=email_data)
        html_content = build_report_email_html(report, runtime_config, email_data=email_data)
        sender = _sender_from_config(runtime_config)
        recipients = runtime_config.get("recipients") or []

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = sender
        message["To"] = ", ".join(recipients)
        message.set_content(text_content)
        message.add_alternative(html_content, subtype="html")

        _send_message(runtime_config, message)
        if record_status:
            update_email_report_status(
                "success",
                report_date="" if force else report_date,
            )
        return {
            "status": "ok",
            "message": f"报告邮件已发送：{report_date}",
            "report_date": report_date,
            "recipients": recipients,
            "subject": subject,
            "important_count": len(email_data["important"]),
            "overview_count": len(email_data["overview"]),
            "ai_summary_error": email_data.get("ai_summary_error", ""),
        }
    except Exception as exc:
        if record_status:
            update_email_report_status("error", error=str(exc))
        raise
