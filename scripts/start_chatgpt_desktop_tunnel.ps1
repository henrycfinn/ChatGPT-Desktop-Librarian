[CmdletBinding()]
param(
    [string]$KnowledgeRoot = $env:WIKI_PATH,
    [string]$TunnelId = 'tunnel_6ade5ff08df48191bb92ea39413955b0',
    [int]$Port = 8766
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $repositoryRoot '.venv-chatgpt-mcp\Scripts\python.exe'
$client = Join-Path $repositoryRoot 'tools\tunnel-client\tunnel-client.exe'
$connectorStdout = Join-Path $env:TEMP 'chatgpt-desktop-librarian-connector.out.log'
$connectorStderr = Join-Path $env:TEMP 'chatgpt-desktop-librarian-connector.err.log'

if (-not $env:CONTROL_PLANE_API_KEY) {
    $env:CONTROL_PLANE_API_KEY = [Environment]::GetEnvironmentVariable('CONTROL_PLANE_API_KEY', 'User')
}
if (-not $env:CONTROL_PLANE_API_KEY) {
    throw 'CONTROL_PLANE_API_KEY is not set in the current user environment.'
}
if (-not $KnowledgeRoot) {
    throw 'KnowledgeRoot is required. Set WIKI_PATH or pass -KnowledgeRoot.'
}
if (-not (Test-Path -LiteralPath $KnowledgeRoot -PathType Container)) {
    throw "Knowledge root does not exist: $KnowledgeRoot"
}
if (-not (Test-Path -LiteralPath $client -PathType Leaf)) {
    throw "Official tunnel-client was not found at: $client"
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    & py -3 -m venv (Join-Path $repositoryRoot '.venv-chatgpt-mcp')
    & $python -m pip install -r (Join-Path $repositoryRoot 'requirements-chatgpt-mcp.txt')
}

Remove-Item -LiteralPath $connectorStdout, $connectorStderr -Force -ErrorAction SilentlyContinue
$connector = Start-Process -FilePath $python -WorkingDirectory $repositoryRoot -ArgumentList @(
    (Join-Path $repositoryRoot 'scripts\chatgpt_desktop_mcp.py'),
    '--knowledge-root', $KnowledgeRoot,
    '--tunnel-only',
    '--port', $Port
) -RedirectStandardOutput $connectorStdout -RedirectStandardError $connectorStderr -PassThru

try {
    $healthy = $false
    foreach ($attempt in 1..20) {
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/healthz" -TimeoutSec 2
            if ($health.status -eq 'ok' -and -not $health.writes_enabled) {
                $healthy = $true
                break
            }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $healthy) {
        $logs = (Get-Content -Raw $connectorStdout, $connectorStderr -ErrorAction SilentlyContinue).Trim()
        throw "The local read-only MCP connector did not become healthy. Diagnostics:`n$logs"
    }

    & $client run --control-plane.tunnel-id $TunnelId --mcp.server-url "http://127.0.0.1:$Port/mcp"
}
finally {
    if ($connector -and -not $connector.HasExited) {
        Stop-Process -Id $connector.Id -Force
    }
}
