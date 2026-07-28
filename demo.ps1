# Recording helper: only checks whether configuration exists; it never reads or displays the key.
$hasConfiguredKey = Test-Path Env:DEEPSEEK_API_KEY
if (-not $hasConfiguredKey) {
    $dotenvPath = Join-Path $PSScriptRoot ".env"
    if (Test-Path -LiteralPath $dotenvPath) {
        $hasConfiguredKey = Select-String -LiteralPath $dotenvPath -Pattern '^\s*DEEPSEEK_API_KEY\s*=\s*\S' -Quiet
    }
}

if (-not $hasConfiguredKey) {
    Write-Host "未检测到 DEEPSEEK_API_KEY。请在脚本旁的 .env 填写非空 Key，或先在本 PowerShell 会话设置它，再开始录屏。" ;
    exit 1
}

Write-Host "准备就绪：已检测到 DEEPSEEK_API_KEY（其值不会显示）。" ;
Write-Host "" ;
Write-Host "1. 启动本地网页" ;
Write-Host "   python -m mini_agent.web" ;
Write-Host "2. 在 weather-chat 演示天气和待办" ;
Write-Host "3. 在 weekly-report 演示周报和待办" ;
Write-Host "4. 分别切回两个 session 继续追问" ;
Write-Host "5. 展示右侧两个 session 的 Agent Trace 与待办互不串线" ;
Write-Host "6. 运行完整 unittest" ;
Write-Host "   python -m unittest discover -s tests -v" ;
