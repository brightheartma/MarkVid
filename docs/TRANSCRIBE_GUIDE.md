# Groq API 增量转录脚本使用指南

## 概述

`transcribe_audio.py` 是一个支持**增量转录**的脚本，用 Groq API 快速转录视频音频。特点：

- ✅ **增量转录**：自动跳过已转录的视频，只处理新增的
- ✅ **自动分片**：大于 18MB 的音频自动分片上传（避免 413 错误）
- ✅ **智能重试**：遇到超时自动重试并降级到分片模式
- ✅ **详细报告**：运行后显示转录统计和质量指标

---

## 快速开始

### 1. 环境准备

确保已安装依赖和配置 API 密钥：

```bash
# 如果还没装，先装依赖
.venv/bin/pip install groq python-dotenv

# 设置 Groq API 密钥（编辑 .env 文件或环境变量）
echo 'GROQ_API_KEY=gsk_your_api_key_here' >> .env
```

### 2. 基本用法

**增量转录**（默认，推荐）：
```bash
.venv/bin/python transcribe_audio.py
```

这会：
- 扫描 `output_media/` 下的所有文件夹
- 检查每个文件夹是否已有 `transcript/audio.srt` 和 `transcript/transcript.md`
- 如果都存在，则跳过（显示 ✅ 已转录）
- 如果不存在或不完整，则执行转录

**强制重新转录所有文件**：
```bash
TRANSCRIBE_FORCE=true .venv/bin/python transcribe_audio.py
```

---

## 高级用法

### 调整分片参数

**减小分片时长**（更稳定，但转录时间更长）：
```bash
TRANSCRIBE_CHUNK_SECONDS=120 .venv/bin/python transcribe_audio.py
```

**调整何时启用分片**（例如 15MB 以上自动分片）：
```bash
TRANSCRIBE_CHUNK_WHEN_OVER_BYTES=$((15*1024*1024)) .venv/bin/python transcribe_audio.py
```

**调整音频压缩参数**（更低码率 = 更小文件 = 更快）：
```bash
TRANSCRIBE_AB=24k .venv/bin/python transcribe_audio.py
```

可用的环境变量：
- `TRANSCRIBE_CHUNK_SECONDS` - 分片时长（秒，默认 300）
- `TRANSCRIBE_CHUNK_WHEN_OVER_BYTES` - 分片阈值（字节，默认 18MB）
- `TRANSCRIBE_AR` - 采样率（Hz，默认 16000）
- `TRANSCRIBE_AC` - 通道数（默认 1，单声道）
- `TRANSCRIBE_AB` - 码率（默认 32k）
- `TRANSCRIBE_FORCE` - 强制重新转录（true/false）

### 组合参数

```bash
# 示例：激进模式（分片 120s、更低码率）
TRANSCRIBE_CHUNK_SECONDS=120 TRANSCRIBE_AB=24k .venv/bin/python transcribe_audio.py

# 示例：保守模式（分片 500s、较高码率）
TRANSCRIBE_CHUNK_SECONDS=500 TRANSCRIBE_AB=40k .venv/bin/python transcribe_audio.py
```

---

## 输出结构

转录完成后，每个视频文件夹会生成：

```
output_media/
└── 00_12_第十二课：离线签名与应用_01_课前讨论.../
    ├── audio.mp3                    # 原始音频
    ├── frame_*.jpg                  # 关键帧（1000+ 张）
    └── transcript/
        ├── audio.srt                # ✨ 转录文本（SRT 格式，带时间戳）
        └── transcript.md            # ✨ 转录文本（Markdown 格式）
```

### 文件说明

- **audio.srt**：SubRip 字幕格式，包含：
  - 序号、时间戳 (HH:MM:SS,mmm)、文本内容
  - 可在视频播放器中作为字幕使用
  - 易于与关键帧时间对齐

- **transcript.md**：Markdown 格式的逐字稿，格式：
  ```markdown
  # 视频逐字稿（视频名称）
  
  **[HH:MM:SS - HH:MM:SS]** 转录文本...
  ```

---

## 状态报告示例

运行脚本后会看到类似的输出：

```
📊 开始扫描转录任务（共 4 个文件夹）...
======================================================================
✅ 已转录: 00_12_第十二课_01_课前讨论
   📄 SRT: 732 行, 10.8KB
✅ 已转录: 00_12_第十二课_02_EIP-191/EIP-712
   📄 SRT: 4592 行, 64.0KB
🚀 正在通过 Groq API 极速转录: 新视频名称 ...
  -> 文件较大（39.4MB），启用分片：300s/片，转码 1ch 16000Hz 32k
    - 分片 1/9: chunk_000.mp3 (offset=0s)
    - 分片 2/9: chunk_001.mp3 (offset=300s)
    ...
  -> ✅ 成功: SRT 已保存至 .../transcript/audio.srt
======================================================================

📈 转录总结:
  - 总文件夹数: 4
  - ✅ 已转录: 1
  - ⏭️  已跳过（已有转录）: 3
  - ❌ 转录失败: 0
  - 运行模式: 增量转录（仅处理新文件）
```

