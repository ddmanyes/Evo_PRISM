# Evo_PRISM 啟動腳本 — Windows PowerShell
# Usage:
#   .\start_bioagent.ps1              # 互動式選擇推理後端
#   .\start_bioagent.ps1 --claude     # Claude API + embedding
#   .\start_bioagent.ps1 --google     # Google Gemini API + embedding
#   .\start_bioagent.ps1 --local      # 本機 Gemma 4 Vision + embedding
#
# 首次執行需解鎖執行權限：
#   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

param(
    [string]$Mode = ""
)

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = "$env:USERPROFILE\.venvs\hermes-bio-memory\Scripts\python.exe"
$LlamaBin   = "$env:USERPROFILE\llama.cpp\llama-server.exe"
$EmbedModel = "$env:USERPROFILE\llama.cpp\models\bge-m3-Q8_0.gguf"
$VisionModel= "$env:USERPROFILE\gemma-4-26B-A4B-it-UD-IQ2_M.gguf"
$MmProj     = "$env:USERPROFILE\mmproj-F16.gguf"
$LogDir     = "$ScriptDir\logs"

# 覆寫路徑：若 .env 有設定則優先使用
if (Test-Path "$ScriptDir\.env") {
    Get-Content "$ScriptDir\.env" | ForEach-Object {
        if ($_ -match "^\s*([^#=\s]+)\s*=\s*(.*)$") {
            [System.Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Trim('"').Trim("'"))
        }
    }
}
if ($env:LLAMACPP_BIN)   { $LlamaBin    = $env:LLAMACPP_BIN }
if ($env:LLAMACPP_MODEL_PATH) { $EmbedModel = $env:LLAMACPP_MODEL_PATH }
if ($env:VISION_MODEL_PATH)   { $VisionModel= $env:VISION_MODEL_PATH }
if ($env:MMPROJ_PATH)    { $MmProj      = $env:MMPROJ_PATH }
if ($env:VENV_PYTHON)    { $VenvPython  = $env:VENV_PYTHON }

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$EmbedPid  = $null
$VisionPid = $null
$WebPid    = $null

function Info  { param($Msg) Write-Host "[bioagent] $Msg" -ForegroundColor Green }
function Warn  { param($Msg) Write-Host "[bioagent] $Msg" -ForegroundColor Yellow }
function Err   { param($Msg) Write-Host "[bioagent] $Msg" -ForegroundColor Red }

function Test-Port {
    param([int]$Port)
    try {
        $r = Invoke-RestMethod "http://localhost:$Port/health" -TimeoutSec 2 -ErrorAction Stop
        return $true
    } catch { return $false }
}

function Wait-Port {
    param([int]$Port, [int]$Seconds = 60, [string]$Label = "service")
    for ($i = 1; $i -le ($Seconds / 2); $i++) {
        if (Test-Port $Port) { Info "$Label ready after $($i*2)s"; return $true }
        Start-Sleep 2
    }
    return $false
}

function Stop-Services {
    if ($VisionPid) { Stop-Process -Id $VisionPid -Force -ErrorAction SilentlyContinue; Info "Gemma 4 Vision stopped" }
    if ($EmbedPid)  { Stop-Process -Id $EmbedPid  -Force -ErrorAction SilentlyContinue; Info "Embedding server stopped" }
    if ($WebPid)    { Stop-Process -Id $WebPid    -Force -ErrorAction SilentlyContinue; Info "Web server stopped" }
}

# ── 互動選擇模式 ──────────────────────────────────────────────
if ($Mode -eq "") {
    Write-Host ""
    Write-Host "選擇推理後端："
    Write-Host "  1) Claude API（雲端，需 ANTHROPIC_API_KEY）"
    Write-Host "  2) Google Gemini API（雲端，需 GOOGLE_API_KEY）"
    Write-Host "  3) 本機 Gemma 4 Vision（離線，需 ~16GB RAM）"
    Write-Host ""
    $Choice = Read-Host "請輸入 1、2 或 3 [預設 1]"
    switch ($Choice) {
        "2" { $Mode = "--google" }
        "3" { $Mode = "--local" }
        default { $Mode = "--claude" }
    }
}

$UseLocal = $Mode -eq "--local"

