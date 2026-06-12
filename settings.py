"""
运行时配置管理模块

本模块负责管理项目的运行时配置，配置存储在 data/settings.json 文件中，
可通过 Web 界面动态修改，无需重启服务即可生效。

与 config.py 的区别：
- config.py：硬编码的常量配置（分类列表、标签候选、评级标准等），修改需改代码
- settings.py（本模块）：运行时可变配置（API密钥、供应商、Prompt模板等），Web界面可改

主要功能：
- AI 供应商管理（预设配置、增删切换）
- API 配置获取（供 analyzer.py 调用）
- Prompt 模板管理
- 代理/抓取/分页等运行时参数
- 管理员密码（SHA-256 哈希存储）

配置文件结构示例见 DEFAULT_SETTINGS 变量。
"""

import json
import os
import logging
import re
from string import Formatter
from config import (
    DB_DIR,
    FETCH_BATCH_DAYS,
    FETCH_BATCH_DELAY,
    FETCH_REQUEST_DELAY,
    SCHEDULE_HOUR,
    SCHEDULE_MINUTE,
)

logger = logging.getLogger(__name__)

# 配置文件路径：data/settings.json
SETTINGS_PATH = os.path.join(DB_DIR, "settings.json")

# ============================================================================
# 预设供应商配置
# ============================================================================
# 用于 Web 设置页的"添加供应商"功能，提供常见 AI API 的预设模板
# 用户选择预设后，base_url 和 models 会自动填充，api_key 需手动输入
# "custom" 为自定义 OpenAI 兼容接口，所有字段留空由用户填写
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
AI_TASK_KEYS = ("basic_analysis", "deep_reading", "report_summary")
AI_TASK_LABELS = {
    "basic_analysis": "基础分析",
    "deep_reading": "深度阅读",
    "report_summary": "报告导读",
}

DEFAULT_PROVIDER_OPTIONS = {
    "available_models": [],
    "temperature": 0.3,
    "temperature_enabled": True,
    "top_p": 1.0,
    "top_p_enabled": False,
    "presence_penalty": 0.0,
    "presence_penalty_enabled": False,
    "frequency_penalty": 0.0,
    "frequency_penalty_enabled": False,
    "max_tokens": 8192,
    "max_tokens_enabled": False,
    "is_thinking": False,
    "thinking_effort": "medium",
}

