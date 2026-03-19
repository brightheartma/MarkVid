"""
generate_note.py
----------------
调用 Groq chat API，根据转录文本和关键帧信息生成 NotebookLM 风格笔记。
生成完成后自动更新 notes/INDEX.md。

策略：同一课程前缀的多个视频段 → 逐段调用 LLM（控制 token）→ 合并写入一个课程笔记文件。

用法：
    # 为某一前缀的课程生成笔记（自动合并同一前缀的多个视频段）
    .venv/bin/python src/generate_note.py --prefix "00_13_第十三课：事件索引"

    # 为所有尚未生成笔记的课程批量生成
    .venv/bin/python src/generate_note.py --all

    # 强制重新生成（覆盖已有笔记）
    .venv/bin/python src/generate_note.py --prefix "00_13_第十三课：事件索引" --force

环境变量：
    GROQ_API_KEY       Groq API 密钥（必需）
    NOTE_MODEL         使用的模型（默认 llama-3.3-70b-versatile）
    NOTE_MAX_TOKENS    最大输出 token（默认 4096）
    NOTE_SRT_CHARS     每段 SRT 最大字符数（默认 6000，约 2000 token）
"""

import os
import re
import sys
import time
import datetime
import argparse
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv
from groq import Groq

# ─── 路径配置 ─────────────────────────────────────────────────────────────────
BASE_DIR     = Path(__file__).resolve().parent.parent
OUTPUT_MEDIA = BASE_DIR / "data" / "output"
NOTES_DIR    = BASE_DIR / "notes"
PROMPTS_DIR  = BASE_DIR / "prompts"
PROMPT_FILE  = PROMPTS_DIR / "notebooklm_prompt.md"
INDEX_FILE   = NOTES_DIR / "INDEX.md"

# ─── 模型配置 ─────────────────────────────────────────────────────────────────
NOTE_MODEL      = os.getenv("NOTE_MODEL", "llama-3.1-8b-instant")
NOTE_MAX_TOKENS = int(os.getenv("NOTE_MAX_TOKENS", "2048"))
# 每段送入 LLM 的 SRT 文本最大字符数（约 1500 token）
NOTE_SRT_CHARS  = int(os.getenv("NOTE_SRT_CHARS", "4000"))
# 段间休眠秒数（避免连续调用撞 TPM 限制）
NOTE_SEGMENT_SLEEP = int(os.getenv("NOTE_SEGMENT_SLEEP", "10"))

# ─── 工具函数 ─────────────────────────────────────────────────────────────────

def _srt_line_count(srt_path: Path) -> int:
    try:
        return sum(1 for _ in srt_path.open(encoding="utf-8"))
    except OSError:
        return 0


def _srt_duration(srt_path: Path) -> str:
    """从 SRT 文件末尾读取最后一个时间戳"""
    try:
        lines = srt_path.read_text(encoding="utf-8").splitlines()
        for line in reversed(lines):
            m = re.search(r"(\d{2}:\d{2}:\d{2}),\d{3}\s*-->", line)
            if m:
                return m.group(1)
    except OSError:
        pass
    return "未知"


def _frame_count(folder: Path) -> int:
    return sum(1 for _ in folder.glob("frame_*.jpg"))


def _read_srt(srt_path: Path, max_chars: int) -> str:
    """读取 SRT，超长时取头 40% + 尾 60%（保留开头脉络和结尾结论）"""
    try:
        text = srt_path.read_text(encoding="utf-8")
    except OSError:
        return ""
    if len(text) <= max_chars:
        return text
    head = int(max_chars * 0.4)
    tail = max_chars - head
    return text[:head] + "\n\n[... 中间内容已截断，仅保留头尾 ...]\n\n" + text[-tail:]


