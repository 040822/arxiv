"""Stable public interface for AI paper analysis and learning workflows."""

from .batch import analyze_papers, analyze_pending_papers, recommend_papers, recommend_pending_papers
from .client import get_openai_client
from .core import build_paper_learning_messages, get_learning_paper_text
from .learning import chat_about_paper, generate_paper_quiz, grade_quiz_answer, socratic_reply
from .papers import analyze_paper_basic, analyze_paper_full, analyze_paper_recommendation, extract_paper_import_metadata
from .report_summary import generate_report_ai_summary

__all__ = [
    "get_openai_client",
    "extract_paper_import_metadata",
    "analyze_paper_basic",
    "analyze_paper_full",
    "analyze_paper_recommendation",
    "get_learning_paper_text",
    "build_paper_learning_messages",
    "chat_about_paper",
    "generate_paper_quiz",
    "grade_quiz_answer",
    "socratic_reply",
    "generate_report_ai_summary",
    "analyze_papers",
    "recommend_papers",
    "recommend_pending_papers",
    "analyze_pending_papers",
]
