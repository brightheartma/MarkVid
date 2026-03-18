# 快速参考 - 转录脚本命令速查表

## 最常用的 3 个命令

```bash
# 1. 日常转录（增量，推荐）
.venv/bin/python transcribe_audio.py

# 2. 强制重新转录所有
TRANSCRIBE_FORCE=true .venv/bin/python transcribe_audio.py

# 3. 调整分片参数重试
TRANSCRIBE_CHUNK_SECONDS=120 .venv/bin/python transcribe_audio.py
```

---

## 环境变量速查

| 命令 | 说明 |
|------|------|
| `TRANSCRIBE_FORCE=true` | 强制重新转录所有文件 |
| `TRANSCRIBE_CHUNK_SECONDS=120` | 改为 120 秒分片（更稳定） |
| `TRANSCRIBE_AB=24k` | 音频码率改为 24k（更小更快） |
| `TRANSCRIBE_CHUNK_WHEN_OVER_BYTES=$((15*1024*1024))` | 15MB 以上启用分片 |

---

## 完整工作流一条龙

```bash
# 新增视频后，一键完成所有
.venv/bin/python extract_media.py && .venv/bin/python transcribe_audio.py
```

---

## 场景快速解决方案

### 场景 1：转录卡住/超时

```bash
# 改成 120 秒分片，更小码率
TRANSCRIBE_CHUNK_SECONDS=120 TRANSCRIBE_AB=24k .venv/bin/python transcribe_audio.py
```

### 场景 2：某个视频转录失败

```bash
# 删除其转录文件，重新转录
rm -f output_media/视频名/transcript/*.srt output_media/视频名/transcript/*.md
.venv/bin/python transcribe_audio.py
```

### 场景 3：全部重新转录

```bash
# 强制模式
TRANSCRIBE_FORCE=true .venv/bin/python transcribe_audio.py
```

### 场景 4：清理临时文件

```bash
find output_media -name "_chunks_work" -type d -exec rm -rf {} +
```

---

## 输出文件位置

```
output_media/视频名/transcript/
├── audio.srt          ← 时间戳字幕版（推荐和关键帧对比）
└── transcript.md      ← Markdown 逐字稿版（易读）
```

---

## 性能数据

- ✅ **已转录视频** → 2 秒跳过
- 🚀 **新增视频** → 1-3 分钟转录（根据长度）
- 📊 **4 段视频总耗时** → 8-10 分钟首次转录

---

## 常见错误快速修复

| 错误 | 解决方案 |
|-----|---------|
| `GROQ_API_KEY` 错误 | `echo 'GROQ_API_KEY=gsk_your_key' > .env` |
| 413 错误 | `TRANSCRIBE_CHUNK_SECONDS=120 ...` |
| 超时 | `TRANSCRIBE_CHUNK_SECONDS=120 TRANSCRIBE_AB=24k ...` |
| 文件缺失 | `TRANSCRIBE_FORCE=true .venv/bin/python transcribe_audio.py` |

---

## 查看帮助

```bash
# 打印完整使用说明
.venv/bin/python transcribe_audio.py  # 缺少 API Key 时自动打印
```