def get_note_prefix(folder_name: str) -> str:
    """从文件夹名提取课程前缀，以最后一个 _NN_ 作为段号分隔符

    例：
    "00_12_第十二课：离线签名与应用_01_课前讨论" → "00_12_第十二课：离线签名与应用"
    "00_14_第十四课：深入合约创建_02_QA..."     → "00_14_第十四课：深入合约创建"

    关键：取最后一个 _\d{2}_ 的位置，避免课程编号里的数字（如 _12_）被误判。
    """
    matches = list(re.finditer(r"_\d{2}_", folder_name))
    if len(matches) >= 2:
        # 最后一个 _NN_ 是段号，取其前缀
        return folder_name[:matches[-1].start()]
    if len(matches) == 1:
        return folder_name[:matches[0].start()]
    return folder_name


def collect_segments(prefix: str) -> List[Dict]:
    """收集同一课程前缀下所有已转录的视频段元数据，按名称排序"""
    segments = []
    for folder in sorted(OUTPUT_MEDIA.iterdir()):
        if not folder.is_dir():
            continue
        if not folder.name.startswith(prefix + "_"):
            continue
        srt   = folder / "transcript" / "audio.srt"
        md    = folder / "transcript" / "transcript.md"
        audio = folder / "audio.mp3"
        if not srt.exists():
            continue
        segments.append({
            "folder":      folder.name,
            "path":        folder,
            "srt":         srt,
            "md":          md,
            "audio":       audio,
            "srt_lines":   _srt_line_count(srt),
            "duration":    _srt_duration(srt),
            "frame_count": _frame_count(folder),
            "audio_mb":    round(audio.stat().st_size / 1024 / 1024, 1) if audio.exists() else 0,
        })
    return segments


def discover_all_prefixes() -> List[str]:
    """从 data/output/ 中发现所有已转录的课程前缀"""
    prefixes: set = set()
    for folder in OUTPUT_MEDIA.iterdir():
        if not folder.is_dir():
            continue
        if (folder / "transcript" / "audio.srt").exists():
            prefixes.add(get_note_prefix(folder.name))
    return sorted(prefixes)


# ─── LLM 调用 ─────────────────────────────────────────────────────────────────

def _build_segment_prompt(seg: Dict, prompt_text: str, seg_idx: int, total: int) -> str:
    """为单个视频段构建 prompt，要求输出三个结构化标记块供后续合并"""
    srt_content = _read_srt(seg["srt"], NOTE_SRT_CHARS)
    seg_name = seg["folder"]
    return "\n".join([
        prompt_text,
        "",
        "=" * 60,
        f"# 待分析视频段 [{seg_idx}/{total}]",
        f"- 视频段名称：{seg_name}",
        f"- 时长：{seg['duration']}",
        f"- 转录行数：{seg['srt_lines']} 行",
        "=" * 60,
        "",
        "## 转录内容（SRT）：",
        srt_content,
        "",
        "=" * 60,
        "## 输出格式要求（必须严格遵守）",
        "",
        "请将输出分为以下三个标记块，每块之间用分隔符隔开：",
        "",
        "===MINDMAP===",
        "（此处输出本视频段的思维导图内容，使用 Markdown 标题层级格式（## 和 ### 和 ####），",
        "不要使用列表符号（-）。层次：## 核心主题 → ### 子概念 → #### 关键论据/例子。",
        "每个节点必须附带精确时间戳，格式如 `[00:05:30]`，写在标题文字之后同一行。",
        "示例：",
        "## EIP-712 结构化签名 [00:12:00]",
        "### 解决问题：可读性差的 bytes 签名",
        "### 实现：TypedData + domain separator [00:15:30]",
        "#### 关键字段：chainId, verifyingContract）",
        "",
        "===DATATABLE===",
        "（此处输出本视频段的数据表格行，不含表头。",
        f"每行格式：| {seg_name} | 时间戳 | 主题/章节 | 关键术语/数据/对比 | 证据（转录行/关键帧） | 可执行结论 |",
        "每个知识点输出一行，至少 3 行。）",
        "",
        "===DETAIL===",
        "（此处输出本视频段的补充说明，包含：重要代码片段、核心概念解释、学习建议。）",
        "",
        "===END===",
        "",
        "注意：必须输出全部三个标记块，标记符本身单独占一行，不要省略或修改标记符。",
    ])


