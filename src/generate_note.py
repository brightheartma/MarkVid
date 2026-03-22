"""
generate_note.py
----------------
按课程前缀聚合分段转录，调用 LLM 生成：
0) 课程级简报（Executive Summary + 正文分析，基于全课转录综合）
1) 思维导图（Markdown 列表 → markmap 代码块）
2) 综合数据表格
3) 各段详情

最终输出为 notes/{prefix}.md，并自动重建 notes/INDEX.md。
"""

import argparse
import datetime
import os
import re
import sys
import time
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv
from groq import Groq

# ─── 路径配置 ─────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_MEDIA = BASE_DIR / "data" / "output"
SRT_EXPORTS_DIR = BASE_DIR / "data" / "srt_exports"
NOTES_DIR = BASE_DIR / "notes"
BLOG_DIR = NOTES_DIR / "blog"
PROMPTS_DIR = BASE_DIR / "prompts"
PROMPT_FILE = PROMPTS_DIR / "notebooklm_prompt.md"
INDEX_FILE = NOTES_DIR / "INDEX.md"

# ─── 模型配置 ─────────────────────────────────────────────────────────────────
NOTE_MODEL = os.getenv("NOTE_MODEL", "llama-3.3-70b-versatile")
NOTE_MAX_TOKENS = int(os.getenv("NOTE_MAX_TOKENS", "4096"))
NOTE_SRT_CHARS = int(os.getenv("NOTE_SRT_CHARS", "4000"))
NOTE_BRIEFING_CHARS = int(os.getenv("NOTE_BRIEFING_CHARS", "12000"))
NOTE_BLOGPOST_CHARS = int(os.getenv("NOTE_BLOGPOST_CHARS", "12000"))
NOTE_SEGMENT_SLEEP = int(os.getenv("NOTE_SEGMENT_SLEEP", "10"))

# ─── 预编译正则 ───────────────────────────────────────────────────────────────
_RE_SRT_TS = re.compile(r"(\d{2}:\d{2}:\d{2}),\d{3}\s*-->")
_RE_DURATION = re.compile(r"(\d+):(\d+):(\d+)")
_RE_PREFIX_SEG = re.compile(r"_\d{2}_")
_RE_BLOCK_PARSE = re.compile(
    r"(?:<<<|===)MINDMAP(?:>>+|===)\s*(.*?)\s*"
    r"(?:<<<|===)DATATABLE(?:>>+|===)\s*(.*?)\s*"
    r"(?:<<<|===)DETAIL(?:>>+|===)\s*(.*?)\s*"
    r"(?:(?:<<<|===)END(?:>>+|===)|$)",
    re.DOTALL | re.IGNORECASE,
)
_RE_HEADING = re.compile(r"^(#+)\s*(.*)$")
_RE_TABLE_SEP = re.compile(r"^\|\s*:?-{2,}")
_RE_LEAKED_SECTION = re.compile(r"^#{1,2}\s*[0-2]\.")
_RE_MARKDOWN_WRAP = re.compile(r"^```\s*markdown\s*$", re.IGNORECASE)
_RE_BACKTICK_LINE = re.compile(r"^```\s*$")
_RE_INDENT_BACKTICK = re.compile(r"^[ \t]+```", re.MULTILINE)
_BLOCK_KEYWORDS = frozenset(("MINDMAP", "DATATABLE", "DETAIL", "END"))
_NOISE_PREFIXES = ("<<<", "===", "|", "```", ">")
_LEAKED_PHRASES = ("素材与覆盖范围", "结构化思维导图", "综合数据表格")
_RATE_LIMIT_KEYS = ("429", "rate limit", "too many requests", "tokens per minute")


# ─── Whisper 误识别词典（在喂给 LLM 之前预处理，不影响 Prompt 逻辑）────────────
# 格式：(错误识别词, 正确词)，按长度降序排列避免短词误匹配
_WHISPER_CORRECTIONS: List[Tuple[str, str]] = [
    # 品牌 / 项目名
    ("分Jablin",     "OpenZeppelin"),
    ("分zippelin",   "OpenZeppelin"),
    ("分ziplin",     "OpenZeppelin"),
    ("分泽平",       "OpenZeppelin"),
    ("open ziplin",  "OpenZeppelin"),
    ("索利体",       "Solidity"),
    ("ipni",         "IPFS"),
    ("Essiline",     "eth_signTypedData"),
    ("以太坊登诺",   "eth_signTypedData"),
    ("Lemix",        "Remix"),
    ("Premiere2",    "Permit2"),
    # 密码学 / 区块链术语
    ("非对签加密",   "非对称加密"),
    ("公要",         "公钥"),
    ("私要",         "私钥"),
    ("团曲线",       "椭圆曲线"),
    ("十六精致",     "十六进制"),
    ("十六精制",     "十六进制"),
    ("推荡",         "推导"),
    ("教验",         "校验"),
    ("弹码",         "代码"),
    ("自征",         "自增"),
    # ERC 标准
    ("earc721",      "ERC-721"),
    ("earc20",       "ERC-20"),
    ("earc",         "ERC"),
    ("mft",          "NFT"),
]

