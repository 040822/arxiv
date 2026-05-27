import json
import os
import logging
from config import DB_DIR

logger = logging.getLogger(__name__)

SETTINGS_PATH = os.path.join(DB_DIR, "settings.json")

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

DEFAULT_SETTINGS = {
    "active_provider": "deepseek",
    "concurrency": 5,
    "providers": {
        "deepseek": {
            "name": "DeepSeek",
            "api_key": "",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "temperature": 0.3,
            "max_tokens": 8192,
        }
    },
    "prompts": {
        "system_prompt": "你是一位AI和机器人领域的资深研究助手，擅长快速阅读论文、提炼核心贡献、评估创新性与影响力。回答应当详尽充分，不吝笔墨。请始终返回合法的JSON格式。",
        "user_prompt": "请对以下论文进行深度阅读分析，按Q&A格式详细回答每个问题，然后给出标签和评级。\n\n论文标题: {title}\n论文作者: {authors}\n\n论文内容:\n{abstract}\n\n请严格按以下JSON格式返回（不要返回其他内容）:\n\n{{\n    \"qa_analysis\": \"### Q1: 这篇论文试图解决什么问题？\\n\\n详细描述论文要解决的核心问题、研究动机和背景。需要涵盖现有方法的不足之处以及本文的出发点，让读者充分理解问题的重要性和难度。\\n\\n### Q2: 有哪些相关研究？\\n\\n列出关键的相关工作，说明每项工作的核心方法和局限性，以及与本文的关系（本文如何在这些工作基础上改进或有何不同）。需要覆盖该领域的主要技术路线。\\n\\n### Q3: 论文如何解决这个问题？\\n\\n详细描述论文提出的核心方法、技术架构和创新点。包括关键算法流程、模型设计细节、训练策略、损失函数等技术要素，让读者能理解方法的全貌。\\n\\n### Q4: 论文做了哪些实验？\\n\\n详细描述实验设置（使用的数据集、基线方法、评估指标）、主要实验结果和消融实验结论。尽量用具体数字说明关键结果，以便读者评估方法的实际效果。\\n\\n### Q5: 有什么可以进一步探索的点？\\n\\n分析论文的局限性以及未来可能的改进方向，包括技术改进、应用扩展、理论分析等多个方面。\\n\\n### Q6: 总结一下论文的主要内容\\n\\n全面概括论文的核心贡献、技术方案、实验验证和实际价值，让读者能在最短时间内了解论文全貌。\",\n    \"tags\": [\"标签1\", \"标签2\"],\n    \"rating\": 3,\n    \"summary_cn\": \"提取论文摘要部分并完整翻译为中文。如果内容中包含Abstract部分，请提取Abstract的原文并逐句翻译；如果没有明确的Abstract部分，则提取论文开头的概述内容进行翻译。要求忠实原文、语句通顺、术语准确。\",\n    \"value_comment\": \"对论文价值的简短评价（2-3句话）\"\n}}\n\n标签选择指南:\n请从以下标签中选择最相关的2-5个标签，也可以自行创建新标签:\n{tag_candidates}\n\n标签精度要求（重要）:\n- 避免过于宽泛的标签，例如: \"Robot Learning\"、\"Embodied AI\"（领域太大）、\"Transformer\"（架构太通用）、\"LLM\"（太泛）、\"Agent\"（太泛）、\"Multimodal\"（太泛）\n- 优先使用具体的技术方法、特定任务、具体架构名称\n- 好的标签示例: \"VLA\"、\"Diffusion Policy\"、\"Sim-to-Real Transfer\"、\"Dexterous Manipulation\"、\"World Model\"、\"Preference Optimization\"\n- 如果论文的核心贡献可以用更精确的词描述，就不要用泛泛的词\n\n{rating_criteria}\n\n重要要求:\n1. qa_analysis 中每个Q&A用 Markdown 标题格式（### Qn: 问题），每个回答应当详尽充分，至少3-5个完整段落\n2. summary_cn 必须是论文Abstract的完整中文翻译，逐句对应，不得省略概括\n3. tags必须是数组格式\n4. rating必须是0-5的整数\n5. 请确保返回合法的JSON格式，qa_analysis中的换行用\\\\n转义",
    }
}


def _ensure_dir():
    os.makedirs(DB_DIR, exist_ok=True)


def _migrate_old_settings(data):
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
    }
    return migrated


def load_settings():
    _ensure_dir()
    if not os.path.exists(SETTINGS_PATH):
        save_settings(DEFAULT_SETTINGS)
        return json.loads(json.dumps(DEFAULT_SETTINGS))
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        migrated = _migrate_old_settings(saved)
        merged = json.loads(json.dumps(DEFAULT_SETTINGS))
        merged["active_provider"] = migrated.get("active_provider", "deepseek")
        for k, v in migrated.get("providers", {}).items():
            if v.get("max_tokens", 0) < 4096:
                v["max_tokens"] = 8192
            merged["providers"][k] = v
        return merged
    except Exception as e:
        logger.error(f"Failed to load settings: {e}")
        return json.loads(json.dumps(DEFAULT_SETTINGS))


def save_settings(settings):
    _ensure_dir()
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Failed to save settings: {e}")
        return False


def get_active_provider():
    settings = load_settings()
    active = settings.get("active_provider", "deepseek")
    providers = settings.get("providers", {})
    return providers.get(active, {})


def get_ai_config():
    prov = get_active_provider()
    return {
        "api_key": prov.get("api_key", ""),
        "base_url": prov.get("base_url", ""),
        "model": prov.get("model", ""),
        "temperature": prov.get("temperature", 0.3),
        "max_tokens": prov.get("max_tokens", 1000),
    }


def get_concurrency():
    settings = load_settings()
    return settings.get("concurrency", 5)


def get_all_providers():
    return load_settings().get("providers", {})


def get_provider_presets():
    return PROVIDER_PRESETS


def add_provider(key, config):
    settings = load_settings()
    settings["providers"][key] = config
    return save_settings(settings)


def remove_provider(key):
    settings = load_settings()
    if key in settings["providers"]:
        del settings["providers"][key]
        if settings["active_provider"] == key:
            remaining = list(settings["providers"].keys())
            settings["active_provider"] = remaining[0] if remaining else ""
        return save_settings(settings)
    return False


def switch_provider(key):
    settings = load_settings()
    if key in settings["providers"]:
        settings["active_provider"] = key
        return save_settings(settings)
    return False


def update_provider(key, config):
    settings = load_settings()
    if key in settings["providers"]:
        settings["providers"][key].update(config)
        return save_settings(settings)
    return False


def get_prompts():
    settings = load_settings()
    default_prompts = DEFAULT_SETTINGS.get("prompts", {})
    prompts = settings.get("prompts", {})
    result = default_prompts.copy()
    result.update(prompts)
    return result


def save_prompts(prompts):
    settings = load_settings()
    settings["prompts"] = prompts
    return save_settings(settings)


def get_admin_password():
    settings = load_settings()
    return settings.get("admin_password", "")


def set_admin_password(password):
    import hashlib
    settings = load_settings()
    if password:
        settings["admin_password"] = hashlib.sha256(password.encode()).hexdigest()
    else:
        settings["admin_password"] = ""
    return save_settings(settings)


def verify_admin_password(password):
    import hashlib
    stored = get_admin_password()
    if not stored:
        return True
    return hashlib.sha256(password.encode()).hexdigest() == stored


def has_admin_password():
    return bool(get_admin_password())
