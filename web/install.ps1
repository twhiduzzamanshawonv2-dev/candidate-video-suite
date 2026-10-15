$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # progress bars make downloads very slow in Windows PowerShell 5.1
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch {}

$Base      = 'https://candidate-video-suite.vercel.app'
$PythonUrl = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip'
$PipUrl    = 'https://bootstrap.pypa.io/get-pip.py'
$FfmpegUrls = @(
    'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip',
    'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip'
)

function Step($m) { Write-Host "[+] $m" }
function Fail($m) { Write-Host "[X] $m" -ForegroundColor Red; throw $m }

# Invoke-WebRequest can stall on big files; curl.exe (Windows 10 1803+) retries, resumes and aborts stalled transfers
function Get-File($url, $out) {
    # curl -C - would append to a leftover file from an earlier run and corrupt it
    if (Test-Path $out) { Remove-Item $out -Force }
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        & $curl.Source -L --fail --silent --show-error --retry 5 --retry-delay 2 --connect-timeout 20 --speed-time 30 --speed-limit 5000 -C - -o $out $url
        if ($LASTEXITCODE -ne 0) { throw "curl failed (exit $LASTEXITCODE)" }
    } else {
        (New-Object System.Net.WebClient).DownloadFile($url, $out)
    }
}
function Download($url, $out) {
    try { Get-File $url $out }
    catch { Fail "Download failed: $url`n    $($_.Exception.Message)`n    Check your internet connection / proxy / antivirus and run setup again." }
}

Write-Host '========================================'
Write-Host '  Candidate Video Helper - One-time setup'
Write-Host '  (no admin rights or winget needed)'
Write-Host '========================================'

if (-not [Environment]::Is64BitOperatingSystem) { Fail 'This helper needs 64-bit Windows.' }

$dir = Join-Path $env:LOCALAPPDATA 'CandidateHelper'
New-Item -ItemType Directory -Force $dir | Out-Null
$tmp = Join-Path $env:TEMP ('cvh_' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force $tmp | Out-Null

# Stop a running copy so files can be replaced
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*CandidateHelper*helper.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
# ...and whatever else is holding the helper port (an old helper started another way, e.g. start_helper.bat,
# would otherwise keep running and make the new one exit silently)
try {
    Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
        $pr = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
        if ($pr -and $pr.ProcessName -match '^pythonw?$') { Step "Stopping old helper (pid $($pr.Id))..."; Stop-Process -Id $pr.Id -Force -ErrorAction SilentlyContinue }
    }
} catch {}
Start-Sleep -Milliseconds 800

# 1. Helper script (use a local copy if run from the project folder)
$helper = Join-Path $dir 'helper.py'
$localHelper = if ($PSScriptRoot) { Join-Path $PSScriptRoot 'helper.py' } else { $null }
if ($localHelper -and (Test-Path $localHelper)) { Step 'Using local helper.py'; Copy-Item $localHelper $helper -Force }
else { Step 'Downloading helper...'; Download "$Base/helper.py" $helper }

# 2. Private Python (embeddable build: no installer, no admin, no PATH changes)
$pyDir = Join-Path $dir 'python'
$py = Join-Path $pyDir 'python.exe'
$needLibs = $true
if (Test-Path $py) {
    & $py -c "import fastapi, uvicorn, requests" 2>$null
    if ($LASTEXITCODE -eq 0) { $needLibs = $false }
}
if ($needLibs) {
    if (Test-Path $pyDir) { Remove-Item $pyDir -Recurse -Force }
    Step 'Downloading Python 3.12 (about 10 MB)...'
    Download $PythonUrl (Join-Path $tmp 'python.zip')
    Expand-Archive -Path (Join-Path $tmp 'python.zip') -DestinationPath $pyDir -Force
    # enable site-packages so pip-installed libraries can be imported
    Get-ChildItem $pyDir -Filter 'python*._pth' | ForEach-Object {
        (Get-Content $_.FullName) -replace '^#\s*import site', 'import site' | Set-Content $_.FullName -Encoding ascii
    }
    Step 'Installing pip...'
    Download $PipUrl (Join-Path $tmp 'get-pip.py')
    & $py (Join-Path $tmp 'get-pip.py') --no-warn-script-location --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { Fail 'pip installation failed.' }
    Step 'Installing libraries (fastapi, uvicorn, requests)...'
    & $py -m pip install --no-warn-script-location --disable-pip-version-check fastapi uvicorn requests
    if ($LASTEXITCODE -ne 0) { Fail 'Library installation failed.' }
} else { Step 'Python and libraries already installed.' }