def call_llm(client: Groq, prompt: str, retry: int = 3) -> str:
    """调用 LLM，支持 TPM 限制时自动等待重试"""
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
                            "结构清晰、带时间戳的 NotebookLM 风格 Markdown 笔记。"
                            "直接输出 Markdown，从标题开始，不含解释性前言。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            err_str = str(e)
            # TPM 限流：等待 65 秒（每分钟重置）后重试
            if "rate_limit_exceeded" in err_str or "tokens per minute" in err_str.lower():
                wait = 65
                print(f"   ⏳ 触发 TPM 限流，等待 {wait}s 后重试（{attempt+1}/{retry}）...", flush=True)
                time.sleep(wait)
                continue
            # 413 token 超大：缩短 SRT 采样后重试
            if "413" in err_str and attempt < retry - 1:
                print(f"   ⚠️  413 Token 超大，缩减内容后重试（{attempt+1}/{retry}）...", flush=True)
                # 在 prompt 中裁切 SRT 内容
                prompt = re.sub(
                    r"(\[... 中间内容已截断.*?\])",
                    "[... 大量内容已省略 ...]",
                    prompt
                )
                # 将 NOTE_SRT_CHARS 减半效果：直接截断 prompt
                half = len(prompt) // 2
                prompt = prompt[:half] + "\n\n[... 内容已缩减 ...]\n\n请根据以上内容生成笔记章节。"
                continue
            raise
    raise RuntimeError(f"LLM 调用失败，已重试 {retry} 次")


# ─── 核心生成函数 ─────────────────────────────────────────────────────────────

def _parse_blocks(raw: str) -> Dict[str, str]:
    """从 LLM 输出中解析 ===MINDMAP=== / ===DATATABLE=== / ===DETAIL=== 三个标记块"""
    blocks = {"mindmap": "", "datatable": "", "detail": raw}
    pattern = re.compile(
        r"===MINDMAP===\s*(.*?)\s*===DATATABLE===\s*(.*?)\s*===DETAIL===\s*(.*?)\s*(?:===END===|$)",
        re.DOTALL | re.IGNORECASE,
    )
    m = pattern.search(raw)
    if m:
        blocks["mindmap"]   = m.group(1).strip()
        blocks["datatable"] = m.group(2).strip()
        blocks["detail"]    = m.group(3).strip()
    return blocks