DEFAULT_AI_TASK_OPTIONS = {
    "basic_analysis": {
        "provider_key": "",
        "model": "",
        "temperature": 0.2,
        "temperature_enabled": True,
        "top_p": 1.0,
        "top_p_enabled": False,
        "presence_penalty": 0.0,
        "presence_penalty_enabled": False,
        "frequency_penalty": 0.0,
        "frequency_penalty_enabled": False,
        "max_tokens": 1200,
        "max_tokens_enabled": True,
        "is_thinking": False,
        "thinking_effort": "medium",
    },
    "deep_reading": {
        "provider_key": "",
        "model": "",
        "temperature": 0.2,
        "temperature_enabled": False,
        "top_p": 1.0,
        "top_p_enabled": False,
        "presence_penalty": 0.0,
        "presence_penalty_enabled": False,
        "frequency_penalty": 0.0,
        "frequency_penalty_enabled": False,
        "max_tokens": 6000,
        "max_tokens_enabled": True,
        "is_thinking": True,
        "thinking_effort": "high",
    },
    "report_summary": {
        "provider_key": "",
        "model": "",
        "temperature": 0.3,
        "temperature_enabled": True,
        "top_p": 1.0,
        "top_p_enabled": False,
        "presence_penalty": 0.0,
        "presence_penalty_enabled": False,
        "frequency_penalty": 0.0,
        "frequency_penalty_enabled": False,
        "max_tokens": 1000,
        "max_tokens_enabled": True,
        "is_thinking": False,
        "thinking_effort": "medium",
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

{rating_criteria}
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

DEFAULT_PROMPT_PROFILES = {
    "basic_analysis": {
        "system": DEFAULT_SYSTEM_PROMPT,
        "instruction": DEFAULT_BASIC_ANALYSIS_INSTRUCTION,
    },
    "deep_reading": {
        "system": DEFAULT_SYSTEM_PROMPT,
        "instruction": DEFAULT_DEEP_READING_INSTRUCTION,
    },
    "report_summary": {
        "system": DEFAULT_SYSTEM_PROMPT,
        "instruction": DEFAULT_REPORT_SUMMARY_INSTRUCTION,
    },
}

# ============================================================================
# 默认配置结构
# ============================================================================
# 当 settings.json 不存在或损坏时，使用此默认配置初始化
# 各字段说明：
#   active_provider: 当前激活的供应商 key（对应 providers 中的键名）
#   concurrency: AI 分析的并发请求数
#   providers: 已配置的供应商列表，每个包含 api_key/base_url/model 等
#   prompts: AI 分析使用的 Prompt 模板
DEFAULT_SETTINGS = {
    "active_provider": "deepseek",
    "concurrency": 5,
    "per_page": 20,
    "schedule": {
        "enabled": True,
        "hour": SCHEDULE_HOUR,
        "minute": SCHEDULE_MINUTE,
    },
    "providers": {
        "deepseek": {
            "name": "DeepSeek",
            "api_key": "",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "temperature": 0.3,
            "max_tokens": 8192,
            "max_tokens_enabled": False,
            "temperature_enabled": True,
            "top_p": 1.0,
            "top_p_enabled": False,
            "presence_penalty": 0.0,
            "presence_penalty_enabled": False,
            "frequency_penalty": 0.0,
            "frequency_penalty_enabled": False,
            "is_thinking": False,
            "thinking_effort": "medium",
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


# ============================================================================
# 内部工具函数
# ============================================================================

def _ensure_dir():
    """确保数据目录存在，如果不存在则递归创建。"""
    os.makedirs(DB_DIR, exist_ok=True)


def _as_bool(value, default=False):
    """将前端/JSON 中的布尔值安全转换为 bool。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _as_float(value, default):
    """将数值配置安全转换为 float。"""
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value, default):
    """将数值配置安全转换为 int。"""
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_schedule(schedule):
    """补齐并约束定时任务配置。"""
    schedule = dict(schedule or {})
    hour = _as_int(schedule.get("hour"), SCHEDULE_HOUR)
    minute = _as_int(schedule.get("minute"), SCHEDULE_MINUTE)
    return {
        "enabled": _as_bool(schedule.get("enabled"), True),
        "hour": max(0, min(23, hour)),
        "minute": max(0, min(59, minute)),
    }


def _normalize_fetch_config(fetch):
    """补齐并约束 arXiv 抓取配置。"""
    fetch = dict(fetch or {})
    request_delay = _as_float(fetch.get("request_delay"), FETCH_REQUEST_DELAY)
    batch_days = _as_int(fetch.get("batch_days"), FETCH_BATCH_DAYS)
    batch_delay = _as_float(fetch.get("batch_delay"), FETCH_BATCH_DELAY)
    return {
        "request_delay": max(3.0, min(300.0, request_delay)),
        "batch_days": max(1, min(365, batch_days)),
        "batch_delay": max(1.0, min(1800.0, batch_delay)),
    }


def _select_provider_key(provider_key, active_provider, providers):
    """选择一个存在的供应商 key，用于任务级路由。"""
    providers = providers or {}
    provider_key = str(provider_key or "").strip()
    if provider_key in providers:
        return provider_key
    if active_provider in providers:
        return active_provider
    return next(iter(providers.keys()), provider_key)


def _normalize_ai_task_config(task, task_key, active_provider, providers):
    """补齐并约束单个 AI 任务的模型和参数配置。"""
    task = dict(task or {})
    defaults = DEFAULT_AI_TASK_OPTIONS.get(task_key, DEFAULT_AI_TASK_OPTIONS["basic_analysis"])
    provider_key = _select_provider_key(
        task.get("provider_key") or defaults.get("provider_key"),
        active_provider,
        providers,
    )
    provider = normalize_provider_config((providers or {}).get(provider_key, {}), provider_key)

    model = str(task.get("model") or defaults.get("model") or provider.get("model", "")).strip()
    thinking_effort = str(task.get("thinking_effort", defaults.get("thinking_effort", "medium"))).lower()
    if thinking_effort not in THINKING_EFFORTS:
        thinking_effort = defaults.get("thinking_effort", "medium")

    return {
        "provider_key": provider_key,
        "model": model,
        "temperature": max(0.0, min(2.0, _as_float(task.get("temperature"), defaults["temperature"]))),
        "temperature_enabled": _as_bool(task.get("temperature_enabled"), defaults["temperature_enabled"]),
        "top_p": max(0.0, min(1.0, _as_float(task.get("top_p"), defaults["top_p"]))),
        "top_p_enabled": _as_bool(task.get("top_p_enabled"), defaults["top_p_enabled"]),
        "presence_penalty": max(-2.0, min(2.0, _as_float(task.get("presence_penalty"), defaults["presence_penalty"]))),
        "presence_penalty_enabled": _as_bool(
            task.get("presence_penalty_enabled"),
            defaults["presence_penalty_enabled"],
        ),
        "frequency_penalty": max(-2.0, min(2.0, _as_float(task.get("frequency_penalty"), defaults["frequency_penalty"]))),
        "frequency_penalty_enabled": _as_bool(
            task.get("frequency_penalty_enabled"),
            defaults["frequency_penalty_enabled"],
        ),
        "max_tokens": max(1, min(200000, _as_int(task.get("max_tokens"), defaults["max_tokens"]))),
        "max_tokens_enabled": _as_bool(task.get("max_tokens_enabled"), defaults["max_tokens_enabled"]),
        "is_thinking": _as_bool(task.get("is_thinking"), defaults["is_thinking"]),
        "thinking_effort": thinking_effort,
    }


def _normalize_ai_tasks(ai_tasks, active_provider, providers):
    """补齐全部 AI 任务路由配置。"""
    ai_tasks = dict(ai_tasks or {})
    return {
        task_key: _normalize_ai_task_config(ai_tasks.get(task_key), task_key, active_provider, providers)
        for task_key in AI_TASK_KEYS
    }


def _extract_deep_reading_questions(instruction):
    """从旧版深度阅读 prompt 中提取 Q&A 问题，提取失败时使用默认问题。"""
    questions = []
    text = (instruction or "").replace("\\n", "\n")
    for match in re.finditer(r"### Q\d+:\s*([^\n]+)", text):
        question = match.group(1).strip()
        if question:
            questions.append(question)
    return questions or DEFAULT_DEEP_READING_QUESTIONS


def _is_legacy_deep_reading_instruction(instruction):
    """判断是否为旧默认/旧问题编辑器生成的深度阅读 prompt。"""
    text = instruction or ""
    has_base_fields = all(
        marker in text
        for marker in ('"tags"', '"rating"', '"summary_cn"', '"value_comment"')
    )
    has_old_template_shape = any(
        marker in text
        for marker in (
            "然后给出标签、评级、中文摘要和价值评价",
            "然后给出标签和评级",
            "论文标题: {title}",
            "标签精度要求（重要）",
        )
    )
    has_old_guidance = ("标签选择指南" in text or "{tag_candidates}" in text) and (
        "评级标准" in text or "{rating_criteria}" in text
    )
    return "qa_analysis" in text and has_base_fields and has_old_template_shape and has_old_guidance


def _is_weak_qa_only_deep_reading_instruction(instruction):
    """判断是否为上一版 Q&A-only 但约束过弱的自动生成 prompt。"""
    text = instruction or ""
    return (
        "请对论文内容进行深度阅读分析，只生成 Q&A 深度阅读内容" in text
        and "qa_analysis" in text
        and "详细回答..." in text
        and "必须按顺序完整回答以下" not in text
    )


def _migrate_deep_reading_instruction(instruction):
    """仅迁移旧默认/旧问题模板；明显自定义的 prompt 原样保留。"""
    if _is_legacy_deep_reading_instruction(instruction) or _is_weak_qa_only_deep_reading_instruction(instruction):
        return _build_deep_reading_instruction(_extract_deep_reading_questions(instruction))
    return instruction


def _normalize_prompt_profiles(profiles, legacy_prompts=None):
    """补齐基础分析、深度阅读、报告导读三套 prompt profile。"""
    result = json.loads(json.dumps(DEFAULT_PROMPT_PROFILES))
    profiles = profiles if isinstance(profiles, dict) else {}

    if not profiles and isinstance(legacy_prompts, dict):
        system = legacy_prompts.get("system_prompt")
        instruction = legacy_prompts.get("user_prompt")
        if system or instruction:
            result["deep_reading"] = {
                "system": system or result["deep_reading"]["system"],
                "instruction": _migrate_deep_reading_instruction(
                    instruction or result["deep_reading"]["instruction"]
                ),
            }

    for task_key in AI_TASK_KEYS:
        profile = profiles.get(task_key)
        if not isinstance(profile, dict):
            continue
        system = profile.get("system")
        instruction = profile.get("instruction")
        if system is not None:
            result[task_key]["system"] = str(system)
        if instruction is not None:
            instruction = str(instruction)
            if task_key == "deep_reading":
                instruction = _migrate_deep_reading_instruction(instruction)
            result[task_key]["instruction"] = instruction
    return result


def _model_leaf(model):
    """获取模型名最后一段，用于兼容 vendor/model 形式。"""
    return (model or "").strip().lower().split("/")[-1]


def is_openai_reasoning_model(model):
    """根据 OpenAI 模型名启发式判断是否为 reasoning 模型。"""
    leaf = _model_leaf(model)
    return leaf.startswith(OPENAI_REASONING_PREFIXES)


def _is_deepseek_v4_model(model):
    leaf = _model_leaf(model)
    return leaf.startswith("deepseek-v4") or "deepseek-v4" in leaf


def _is_deepseek_reasoning_model(model):
    leaf = _model_leaf(model)
    return "deepseek-reasoner" in leaf or "deepseek-r1" in leaf or leaf.startswith("r1")


def _is_qwen_like_model(model):
    lower_model = (model or "").lower()
    return any(token in lower_model for token in ("qwen", "qwq", "mimo"))


def _is_openai_base_url(base_url):
    return "api.openai.com" in (base_url or "").lower()


def _is_deepseek_base_url(base_url):
    return "deepseek" in (base_url or "").lower()


def _is_qwen_like_base_url(base_url):
    lower_url = (base_url or "").lower()
    return any(token in lower_url for token in ("dashscope", "aliyuncs", "qwen", "xiaomi", "mimo"))


def get_thinking_protocol(provider):
    """
    推断当前供应商/模型的思考模式协议。

    返回值用于构建 OpenAI 兼容 Chat Completions 参数：
    openai_reasoning/deepseek_v4/deepseek_legacy/qwen_compatible/generic_enable_thinking/none
    """
    model = provider.get("model", "")
    base_url = provider.get("base_url", "")

    if _is_deepseek_v4_model(model):
        return "deepseek_v4"
    if _is_deepseek_reasoning_model(model):
        return "deepseek_legacy"
    if _is_qwen_like_model(model) or _is_qwen_like_base_url(base_url):
        return "qwen_compatible"
    if is_openai_reasoning_model(model) and _is_openai_base_url(base_url):
        return "openai_reasoning"
    if _is_deepseek_base_url(base_url) and provider.get("is_thinking"):
        return "deepseek_v4"
    if provider.get("is_thinking"):
        return "generic_enable_thinking"
    return "none"


def is_known_thinking_model(provider):
    """根据模型名判断无需用户手动勾选也应按思考模型处理的模型。"""
    model = provider.get("model", "")
    base_url = provider.get("base_url", "")
    return (
        is_openai_reasoning_model(model)
        and _is_openai_base_url(base_url)
    ) or _is_deepseek_reasoning_model(model)


def normalize_provider_config(config, key=None):
    """补齐供应商配置字段，兼容旧版 settings.json。"""
    config = dict(config or {})
    preset = PROVIDER_PRESETS.get(key or "", {})

    normalized = {
        "name": config.get("name", preset.get("name", key or "")),
        "api_key": config.get("api_key", ""),
        "base_url": config.get("base_url", preset.get("base_url", "")),
        "model": config.get("model", ""),
    }

    available_models = config.get("available_models")
    if not isinstance(available_models, list):
        available_models = preset.get("models", [])
    normalized["available_models"] = [str(m) for m in available_models if str(m).strip()]

    raw_is_thinking = _as_bool(config.get("is_thinking"), DEFAULT_PROVIDER_OPTIONS["is_thinking"])
    normalized["is_thinking"] = raw_is_thinking
    normalized["effective_is_thinking"] = raw_is_thinking or is_known_thinking_model({**config, **normalized})

    normalized["temperature"] = _as_float(config.get("temperature"), DEFAULT_PROVIDER_OPTIONS["temperature"])
    normalized["temperature_enabled"] = _as_bool(
        config.get("temperature_enabled"),
        not normalized["is_thinking"],
    )
    normalized["top_p"] = _as_float(config.get("top_p"), DEFAULT_PROVIDER_OPTIONS["top_p"])
    normalized["top_p_enabled"] = _as_bool(config.get("top_p_enabled"), DEFAULT_PROVIDER_OPTIONS["top_p_enabled"])
    normalized["presence_penalty"] = _as_float(
        config.get("presence_penalty"),
        DEFAULT_PROVIDER_OPTIONS["presence_penalty"],
    )
    normalized["presence_penalty_enabled"] = _as_bool(
        config.get("presence_penalty_enabled"),
        DEFAULT_PROVIDER_OPTIONS["presence_penalty_enabled"],
    )
    normalized["frequency_penalty"] = _as_float(
        config.get("frequency_penalty"),
        DEFAULT_PROVIDER_OPTIONS["frequency_penalty"],
    )
    normalized["frequency_penalty_enabled"] = _as_bool(
        config.get("frequency_penalty_enabled"),
        DEFAULT_PROVIDER_OPTIONS["frequency_penalty_enabled"],
    )

    max_tokens = _as_int(config.get("max_tokens"), DEFAULT_PROVIDER_OPTIONS["max_tokens"])
    normalized["max_tokens"] = max_tokens
    normalized["max_tokens_enabled"] = _as_bool(
        config.get("max_tokens_enabled"),
        DEFAULT_PROVIDER_OPTIONS["max_tokens_enabled"],
    )

    thinking_effort = str(config.get("thinking_effort", DEFAULT_PROVIDER_OPTIONS["thinking_effort"])).lower()
    if thinking_effort not in THINKING_EFFORTS:
        thinking_effort = DEFAULT_PROVIDER_OPTIONS["thinking_effort"]
    normalized["thinking_effort"] = thinking_effort

    for field, default in DEFAULT_PROVIDER_OPTIONS.items():
        normalized.setdefault(field, default)
    return normalized


def _normalize_effort(effort):
    effort = str(effort or DEFAULT_PROVIDER_OPTIONS["thinking_effort"]).lower()
    return effort if effort in THINKING_EFFORTS else DEFAULT_PROVIDER_OPTIONS["thinking_effort"]


def _map_openai_effort(effort):
    effort = _normalize_effort(effort)
    if effort == "auto":
        return None
    if effort == "max":
        return "high"
    return effort if effort in {"low", "medium", "high"} else "medium"


def _map_deepseek_effort(effort):
    effort = _normalize_effort(effort)
    if effort in {"auto", "low", "medium", "high"}:
        return "high"
    return "max"


def _map_thinking_budget(effort):
    effort = _normalize_effort(effort)
    if effort == "auto":
        return None
    return THINKING_BUDGETS.get(effort, THINKING_BUDGETS["medium"])


def _should_use_max_completion_tokens(provider, protocol):
    return protocol == "openai_reasoning" or (
        _is_openai_base_url(provider.get("base_url", ""))
        and is_openai_reasoning_model(provider.get("model", ""))
    )


def build_chat_completion_kwargs(provider, messages, token_limit_override=None, force_thinking=False):
    """
    统一构建 OpenAI 兼容 Chat Completions 请求参数。

    - 普通模型只发送显式启用的采样参数和 token 上限。
    - 思考模型省略 temperature/top_p/presence_penalty/frequency_penalty。
    - 不同供应商的思考参数在这里做协议映射。
    """
    cfg = normalize_provider_config(provider)
    protocol = get_thinking_protocol(cfg)
    if force_thinking and protocol == "none" and not _is_openai_base_url(cfg.get("base_url", "")):
        protocol = "generic_enable_thinking"
    thinking_enabled = bool(force_thinking or cfg.get("is_thinking") or is_known_thinking_model(cfg))

    kwargs = {
        "model": cfg.get("model", ""),
        "messages": messages,
    }
    extra_body = {}

    if thinking_enabled:
        effort = cfg.get("thinking_effort", DEFAULT_PROVIDER_OPTIONS["thinking_effort"])
        if protocol == "openai_reasoning":
            mapped = _map_openai_effort(effort)
            if mapped:
                kwargs["reasoning_effort"] = mapped
        elif protocol == "deepseek_v4":
            kwargs["reasoning_effort"] = _map_deepseek_effort(effort)
            extra_body["thinking"] = {"type": "enabled"}
        elif protocol == "qwen_compatible":
            extra_body["enable_thinking"] = True
            budget = _map_thinking_budget(effort)
            if budget:
                extra_body["thinking_budget"] = budget
        elif protocol == "generic_enable_thinking":
            extra_body["enable_thinking"] = True
    else:
        if protocol == "deepseek_v4":
            extra_body["thinking"] = {"type": "disabled"}
        if cfg.get("temperature_enabled"):
            kwargs["temperature"] = cfg.get("temperature", DEFAULT_PROVIDER_OPTIONS["temperature"])
        if cfg.get("top_p_enabled"):
            kwargs["top_p"] = cfg.get("top_p", DEFAULT_PROVIDER_OPTIONS["top_p"])
        if cfg.get("presence_penalty_enabled"):
            kwargs["presence_penalty"] = cfg.get("presence_penalty", DEFAULT_PROVIDER_OPTIONS["presence_penalty"])
        if cfg.get("frequency_penalty_enabled"):
            kwargs["frequency_penalty"] = cfg.get("frequency_penalty", DEFAULT_PROVIDER_OPTIONS["frequency_penalty"])

    token_limit = token_limit_override
    if token_limit is None and cfg.get("max_tokens_enabled"):
        token_limit = cfg.get("max_tokens")
    if token_limit:
        token_key = "max_completion_tokens" if _should_use_max_completion_tokens(cfg, protocol) else "max_tokens"
        kwargs[token_key] = int(token_limit)

    if extra_body:
        kwargs["extra_body"] = extra_body
    return kwargs


def _migrate_old_settings(data):
    """
    迁移旧版配置格式到新版多供应商格式。

    旧版配置将 api_key/base_url/model 等字段平铺在顶层，
    新版将其收纳到 providers 字典中，支持多供应商切换。

    如果数据已是新版格式（包含 "providers" 字段），直接返回。
    否则将旧版平铺字段重组为 providers[active_provider] 结构。

    参数:
        data: 从 settings.json 读取的原始字典

    返回:
        迁移后的配置字典（新版格式）
    """
    if "providers" in data:
        return data
    migrated = {
        "active_provider": data.get("provider", "deepseek"),
        "providers": {}
    }
    prov_key = data.get("provider", "deepseek")
    preset = PROVIDER_PRESETS.get(prov_key, {})
    migrated["providers"][prov_key] = {
        "name": preset.get("name", prov_key),
        "api_key": data.get("api_key", ""),
        "base_url": data.get("base_url", preset.get("base_url", "")),
        "model": data.get("model", ""),
        "temperature": data.get("temperature", 0.3),
        "max_tokens": data.get("max_tokens", 1000),
        "max_tokens_enabled": data.get("max_tokens_enabled", False),
        "is_thinking": data.get("is_thinking", False),
        "thinking_effort": data.get("thinking_effort", "medium"),
    }
    return migrated


# ============================================================================
# 配置加载与保存
# ============================================================================

def load_settings():
    """
    加载运行时配置。

    加载流程：
    1. 确保数据目录存在
    2. 如果配置文件不存在，写入默认配置并返回
    3. 读取配置文件，执行旧版格式迁移
    4. 以 DEFAULT_SETTINGS 为基础，用文件中的值覆盖（合并逻辑）
    5. 补齐新增的供应商参数开关，兼容旧配置

    注意：添加新配置字段时，必须在此函数的合并逻辑中显式添加对应的
    if "key" in migrated 判断，否则新字段在读取时会被 DEFAULT_SETTINGS
    的默认值覆盖而丢失！这是已踩过的坑。

    返回:
        合并后的完整配置字典
    """
    _ensure_dir()
    if not os.path.exists(SETTINGS_PATH):
        default_settings = json.loads(json.dumps(DEFAULT_SETTINGS))
        default_settings["ai_tasks"] = _normalize_ai_tasks(
            default_settings.get("ai_tasks", {}),
            default_settings.get("active_provider", ""),
            default_settings.get("providers", {}),
        )
        default_settings["prompt_profiles"] = _normalize_prompt_profiles(default_settings.get("prompt_profiles", {}))
        save_settings(default_settings)
        return default_settings
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        # 执行旧版格式迁移
        migrated = _migrate_old_settings(saved)
        # 深拷贝默认配置作为合并基础
        merged = json.loads(json.dumps(DEFAULT_SETTINGS))
        # 逐字段合并：文件中有的字段覆盖默认值
        merged["active_provider"] = migrated.get("active_provider", "deepseek")
        if "concurrency" in migrated:
            merged["concurrency"] = migrated["concurrency"]
        if "per_page" in migrated:
            merged["per_page"] = migrated["per_page"]
        if "schedule" in migrated:
            merged["schedule"] = _normalize_schedule(migrated["schedule"])
        if "proxy" in migrated:
            merged["proxy"] = migrated["proxy"]
        if "fetch" in migrated:
            merged["fetch"] = _normalize_fetch_config(migrated["fetch"])
        if "admin_password" in migrated:
            merged["admin_password"] = migrated["admin_password"]
        if "prompts" in migrated:
            merged["prompts"] = migrated["prompts"]
        # 合并供应商配置，同时补齐新增参数开关
        for k, v in migrated.get("providers", {}).items():
            merged["providers"][k] = normalize_provider_config(v, k)
        merged["ai_tasks"] = _normalize_ai_tasks(
            migrated.get("ai_tasks", {}),
            merged.get("active_provider", ""),
            merged.get("providers", {}),
        )
        merged["prompt_profiles"] = _normalize_prompt_profiles(
            migrated.get("prompt_profiles", {}),
            migrated.get("prompts", {}),
        )
        return merged
    except Exception as e:
        logger.error(f"Failed to load settings: {e}")
        return json.loads(json.dumps(DEFAULT_SETTINGS))


def save_settings(settings):
    """
    将配置字典保存到 settings.json 文件。

    使用 ensure_ascii=False 保留中文字符，indent=2 格式化输出便于手动编辑。

    参数:
        settings: 要保存的配置字典

    返回:
        bool: 保存成功返回 True，失败返回 False
    """
    _ensure_dir()
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Failed to save settings: {e}")
        return False


# ============================================================================
# AI 配置获取函数（供 analyzer.py 调用）
# ============================================================================

def get_active_provider():
    """
    获取当前激活供应商的完整配置。

    从 settings 中读取 active_provider 键名，然后从 providers 字典中
    取出对应的供应商配置。

    返回:
        dict: 当前激活供应商的配置字典，包含 name/api_key/base_url/model 等字段。
              如果激活的供应商不存在，返回空字典。
    """
    settings = load_settings()
    active = settings.get("active_provider", "deepseek")
    providers = settings.get("providers", {})
    return providers.get(active, {})


def get_ai_config():
    """
    获取 AI API 调用所需的精简配置。

    这是 analyzer.py 调用 OpenAI API 时使用的核心函数，提取供应商配置中
    与 API 调用直接相关的字段。

    返回:
        dict: 包含以下字段的配置字典：
            - api_key: API 密钥
            - base_url: API 端点地址
            - model: 模型名称
            - temperature/top_p/presence_penalty/frequency_penalty: 采样参数
            - max_tokens/max_tokens_enabled: 可选最大生成 token 数
            - is_thinking/thinking_effort: 思考模式配置
    """
    prov = get_active_provider()
    return normalize_provider_config(prov)


def get_ai_tasks():
    """获取三类 AI 功能的任务级模型与参数配置。"""
    settings = load_settings()
    return _normalize_ai_tasks(
        settings.get("ai_tasks", {}),
        settings.get("active_provider", ""),
        settings.get("providers", {}),
    )


def save_ai_tasks(ai_tasks):
    """保存任务级模型与参数配置。"""
    settings = load_settings()
    settings["ai_tasks"] = _normalize_ai_tasks(
        ai_tasks,
        settings.get("active_provider", ""),
        settings.get("providers", {}),
    )
    return save_settings(settings)


def get_ai_task_config(task_key):
    """
    获取某个 AI 功能的实际调用配置。

    任务配置只保存 provider_key/model/参数开关；这里会与供应商 API Key/Base URL 合并，
    返回值可直接传给 OpenAI 客户端和 build_chat_completion_kwargs()。
    """
    settings = load_settings()
    if task_key not in AI_TASK_KEYS:
        task_key = "basic_analysis"
    tasks = _normalize_ai_tasks(
        settings.get("ai_tasks", {}),
        settings.get("active_provider", ""),
        settings.get("providers", {}),
    )
    task = tasks[task_key]
    provider_key = _select_provider_key(
        task.get("provider_key"),
        settings.get("active_provider", ""),
        settings.get("providers", {}),
    )
    provider = normalize_provider_config(settings.get("providers", {}).get(provider_key, {}), provider_key)
    merged = {**provider, **task}
    merged["provider_key"] = provider_key
    merged["provider_name"] = provider.get("name", provider_key)
    merged["task_key"] = task_key
    if not merged.get("model"):
        merged["model"] = provider.get("model", "")
    return normalize_provider_config(merged, provider_key) | {
        "provider_key": provider_key,
        "provider_name": provider.get("name", provider_key),
        "task_key": task_key,
    }


# ============================================================================
# 运行时参数获取/保存函数
# ============================================================================

def get_concurrency():
    """
    获取 AI 分析的并发请迂数。

    并发数控制 ThreadPoolExecutor 同时发起的 API 请求数量，
    默认值为 5。过大会触发 API 速率限制，过小则分析速度慢。

    返回:
        int: 并发请求数
    """
    settings = load_settings()
    return settings.get("concurrency", 5)


def get_per_page():
    """
    获取 Web 界面每页显示的论文数量。

    返回:
        int: 每页论文数，默认 20
    """
    settings = load_settings()
    return settings.get("per_page", 20)


def get_fetch_config():
    """
    获取论文抓取的运行时配置。

    抓取配置控制 arXiv API 的请求行为：
    - request_delay: 两次 API 请求之间的间隔秒数（避免被限流）
    - batch_days: 每个批次抓取的天数范围
    - batch_delay: 两个批次之间的间隔秒数

    如果用户未自定义，使用 config.py 中的默认值。

    返回:
        dict: 包含 request_delay/batch_days/batch_delay 的配置字典
    """
    settings = load_settings()
    return _normalize_fetch_config(settings.get("fetch", {}))


def get_schedule_config():
    """
    获取每日自动任务配置。

    返回:
        dict: enabled/hour/minute，默认来自 config.py 的 SCHEDULE_HOUR/MINUTE
    """
    settings = load_settings()
    return _normalize_schedule(settings.get("schedule", {}))


def save_schedule_config(schedule_config):
    """
    保存每日自动任务配置到 settings.json。

    参数:
        schedule_config: 包含 enabled/hour/minute 的字典

    返回:
        bool: 保存是否成功
    """
    settings = load_settings()
    settings["schedule"] = _normalize_schedule(schedule_config)
    return save_settings(settings)


def save_fetch_config(fetch_config):
    """
    保存论文抓取配置到 settings.json。

    参数:
        fetch_config: 包含 request_delay/batch_days/batch_delay 的字典

    返回:
        bool: 保存是否成功
    """
    settings = load_settings()
    settings["fetch"] = _normalize_fetch_config(fetch_config)
    return save_settings(settings)


def get_proxy_config():
    """
    获取 HTTP 代理配置。

    代理用于在无法直接访问 arXiv/OpenAI 等服务时进行网络转发。
    配置项包括是否启用、HTTP 代理地址、HTTPS 代理地址。

    返回:
        dict: 包含以下字段的代理配置：
            - enabled: bool, 是否启用代理
            - http: str, HTTP 代理地址（如 "http://127.0.0.1:7890"）
            - https: str, HTTPS 代理地址
    """
    settings = load_settings()
    proxy = settings.get("proxy", {})
    return {
        "enabled": proxy.get("enabled", False),
        "http": proxy.get("http", ""),
        "https": proxy.get("https", ""),
    }


def save_proxy_config(proxy_config):
    """
    保存代理配置到 settings.json。

    参数:
        proxy_config: 包含 enabled/http/https 的代理配置字典

    返回:
        bool: 保存是否成功
    """
    settings = load_settings()
    settings["proxy"] = proxy_config
    return save_settings(settings)


# ============================================================================
# 供应商 CRUD 操作
# ============================================================================

def get_all_providers():
    """
    获取所有已配置的供应商列表。

    返回:
        dict: 以供应商 key 为键、配置字典为值的字典
    """
    return load_settings().get("providers", {})


def get_provider_presets():
    """
    获取所有预设供应商模板。

    预设模板用于 Web 设置页的"添加供应商"下拉菜单，
    提供常见 AI API 的 base_url 和 models 预填值。

    返回:
        dict: 预设供应商配置字典（PROVIDER_PRESETS）
    """
    return PROVIDER_PRESETS


def add_provider(key, config):
    """
    添加新供应商到配置。

    参数:
        key: 供应商唯一标识（如 "openai"、"my_api"）
        config: 供应商配置字典，包含 name/api_key/base_url/model 等

    返回:
        bool: 保存是否成功
    """
    settings = load_settings()
    settings["providers"][key] = normalize_provider_config(config, key)
    return save_settings(settings)


def remove_provider(key):
    """
    删除指定供应商。

    如果删除的是当前激活的供应商，会自动切换到剩余供应商中的第一个。
    如果没有任何剩余供应商，active_provider 设为空字符串。

    参数:
        key: 要删除的供应商标识

    返回:
        bool: 删除是否成功（供应商不存在时返回 False）
    """
    settings = load_settings()
    if key in settings["providers"]:
        del settings["providers"][key]
        # 如果删除的是当前激活供应商，自动切换到第一个剩余供应商
        if settings["active_provider"] == key:
            remaining = list(settings["providers"].keys())
            settings["active_provider"] = remaining[0] if remaining else ""
        return save_settings(settings)
    return False


def switch_provider(key):
    """
    切换当前激活的供应商。

    切换后，AI 分析将使用新供应商的 API 配置。
    供应商必须已存在于 providers 中。

    参数:
        key: 要切换到的供应商标识

    返回:
        bool: 切换是否成功（供应商不存在时返回 False）
    """
    settings = load_settings()
    if key in settings["providers"]:
        settings["active_provider"] = key
        return save_settings(settings)
    return False


def update_provider(key, config):
    """
    更新已有供应商的配置字段。

    使用 dict.update() 合并新配置，只更新传入的字段，
    未传入的字段保持不变。

    参数:
        key: 要更新的供应商标识
        config: 要更新的字段字典（如 {"api_key": "sk-xxx"}）

    返回:
        bool: 更新是否成功（供应商不存在时返回 False）
    """
    settings = load_settings()
    if key in settings["providers"]:
        settings["providers"][key].update(config)
        settings["providers"][key] = normalize_provider_config(settings["providers"][key], key)
        return save_settings(settings)
    return False


# ============================================================================
# Prompt 模板管理
# ============================================================================

def get_prompt_profiles():
    """获取基础分析、深度阅读、报告导读三套 prompt profile。"""
    settings = load_settings()
    return _normalize_prompt_profiles(
        settings.get("prompt_profiles", {}),
        settings.get("prompts", {}),
    )


def get_prompt_profile(task_key):
    """获取单个任务的 prompt profile。"""
    profiles = get_prompt_profiles()
    if task_key not in profiles:
        task_key = "basic_analysis"
    return profiles[task_key]


def save_prompt_profiles(profiles):
    """保存全部 prompt profiles。"""
    settings = load_settings()
    settings["prompt_profiles"] = _normalize_prompt_profiles(profiles, settings.get("prompts", {}))
    deep = settings["prompt_profiles"]["deep_reading"]
    settings["prompts"] = {
        "system_prompt": deep.get("system", ""),
        "user_prompt": deep.get("instruction", ""),
    }
    return save_settings(settings)


def save_prompt_profile(task_key, profile):
    """保存单个任务的 prompt profile。"""
    if task_key not in AI_TASK_KEYS:
        return False
    settings = load_settings()
    profiles = _normalize_prompt_profiles(settings.get("prompt_profiles", {}), settings.get("prompts", {}))
    profile = dict(profile or {})
    if "system" in profile:
        profiles[task_key]["system"] = str(profile.get("system") or "")
    if "instruction" in profile:
        profiles[task_key]["instruction"] = str(profile.get("instruction") or "")
    settings["prompt_profiles"] = profiles
    deep = profiles["deep_reading"]
    settings["prompts"] = {
        "system_prompt": deep.get("system", ""),
        "user_prompt": deep.get("instruction", ""),
    }
    return save_settings(settings)


def get_prompts():
    """
    获取 AI 分析使用的 Prompt 模板。

    以默认 prompt 为基础，用用户自定义的 prompt 覆盖。
    这样用户只需修改想改的部分，其余保持默认。

    返回:
        dict: 包含 system_prompt 和 user_prompt 的旧版兼容字典，并附带 prompt_profiles
            - system_prompt/user_prompt: 映射到 deep_reading profile
            - prompt_profiles: 新版按任务拆分的稳定 Prompt 前缀
    """
    profiles = get_prompt_profiles()
    deep = profiles.get("deep_reading", DEFAULT_PROMPT_PROFILES["deep_reading"])
    return {
        "system_prompt": deep.get("system", ""),
        "user_prompt": deep.get("instruction", ""),
        "prompt_profiles": profiles,
    }


def save_prompts(prompts):
    """
    保存用户自定义的 Prompt 模板。

    参数:
        prompts: 包含 system_prompt 和/或 user_prompt 的字典

    返回:
        bool: 保存是否成功
    """
    settings = load_settings()
    settings["prompts"] = prompts
    profiles = _normalize_prompt_profiles(settings.get("prompt_profiles", {}), prompts)
    profiles["deep_reading"] = {
        "system": prompts.get("system_prompt", profiles["deep_reading"]["system"]),
        "instruction": prompts.get("user_prompt", profiles["deep_reading"]["instruction"]),
    }
    settings["prompt_profiles"] = profiles
    return save_settings(settings)


PROFILE_REQUIRED_PROMPT_FIELDS = {
    "basic_analysis": {"tag_candidates", "rating_criteria"},
    "deep_reading": set(),
    "report_summary": set(),
}


def validate_prompt_template(user_prompt, profile_key=None):
    """
    校验用户 Prompt 模板是否能被 str.format 正常渲染。

    返回:
        tuple(bool, str): 是否有效，以及错误消息
    """
    if profile_key in AI_TASK_KEYS:
        fields = set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", user_prompt or ""))
        required = PROFILE_REQUIRED_PROMPT_FIELDS.get(profile_key, set())
        missing = sorted(required - fields)
        if missing:
            return False, "Prompt 缺少建议变量：" + ", ".join("{" + name + "}" for name in missing)
        return True, ""

    try:
        fields = set()
        for _, field_name, _, _ in Formatter().parse(user_prompt or ""):
            if field_name:
                fields.add(field_name.split(".")[0].split("[")[0])
    except ValueError as e:
        return False, f"Prompt 大括号格式错误：{e}。普通 JSON 大括号需要写成 {{ 和 }}。"

    missing = sorted(REQUIRED_PROMPT_FIELDS - fields)
    if missing:
        return False, "Prompt 缺少必需变量：" + ", ".join("{" + name + "}" for name in missing)

    try:
        (user_prompt or "").format(
            title="测试标题",
            authors="测试作者",
            abstract="测试摘要",
            tag_candidates="标签A, 标签B",
            rating_criteria="评级标准",
        )
    except (KeyError, IndexError, ValueError) as e:
        return False, f"Prompt 渲染失败：{e}"

    return True, ""


# ============================================================================
# 管理员密码管理
# ============================================================================

def get_admin_password():
    """
    获取管理员密码的 SHA-256 哈希值。

    密码以哈希形式存储，不保存明文。
    空字符串表示未设置密码（无需验证）。

    返回:
        str: 密码的 SHA-256 哈希值，未设置时返回空字符串
    """
    settings = load_settings()
    return settings.get("admin_password", "")


def set_admin_password(password):
    """
    设置管理员密码。

    密码使用 SHA-256 哈希后存储，传入空字符串则清除密码。

    参数:
        password: 要设置的明文密码，空字符串表示清除密码

    返回:
        bool: 保存是否成功
    """
    import hashlib
    settings = load_settings()
    if password:
        settings["admin_password"] = hashlib.sha256(password.encode()).hexdigest()
    else:
        settings["admin_password"] = ""
    return save_settings(settings)


def verify_admin_password(password):
    """
    验证管理员密码是否正确。

    如果未设置密码（哈希值为空），直接返回 True（无需验证）。
    否则将输入密码哈希后与存储的哈希值比较。

    参数:
        password: 待验证的明文密码

    返回:
        bool: 密码正确或未设置密码时返回 True
    """
    import hashlib
    stored = get_admin_password()
    if not stored:
        return True
    return hashlib.sha256(password.encode()).hexdigest() == stored


def has_admin_password():
    """
    检查是否已设置管理员密码。

    返回:
        bool: 已设置密码返回 True，未设置返回 False
    """
    return bool(get_admin_password())