### 报告字段解释

- **总文件夹数**：扫描的文件夹总数
- **已转录**：新转录的文件夹数
- **已跳过**：因已有转录而跳过的数量
- **转录失败**：因错误失败的数量
- **运行模式**：当前使用的转录模式

---

## 常见场景

### 场景 1：新增视频后自动转录

1. 把新视频放入 `videos/` 文件夹
2. 运行 `extract_media.py` 提取音频和关键帧
3. 运行 `transcribe_audio.py`
4. 脚本自动识别新文件夹，只转录新增的视频

```bash
# 一键完成
.venv/bin/python extract_media.py && .venv/bin/python transcribe_audio.py
```

### 场景 2：某个转录出错，需要重新转录

1. 删除对应的 `transcript/audio.srt` 和 `transcript/transcript.md`
2. 运行脚本，它会自动重新转录

```bash
# 或直接强制重新转录某个视频
rm -f output_media/视频名/transcript/*.srt output_media/视频名/transcript/*.md
.venv/bin/python transcribe_audio.py
```

### 场景 3：所有转录失败，需要调整参数重试

```bash
# 尝试更激进的分片参数
TRANSCRIBE_CHUNK_SECONDS=120 TRANSCRIBE_FORCE=true .venv/bin/python transcribe_audio.py

# 如果还是失败，尝试更小的音频码率
TRANSCRIBE_AB=20k TRANSCRIBE_FORCE=true .venv/bin/python transcribe_audio.py
```

---

## 故障排查

### 问题 1：API Key 错误

**症状**：
```
❌ 错误：未检测到 GROQ_API_KEY。
```

**解决**：
```bash
# 方法 1：编辑 .env 文件
echo 'GROQ_API_KEY=gsk_your_key_here' > .env

# 方法 2：临时环境变量
export GROQ_API_KEY=gsk_your_key_here
.venv/bin/python transcribe_audio.py
```

### 问题 2：转录一直卡住

**症状**：脚本运行很长时间，但没有输出

**原因**：
- 网络不稳定
- 单个分片太大（超时）

**解决**：
```bash
# 减小分片时长
TRANSCRIBE_CHUNK_SECONDS=120 .venv/bin/python transcribe_audio.py

# 或减小音频码率
TRANSCRIBE_AB=24k .venv/bin/python transcribe_audio.py
```

### 问题 3：413 Request Entity Too Large

**症状**：
```
❌ 失败: API 请求出错 (Error code: 413 - ...)
```

**原因**：音频文件过大，即使分片仍超过单个分片限制

**解决**：
```bash
# 自动启用分片（脚本会自动处理）
# 或手动调整分片时长为更小值
TRANSCRIBE_CHUNK_SECONDS=120 .venv/bin/python transcribe_audio.py

# 或减小音频码率
TRANSCRIBE_AB=20k TRANSCRIBE_CHUNK_SECONDS=120 .venv/bin/python transcribe_audio.py
```

### 问题 4：转录质量差

**症状**：SRT 文件行数很少，或内容很乱

**可能原因**：
- 音频质量差
- 转录参数不合适
- API 返回不完整

**建议**：
- 检查 audio.srt 是否生成正常
- 尝试用原始参数重新转录
- 查看 Groq API 的可用性/限制

---

## 工作流建议

### 完整的视频处理流程

```bash
#!/bin/bash

# 1. 清理临时文件（可选）
rm -rf output_media/*/transcript/_chunks_work

# 2. 提取媒体（音频 + 关键帧）
.venv/bin/python extract_media.py

# 3. 转录（增量：只处理新视频）
.venv/bin/python transcribe_audio.py

# 4. 生成笔记（用 AI 分析转录和关键帧）
# （这一步需要手动或集成到其他工具）
```

---

## 性能参考

基于实测数据（使用 Groq API）：

| 音频长度 | 文件大小 | 转录方式 | 耗时 |
|---------|---------|---------|-----|
| 5 分钟 | 5.5MB | 直接 | ~15s |
| 40 分钟 | 39MB | 分片 (300s) | ~3m |
| 20 分钟 | 20MB | 分片 (300s) | ~2m |
| 27 分钟 | 26MB | 分片 (300s) | ~2.5m |

**总计**（4 段视频，~92 分钟）：约 8-10 分钟

---

## 最佳实践

1. **保持转录文件**：除非确实需要重新转录，否则保留已有的 SRT/MD 文件
2. **定期清理临时文件**：
   ```bash
   find output_media -name "_chunks_work" -type d -exec rm -rf {} +
   ```
3. **备份重要转录**：如果转录质量优秀，备份一份
4. **使用版本控制**：在 git 中跟踪笔记文件（但不跟踪音频和图片）
5. **监控 API 成本**：注意 Groq API 的使用量和配额

---

## 相关文件

- `transcribe_audio.py` - 主转录脚本
- `.env` - API 密钥配置
- `extract_media.py` - 媒体提取脚本
- `notebooklm_prompt.md` - 笔记生成提示词

