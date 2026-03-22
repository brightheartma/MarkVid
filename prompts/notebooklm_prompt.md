# 笔记生成 Prompt 文档（与 generate_note.py 同步）

> **说明**：此文件为 `src/generate_note.py` 中分段 Prompt（`_build_segment_prompt()`）、课程简报 Prompt（`_build_briefing_prompt()`）、课程博文 Prompt（`_build_blogpost_prompt()`）的文档化版本，供人工审阅与调整参考。每次修改 Python 中的 Prompt 逻辑后，应同步更新本文件。

---

## 转录数据源（与 `data/srt_exports/` 的关系）

| 来源 | 路径约定 | 说明 |
| :--- | :--- | :--- |
| 流水线输出 | `data/output/{段目录}/transcript/audio.srt` | 可与同段目录下关键帧一并统计 `frames` |
| 导出归档 | `data/srt_exports/{课程前缀}/*.srt` | 每课一个子文件夹，每段一个 `.srt` 文件；**无 `data/output` 时脚本仍可从该目录发现课程** |

**同一课程前缀**在两边同时存在时，脚本**只采用 `data/output`**，避免重复。仅存在 `srt_exports` 时，`frames` 记为 `0`。

---

## 角色与目标

你是一个高信噪比的视频课程内容分析师。  
针对每个视频段的 SRT 转录文本，生成结构化的 **三区块**（`<<<MINDMAP>>>` / `<<<DATATABLE>>>` / `<<<DETAIL>>>`）笔记内容；全部段完成后，再基于**全课转录**生成一节 **简报**（见「课程级简报」）；可选择同时生成独立的**博文**（见「课程级博文」）。  
最终由 Python 脚本汇总为可在 Obsidian 中完美渲染的 Markdown 课程笔记，博文输出到独立文件。

---

## ⚠️ 最高优先级规则——反幻觉

1. **忠于原文**：所有内容必须 100% 来自转录文本，禁止添加转录中不存在的信息。
2. **禁止编造代码**：如果转录中没有逐行念出代码，就不要输出任何代码块。只有当讲师在转录中逐行念出了完整代码时，才可以用代码块还原。
3. **禁止指令复读**：禁止将 Prompt 中的任何指令文字原样输出为内容。
4. **禁止假代码块**：绝对禁止输出类似 `` ```solidity 无代码 ``` `` 这种反引号内含有中文描述的单行假代码块。

---

## 输入

每次调用包含以下上下文：

- 视频段名称、当前段序号/总段数、时长、转录行数
- 经过头尾截断的 SRT 转录文本（最多 `NOTE_SRT_CHARS` 字符，默认 4000）

---

## 输出格式

**使用三个区块标记符**（每个标记符独占一行），Python 脚本解析后分别组装：

```
<<<MINDMAP>>>
（思维导图内容）

<<<DATATABLE>>>
（数据表格行）

<<<DETAIL>>>
（详细解析内容）

<<<END>>>
```

---

## 区块 1：`<<<MINDMAP>>>`

**格式要求：**
- 使用 Markdown 无序列表 `- ` 输出本段知识点层级
- **绝对禁止使用 `#` 标题语法**，只能通过缩进（每级 2 个空格）体现层级
- 每个节点尾部附真实时间戳 `[HH:MM:SS]`

**Python 后处理：**  
`_clean_mindmap()` 会过滤掉 `#` 标题（转换为列表项）、Prompt 指令复读文本、区块标记符等噪声。  
`_list_to_markmap()` 再将列表转换为 ` ```markmap ` 代码块，供 Obsidian **Mindmap NextGen** 插件渲染。

---

## 区块 2：`<<<DATATABLE>>>`

**格式要求：**
- 仅输出数据行，**禁止输出表头、分割线、占位符**
- 每行列顺序：`| 视频段名 | HH:MM:SS | 主题 | 关键术语 | 证据来源 | 可执行结论 |`
- 时间戳必须是 `HH:MM:SS` 格式（禁止带毫秒，禁止带 `-->` 箭头）
- **每行必须有且仅有 7 个管道符 `|`**（6 列），多列或少列会导致 Obsidian 表格崩溃

**Python 后处理：**  
`_clean_datatable()` 负责：过滤占位符关键词、清除毫秒 `00:00:12,040 → 00:00:12`、清除 SRT 箭头、过滤幽灵空行、**强制丢弃管道符数量 ≠ 7 的行**。  
最终在笔记中拼接统一表头：

| 视频段 | 时间戳节点 | 主题/章节 | 关键术语/数据/对比 | 证据来源（转录/关键帧） | 可执行结论 |
| :--- | :--- | :--- | :--- | :--- | :--- |

---

## 区块 3：`<<<DETAIL>>>`

**格式要求：**  
严格输出 3 个项目符号，不得增减：

- **核心大纲**：2-3 句概述核心目标与讨论焦点
- **关键数据与术语**：提取转录中出现的专业名词并简短解释
- **详细解析**：基于转录复盘论述逻辑，用纯文本描述

如果转录中讲师逐行念出了代码，可用代码块还原（如 ` ```solidity `）；否则直接纯文本，禁止编造。