def generate_for_prefix(
    client: Groq,
    prefix: str,
    prompt_text: str,
    *,
    force: bool = False,
) -> bool:
    """为指定前缀的课程生成一个合并笔记文件

    策略：逐段调用 LLM（每段输出 MINDMAP / DATATABLE / DETAIL 块）
         → 解析各段内容 → 组装成统一的 Mind Map + Data Table + 各段详情
    """
    NOTES_DIR.mkdir(exist_ok=True)
    out_file = NOTES_DIR / f"{prefix}.md"

    if out_file.exists() and not force:
        print(f"⏭️  已跳过: {prefix}（笔记已存在，使用 --force 覆盖）", flush=True)
        return False

    segments = collect_segments(prefix)
    if not segments:
        print(f"❌ 跳过: {prefix}（未找到已转录的视频段）", flush=True)
        return False

    total_lines  = sum(s["srt_lines"] for s in segments)
    total_frames = sum(s["frame_count"] for s in segments)
    total_secs   = 0
    for s in segments:
        m = re.match(r"(\d+):(\d+):(\d+)", s["duration"])
        if m:
            total_secs += int(m.group(1))*3600 + int(m.group(2))*60 + int(m.group(3))
    total_dur = str(datetime.timedelta(seconds=total_secs)) if total_secs else "未知"

    print(f"\n📚 开始生成课程笔记: {prefix}", flush=True)
    print(f"   {len(segments)} 个视频段 | 总时长 {total_dur} | {total_lines:,} 行转录 | {total_frames:,} 帧", flush=True)

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # ── 逐段调用 LLM，收集解析结果 ──
    all_mindmap:   List[str] = []
    all_datatable: List[str] = []
    all_detail:    List[str] = []

    for i, seg in enumerate(segments, 1):
        seg_short = seg["folder"].replace(prefix + "_", "")
        print(f"   [{i}/{len(segments)}] 生成章节: {seg_short}", flush=True)
        prompt = _build_segment_prompt(seg, prompt_text, i, len(segments))
        try:
            raw = call_llm(client, prompt)
            blocks = _parse_blocks(raw)
        except Exception as e:
            print(f"   ❌ 章节生成失败: {e}", flush=True)
            blocks = {
                "mindmap":   f"- ⚠️ {seg_short}（生成失败：{e}）",
                "datatable": f"| {seg['folder']} | — | 生成失败 | — | — | — |",
                "detail":    f"> ⚠️ 本章节生成失败：{e}",
            }

        # 每段作为 markmap 的一级分支（## 标题），其内容降一级（## → ###, ### → ####）
        all_mindmap.append(f"## {seg_short}")
        for line in blocks["mindmap"].splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                # 已有标题层级：整体降一级（## → ###）
                all_mindmap.append("#" + line.lstrip("#").rstrip() if stripped.startswith("##") else line)
            elif stripped:
                # 非标题行（意外输出的列表等）：作为三级节点
                all_mindmap.append(f"### {stripped.lstrip('- ').lstrip('* ')}")
        all_mindmap.append("")

        all_datatable.extend(
            line for line in blocks["datatable"].splitlines()
            if line.strip().startswith("|")
        )

        all_detail.append(f"\n### 补充说明：{seg_short}\n\n{blocks['detail']}\n")

        if i < len(segments):
            time.sleep(NOTE_SEGMENT_SLEEP)

    # ── 组装最终笔记 ──
    table_header = (
        "| 视频段 | 时间戳节点 | 主题/章节 | 关键术语/数据/对比 | "
        "证据来源（转录/关键帧） | 可执行结论 |\n"
        "|--------|-----------|---------|------------------|---------------------|--------|"
    )
    datatable_body = "\n".join(all_datatable) if all_datatable else "| — | — | — | — | — | — |"

    parts = [
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
        seg_short = seg["folder"].replace(prefix + "_", "")
        parts.append(
            f"- **{seg_short}**：时长 {seg['duration']}，"
            f"{seg['srt_lines']:,} 行转录，{seg['frame_count']:,} 张关键帧"
        )
    # markmap 代码块：根节点为课程名，各段为一级分支
    markmap_body = "\n".join(all_mindmap).strip()
    parts += [
        "",
        "---",
        "",
        "## 1. 结构化思维导图 (Mind Map)",
        "",
        "> 需要 Obsidian 插件 [Mindmap NextGen](obsidian://show-plugin?id=mindmap-nextgen) 或 [Render Block Markmap](obsidian://show-plugin?id=obsidian-render-block-markmap) 以渲染交互式思维导图。",
        "",
        "```markmap",
        f"# {prefix}",
        markmap_body,
        "```",
    ]
    parts += [
        "",
        "---",
        "",
        "## 2. 综合数据表格 (Data Table)",
        "",
        table_header,
        datatable_body,
        "",
        "---",
        "",
        "## 3. 各段详情",
    ]
    parts.extend(all_detail)

    full_content = "\n".join(parts)
    out_file.write_text(full_content, encoding="utf-8")

    size_kb = round(out_file.stat().st_size / 1024, 1)
    note_lines = full_content.count("\n") + 1
    print(f"   ✅ 合并笔记已保存：notes/{prefix}.md（{size_kb}KB，{note_lines} 行）", flush=True)
    return True


# ─── INDEX.md 自动重建 ────────────────────────────────────────────────────────

def rebuild_index() -> None:
    """扫描 notes/ 文件夹，完全重建 INDEX.md"""
    NOTES_DIR.mkdir(exist_ok=True)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    note_files = sorted(
        f for f in NOTES_DIR.glob("*.md")
        if f.name not in ("INDEX.md", "README.md")
    )

    rows: List[Dict] = []
    for note_file in note_files:
        prefix = note_file.stem
        segs   = collect_segments(prefix)

        total_srt    = sum(s["srt_lines"]  for s in segs)
        total_frames = sum(s["frame_count"] for s in segs)
        total_secs   = 0
        for s in segs:
            m = re.match(r"(\d+):(\d+):(\d+)", s["duration"])
            if m:
                total_secs += int(m.group(1))*3600 + int(m.group(2))*60 + int(m.group(3))
        duration_str = str(datetime.timedelta(seconds=total_secs)) if total_secs else "未知"

        rows.append({
            "course":    prefix,
            "file":      prefix,
            "segments":  str(len(segs)),
            "duration":  duration_str,
            "srt_lines": f"{total_srt:,}",
            "frames":    f"{total_frames:,}",
        })

    lines = [
        "---",
        "title: 📚 课程笔记库",
        f"updated: {now}",
        "---",
        "",
        "# 📚 视频课程笔记库",
        "",
        "> 由 `src/generate_note.py` 自动维护，每次生成笔记后自动更新本文件。",
        "",
        "## 课程索引",
        "",
        "| 课程名称 | 笔记文件 | 段数 | 时长 | 转录行数 | 关键帧数 |",
        "|---------|---------|------|------|---------|---------|",
    ]
    for r in rows:
        link = f"[[{r['file']}]]"
        lines.append(
            f"| {r['course']} | {link} | {r['segments']} | {r['duration']} "
            f"| {r['srt_lines']} | {r['frames']} |"
        )

    total_seg     = sum(int(r["segments"]) for r in rows)
    total_srt_raw = sum(int(r["srt_lines"].replace(",", "")) for r in rows)
    total_fr_raw  = sum(int(r["frames"].replace(",", "")) for r in rows)

    lines += [
        f"| **合计** | **{len(rows)} 门课程** | **{total_seg}** | — "
        f"| **{total_srt_raw:,}** | **{total_fr_raw:,}** |",
        "",
        "---",
        "",
        "## 快速命令",
        "",
        "```bash",
        "# 生成指定课程笔记（合并所有视频段）",
        '.venv/bin/python src/generate_note.py --prefix "00_14_第十四课：深入合约创建"',
        "",
        "# 批量生成所有课程笔记（跳过已有）",
        ".venv/bin/python src/generate_note.py --all",
        "",
        "# 强制重新生成",
        ".venv/bin/python src/generate_note.py --all --force",
        "",
        "# 仅重建 INDEX",
        ".venv/bin/python src/generate_note.py --update-index",
        "```",
        "",
        f"---",
        f"*最后更新：{now}*",
    ]

    INDEX_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"✅ INDEX.md 已更新（{len(rows)} 门课程）", flush=True)


