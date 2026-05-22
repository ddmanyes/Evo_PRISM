# Evo_PRISM 停止腳本 — Windows PowerShell
# 停止所有 BioAgent 服務（port 8080 推理、8081 Embedding、8000 Web UI）

function Info { param($Msg) Write-Host "[bioagent] $Msg" -ForegroundColor Green }
function Warn { param($Msg) Write-Host "[bioagent] $Msg" -ForegroundColor Yellow }

function Stop-Port {
    param([int]$Port, [string]$Name)
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($conns) {
        $conns | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {
            Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
        }
        Info "$Name (port $Port) stopped"
    } else {
        Warn "$Name (port $Port) was not running"
    }
}

Stop-Port 8080 "Gemma 4 Vision server"
Stop-Port 8081 "Embedding server (bge-m3)"
Stop-Port 8000 "Web UI (FastAPI)"

Info "All services stopped."
