#!/usr/bin/env bash
# =============================================================================
# add_video.sh — 添加新视频并触发处理流水线
# =============================================================================
# 用法：
#   # 复制单个视频并处理
#   bash scripts/add_video.sh /path/to/video.mp4
#
#   # 复制视频但不自动处理（只放入待处理队列）
#   bash scripts/add_video.sh --no-run /path/to/video.mp4
#
#   # 批量放入一个文件夹下的所有视频
#   bash scripts/add_video.sh /path/to/folder/*.mp4
#
# 命名规范：
#   视频文件名决定最终笔记名，建议格式：
#   {课程编号}_{课程名}_{段编号}_{段标题}.mp4
#   例：00_14_第十四课：合约升级_01_代理模式基础.mp4
#
# 待处理视频放置目录：data/input/
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
INBOX="${PROJECT_ROOT}/data/input"

# ─── 参数解析 ─────────────────────────────────────────────────────────────────
AUTO_RUN=true
FILES=()
for arg in "$@"; do
    case "$arg" in
        --no-run) AUTO_RUN=false ;;
        --help|-h)
            sed -n '2,25p' "$0" | sed 's/^# \{0,2\}//'
            exit 0
            ;;
        *)
            if [[ -f "$arg" ]]; then
                FILES+=("$arg")
            else
                echo "⚠️  文件不存在，跳过: $arg"
            fi
            ;;
    esac
done

if [[ ${#FILES[@]} -eq 0 ]]; then
    echo "用法: bash scripts/add_video.sh [--no-run] /path/to/video.mp4 ..."
    echo "      bash scripts/add_video.sh --help  查看完整说明"
    echo ""
    echo "待处理队列（当前 data/input/ 中的视频）："
    ls "$INBOX"/*.mp4 2>/dev/null | while read f; do echo "  - $(basename "$f")"; done || echo "  （空）"
    exit 1
fi

# ─── 复制视频 ─────────────────────────────────────────────────────────────────
echo "📥 正在添加视频到 data/input/ ..."
ADDED=0
for src in "${FILES[@]}"; do
    fname=$(basename "$src")
    dest="${INBOX}/${fname}"
    if [[ -f "$dest" ]]; then
        echo "  ⏭️  已存在，跳过: $fname"
    else
        cp "$src" "$dest"
        echo "  ✅ 已添加: $fname"
        ADDED=$((ADDED + 1))
    fi
done

echo ""
echo "📋 data/input/ 中当前共 $(ls "$INBOX"/*.mp4 2>/dev/null | wc -l | tr -d ' ') 个视频"

# ─── 自动处理 ─────────────────────────────────────────────────────────────────
if [[ "$AUTO_RUN" == true && "$ADDED" -gt 0 ]]; then
    echo ""
    echo "🚀 检测到新视频，自动启动处理流水线 ..."
    bash "${SCRIPT_DIR}/run_pipeline.sh"
else
    echo ""
    echo "💡 手动处理命令："
    echo "   bash scripts/run_pipeline.sh"
fi
