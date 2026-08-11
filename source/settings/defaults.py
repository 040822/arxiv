"""Settings defaults implementation."""

import hashlib
import json
import logging
import os
import re
import secrets
from string import Formatter

from source.config import SCHEDULE_HOUR, SCHEDULE_MINUTE

logger = logging.getLogger(__name__)


logger = logging.getLogger(__name__)


PROVIDER_PRESETS = {
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
    },
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "models": ["deepseek-chat", "deepseek-reasoner"],
    },
    "xiaomi": {
        "name": "小米 Token Plan",
        "base_url": "https://api.xiaomi.com/v1",
        "models": ["MiMo-7B-RL", "MiMo-7B-SFT"],
    },
    "newapi": {
        "name": "New API",
        "base_url": "https://api.newapi.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini", "claude-3-5-sonnet", "claude-3-haiku"],
    },
    "siliconflow": {
        "name": "SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "models": ["deepseek-ai/DeepSeek-V3", "deepseek-ai/DeepSeek-R1", "Qwen/Qwen2.5-72B-Instruct"],
    },
    "zhipu": {
        "name": "智谱 AI",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4-plus", "glm-4-flash", "glm-4-long"],
    },
    "moonshot": {
        "name": "Moonshot (Kimi)",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["moonshot-v1-128k", "moonshot-v1-32k", "moonshot-v1-8k"],
    },
    "custom": {
        "name": "自定义 (OpenAI兼容)",
        "base_url": "",
        "models": [],
    },
}


THINKING_EFFORTS = {"auto", "low", "medium", "high", "max"}


THINKING_BUDGETS = {
    "low": 1024,
    "medium": 4096,
    "high": 8192,
    "max": 16384,
}


OPENAI_REASONING_PREFIXES = ("o1", "o3", "o4", "gpt-5", "gpt-oss")


REQUIRED_PROMPT_FIELDS = {"title", "authors", "abstract", "tag_candidates", "rating_criteria"}


AI_TASK_KEYS = ("basic_analysis", "paper_import", "deep_reading", "report_summary", "recommendation", "paper_chat", "paper_quiz")


AI_TASK_LABELS = {
    "basic_analysis": "基础分析",
    "paper_import": "PDF 元数据提取",
    "deep_reading": "深度阅读",
    "report_summary": "报告导读",
    "recommendation": "个性化推荐",
    "paper_chat": "论文对话",
    "paper_quiz": "论文问答练习",
}


DEFAULT_PROVIDER_OPTIONS = {
    "available_models": [],
    "temperature": 0.3,
    "temperature_enabled": True,
    "max_tokens": 8192,
    "max_tokens_enabled": False,
    "is_thinking": False,
    "thinking_effort": "medium",
}


DEFAULT_AI_TASK_OPTIONS = {
    "basic_analysis": {
        "provider_key": "deepseek",
        "model": "deepseek-chat",
        "temperature": 0.2,
        "temperature_enabled": True,
        "max_tokens": 1200,
        "max_tokens_enabled": True,
        "is_thinking": False,
        "thinking_effort": "medium",
    },
    "paper_import": {
        "provider_key": "deepseek",
        "model": "deepseek-chat",
        "temperature": 0.1,
        "temperature_enabled": True,
        "max_tokens": 1000,
        "max_tokens_enabled": True,
        "is_thinking": False,
        "thinking_effort": "medium",
    },
    "deep_reading": {
        "provider_key": "deepseek",
        "model": "deepseek-reasoner",
        "temperature": 0.2,
        "temperature_enabled": False,
        "max_tokens": 6000,
        "max_tokens_enabled": True,
        "is_thinking": True,
        "thinking_effort": "high",
    },
    "report_summary": {
        "provider_key": "deepseek",
        "model": "deepseek-chat",
        "temperature": 0.3,
        "temperature_enabled": True,
        "max_tokens": 1000,
        "max_tokens_enabled": True,
        "is_thinking": False,
        "thinking_effort": "medium",
    },
    "recommendation": {
        "provider_key": "deepseek",
        "model": "deepseek-chat",
        "temperature": 0.2,
        "temperature_enabled": True,
        "max_tokens": 500,
        "max_tokens_enabled": True,
        "is_thinking": False,
        "thinking_effort": "medium",
    },
    "paper_chat": {
        "provider_key": "deepseek",
        "model": "deepseek-reasoner",
        "temperature": 0.4,
        "temperature_enabled": False,
        "max_tokens": 4000,
        "max_tokens_enabled": True,
        "is_thinking": True,
        "thinking_effort": "high",
    },
    "paper_quiz": {
        "provider_key": "deepseek",
        "model": "deepseek-reasoner",
        "temperature": 0.3,
        "temperature_enabled": False,
        "max_tokens": 3000,
        "max_tokens_enabled": True,
        "is_thinking": True,
        "thinking_effort": "high",
    },
}


