param(
    [switch]$EnableAgent,
    [switch]$EnableVideo,
    [switch]$VideoCpu
)

$ErrorActionPreference = "Stop"

function Import-LocalEnvironment {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }
        if ($trimmed -match '^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            $name = $Matches[1]
            $value = $Matches[2].Trim().Trim('"').Trim("'")
            if (-not (Test-Path "Env:$name")) {
                Set-Item -Path "Env:$name" -Value $value
            }
        }
    }
}

Import-LocalEnvironment -Path (Join-Path $PSScriptRoot ".env")

# 灵犀探讯已是正式入口，因此默认开启。仍可在启动前显式设置
# WECHAT_AGENT_AGENT_ENABLED=0 来关闭；-EnableAgent 会强制开启。
if ($EnableAgent -or -not (Test-Path Env:WECHAT_AGENT_AGENT_ENABLED)) {
    $env:WECHAT_AGENT_AGENT_ENABLED = "1"
}

if ($EnableVideo) {
    $videoArgs = @{}
    if ($VideoCpu) {
        $videoArgs.Cpu = $true
    }
    & (Join-Path $PSScriptRoot "start-video.ps1") @videoArgs
    $env:WECHAT_AGENT_VIDEO_ENABLED = "1"
}

$backend = Join-Path $PSScriptRoot "backend"
$venvPython = Join-Path $backend ".venv\Scripts\python.exe"
$python = if (Test-Path -LiteralPath $venvPython) {
    $venvPython
} else {
    (Get-Command python -ErrorAction Stop).Source
}

$dataDirectory = Join-Path $backend "data"
New-Item -ItemType Directory -Force -Path $dataDirectory | Out-Null

if (-not $env:WECHAT_AGENT_DATABASE_URL) {
    $env:WECHAT_AGENT_DATABASE_URL = "sqlite:///./data/wechat_agent.db"
}
# Temporary cost-control policy: collection remains fully active while all
# distribution-side LLM passes use the deterministic fallback.  Content
# creation keeps its independent provider configuration.
if (-not $env:WECHAT_AGENT_DISTRIBUTION_LLM_DAILY_CALL_LIMIT) {
    $env:WECHAT_AGENT_DISTRIBUTION_LLM_DAILY_CALL_LIMIT = "0"
}
$env:PYTHONPATH = Join-Path $backend "src"

Write-Host ""
Write-Host "AI信息小屋 · 公众号原创助手" -ForegroundColor Green
Write-Host "操作页面：http://127.0.0.1:8010/" -ForegroundColor Cyan
Write-Host "接口文档：http://127.0.0.1:8010/docs" -ForegroundColor DarkCyan
Write-Host "停止服务：在本窗口按 Ctrl+C" -ForegroundColor Yellow
Write-Host ""

Set-Location -LiteralPath $backend
& $python -m uvicorn wechat_agent.main:app `
    --app-dir (Join-Path $backend "src") `
    --host 127.0.0.1 `
    --port 8010 `
    --workers 1
