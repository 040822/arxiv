"""Public report-email interface."""

from .content import (
    build_report_email_data,
    build_report_email_html,
    build_report_email_subject,
    build_report_email_text,
)
from .service import send_report_email

__all__ = [
    "build_report_email_data",
    "build_report_email_html",
    "build_report_email_text",
    "build_report_email_subject",
    "send_report_email",
]
