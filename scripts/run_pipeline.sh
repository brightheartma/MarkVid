#!/usr/bin/env bash
# =============================================================================
# run_pipeline.sh — VideoToText 完整流水线
# =============================================================================
# 用法：
#   bash scripts/run_pipeline.sh               # 全流程（默认增量）
#   bash scripts/run_pipeline.sh --force-note  # 强制重新生成所有笔记
#   bash scripts/run_pipeline.sh --skip-extract # 跳过媒体提取（仅转录+笔记）
#   bash scripts/run_pipeline.sh --skip-transcribe # 跳过转录（仅提取+笔记）
#   bash scripts/run_pipeline.sh --note-only   # 仅重建笔记和索引
# =============================================================================

set -euo pipefail

# ─── 路径 ────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PYTHON="${PROJECT_ROOT}/.venv/bin/python"

# ─── 参数解析 ─────────────────────────────────────────────────────────────────
SKIP_EXTRACT=false
SKIP_TRANSCRIBE=false
NOTE_ONLY=false
FORCE_NOTE=false
FORCE_TRANSCRIBE=false

for arg in "$@"; do
    case "$arg" in
        --skip-extract)      SKIP_EXTRACT=true ;;
        --skip-transcribe)   SKIP_TRANSCRIBE=true ;;
        --note-only)         NOTE_ONLY=true; SKIP_EXTRACT=true; SKIP_TRANSCRIBE=true ;;
        --force-note)        FORCE_NOTE=true ;;
        --force-transcribe)  FORCE_TRANSCRIBE=true ;;
        --force-all)         FORCE_NOTE=true; FORCE_TRANSCRIBE=true ;;
        *)                   echo "⚠️  未知参数: $arg（忽略）" ;;
    esac
done

# ─── 辅助函数 ─────────────────────────────────────────────────────────────────
step() { echo ""; echo "════════════════════════════════════════════════════"; echo "▶  $1"; echo "════════════════════════════════════════════════════"; }
ok()   { echo "  ✅  $1"; }
fail() { echo "  ❌  $1"; exit 1; }

# ─── 前置检查 ─────────────────────────────────────────────────────────────────
step "环境检查"

[[ -f "$PYTHON" ]] || fail "未找到虚拟环境：$PYTHON\n请先运行：python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"

[[ -f "${PROJECT_ROOT}/.env" ]] && source "${PROJECT_ROOT}/.env" || true
[[ -n "${GROQ_API_KEY:-}" ]] || fail "未设置 GROQ_API_KEY\n请在 .env 文件中添加：GROQ_API_KEY=gsk_xxx"

ok "Python: $($PYTHON --version)"
ok "GROQ_API_KEY: 已设置（前6位：${GROQ_API_KEY:0:6}...）"

# ─── Step 1: 提取媒体 ─────────────────────────────────────────────────────────
if [[ "$SKIP_EXTRACT" == false ]]; then
    step "Step 1 / 3 — 提取媒体（音频 + 关键帧）"
    "$PYTHON" "${PROJECT_ROOT}/src/extract_media.py"
    ok "媒体提取完成"
else
    echo "⏭️  跳过 Step 1（--skip-extract）"
fi

# ─── Step 2: 增量转录 ─────────────────────────────────────────────────────────
if [[ "$SKIP_TRANSCRIBE" == false ]]; then
    step "Step 2 / 3 — 转录音频（增量，跳过已转录）"
    if [[ "$FORCE_TRANSCRIBE" == true ]]; then
        TRANSCRIBE_FORCE=true "$PYTHON" -u "${PROJECT_ROOT}/src/transcribe_audio.py"
    else
        "$PYTHON" -u "${PROJECT_ROOT}/src/transcribe_audio.py"
    fi
    ok "转录完成"
else
    echo "⏭️  跳过 Step 2（--skip-transcribe）"
fi

# ─── Step 3: 生成笔记 + 更新 INDEX ────────────────────────────────────────────
step "Step 3 / 3 — 生成课程笔记 + 更新 INDEX.md"
NOTE_ARGS=("--all")
[[ "$FORCE_NOTE" == true ]] && NOTE_ARGS+=("--force")
"$PYTHON" "${PROJECT_ROOT}/src/generate_note.py" "${NOTE_ARGS[@]}"
ok "笔记生成完成，INDEX.md 已自动更新"

# ─── 清理临时文件 ─────────────────────────────────────────────────────────────
step "清理临时分片文件"
find "${PROJECT_ROOT}/data/output" -type d -name "_chunks_work" -exec rm -rf {} + 2>/dev/null || true
ok "临时文件已清理"

# ─── 完成 ─────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║  🎉 流水线执行完成！                                              ║"
echo "║                                                                  ║"
echo "║  📚 笔记位置：notes/                                              ║"
echo "║  📖 索引文件：notes/INDEX.md                                      ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
