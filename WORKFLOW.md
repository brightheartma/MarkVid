#!/usr/bin/env bash
# =============================================================================
# 📖 VideoToText 完整工作流指南
# =============================================================================
# 本文档说明从"有新视频"到"生成笔记"的完整流程
# =============================================================================

# 【第一步】准备视频文件
# ─────────────────────────────────────────────────────────────────────────

# 视频命名规范（决定最终笔记名）：
#   {课程编号}_{课程名}_{段编号}_{段标题}.mp4
#
# 例如：
#   ✅ 00_14_第十四课：合约升级_01_代理模式基础.mp4
#   ✅ 00_14_第十四课：合约升级_02_透明代理与 UUPS_实现.mp4
#   ❌ lesson14.mp4                  （太简洁，无法识别）
#   ❌ 视频_final_final2_v3.mp4      （无课程编号）

# 视频位置 = data/input/


# 【第二步】添加视频（三种方式任选）
# ─────────────────────────────────────────────────────────────────────────

# 🎯 方式 A：推荐使用 add_video.sh（自动复制 + 触发处理）
bash scripts/add_video.sh /path/to/00_14_第十四课：合约升级_01_代理模式基础.mp4
# → 自动执行完整流水线：提取 → 转录 → 生成笔记 → 更新 INDEX

# 🎯 方式 B：仅复制视频，延后处理
bash scripts/add_video.sh --no-run /path/to/video.mp4
# → 视频放入 data/input/，等待手动触发流水线

# 🎯 方式 C：手动复制文件
cp /path/to/video.mp4 data/input/
# → 之后手动运行流水线


# 【第三步】执行处理流水线
# ─────────────────────────────────────────────────────────────────────────

# 如果在第二步已选 方式 A，可跳过本步（自动执行）
# 否则手动运行一键流水线：

bash scripts/run_pipeline.sh

# 这会依次执行：
#   Step 1 - 提取媒体   .venv/bin/python src/extract_media.py
#           ├─ 从 data/input/ 的所有视频中提取
#           ├─ 生成 data/output/{视频名}/audio.mp3
#           └─ 生成 data/output/{视频名}/frame_XXXX_HH-MM-SS.jpg
#
#   Step 2 - 转录音频   .venv/bin/python src/transcribe_audio.py
#           ├─ 使用 Groq API 转录
#           ├─ 已转录的跳过（增量模式）
#           ├─ 大文件自动分片（5分钟/片）
#           ├─ 生成 data/output/{视频名}/transcript/audio.srt
#           └─ 生成 data/output/{视频名}/transcript/transcript.md
#
#   Step 3 - 生成笔记   .venv/bin/python src/generate_note.py --all
#           ├─ 调用 Groq LLM（llama-3.3-70b）
#           ├─ 自动合并同课程前缀的多个视频段
#           ├─ 生成 notes/00_14_第十四课：合约升级.md
#           └─ 自动重建 notes/INDEX.md（包含课程统计）
#
#   Step 4 - 清理       rm -rf data/output/*/transcript/_chunks_work/


# 【第四步】查看结果
# ─────────────────────────────────────────────────────────────────────────

# 笔记位置：
#   notes/00_14_第十四课：合约升级.md     ← 最终课程笔记

# 索引位置：
#   notes/INDEX.md                        ← Obsidian Wiki 风格索引

# 转录文件：
#   data/output/{视频名}/transcript/audio.srt          ← SRT 格式（带时间戳）
#   data/output/{视频名}/transcript/transcript.md      ← Markdown 格式


# 【附录】手动执行单个步骤
# ─────────────────────────────────────────────────────────────────────────

# 仅提取媒体（不转录）
.venv/bin/python src/extract_media.py

# 仅转录已提取的音频（不生成笔记）
.venv/bin/python src/transcribe_audio.py

# 强制重新转录所有（包括已完成）
TRANSCRIBE_FORCE=true .venv/bin/python src/transcribe_audio.py

# 为指定课程生成笔记（不处理其他课程）
.venv/bin/python src/generate_note.py --prefix "00_14_第十四课：合约升级"

# 强制重新生成已有笔记
.venv/bin/python src/generate_note.py --all --force

# 仅重建 INDEX.md（不生成笔记）
.venv/bin/python src/generate_note.py --update-index


# 【附录】高级参数
# ─────────────────────────────────────────────────────────────────────────

# 转录参数（环境变量）
TRANSCRIBE_FORCE=true          # 强制重新转录所有
TRANSCRIBE_CHUNK_SECONDS=600   # 分片时长（默认 300秒）

# 笔记生成参数（环境变量）
NOTE_MODEL=llama-3.3-70b-versatile  # 模型选择（默认）
NOTE_MAX_TOKENS=8192                # 最大输出长度


# 【附录】流水线参数
# ─────────────────────────────────────────────────────────────────────────

bash scripts/run_pipeline.sh --force-note        # 强制重新生成所有笔记
bash scripts/run_pipeline.sh --force-transcribe  # 强制重新转录
bash scripts/run_pipeline.sh --force-all         # 全部强制重新处理
bash scripts/run_pipeline.sh --skip-extract      # 跳过提取（仅转录+笔记）
bash scripts/run_pipeline.sh --skip-transcribe   # 跳过转录（仅提取+笔记）
bash scripts/run_pipeline.sh --note-only         # 仅生成笔记（跳过提取和转录）


# 【附录】项目结构
# ─────────────────────────────────────────────────────────────────────────

VideoToText/
├── data/
│   ├── input/              ← 📥 新视频放这里
│   │   ├── 00_14_...01....mp4
│   │   └── 00_14_...02....mp4
│   └── output/             ← 🔄 提取产物
│       ├── 00_14_...01.../
│       │   ├── audio.mp3
│       │   ├── frame_0000_0-00-00.jpg
│       │   ├── frame_0001_0-00-02.jpg
│       │   └── transcript/
│       │       ├── audio.srt
│       │       └── transcript.md
│       └── 00_14_...02.../
│           └── ...
│
├── notes/                  ← 📚 最终笔记
│   ├── 00_14_第十四课：合约升级.md
│   └── INDEX.md            ← 索引
│
├── src/                    ← 🔧 核心脚本
│   ├── extract_media.py
│   ├── transcribe_audio.py
│   └── generate_note.py
│
├── scripts/                ← 🚀 入口脚本
│   ├── add_video.sh        ← 【标准入口】添加视频
│   ├── run_pipeline.sh     ← 一键流水线
│   └── transcribe_test.sh  ← 测试脚本
│
├── prompts/
│   └── notebooklm_prompt.md  ← 笔记生成提示词
│
├── docs/                   ← 📄 文档
└── README.md
