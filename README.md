# VideoToText

视频课程 → 转录 → NotebookLM 风格笔记的自动化工作流。

## 项目结构

```
VideoToText/
├── src/                      # 核心 Python 脚本
│   ├── extract_media.py      # 从视频提取音频和关键帧
│   ├── transcribe_audio.py   # Groq API 增量转录
│   └── generate_note.py      # 生成笔记 + 自动更新 INDEX.md
│
├── prompts/
│   └── notebooklm_prompt.md  # AI 生成笔记的提示词
│
├── notes/                    # 生成的课程笔记（由脚本自动维护）
│   ├── INDEX.md              # ← 自动维护，每次生成后更新
│   └── 00_XX_第X课：XXX.md
│
├── scripts/
│   └── run_pipeline.sh       # 一键执行全流程
│
├── data/                     # 所有数据（唯一入口，无冗余路径）
│   ├── input/                # 原始视频 ← 新视频放这里
│   └── output/               # 提取产物（音频、关键帧、转录）
│
├── docs/                     # 项目文档
├── .env                      # API 密钥（不提交到 git）
├── .env.example              # 密钥配置模板
└── requirements.txt
```

## 快速开始

### 1. 环境配置

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# 编辑 .env，填入 GROQ_API_KEY
```

### 2. 添加视频（推荐入口）

```bash
# 添加单个视频并自动触发完整流水线
bash scripts/add_video.sh /path/to/00_14_第十四课：XXX_01_YYY.mp4

# 仅添加视频，不自动处理
bash scripts/add_video.sh --no-run /path/to/video.mp4

# 也可直接将视频拖入 data/input/ 文件夹，再手动运行流水线
bash scripts/run_pipeline.sh
```

> **命名规范**：`{课程编号}_{课程名}_{段编号}_{段标题}.mp4`
> 例：`00_14_第十四课：合约升级_01_代理模式基础.mp4`

### 3. 单步执行

```bash
.venv/bin/python src/extract_media.py               # 提取音频和关键帧
.venv/bin/python src/transcribe_audio.py            # 转录（自动跳过已完成）
.venv/bin/python src/generate_note.py --all         # 生成笔记（自动跳过已有）
.venv/bin/python src/generate_note.py --update-index  # 仅重建 INDEX.md
```

## 关键特性

- **增量处理**：已处理的视频自动跳过，只处理新增视频
- **自动分片**：超过 18MB 的音频自动按 5 分钟分片上传，解决 413 / 超时问题
- **笔记自动索引**：每次生成笔记后自动更新 `notes/INDEX.md`，Obsidian Wiki 链接兼容
- **强制重处理**：支持 `TRANSCRIBE_FORCE=true`、`--force` 参数覆盖已有结果

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GROQ_API_KEY` | 必需 | Groq API 密钥 |
| `NOTE_MODEL` | `llama-3.3-70b-versatile` | 生成笔记使用的模型 |
| `NOTE_MAX_TOKENS` | `8192` | 笔记最大输出长度 |
| `TRANSCRIBE_FORCE` | `false` | 强制重新转录所有 |
| `TRANSCRIBE_CHUNK_SECONDS` | `300` | 分片时长（秒） |
