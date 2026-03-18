#!/bin/bash

# 转录脚本测试和演示

set -e

echo "╔════════════════════════════════════════════════════════════════════╗"
echo "║             Groq API 增量转录 - 功能演示                          ║"
echo "╚════════════════════════════════════════════════════════════════════╝"
echo ""

echo "✅ 测试 1：增量转录（默认）"
echo "   说明：跳过已有转录的视频，只处理新增的"
echo "---"
.venv/bin/python transcribe_audio.py
echo ""

read -p "按 Enter 键继续测试 2..."
echo ""

echo "✅ 测试 2：强制重新转录某个视频"
echo "   说明：删除第一个视频的转录文件，然后重新运行"
echo "---"

VIDEO_DIR="data/output/00_12_第十二课：离线签名与应用_01_课前讨论：ERC721 合约与数字签名问题"
if [ -d "$VIDEO_DIR" ]; then
    echo "删除：$VIDEO_DIR/transcript/audio.srt"
    rm -f "$VIDEO_DIR/transcript/audio.srt"
    echo "删除：$VIDEO_DIR/transcript/transcript.md"
    rm -f "$VIDEO_DIR/transcript/transcript.md"
    echo ""
    echo "运行增量转录（会发现缺失的文件，自动转录）..."
    .venv/bin/python transcribe_audio.py
else
    echo "❌ 视频目录不存在：$VIDEO_DIR"
fi

echo ""
echo "✅ 演示完成！"
echo ""
echo "💡 其他用法："
echo "   # 调整分片参数"
echo "   TRANSCRIBE_CHUNK_SECONDS=120 .venv/bin/python transcribe_audio.py"
echo ""
echo "   # 强制重新转录所有"
echo "   TRANSCRIBE_FORCE=true .venv/bin/python transcribe_audio.py"
echo ""