& $py -m py_compile $helper
if ($LASTEXITCODE -ne 0) { Fail 'helper.py is damaged (bad download). Run setup again.' }

# 3. FFmpeg (private copy, unless one is already on PATH)
$ffDir = Join-Path $dir 'ffmpeg'
if ((Test-Path (Join-Path $ffDir 'ffmpeg.exe')) -or (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Step 'FFmpeg already available.'
} else {
    $ok = $false
    foreach ($u in $FfmpegUrls) {
        try {
            Step "Downloading FFmpeg (about 80 MB, please wait)..."
            $zip = Join-Path $tmp 'ffmpeg.zip'
            if (Test-Path $zip) { Remove-Item $zip -Force }
            Get-File $u $zip
            $ex = Join-Path $tmp 'ffmpeg'
            Expand-Archive -Path $zip -DestinationPath $ex -Force
            $bin = Get-ChildItem $ex -Recurse -Filter 'ffmpeg.exe' | Select-Object -First 1
            if (-not $bin) { throw 'ffmpeg.exe not found in archive' }
            New-Item -ItemType Directory -Force $ffDir | Out-Null
            Copy-Item $bin.FullName $ffDir -Force
            $probe = Join-Path $bin.DirectoryName 'ffprobe.exe'
            if (Test-Path $probe) { Copy-Item $probe $ffDir -Force }
            $ok = $true; break
        } catch { Write-Host "    ($u failed: $($_.Exception.Message))" }
    }
    if (-not $ok) { Write-Host '[!] FFmpeg could not be installed. Downloads still work; Convert (.mp4) needs FFmpeg.' -ForegroundColor Yellow }
}

# 4. Start at login and the cvhelper:// link. pythonw.exe has no window, so it is launched
#    directly (no Windows Script Host, which some PCs have disabled).
$pyw = Join-Path $pyDir 'pythonw.exe'
$cmd = '"' + $pyw + '" "' + $helper + '"'
Remove-Item (Join-Path $dir 'launcher.vbs') -Force -ErrorAction SilentlyContinue

Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'CandidateHelper' -Value $cmd
$proto = 'HKCU:\Software\Classes\cvhelper'
New-Item -Path "$proto\shell\open\command" -Force | Out-Null
Set-ItemProperty -Path $proto -Name '(default)' -Value 'URL:Candidate Helper'
New-ItemProperty -Path $proto -Name 'URL Protocol' -Value '' -Force | Out-Null
Set-ItemProperty -Path "$proto\shell\open\command" -Name '(default)' -Value $cmd

# 5. Start now and verify
Step 'Starting helper...'
Start-Process -FilePath $pyw -ArgumentList ('"' + $helper + '"') -WorkingDirectory $dir -WindowStyle Hidden
$up = $false
# first start can be slow (Python compiles the libraries, antivirus scans them)
for ($i = 0; $i -lt 60 -and -not $up; $i++) {
    Start-Sleep -Milliseconds 750
    try { $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/ping' -TimeoutSec 2; if ($r.ok) { $up = $true } } catch {}
}
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ''
if ($up) {
    Write-Host "[+] Done. Helper is running (Python $($r.python), FFmpeg: $($r.ffmpeg))." -ForegroundColor Green
    Write-Host '[+] It starts automatically at every login. Go back to the web page and click Re-check.'
} else {
    Write-Host '[X] Installed, but the helper did not respond on port 8765.' -ForegroundColor Red
    Write-Host '    Try signing out and in again, or check that antivirus/firewall is not blocking python.exe.'
    $log = Join-Path $dir 'helper.log'
    if (Test-Path $log) { Write-Host '    --- helper.log ---'; Get-Content $log -Tail 25 | ForEach-Object { Write-Host "    $_" } }
    else {
        Write-Host '    --- trying to run the helper in this window to show the error ---'
        $out = Join-Path $env:TEMP 'cvh_run.txt'
        $p = Start-Process -FilePath $py -ArgumentList ('"' + $helper + '"') -RedirectStandardError $out -NoNewWindow -PassThru
        Start-Sleep -Seconds 8
        if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force; Write-Host '    (it started fine here - something blocks the hidden start; send this to the developer)' }
        if (Test-Path $out) { Get-Content $out -Tail 25 | ForEach-Object { Write-Host "    $_" } }
    }
}