def _apply_whisper_corrections(text: str) -> str:
    """将 Whisper 已知误识别词替换为正确词，在喂给 LLM 前调用。"""
    for wrong, correct in _WHISPER_CORRECTIONS:
        text = text.replace(wrong, correct)
    return text


# ─── SRT 一次性读取 ──────────────────────────────────────────────────────────
def _read_srt_meta(srt_path: Path) -> Tuple[str, int, str]:
    """一次读取 SRT 文件，返回 (全文, 行数, 末尾时间戳)。"""
    try:
        text = srt_path.read_text(encoding="utf-8")
    except OSError:
        return "", 0, "未知"

    lines = text.splitlines()
    line_count = len(lines)

    duration = "未知"
    for line in reversed(lines):
        m = _RE_SRT_TS.search(line)
        if m:
            duration = m.group(1)
            break

    text = _apply_whisper_corrections(text)
    return text, line_count, duration


def _truncate_srt(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    head = int(max_chars * 0.45)
    tail = max_chars - head
    return (
        text[:head]
        + "\n\n[... 中间内容已截断，保留头尾关键上下文 ...]\n\n"
        + text[-tail:]
    )


def _frame_count(folder: Path) -> int:
    """统计关键帧数（仅匹配文件名，不读文件内容）。"""
    try:
        return sum(1 for f in folder.iterdir() if f.suffix == ".jpg" and f.name.startswith("frame_"))
    except OSError:
        return 0


# ─── 前缀与段落扫描（全局缓存）──────────────────────────────────────────────
def get_note_prefix(folder_name: str) -> str:
    matches = list(_RE_PREFIX_SEG.finditer(folder_name))
    return folder_name[: matches[-1].start()] if matches else folder_name


def _scan_output_segments() -> Dict[str, List[Dict]]:
    """扫描 data/output/{段目录}/transcript/audio.srt（含关键帧目录）。"""
    grouped: Dict[str, List[Dict]] = {}
    if not OUTPUT_MEDIA.exists():
        return grouped

    for folder in OUTPUT_MEDIA.iterdir():
        if not folder.is_dir():
            continue
        srt_path = folder / "transcript" / "audio.srt"
        if not srt_path.exists():
            continue

        srt_text, srt_lines, duration = _read_srt_meta(srt_path)
        prefix = get_note_prefix(folder.name)
        seg = {
            "folder": folder.name,
            "path": folder,
            "srt": srt_path,
            "srt_text": srt_text,
            "duration": duration,
            "srt_lines": srt_lines,
            "frame_count": _frame_count(folder),
        }
        grouped.setdefault(prefix, []).append(seg)

    return grouped


def _scan_srt_exports_segments() -> Dict[str, List[Dict]]:
    """扫描 data/srt_exports/{课程前缀}/*.srt（每文件一段，无关键帧时 frame_count=0）。

    与 output 并存时：**同一课程前缀仅以 output 为准**，避免重复生成。
    """
    grouped: Dict[str, List[Dict]] = {}
    if not SRT_EXPORTS_DIR.exists():
        return grouped

    for course_dir in sorted(SRT_EXPORTS_DIR.iterdir()):
        if not course_dir.is_dir():
            continue
        prefix = course_dir.name
        srts = sorted(course_dir.glob("*.srt"))
        if not srts:
            continue

        segs: List[Dict] = []
        for srt_path in srts:
            srt_text, srt_lines, duration = _read_srt_meta(srt_path)
            stem = srt_path.stem
            folder_name = f"{prefix}_{stem}"
            segs.append(
                {
                    "folder": folder_name,
                    "path": course_dir,
                    "srt": srt_path,
                    "srt_text": srt_text,
                    "duration": duration,
                    "srt_lines": srt_lines,
                    "frame_count": 0,
                }
            )
        grouped[prefix] = segs

    return grouped


def _scan_all_segments() -> Dict[str, List[Dict]]:
    """合并 data/output 与 data/srt_exports；同一前缀优先使用 output 段落。"""
    grouped = _scan_output_segments()
    exports = _scan_srt_exports_segments()
    for prefix, segs in exports.items():
        if prefix not in grouped:
            grouped[prefix] = segs

    for segs in grouped.values():
        segs.sort(key=lambda x: x["folder"])
    return grouped


_segment_cache: Optional[Dict[str, List[Dict]]] = None


def _get_segment_cache() -> Dict[str, List[Dict]]:
    global _segment_cache
    if _segment_cache is None:
        _segment_cache = _scan_all_segments()
    return _segment_cache


def collect_segments(prefix: str) -> List[Dict]:
    return list(_get_segment_cache().get(prefix, []))


def discover_prefixes() -> List[str]:
    return sorted(_get_segment_cache().keys())


# ─── LLM 提示构建 ──────────────────────────────────────────────────────────────
def _build_segment_prompt(seg: Dict, seg_idx: int, total: int) -> str:
    srt_content = _truncate_srt(seg.get("srt_text", ""), NOTE_SRT_CHARS)
    seg_name = seg["folder"]
    seg_short = seg_name.split("_", 3)[-1] if "_" in seg_name else seg_name

    return "\n".join([
        "你是一个高信噪比的视频课程内容分析师。",
        "【最高优先级规则——反幻觉】",
        "1. 所有内容必须 100% 来自下方转录文本，禁止添加转录中不存在的信息。",
        "2. 绝对禁止编造代码！如果转录中没有逐行念出代码，就不要输出任何代码块。",
        "3. 只有当讲师在转录中逐行念出了完整代码时，才可以用代码块还原。",
        "4. 禁止复读本提示词中的任何指令文本。",
        "",
        f"视频段：{seg_name}（第 {seg_idx}/{total} 段，时长 {seg['duration']}，{seg['srt_lines']} 行转录）",
        "",
        "--- 转录内容 ---",
        srt_content,
        "--- 输出要求（三个区块，标记符独占一行）---",
        "",
        "<<<MINDMAP>>>",
        "用 Markdown 无序列表（-）输出本段知识点层级。",
        "【警告】绝对禁止使用 `#` 标题语法！只能通过前面加 2 个或 4 个空格来体现层级！",
        "每个节点尾部附 [HH:MM:SS]。",
        "",
        "<<<DATATABLE>>>",
        "仅输出真实数据行（| 开头 | 结尾），禁止表头、分割线、占位符。",
        f"列顺序：| {seg_short} | HH:MM:SS | 主题 | 关键术语 | 证据来源 | 可执行结论 |",
        "时间戳必须是 HH:MM:SS 格式（禁止带毫秒，禁止带箭头）。",
        "【警告】绝对禁止改变列数！每行必须有且仅有 7 个管道符 `|`！",
        "",
        "<<<DETAIL>>>",
        f"输出本段（{seg_short}）的详细解析，严格 3 个项目符号：",
        "- **核心大纲**：2-3 句概述核心目标与讨论焦点。",
        "- **关键数据与术语**：提取转录中出现的专业名词并简短解释。",
        "- **详细解析**：基于转录复盘论述逻辑，用纯文本描述。",
        "【警告】如果没有代码，直接输出纯文本！绝对禁止输出类似 ```solidity 无代码 ``` 这种包含中文的假代码块！",
        "",
        "<<<END>>>",
    ])


def _concatenate_course_srt(segments: List[Dict], max_chars: int) -> str:
    """合并全课各段转录，超长时头尾截断。"""
    parts: List[str] = []
    for seg in segments:
        short = seg["folder"].split("_", 3)[-1] if "_" in seg["folder"] else seg["folder"]
        parts.append(f"\n\n=== 视频段：{short} ===\n\n")
        parts.append(seg.get("srt_text", ""))
    full = _apply_whisper_corrections("".join(parts).strip())
    if len(full) <= max_chars:
        return full
    return _truncate_srt(full, max_chars)


def _build_briefing_prompt(prefix: str, segments: List[Dict], combined_srt: str) -> str:
    seg_list = ", ".join(
        s["folder"].split("_", 3)[-1] if "_" in s["folder"] else s["folder"]
        for s in segments
    )
    return "\n".join([
        "你是资深课程内容分析师，需根据下方**完整课程转录**撰写一份简报。",
        "",
        "【最高优先级——反幻觉】",
        "1. 论点、事实、术语必须能在转录中找到依据；禁止编造转录中不存在的内容。",
        "2. 禁止逐字复述本提示词中的英文说明。",
        "3. 输出语言：简体中文（专有名词可保留英文）。",
        "",
        "【简报写作要求（须严格满足结构与语气）】",
        "Create a comprehensive briefing document that synthesizes the main themes and ideas "
        "from the sources. Start with a concise Executive Summary that presents the most "
        "critical takeaways upfront. The body of the document must provide a detailed and "
        "thorough examination of the main themes, evidence, and conclusions found in the "
        "sources. This analysis should be structured logically with headings and bullet points "
        "to ensure clarity. The tone must be objective and incisive.",
        "",
        f"课程名称：{prefix}",
        f"包含视频段（共 {len(segments)} 段）：{seg_list}",
        "",
        "--- 课程转录（sources）---",
        combined_srt,
        "",
        "--- 输出格式（直接输出 Markdown 正文，勿加外层代码块包裹全文）---",
        "以二级标题 `## 执行摘要` 开头，用若干短段落或要点列出最关键结论（对应 Executive Summary）。",
        "随后用二级标题组织正文（例如 `## 主题与论证`、`## 证据与结论` 等，按内容自拟），",
        "正文中充分使用三级标题与无序列表，层次清晰。",
        "全文不要使用一级标题 `#`。",
    ])


def _clean_briefing(text: str) -> str:
    """去除简报输出中的标记泄漏与外层 markdown 包裹。"""
    lines = text.splitlines()
    if lines and _RE_MARKDOWN_WRAP.match(lines[0]):
        lines = lines[1:]
        for i in range(len(lines) - 1, -1, -1):
            if _RE_BACKTICK_LINE.match(lines[i].strip()):
                lines = lines[:i]
                break
        text = "\n".join(lines)

    out: List[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("<<<"):
            continue
        out.append(line)

    cleaned = "\n".join(out).strip()
    if cleaned.startswith("# "):
        # 去掉单一整课标题，避免与笔记主标题重复
        rest = cleaned.split("\n", 1)
        if len(rest) > 1:
            cleaned = rest[1].strip()

    if not cleaned:
        cleaned = (
            "## 执行摘要\n\n"
            "- 转录内容不足或生成失败，请检查 `NOTE_BRIEFING_CHARS` 或重试生成。\n"
        )
    return cleaned


def _build_blogpost_prompt(prefix: str, segments: List[Dict], combined_srt: str) -> str:
    seg_list = ", ".join(
        s["folder"].split("_", 3)[-1] if "_" in s["folder"] else s["folder"]
        for s in segments
    )
    return "\n".join([
        "你是一位文字简洁、思维犀利的内容创作者，正在为一个以高质量洞察见长的在线发布平台撰写博客文章。",
        "",
        "【最高优先级——反幻觉】",
        "1. 所有论点、引语、事实均须来自下方转录，禁止凭空编造内容。",
        "2. 如需引用讲师原话，必须是转录中出现的原话，不得改写或发明。",
        "3. 禁止复读本提示词中的任何英文指令。",
        "4. 输出语言：简体中文（专有名词保留英文）。",
        "",
        "【博文写作要求——严格遵循以下结构与风格】",
        "",
        "参考写作指令（须内化到写作风格，勿逐字输出）：",
        "Act as a thoughtful writer and synthesizer of ideas, tasked with creating an engaging",
        "and readable blog post for a popular online publishing platform known for its clean",
        "aesthetic and insightful content. Your goal is to distill the top most surprising,",
        "counter-intuitive, or impactful takeaways from the provided source materials into a",
        "compelling listicle. The writing style should be clean, accessible, and highly",
        "scannable, employing a conversational yet intelligent tone.",
        "Craft a compelling, click-worthy headline.",
        "Begin the article with a short introduction that hooks the reader by establishing a",
        "relatable problem or curiosity, then present each of the takeaway points as a distinct",
        "section with a clear, bolded subheading. Within each section, use short paragraphs to",
        "explain the concept clearly, and don't just summarize; offer a brief analysis or a",
        "reflection on why this point is so interesting or important, and if a powerful quote",
        "exists in the sources, feature it in a blockquote for emphasis. Conclude the post with",
        "a brief, forward-looking summary that leaves the reader with a final",
        "thought-provoking question or a powerful takeaway to ponder.",
        "",
        "【输出格式规范】",
        "- 第一行：一级标题（`# 标题`），即点击率高、令人好奇的文章标题",
        "- 第二部分：2-3 句钩子式引言（不用二级标题，直接正文段落）",
        "- 随后 4-6 个独立 takeaway 点，每点结构：",
        "    `## [加粗短语式小标题]`",
        "    短段落解释 + 分析（为什么这个点反直觉或重要）",
        "    若有讲师金句，用 `> 引用块` 呈现",
        "- 最后：`## 最后的问题` 节，1 段结语 + 1 个发人深省的问题句",
        "- 全文不超过 1200 字，段落间保持一个空行",
        "",
        f"课程名称：{prefix}",
        f"包含视频段（共 {len(segments)} 段）：{seg_list}",
        "",
        "--- 课程转录（sources）---",
        combined_srt,
        "",
        "--- 直接输出博文 Markdown，勿加任何外层说明或代码块 ---",
    ])


def _clean_blogpost(text: str) -> str:
    """清理博文 LLM 输出：去掉外层 markdown 包裹和泄漏的标记行。"""
    lines = text.splitlines()
    if lines and _RE_MARKDOWN_WRAP.match(lines[0]):
        lines = lines[1:]
        for i in range(len(lines) - 1, -1, -1):
            if _RE_BACKTICK_LINE.match(lines[i].strip()):
                lines = lines[:i]
                break
        text = "\n".join(lines)

    out: List[str] = []
    for line in text.splitlines():
        if line.strip().startswith("<<<") or line.strip().startswith("==="):
            continue
        out.append(line)

    cleaned = "\n".join(out).strip()
    if not cleaned:
        cleaned = "# 博文生成失败\n\n请使用 `--force` 重新生成或检查 API 配置。\n"
    return cleaned


# ─── LLM 调用 ─────────────────────────────────────────────────────────────────
def call_llm(client: Groq, prompt: str, retry: int = 6) -> str:
    for attempt in range(retry):
        try:
            resp = client.chat.completions.create(
                model=NOTE_MODEL,
                max_tokens=NOTE_MAX_TOKENS,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是一位专业的视频课程内容分析师，擅长将视频转录整理成"
                            "结构清晰、带时间戳的 Markdown 笔记。直接输出内容，不要解释。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            err_str = str(e).lower()
            if any(k in err_str for k in _RATE_LIMIT_KEYS):
                wait = 65
                print(f"   ⏳ 触发 API 限流，休眠 {wait}s 后重试（{attempt+1}/{retry}）...", flush=True)
                time.sleep(wait)
                continue
            if "413" in err_str and attempt < retry - 1:
                print(f"   ⚠️  内容超大 (413)，缩减 prompt 后重试（{attempt+1}/{retry}）...", flush=True)
                prompt = prompt[: len(prompt) // 2] + "\n\n[... 内容已缩减 ...]\n<<<END>>>"
                time.sleep(5)
                continue
            print(f"   ❌ LLM 请求失败: {e}", flush=True)
            raise
    raise RuntimeError(f"LLM 调用失败，已耗尽 {retry} 次重试")


# ─── 区块解析与清洗 ────────────────────────────────────────────────────────────
def _parse_blocks(raw: str) -> Dict[str, str]:
    blocks: Dict[str, str] = {"mindmap": "", "datatable": "", "detail": raw}
    m = _RE_BLOCK_PARSE.search(raw)
    if not m:
        # 解析失败时仍需清洗，防止 <<<MINDMAP>> 等标记泄漏到笔记
        blocks["detail"] = _clean_detail(raw)
        return blocks
    blocks["mindmap"] = _clean_mindmap(m.group(1))
    blocks["datatable"] = _clean_datatable(m.group(2))
    blocks["detail"] = _clean_detail(m.group(3))
    return blocks


_MINDMAP_NOISE = (
    "用 markdown", "用 Markdown", "无序列表", "思维导图",
    "输出本段", "层次用缩进", "禁止输出",
)

def _clean_mindmap(text: str) -> str:
    lines: List[str] = []
    for line in text.splitlines():
        s = line.rstrip()
        stripped = s.lstrip()
        if not stripped or stripped.startswith(_NOISE_PREFIXES):
            continue
        if any(k in stripped.upper() for k in _BLOCK_KEYWORDS):
            continue
        # 过滤 LLM 复读的 prompt 指令文本
        if any(noise in stripped for noise in _MINDMAP_NOISE):
            continue
        if stripped.startswith("- "):
            lines.append(s)
        elif stripped.startswith("#"):
            m = _RE_HEADING.match(stripped)
            if m:
                indent = "  " * max(0, len(m.group(1)) - 2)
                lines.append(f"{indent}- {m.group(2).strip()}")
    return "\n".join(lines).strip()


_DATATABLE_PLACEHOLDERS = ("视频段", "时间戳", "HH:MM:SS", "主题/章节", "关键术语/对比", "证据来源")
_RE_SRT_ARROW = re.compile(r"\d{2}:\d{2}:\d{2}[,:]\d{3}\s*-->")

_RE_TIMESTAMP_MS = re.compile(r"(\d{2}:\d{2}:\d{2})[,:.]\d{3}")
_EXPECTED_PIPE_COUNT = 7

def _clean_datatable(text: str) -> str:
    rows: List[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or not s.startswith("|"):
            continue
        if any(kw in s for kw in _DATATABLE_PLACEHOLDERS):
            continue
        if _RE_TABLE_SEP.match(s):
            continue

        # 剔除时间戳毫秒 (00:00:12,040 -> 00:00:12)
        s = _RE_TIMESTAMP_MS.sub(r"\1", s)

        # 清除 SRT 箭头
        if _RE_SRT_ARROW.search(s):
            s = _RE_SRT_ARROW.sub("", s)

        if not s.endswith("|"):
            s += " |"

        # 过滤幽灵行
        if len(s.replace("|", "").strip()) < 5:
            continue

        # 强制列数校验：必须恰好 7 个管道符（6 列数据）
        if s.count("|") != _EXPECTED_PIPE_COUNT:
            continue

        rows.append(s)
    return "\n".join(rows).strip()


def _list_to_markmap(lines: List[str], root_title: str) -> str:
    out = [f"# {root_title}"]
    for line in lines:
        s = line.rstrip()
        stripped = s.lstrip()
        if not stripped or not stripped.startswith("- "):
            continue
        depth = (len(s) - len(stripped)) // 2
        body = stripped[2:].strip()
        if body.startswith("**") and body.endswith("**"):
            body = body[2:-2]
        out.append("#" * min(depth + 2, 6) + " " + body)
    return "\n".join(out)


_RE_FAKE_CODE_LINE = re.compile(
    r"^```[a-zA-Z]*[^\n`]*[\u4e00-\u9fa5]+[^\n`]*```\s*$", re.MULTILINE
)

def _clean_detail(text: str) -> str:
    # 一击必杀：同一行内反引号夹杂中文的假代码块（如 ```solidity 无可执行代码块 ```）
    text = _RE_FAKE_CODE_LINE.sub("", text)

    lines = text.splitlines()
    if lines and _RE_MARKDOWN_WRAP.match(lines[0]):
        lines = lines[1:]
        for i in range(len(lines) - 1, -1, -1):
            if _RE_BACKTICK_LINE.match(lines[i].strip()):
                lines = lines[:i]
                break
        text = "\n".join(lines)

    text = _RE_INDENT_BACKTICK.sub("```", text)

    out: List[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            out.append(line)
            continue
        if s.startswith(("<<<", "===")):
            continue
        if _RE_LEAKED_SECTION.match(s):
            continue
        if any(p in s for p in _LEAKED_PHRASES):
            continue
        out.append(line)

    result: List[str] = []
    for line in out:
        spaces = len(line) - len(line.lstrip())
        if spaces >= 4:
            stripped = line.lstrip()
            if stripped.startswith("```") or not re.match(r"^[-*#|>\d.]", stripped):
                result.append(stripped)
            else:
                result.append(line)
        else:
            result.append(line)

    result = _strip_fabricated_code_blocks(result)

    cleaned = "\n".join(result).strip()
    if not cleaned:
        cleaned = (
            "- **核心大纲**：本段内容可读信息较少，建议结合关键帧复核。\n"
            "- **关键数据与术语**：待转录验证。\n"
            "- **详细解析**：未提取到稳定结构化描述。"
        )
    return cleaned


_RE_CODE_FENCE_OPEN = re.compile(r"^```\w*")
_FABRICATION_SIGNALS = (
    "// ...", "// 例子", "// 示例", "// 使用", "// 加密", "// 解密",
    "// 验证", "// 美化", "// 口语", "// 变通", "// 实现", "// 这个",
    "// 非相关", "var 美化", "var 变通", "var 实现", "var 提升",
    "var 口语", "var 提高",
)

def _strip_fabricated_code_blocks(lines: List[str]) -> List[str]:
    """检测并移除 LLM 编造的代码块。
    判定标准：代码块内有效代码行（非注释、非空、非大括号）少于 2 行，
    或包含明显的编造信号词。
    """
    out: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if _RE_CODE_FENCE_OPEN.match(line.strip()):
            block_lines = [line]
            i += 1
            while i < len(lines):
                block_lines.append(lines[i])
                if _RE_BACKTICK_LINE.match(lines[i].strip()):
                    i += 1
                    break
                i += 1
            else:
                out.extend(block_lines)
                continue

            inner = block_lines[1:-1]
            # 检查编造信号词
            block_text = "\n".join(inner)
            has_fabrication = any(sig in block_text for sig in _FABRICATION_SIGNALS)

            # 统计有效代码行（排除注释、空行、纯括号行）
            real_code = sum(
                1 for ln in inner
                if ln.strip()
                and not ln.strip().startswith("//")
                and not ln.strip().startswith("#")
                and ln.strip() not in ("{", "}", "};", ");", "});", "};};")
            )

            if has_fabrication or real_code < 2:
                continue
            out.extend(block_lines)
        else:
            out.append(line)
            i += 1
    return out


# ─── 时长计算 ─────────────────────────────────────────────────────────────────
def _duration_sum(segs: List[Dict]) -> str:
    total = sum(
        int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        for s in segs
        if (m := _RE_DURATION.match(s["duration"]))
    )
    return str(datetime.timedelta(seconds=total)) if total else "未知"


# ─── 核心生成 ─────────────────────────────────────────────────────────────────
def generate_for_prefix(
    client: Groq,
    prefix: str,
    *,
    force: bool = False,
) -> bool:
    NOTES_DIR.mkdir(exist_ok=True)
    out_file = NOTES_DIR / f"{prefix}.md"
    if out_file.exists() and not force:
        print(f"⏭️  已跳过: {prefix}（笔记已存在，使用 --force 覆盖）", flush=True)
        return False

    segments = collect_segments(prefix)
    if not segments:
        print(f"❌ 跳过: {prefix}（未找到已转录视频段）", flush=True)
        return False

    total_lines = sum(s["srt_lines"] for s in segments)
    total_frames = sum(s["frame_count"] for s in segments)
    total_dur = _duration_sum(segments)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    print(f"\n📚 开始生成课程笔记: {prefix}", flush=True)
    print(f"   {len(segments)} 个视频段 | 总时长 {total_dur} | {total_lines:,} 行转录 | {total_frames:,} 帧", flush=True)

    all_mindmap: List[str] = []
    all_datatable: List[str] = []
    all_detail: List[str] = []

    for i, seg in enumerate(segments, 1):
        seg_short = seg["folder"].replace(prefix + "_", "")
        print(f"   [{i}/{len(segments)}] 生成章节: {seg_short}", flush=True)
        prompt = _build_segment_prompt(seg, i, len(segments))
        try:
            raw = call_llm(client, prompt)
            blocks = _parse_blocks(raw)
        except Exception as e:
            print(f"   ❌ 章节生成失败: {e}", flush=True)
            blocks = {
                "mindmap": "- ⚠️ 生成失败，待重试验证。",
                "datatable": f"| {seg_short} | — | 生成失败 | — | — | — |",
                "detail": "- **核心大纲**：本段生成失败，待重试。\n- **关键数据与术语**：待转录验证。\n- **详细解析**：未生成。",
            }

        all_mindmap.append(f"- **{seg_short}**")
        for line in blocks["mindmap"].splitlines():
            if line.strip():
                all_mindmap.append("  " + line)
        all_mindmap.append("")

        all_datatable.extend(r.strip() for r in blocks["datatable"].splitlines() if r.strip().startswith("|"))

        all_detail.append(f"\n### 4.{i} {seg_short}\n\n{blocks['detail']}\n")

        if i < len(segments):
            time.sleep(NOTE_SEGMENT_SLEEP)

    # ── 课程级简报（全课转录综合）──
    if len(segments) > 0:
        time.sleep(NOTE_SEGMENT_SLEEP)
    combined_srt = _concatenate_course_srt(segments, NOTE_BRIEFING_CHARS)
    briefing_prompt = _build_briefing_prompt(prefix, segments, combined_srt)
    try:
        print("   [简报] 正在根据全课转录生成 Executive Summary 与主题分析 …", flush=True)
        briefing_raw = call_llm(client, briefing_prompt)
        briefing_md = _clean_briefing(briefing_raw)
    except Exception as e:
        print(f"   ⚠️  简报生成失败: {e}", flush=True)
        briefing_md = (
            "## 执行摘要\n\n"
            "- 本课程简报生成失败，请使用 `--force` 重新生成或检查 API。\n"
        )

    # ── 组装笔记 ──
    table_header = (
        "| 视频段 | 时间戳节点 | 主题/章节 | 关键术语/数据/对比 | "
        "证据来源（转录/关键帧） | 可执行结论 |\n"
        "| :--- | :--- | :--- | :--- | :--- | :--- |"
    )
    markmap_block = _list_to_markmap(all_mindmap, prefix)

    buf: List[str] = [
        "---",
        f"title: {prefix}",
        f"created: {now}",
        f"segments: {len(segments)}",
        f"duration: {total_dur}",
        f"srt_lines: {total_lines}",
        f"frames: {total_frames}",
        "---",
        "",
        f"# {prefix}",
        "",
        "> **课程概况**",
        f"> - 视频段数：{len(segments)} 段",
        f"> - 总时长：{total_dur}",
        f"> - 转录行数：{total_lines:,} 行",
        f"> - 关键帧数：{total_frames:,} 张",
        f"> - 生成时间：{now}",
        "",
        "---",
        "",
        "## 0. 素材与覆盖范围",
        "",
    ]
    for seg in segments:
        s = seg["folder"].replace(prefix + "_", "")
        buf.append(f"- **{s}**：时长 {seg['duration']}，{seg['srt_lines']:,} 行转录，{seg['frame_count']:,} 张关键帧")

    buf += [
        "",
        "---",
        "",
        "## 1. 简报 (Briefing)",
        "",
        briefing_md,
        "",
        "---",
        "",
        "## 2. 结构化思维导图 (Mind Map)",
        "",
        "> 使用 Obsidian 插件 [Mindmap NextGen](obsidian://show-plugin?id=mindmap-nextgen) 可渲染为交互式思维导图。",
        "",
        "```markmap",
        markmap_block,
        "```",
        "",
        "---",
        "",
        "## 3. 综合数据表格 (Data Table)",
        "",
        table_header,
        "\n".join(all_datatable) if all_datatable else "| — | — | — | — | — | — |",
        "",
        "---",
        "",
        "## 4. 各段详情 (Segment Details)",
    ]
    buf.extend(all_detail)

    content = "\n".join(buf)
    out_file.write_text(content, encoding="utf-8")
    print(f"   ✅ 合并笔记已保存：notes/{prefix}.md（{out_file.stat().st_size / 1024:.1f}KB，{content.count(chr(10)) + 1} 行）", flush=True)
    return True


# ─── 博文生成 ─────────────────────────────────────────────────────────────────
def generate_blogpost_for_prefix(
    client: Groq,
    prefix: str,
    *,
    force: bool = False,
) -> bool:
    BLOG_DIR.mkdir(parents=True, exist_ok=True)
    out_file = BLOG_DIR / f"{prefix}_blog.md"
    if out_file.exists() and not force:
        print(f"⏭️  博文已跳过: {prefix}（文件已存在，使用 --force 覆盖）", flush=True)
        return False

    segments = collect_segments(prefix)
    if not segments:
        print(f"❌ 博文跳过: {prefix}（未找到已转录视频段）", flush=True)
        return False

    print(f"\n✍️  开始生成博文: {prefix}", flush=True)
    combined_srt = _concatenate_course_srt(segments, NOTE_BLOGPOST_CHARS)
    prompt = _build_blogpost_prompt(prefix, segments, combined_srt)
    try:
        print("   [博文] 正在根据全课转录生成 Listicle 博文 …", flush=True)
        raw = call_llm(client, prompt)
        blog_md = _clean_blogpost(raw)
    except Exception as e:
        print(f"   ⚠️  博文生成失败: {e}", flush=True)
        blog_md = f"# 博文生成失败\n\n> 课程：{prefix}\n> 错误：{e}\n"

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    header = "\n".join([
        "---",
        f"title: {prefix} - 博文",
        f"created: {now}",
        f"source_note: \"[[{prefix}.md]]\"",
        "tags: [blog]",
        "---",
        "",
    ])
    content = header + blog_md + "\n"
    out_file.write_text(content, encoding="utf-8")
    print(
        f"   ✅ 博文已保存：notes/blog/{prefix}_blog.md"
        f"（{out_file.stat().st_size / 1024:.1f}KB）",
        flush=True,
    )
    return True


# ─── INDEX 重建 ───────────────────────────────────────────────────────────────
def rebuild_index() -> None:
    NOTES_DIR.mkdir(exist_ok=True)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    _SKIP = {"INDEX.md", "README.md", "000_我的视频知识大盘.md"}
    note_files = sorted(f for f in NOTES_DIR.glob("*.md") if f.name not in _SKIP)

    rows: List[str] = []
    for nf in note_files:
        segs = collect_segments(nf.stem)
        rows.append(
            f"| {nf.stem} | [[{nf.name}]] | {len(segs)} | "
            f"{_duration_sum(segs)} | {sum(s['srt_lines'] for s in segs):,} | {sum(s['frame_count'] for s in segs):,} |"
        )

    lines = [
        "# 课程笔记索引",
        "",
        f"> 更新时间：{now}",
        f"> 课程数量：{len(rows)}",
        "",
        "| 课程 | 文件 | 段数 | 总时长 | 转录行数 | 关键帧数 |",
        "| :--- | :--- | :--- | :--- | :--- | :--- |",
        *rows,
    ]
    INDEX_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"✅ INDEX.md 已更新（{len(rows)} 门课程）", flush=True)


# ─── CLI ──────────────────────────────────────────────────────────────────────
def main() -> int:
    load_dotenv()
    p = argparse.ArgumentParser(description="根据转录自动生成课程笔记并更新索引")
    p.add_argument("--prefix", type=str, default=None)
    p.add_argument("--all", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--update-index", action="store_true")
    p.add_argument("--blog", action="store_true", help="同时生成博文（notes/blog/）")
    p.add_argument("--blog-only", action="store_true", dest="blog_only", help="只生成博文，跳过笔记生成")
    args = p.parse_args()

    if args.update_index and not args.all and not args.prefix:
        print("🔄 正在更新 notes/INDEX.md ...", flush=True)
        rebuild_index()
        return 0

    if not args.all and not args.prefix:
        print("请提供 --all 或 --prefix", flush=True)
        return 2

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        print("❌ 未检测到 GROQ_API_KEY，请先在 .env 中配置", flush=True)
        return 2
    if not OUTPUT_MEDIA.exists() and not SRT_EXPORTS_DIR.exists():
        print(
            f"❌ 未找到转录数据源：{OUTPUT_MEDIA} 或 {SRT_EXPORTS_DIR} 至少其一需存在",
            flush=True,
        )
        return 2

    client = Groq(api_key=api_key)
    prefixes = discover_prefixes() if args.all else ([args.prefix] if args.prefix else [])

    if args.all:
        print(f"🔍 共 {len(prefixes)} 个课程前缀：{prefixes}", flush=True)

    generate_notes = not args.blog_only
    generate_blogs = args.blog or args.blog_only

    generated_notes = 0
    generated_blogs = 0

    for pf in prefixes:
        if generate_notes:
            if generate_for_prefix(client, pf, force=args.force):
                generated_notes += 1
            if generate_blogs:
                time.sleep(NOTE_SEGMENT_SLEEP)
        if generate_blogs:
            if generate_blogpost_for_prefix(client, pf, force=args.force):
                generated_blogs += 1

    if generate_notes:
        print("\n🔄 正在更新 notes/INDEX.md ...", flush=True)
        rebuild_index()

    parts = []
    if generate_notes:
        parts.append(f"{generated_notes} 篇课程笔记")
    if generate_blogs:
        parts.append(f"{generated_blogs} 篇博文")
    print(f"\n✅ 完成！本次新生成 {' + '.join(parts) if parts else '0 项'}。", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
