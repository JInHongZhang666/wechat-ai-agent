param(
    [switch]$Cpu,
    [switch]$Stop
)

$ErrorActionPreference = "Stop"
$compose = Join-Path $PSScriptRoot "services\video-transcriber\compose.yaml"
$cudaCompose = Join-Path $PSScriptRoot "services\video-transcriber\compose.cuda.yaml"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "未检测到 Docker Desktop，请先安装并启动 Docker。"
}

if ($Stop) {
    & docker compose -f $compose down
    exit $LASTEXITCODE
}

$hasNvidia = $null -ne (Get-Command nvidia-smi -ErrorAction SilentlyContinue)
$useCuda = $hasNvidia -and -not $Cpu
if ($useCuda) {
    try {
        $driverText = (& nvidia-smi --query-gpu=driver_version --format=csv,noheader,nounits | Select-Object -First 1).Trim()
        if ([version]$driverText -lt [version]"560.76") {
            Write-Warning "NVIDIA 驱动 $driverText 低于当前 CUDA 镜像要求，使用 CPU 模式。"
            $useCuda = $false
        }
    } catch {
        Write-Warning "无法确认 NVIDIA 驱动版本，先尝试 GPU，失败后自动回退 CPU。"
    }
}

if (-not $useCuda) {
    Write-Host "正在启动 CPU 视频转写服务..." -ForegroundColor Cyan
    & docker compose -f $compose up -d
} else {
    Write-Host "正在启动 NVIDIA GPU 视频转写服务..." -ForegroundColor Cyan
    & docker compose -f $compose -f $cudaCompose up -d
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "当前 NVIDIA 驱动与转写镜像不兼容，自动切换到 CPU 模式。"
        & docker compose -f $compose down
        & docker compose -f $compose up -d
    }
}

if ($LASTEXITCODE -ne 0) {
    throw "视频转写服务启动失败。"
}

$serviceUrl = "http://127.0.0.1:8011"
$serviceReady = $false
foreach ($attempt in 1..30) {
    try {
        Invoke-RestMethod "$serviceUrl/health" -TimeoutSec 3 | Out-Null
        $serviceReady = $true
        break
    } catch {
        Start-Sleep -Seconds 2
    }
}
if (-not $serviceReady) {
    throw "视频转写服务已启动，但健康检查未在 60 秒内通过。"
}

$model = $env:WECHAT_AGENT_VIDEO_MODEL
if ([string]::IsNullOrWhiteSpace($model)) {
    $model = "Systran/faster-whisper-small"
}
$installedModels = Invoke-RestMethod "$serviceUrl/v1/models" -TimeoutSec 15
$installedIds = @($installedModels.data | ForEach-Object { $_.id })
if ($installedIds -notcontains $model) {
    Write-Host "首次使用：正在下载语音识别模型 $model ..." -ForegroundColor Cyan
    Invoke-RestMethod -Method Post "$serviceUrl/v1/models/$model" -TimeoutSec 1800 | Out-Null
}
Write-Host "视频转写服务：http://127.0.0.1:8011" -ForegroundColor Green