DEFAULT_SYSTEM_PROMPT = (
    "你是一位AI和机器人领域的资深研究助手，擅长快速阅读论文、提炼核心贡献、"
    "评估创新性与影响力。请始终返回合法的JSON格式。"
)


DEFAULT_BASIC_ANALYSIS_INSTRUCTION = """请完成低成本基础论文分析。只基于论文标题、作者和摘要判断，不要编写Q&A深度阅读内容。

请严格返回合法 JSON，不要返回额外解释：

{
  "tags": ["标签1", "标签2"],
  "rating": 3,
  "summary_cn": "将论文摘要完整翻译为中文，要求忠实原文、语句通顺、术语准确。",
  "value_comment": "对论文价值的简短评价（2-3句话）"
}

标签选择指南：
{tag_candidates}

标签精度要求：
- 避免过于宽泛的标签，例如 Robot Learning、Embodied AI、Transformer、LLM、Agent、Multimodal
- 优先使用具体技术方法、任务、架构或数据集名称

评级标准：
{rating_criteria}

评级校准要求：
- 必须充分使用 0-5 星，不要把 3 星作为默认安全分
- 普通增量或证据不足的工作应给 1-2 星
- 3 星表示扎实合格但非突出；4 星需要明显强于同类工作
- 5 星非常罕见，只给可能形成方向级影响且证据充分的论文
"""


DEFAULT_DEEP_READING_QUESTIONS = [
    "这篇论文试图解决什么问题？",
    "有哪些相关研究？",
    "论文如何解决这个问题？",
    "论文做了哪些实验？",
    "有什么可以进一步探索的点？",
    "总结一下论文的主要内容",
]


def _build_deep_reading_instruction(questions=None):
    questions = questions or DEFAULT_DEEP_READING_QUESTIONS
    question_list = "\n".join(
        f"{i}. {question}"
        for i, question in enumerate(questions, start=1)
    )
    qa_block = "\\n\\n".join(
        f"### Q{i}: {question}\\n\\n（这里完整回答 Q{i}）"
        for i, question in enumerate(questions, start=1)
    )
    return f"""请对论文内容进行深度阅读分析，只生成 Q&A 深度阅读内容。

必须按顺序完整回答以下 {len(questions)} 个问题，每个问题都要保留对应的 Markdown 标题，不得省略、合并或只回答最后的总结问题：
{question_list}

请严格返回合法 JSON，不要返回额外解释：

{{
  "qa_analysis": "{qa_block}"
}}

重要要求：
1. qa_analysis 中每个 Q&A 使用 Markdown 标题格式（### Qn: 问题）
2. 每个回答应当详尽充分，优先覆盖方法、实验、局限和可复现细节
3. 必须输出 Q1 到 Q{len(questions)} 的全部条目，即使某个问题论文信息不足，也要说明“不足之处”而不是跳过
4. 除 qa_analysis 外不要返回其他字段
5. qa_analysis 中的换行必须用 \\n 转义
"""


DEFAULT_DEEP_READING_INSTRUCTION = _build_deep_reading_instruction()


DEFAULT_REPORT_SUMMARY_INSTRUCTION = """请根据当天论文列表生成一段中文日报导读，用于报告顶部展示。

请严格返回合法 JSON，不要返回额外解释：

{
  "summary": "150-300字中文导读，概括当天论文的主要方向、值得关注的高分工作和整体趋势。"
}

写作要求：
- 不要逐篇罗列所有论文
- 优先总结技术趋势、共同主题和最值得读的论文
- 如果数据不足，请明确说明报告主要基于已有分析结果
"""


DEFAULT_RECOMMENDATION_INSTRUCTION = """请根据用户研究兴趣，判断论文对该用户的个性化相关性和阅读优先级。

请严格返回合法 JSON，不要返回额外解释：

{
  "recommendation_score": 85,
  "recommendation_reason": "1-2句中文理由，说明论文与用户研究兴趣的匹配点。"
}

评分规则：
- 90-100：与研究兴趣高度一致，建议优先精读
- 70-89：明显相关，值得阅读
- 50-69：部分相关，可按时间关注
- 0-49：关联较弱或不相关

要求：
- recommendation_score 必须是 0 到 100 的整数
- recommendation_reason 要具体说明匹配的技术点、任务或应用场景
- 只基于输入的论文信息判断，不要编造摘要中没有的信息
"""


DEFAULT_PAPER_CHAT_SYSTEM_PROMPT = (
    "你是一位陪用户精读论文的研究伙伴。请基于给定论文上下文回答，帮助用户澄清概念、"
    "比较方法、检查理解和形成可复述的认识。不要编造论文中没有的内容；信息不足时明确说明。"
)


