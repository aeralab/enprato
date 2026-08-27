# Enprato agent 防崩约定（给 AI / 自己用）
# 根因：嵌套 powershell -Command 时，$变量常被外层吃掉 → 解析失败 → 工具链中断 → Cursor 报 Unexpected error
#
# 强制规则：
# 1. 凡超过 2 行的 PowerShell，写成 .ps1 文件再 -File 执行
# 2. 单次并行工具不超过 4 个；长任务拆成短回合
# 3. 不要在一行命令里嵌套 $(...) 含大量引号
# 4. 改完前端后清 Vite 缓存或硬刷新，避免热更新残留旧符号（audioCtxRef/testBeep）

$ErrorActionPreference = 'Stop'
Write-Output 'agent-safe ok'
