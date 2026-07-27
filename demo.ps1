# Recording helper: only checks whether the API Key variable exists.
if (-not (Test-Path Env:DEEPSEEK_API_KEY)) {
    Write-Host "未检测到 DEEPSEEK_API_KEY。请先在本 PowerShell 会话设置它，再开始录屏。" ;
    exit 1
}

Write-Host "准备就绪：已检测到 DEEPSEEK_API_KEY（其值不会显示）。" ;
Write-Host "" ;
Write-Host "1. 终端 1 使用 session weather-chat 查询天气并添加待办" ;
Write-Host "   python -m mini_agent --session weather-chat" ;
Write-Host "2. 终端 2 使用 session weekly-report 生成周报并添加待办" ;
Write-Host "   python -m mini_agent --session weekly-report" ;
Write-Host "3. 分别继续两个 session" ;
Write-Host "   在两个终端分别再次运行相同的 --session 命令，并继续提问。" ;
Write-Host "4. 在交互模式输入 /trace" ;
Write-Host "5. 运行完整 unittest" ;
Write-Host "   python -m unittest discover -v" ;