**Python 后处理：**  
`_clean_detail()` 负责：
1. 正则一击必杀同行内含中文的假代码块
2. 剥除 LLM 错误包裹的外层 `` ```markdown `` 
3. 清除泄漏的区块标记符和章节标题
4. 修复 4+ 空格缩进导致的 Markdown 误渲染
5. `_strip_fabricated_code_blocks()` 兜底移除编造代码块（有效代码行 < 2 或含编造信号词）

---

## 最终笔记结构

Python 脚本将所有段落的三区块内容汇总，生成以下结构的 `notes/{prefix}.md`：

```
---
title / created / segments / duration / srt_lines / frames
---

# {课程名}

> 课程概况

## 0. 素材与覆盖范围
## 1. 简报 (Briefing)               ← 全课转录综合，执行摘要 + 正文
## 2. 结构化思维导图 (Mind Map)   ← markmap 代码块
## 3. 综合数据表格 (Data Table)   ← 汇总所有段的表格行
## 4. 各段详情 (Segment Details)  ← 每段一个 ### 4.X 小节
```

`notes/INDEX.md` 在每次笔记生成后自动更新。博文不计入 INDEX，统一存放在 `notes/blog/` 目录。

---

## 课程级简报：`NOTE_BRIEFING_CHARS`（`generate_note.py`）

在**所有分段**的 `<<<MINDMAP>>>` / `<<<DATATABLE>>>` / `<<<DETAIL>>>` 生成完成后，脚本会再调用一次 LLM：合并全课转录（超长则头尾截断，上限默认 `NOTE_BRIEFING_CHARS=12000`），生成 **## 1. 简报 (Briefing)**。

**写作要求（与实现对齐）**：在反幻觉前提下，按下列英文规范内化撰写（输出为简体中文，结构含执行摘要 + 分主题正文）：

> Create a comprehensive briefing document that synthesizes the main themes and ideas from the sources. Start with a concise Executive Summary that presents the most critical takeaways upfront. The body of the document must provide a detailed and thorough examination of the main themes, evidence, and conclusions found in the sources. This analysis should be structured logically with headings and bullet points to ensure clarity. The tone must be objective and incisive.

**笔记中的章节编号**：`0` 素材 → `1` 简报 → `2` Mind Map → `3` Data Table → `4` 各段详情（分段小标题为 `### 4.x`）。

---

## 课程级博文：`NOTE_BLOGPOST_CHARS`（`generate_note.py`）

通过 `--blog` 或 `--blog-only` flag 触发，为每门课独立生成一篇博文，输出到 `notes/blog/{prefix}_blog.md`。

**调用函数**：`_build_blogpost_prompt()` / `_clean_blogpost()` / `generate_blogpost_for_prefix()`

**转录截断上限**：`NOTE_BLOGPOST_CHARS`（默认 `12000`，与简报独立配置）

**写作要求（与实现对齐）**：在反幻觉前提下，按下列英文规范内化撰写（输出为简体中文）：

> Act as a thoughtful writer and synthesizer of ideas, tasked with creating an engaging and readable blog post for a popular online publishing platform known for its clean aesthetic and insightful content. Your goal is to distill the top most surprising, counter-intuitive, or impactful takeaways from the provided source materials into a compelling listicle. The writing style should be clean, accessible, and highly scannable, employing a conversational yet intelligent tone. Craft a compelling, click-worthy headline. Begin the article with a short introduction that hooks the reader by establishing a relatable problem or curiosity, then present each of the takeaway points as a distinct section with a clear, bolded subheading. Within each section, use short paragraphs to explain the concept clearly, and don't just summarize; offer a brief analysis or a reflection on why this point is so interesting or important, and if a powerful quote exists in the sources, feature it in a blockquote for emphasis. Conclude the post with a brief, forward-looking summary that leaves the reader with a final thought-provoking question or a powerful takeaway to ponder.

**输出结构**：

```
# [钩子式标题]                         ← 一级标题
                                        ← 2–3 句引言（无小标题）
## [Takeaway 1 短语式小标题]           ← 4–6 个 takeaway 节
短段落 + 分析 + 可选 > 引用块

## 最后的问题                           ← 结语节
1 段结语 + 1 个发人深省的问题句
```

**YAML frontmatter**（由脚本自动添加）：

```yaml
---
title: {prefix} - 博文
created: YYYY-MM-DD HH:MM
source_note: "[[{prefix}.md]]"
tags: [blog]
---
```

**CLI 用法**：

```bash
# 同时生成笔记 + 博文
python3 src/generate_note.py --prefix 00_15_第十五课：深入EVM与存储布局 --blog

# 只生成博文（笔记已存在时）
python3 src/generate_note.py --prefix 00_15_第十五课：深入EVM与存储布局 --blog-only

# 全部课程都生成博文
python3 src/generate_note.py --all --blog-only --force
```