DEFAULT_PAPER_CHAT_INSTRUCTION = """你将和用户围绕同一篇论文进行多轮讨论。

要求：
- 优先引用论文上下文中的方法、实验、结论和局限
- 回答要适合学习和复述，不要只堆砌摘要
- 如果用户的理解有误，请温和指出并给出修正版
- 如果用户要求扩展，请区分论文内容和你的推断
"""


DEFAULT_PAPER_QUIZ_SYSTEM_PROMPT = (
    "你是一位严格但友善的论文学习教练。请基于给定论文上下文生成问题、追问用户、"
    "评价答案并帮助用户补齐理解。除非任务明确要求自然语言，否则请严格返回合法 JSON。"
)


DEFAULT_PAPER_QUIZ_INSTRUCTION = """你将基于论文上下文帮助用户主动回忆。

支持三类任务：
1. 生成 quick3 或 standard6 练习题
2. 根据用户答案给出评分和反馈
3. 进行独立苏格拉底式追问

评分反馈必须关注：用户说对了什么、漏掉了什么、有没有误解、如何改成更好的答案。
"""


DEFAULT_PROMPT_PROFILES = {
    "basic_analysis": {
        "system": DEFAULT_SYSTEM_PROMPT,
        "instruction": DEFAULT_BASIC_ANALYSIS_INSTRUCTION,
    },
    "paper_import": {
        "system": "你是严谨的论文元数据抽取助手，必须返回合法 JSON。",
        "instruction": (
            "从论文文本中提取书目信息，只返回 JSON："
            '{"title":"","authors":[],"abstract":"","venue":"",'
            '"published_date":"YYYY-MM-DD或空字符串"}。不确定的字段留空。'
        ),
    },
    "deep_reading": {
        "system": DEFAULT_SYSTEM_PROMPT,
        "instruction": DEFAULT_DEEP_READING_INSTRUCTION,
    },
    "report_summary": {
        "system": DEFAULT_SYSTEM_PROMPT,
        "instruction": DEFAULT_REPORT_SUMMARY_INSTRUCTION,
    },
    "recommendation": {
        "system": DEFAULT_SYSTEM_PROMPT,
        "instruction": DEFAULT_RECOMMENDATION_INSTRUCTION,
    },
    "paper_chat": {
        "system": DEFAULT_PAPER_CHAT_SYSTEM_PROMPT,
        "instruction": DEFAULT_PAPER_CHAT_INSTRUCTION,
    },
    "paper_quiz": {
        "system": DEFAULT_PAPER_QUIZ_SYSTEM_PROMPT,
        "instruction": DEFAULT_PAPER_QUIZ_INSTRUCTION,
    },
}


DEFAULT_SETTINGS = {
    "settings_schema_version": 3,
    "concurrency": 5,
    "per_page": 20,
    "admin_password": "",
    "admin_password_change_recommended": False,
    "session_secret": "",
    "personalization": {
        "research_interests": "",
    },
    "webdav_backup": {
        "enabled": False,
        "url": "",
        "username": "",
        "password": "",
        "remote_dir": "arxiv-backups",
        "history_days": 3,
        "last_status": "",
        "last_success_at": "",
        "last_error": "",
        "last_uploaded_file": "",
    },
    "email_report": {
        "enabled": False,
        "smtp_host": "",
        "smtp_port": 587,
        "security": "starttls",
        "username": "",
        "password": "",
        "sender": "",
        "recipients": [],
        "subject_template": "AI 论文日报 {date} - {paper_count} 篇论文",
        "site_url": "",
        "important_score_threshold": 80,
        "overview_limit": 20,
        "last_status": "",
        "last_success_at": "",
        "last_error": "",
        "last_sent_report_date": "",
    },
    "schedule": {
        "enabled": True,
        "days_of_week": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        "hour": SCHEDULE_HOUR,
        "minute": SCHEDULE_MINUTE,
        "fetch_days": 3,
        "analyze_limit": 1000,
        "fetch_retry_interval_minutes": 10,
        "fetch_max_retries": 20,
    },
    "providers": {
        "deepseek": {
            "name": "DeepSeek",
            "api_key": "",
            "base_url": "https://api.deepseek.com",
            "available_models": ["deepseek-chat", "deepseek-reasoner"],
        }
    },
    "prompts": {
        "system_prompt": DEFAULT_SYSTEM_PROMPT,
        "user_prompt": DEFAULT_DEEP_READING_INSTRUCTION,
    },
    "prompt_profiles": DEFAULT_PROMPT_PROFILES,
    "ai_tasks": DEFAULT_AI_TASK_OPTIONS,
}
