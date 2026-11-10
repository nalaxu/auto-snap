# 一键启动外部面板：按需建虚拟环境 / 装依赖 / 构建前端，然后运行 python main.py ui
# 用法：.\start.ps1 [-Rebuild] [-NoBrowser] [-Port 8765]
param(
    [switch]$Rebuild,     # 强制重新构建前端
    [switch]$NoBrowser,   # 不自动打开浏览器
    [int]$Port = 8765
)
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$env:PYTHONUTF8 = "1"   # requirements.txt 等含中文，避免 pip / Python 按 GBK 解码
[Console]::OutputEncoding = [Text.Encoding]::UTF8

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

# 1. Python 虚拟环境
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Step "创建虚拟环境 .venv"
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "创建虚拟环境失败（需要 Python 3 在 PATH 中）" }
}

# 2. Python 依赖：requirements.txt 变化时才重新安装
$req = Join-Path $PSScriptRoot "requirements.txt"
$stamp = Join-Path $PSScriptRoot ".venv\.requirements.sha256"
$reqHash = (Get-FileHash $req -Algorithm SHA256).Hash
$oldHash = if (Test-Path $stamp) { (Get-Content $stamp -Raw).Trim() } else { "" }
if ($reqHash -ne $oldHash) {
    Step "安装 Python 依赖"
    & $py -m pip install -r $req
    if ($LASTEXITCODE -ne 0) { throw "pip install 失败" }
    Set-Content -Path $stamp -Value $reqHash -Encoding ascii
}

# 3. 前端：dist 不存在、源码比 dist 新、或指定 -Rebuild 时构建
$web = Join-Path $PSScriptRoot "ui\web"
$distIndex = Join-Path $web "dist\index.html"
$needBuild = $Rebuild -or -not (Test-Path $distIndex)
if (-not $needBuild) {
    $builtAt = (Get-Item $distIndex).LastWriteTime
    $sources = @(Get-ChildItem (Join-Path $web "src") -Recurse -File) +
               @(Get-Item (Join-Path $web "index.html"), (Join-Path $web "package.json"), (Join-Path $web "vite.config.ts"))
    $needBuild = [bool]($sources | Where-Object { $_.LastWriteTime -gt $builtAt } | Select-Object -First 1)
}
if ($needBuild) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw "需要构建前端，但找不到 npm（请安装 Node 18+）" }
    Push-Location $web
    try {
        if (-not (Test-Path "node_modules")) {
            Step "安装前端依赖"
            npm install
            if ($LASTEXITCODE -ne 0) { throw "npm install 失败" }
        }
        Step "构建前端"
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build 失败" }
    } finally { Pop-Location }
}

# 4. 启动面板
$uiArgs = @("main.py", "ui", "--port", $Port)
if ($NoBrowser) { $uiArgs += "--no-browser" }
Step "启动面板 http://127.0.0.1:$Port （Ctrl+C 停止）"
& $py @uiArgs
