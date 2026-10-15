$ErrorActionPreference = 'Stop'

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}

Write-Host '========================================'
Write-Host '  Candidate Video Helper - One-time setup'
Write-Host '========================================'

$dir = Join-Path $env:LOCALAPPDATA 'CandidateHelper'
New-Item -ItemType Directory -Force $dir | Out-Null
Copy-Item (Join-Path $PSScriptRoot 'helper.py') (Join-Path $dir 'helper.py') -Force

Refresh-Path
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host '[-] Installing Python 3.12 via winget...'
    winget install --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    Refresh-Path
}
$py = (& python -c "import sys;print(sys.executable)").Trim()
$pyw = Join-Path (Split-Path $py) 'pythonw.exe'

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host '[-] Installing FFmpeg via winget...'
    winget install --id GYAN.FFmpeg --silent --accept-package-agreements --accept-source-agreements
    Refresh-Path
}

Write-Host '[+] Installing Python libraries...'
& python -m pip install --upgrade pip
& python -m pip install fastapi uvicorn requests

$vbs = Join-Path $dir 'launcher.vbs'
$helper = Join-Path $dir 'helper.py'
$line = 'CreateObject("WScript.Shell").Run """' + $pyw + '"" ""' + $helper + '""", 0, False'
Set-Content -Path $vbs -Value $line -Encoding ASCII
$cmd = 'wscript.exe "' + $vbs + '"'

# Start at login
Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'CandidateHelper' -Value $cmd

# cvhelper:// link so the web page can start the helper
$proto = 'HKCU:\Software\Classes\cvhelper'
New-Item -Path "$proto\shell\open\command" -Force | Out-Null
Set-ItemProperty -Path $proto -Name '(default)' -Value 'URL:Candidate Helper'
New-ItemProperty -Path $proto -Name 'URL Protocol' -Value '' -Force | Out-Null
Set-ItemProperty -Path "$proto\shell\open\command" -Name '(default)' -Value $cmd

Start-Process wscript.exe -ArgumentList ('"' + $vbs + '"')

Write-Host ''
Write-Host '[+] Done. The helper now starts automatically at login.'
Write-Host '[+] Go back to the web page and click Retry.'