# ─── 入口 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    load_dotenv()
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        print("❌ 未检测到 GROQ_API_KEY，请在 .env 中设置。")
        sys.exit(1)
    if not PROMPT_FILE.exists():
        print(f"❌ 未找到提示词文件：{PROMPT_FILE}")
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="为视频转录生成 NotebookLM 风格笔记（同一课程的多视频段合并为一份笔记）"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prefix", "-p",
                       help="课程前缀，例如 '00_14_第十四课：深入合约创建'")
    group.add_argument("--all", "-a", action="store_true",
                       help="批量生成所有课程笔记（跳过已有）")
    group.add_argument("--update-index", action="store_true",
                       help="仅重建 INDEX.md，不生成笔记")
    parser.add_argument("--force", "-f", action="store_true",
                        help="强制重新生成已有笔记")
    args = parser.parse_args()

    prompt_text = PROMPT_FILE.read_text(encoding="utf-8")
    client      = Groq(api_key=api_key, timeout=300.0)

    if args.update_index:
        rebuild_index()
        return

    prefixes = [args.prefix] if args.prefix else discover_all_prefixes()
    print(f"🔍 共 {len(prefixes)} 个课程前缀：{prefixes}", flush=True)

    generated = 0
    for prefix in prefixes:
        if generate_for_prefix(client, prefix, prompt_text, force=args.force):
            generated += 1

    print("\n🔄 正在更新 notes/INDEX.md ...", flush=True)
    rebuild_index()
    print(f"\n✅ 完成！本次新生成 {generated} 篇课程笔记。", flush=True)


if __name__ == "__main__":
    main()
