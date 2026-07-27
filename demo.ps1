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
Write-Host "1. 终端 1 使用 session weather-chat，查询天气并添加待办" ;
Write-Host "   python -m mini_agent --session weather-chat" ;
Write-Host "   输入：查询杭州明天天气；如有雨，添加待办带雨伞" ;
Write-Host "2. 终端 2 使用 session weekly-report，生成周报并添加待办" ;
Write-Host "   python -m mini_agent --session weekly-report" ;
Write-Host "   输入：生成一份本周工作周报，并添加待办检查周报" ;
Write-Host "3. 重新打开 weather-chat 并继续提问" ;
Write-Host "   python -m mini_agent --session weather-chat" ;
Write-Host "   重新打开 weekly-report 并继续提问" ;
Write-Host "   python -m mini_agent --session weekly-report" ;
Write-Host "4. 在交互模式输入 /trace" ;
Write-Host "5. 运行完整 unittest" ;
Write-Host "   python -m unittest discover -s tests -v" ;
