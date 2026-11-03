#!/usr/bin/env bash
set -euo pipefail

errors=0
script_dir="${BASH_SOURCE[0]%/*}"
repo_root="$(cd "$script_dir/.." && pwd)"

check_file() {
    local file="$1"
    local description="$2"
    if [[ ! -f "$repo_root/$file" ]]; then
        echo "MISSING: $file — $description"
        ((errors++))
    fi
}

echo "=== 项目指引检查 ==="

# 核心规则文件
check_file "AGENTS.md"            "唯一 Agent 规则源"
check_file "readme.md"            "项目入口"
check_file ".github/copilot-instructions.md" "Copilot 入口"

# 权威文档
check_file "docs/readme.md" "文档索引"
check_file "docs/project/context.md" "稳定事实与能力索引"
check_file "docs/project/task-division.md" "三人任务分工"
check_file "docs/project/team-workflow.md" "团队协作流程"
check_file "docs/design/system-architecture.md" "系统架构"
check_file "docs/interfaces/readme.md" "共享接口入口"
check_file "docs/handoffs/template.md" "交接模板"

# PR 与 CI
check_file ".github/pull_request_template.md" "PR 模板"

# ADR 文件命名检查
if [[ -d "$repo_root/docs/decisions" ]]; then
    for adr in "$repo_root/docs/decisions"/[0-9]*.md; do
        [[ -f "$adr" ]] || continue
        filename="${adr##*/}"
        if [[ ! "$filename" =~ ^[0-9]{4}-[a-z0-9-]+\.md$ ]]; then
            echo "WARNING: ADR 命名不规范: $filename (应为 NNNN-xxx.md)"
            ((errors++))
        fi
    done
fi

if [[ $errors -eq 0 ]]; then
    echo "project guidance check passed"
else
    echo "project guidance check FAILED: $errors issue(s)"
    exit 1
fi
