"""Text normalization, evidence matching and checksums for the benchmark."""

import hashlib
import json
import re
import unicodedata

_WS_RE = re.compile(r"\s+")
# 词内/词尾连字符：行断连字（"obser- va tions"）与复合连字（"demonstration-efficient"）
# 在匹配两侧使用同一变换，保证一致性。
_HYPHEN_ARTIFACT = re.compile(r"(?<=\w)[\u2013\u2014-](\s+|(?=\w))")
_PAREN_SPACE = re.compile(r"\(\s+|\s+\)")
_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?])")
_QUOTE_LIKE = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})


def normalize_text(text):
    """归一化文本用于证据匹配（两侧使用同一变换，保持一致性）：
    NFKC（连字/智能引号/减号）→ 折叠空白（含断行）→ 移除词内/行断连字符
    → 移除括号内外多余空白与标点前空白（PDF 提取毛刺）。
    """
    text = unicodedata.normalize("NFKC", str(text or "")).translate(_QUOTE_LIKE)
    text = _WS_RE.sub(" ", text)
    text = _HYPHEN_ARTIFACT.sub("", text)
    text = _PAREN_SPACE.sub("", text)
    text = _BEFORE_PUNCT.sub(r"\1", text)
    return text.strip()


def evidence_matches(fragment, full_text):
    """证据片段必须在冻结全文中精确匹配（空白折叠、忽略大小写）。"""
    fragment = normalize_text(fragment)
    if not fragment:
        return False
    return fragment.lower() in normalize_text(full_text).lower()


def check_evidence(fragments, full_text):
    """逐条校验证据片段，返回 {fragment: bool}。"""
    return {fragment: evidence_matches(fragment, full_text) for fragment in (fragments or [])}


def text_sha256(text):
    """计算文本 SHA-256。"""
    return hashlib.sha256((str(text or "")).encode("utf-8")).hexdigest()


def config_hash(config):
    """计算候选/路由配置的稳定哈希（只包含影响请求的参数）。"""
    keys = (
        "provider_key", "model", "is_thinking", "thinking_effort",
        "temperature_enabled", "temperature", "max_tokens_enabled", "max_tokens",
    )
    canonical = json.dumps(
        {key: config.get(key) for key in keys},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def suite_checksum(subset_name, version_label, deep_reading_prompt, paper_chat_prompt,
                   papers, cases, scoring_revision=None):
    """计算题库校验和：论文快照、题目、Prompt 快照任一变化都会改变校验和。"""
    payload = {
        "subset_name": subset_name,
        "version_label": version_label,
        "deep_reading_prompt": deep_reading_prompt,
        "paper_chat_prompt": paper_chat_prompt,
        "papers": [
            {
                "paper_key": p.get("paper_key"),
                "title": p.get("title"),
                "text_sha256": p.get("text_sha256"),
                "pdf_sha256": p.get("pdf_sha256"),
                "extractor_version": p.get("extractor_version"),
            }
            for p in papers
        ],
        "cases": [
            {
                "paper_key": c.get("paper_key"),
                "track": c.get("track"),
                "position": c.get("position"),
                "question": c.get("question"),
                "reference_answer": c.get("reference_answer"),
                "evidence": c.get("evidence"),
                "rubric": c.get("rubric"),
                "requires_reject": c.get("requires_reject"),
            }
            for c in cases
        ],
    }
    if scoring_revision:
        payload["scoring_revision"] = scoring_revision
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def parse_deep_reading_questions(instruction):
    """从冻结的深度阅读 instruction 中提取 ### Qn: 编号与标题。"""
    questions = []
    text = str(instruction or "").replace("\\n", "\n")
    for match in re.finditer(r"###\s*Q(\d+)\s*:\s*([^\n]+)", text):
        questions.append({"number": int(match.group(1)), "title": match.group(2).strip()})
    return questions


def split_qa_sections(qa_text):
    """把深度阅读输出按 ### Qn: 拆分为 {编号: 原文片段}。"""
    sections = {}
    text = str(qa_text or "")
    matches = list(re.finditer(r"(?m)^###\s*Q(\d+)\s*:", text, re.IGNORECASE))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[int(match.group(1))] = text[match.start():end].strip()
    return sections