# ── 1. Gemma 4 Vision (port 8080) — 僅 local 模式 ────────────
if ($UseLocal) {
    if (Test-Port 8080) {
        Warn "Gemma 4 already running on port 8080 (not managed)"
    } else {
        if (-not (Test-Path $LlamaBin))   { Err "找不到 llama-server.exe：$LlamaBin"; exit 1 }
        if (-not (Test-Path $VisionModel)) { Err "找不到 Vision 模型：$VisionModel"; exit 1 }
        if (-not (Test-Path $MmProj))     { Err "找不到 mmproj：$MmProj"; exit 1 }
        Info "Starting Gemma 4 Vision server (port 8080)..."
        $CpuCount = $env:NUMBER_OF_PROCESSORS
        $proc = Start-Process -FilePath $LlamaBin `
            -ArgumentList "-m `"$VisionModel`" --mmproj `"$MmProj`" --port 8080 --ctx-size 16384 --n-gpu-layers 99 --flash-attn on -ctk q8_0 -ctv q8_0 --threads $CpuCount" `
            -RedirectStandardOutput "$LogDir\llama_server.log" `
            -RedirectStandardError  "$LogDir\llama_server_err.log" `
            -NoNewWindow -PassThru
        $VisionPid = $proc.Id
        Info "Gemma 4 PID=$VisionPid  log -> $LogDir\llama_server.log"
        if (-not (Wait-Port 8080 120 "Gemma 4")) {
            Err "Gemma 4 not ready after 120s, aborting. Check $LogDir\llama_server.log"
            Stop-Services; exit 1
        }
    }
}

# ── 2. Embedding server (port 8081) ──────────────────────────
if (Test-Port 8081) {
    Warn "Embedding server already running on port 8081 (not managed)"
} else {
    if (-not (Test-Path $LlamaBin))  { Err "找不到 llama-server.exe：$LlamaBin"; exit 1 }
    if (-not (Test-Path $EmbedModel)) { Err "找不到 Embedding 模型：$EmbedModel"; exit 1 }
    Info "Starting embedding server bge-m3 (port 8081)..."
    $proc = Start-Process -FilePath $LlamaBin `
        -ArgumentList "-m `"$EmbedModel`" --embedding --port 8081 --ctx-size 8192 --n-gpu-layers 99" `
        -RedirectStandardOutput "$LogDir\embed_server.log" `
        -RedirectStandardError  "$LogDir\embed_server_err.log" `
        -NoNewWindow -PassThru
    $EmbedPid = $proc.Id
    Info "Embedding server PID=$EmbedPid  log -> $LogDir\embed_server.log"
    if (-not (Wait-Port 8081 60 "Embedding server")) {
        Err "Embedding server not ready after 60s. Check $LogDir\embed_server.log"
        Stop-Services; exit 1
    }
}

# ── 3. FastAPI Web UI (port 8000) ────────────────────────────
if (Test-Port 8000) {
    Warn "Web server already running on port 8000 (not managed)"
} else {
    if (-not (Test-Path $VenvPython)) { Err "找不到 venv Python：$VenvPython`n請先執行 Windows 安裝步驟（docs\guides\WINDOWS_SETUP.md）"; exit 1 }
    Info "Starting BioAgent Web UI (port 8000)..."
    $proc = Start-Process -FilePath $VenvPython `
        -ArgumentList "server\web_app.py" `
        -WorkingDirectory $ScriptDir `
        -RedirectStandardOutput "$LogDir\web_app.log" `
        -RedirectStandardError  "$LogDir\web_app_err.log" `
        -NoNewWindow -PassThru
    $WebPid = $proc.Id
    Info "Web server PID=$WebPid  log -> $LogDir\web_app.log"
    if (-not (Wait-Port 8000 30 "Web server")) {
        Err "Web server failed to start. Check $LogDir\web_app.log"
        Stop-Services; exit 1
    }
}

$BackendLabel = switch ($Mode) {
    "--local"  { "本機 Gemma 4 Vision" }
    "--google" { "Google Gemini API" }
    default    { "Claude API" }
}

Write-Host ""
Info "========================================"
Info "  Evo_PRISM ready  [$BackendLabel]"
Info "  http://localhost:8000"
Info "  Press Ctrl+C to stop managed services"
Info "========================================"
Write-Host ""

# 等待 Ctrl+C
try {
    while ($true) {
        Start-Sleep 5
        # 若任何受管程序意外退出則警示
        foreach ($pair in @(($VisionPid,"Gemma 4"),($EmbedPid,"Embedding"),($WebPid,"Web"))) {
            $pid = $pair[0]; $name = $pair[1]
            if ($pid -and -not (Get-Process -Id $pid -ErrorAction SilentlyContinue)) {
                Warn "$name (PID=$pid) exited unexpectedly"
            }
        }
    }
} finally {
    Stop-Services
}
