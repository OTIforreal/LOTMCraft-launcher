# Rebuilds the PyInstaller bootloader from source (no Visual Studio needed: uses Zig as the C compiler).
# Result: bootloader\run.exe and bootloader\runw.exe, which build.ps1 installs before packaging.
# A self-compiled bootloader has a different fingerprint than the stock one that antivirus ML models know.
Set-Location $PSScriptRoot
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $py -m pip install -q ziglang
$ver = (& $py -c "import PyInstaller;print(PyInstaller.__version__)").Trim()
$work = Join-Path $env:TEMP "pyi_build_$ver"
Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory $work | Out-Null
& $py -m pip download "pyinstaller==$ver" --no-binary :all: --no-deps -d $work | Out-Null
tar -xzf (Join-Path $work "pyinstaller-$ver.tar.gz") -C $work 2>$null
$bin = Join-Path $work "zigbin"
New-Item -ItemType Directory $bin | Out-Null
@"
import subprocess, sys
drop = {"-m64", "-Wl,--enable-auto-import", "-Wl,-Bstatic", "-Wl,-Bdynamic", "-Wl,--as-needed", "-Wl,--no-as-needed"}
args = [a for a in sys.argv[1:] if a not in drop]
sys.exit(subprocess.call([sys.executable, "-m", "ziglang", "cc", "-target", "x86_64-windows-gnu", "-Wno-macro-redefined", "-Wno-error"] + args))
"@ | Set-Content (Join-Path $bin "zigcc.py")
Set-Content (Join-Path $bin "gcc.cmd") "@`"$py`" `"$bin\zigcc.py`" %*"
Set-Content (Join-Path $bin "ar.cmd") "@`"$py`" -m ziglang ar %*"
Set-Content (Join-Path $bin "strip.cmd") "@exit /b 0"
$env:PATH = "$bin;$env:PATH"; $env:CC = "$bin\gcc.cmd"; $env:AR = "$bin\ar.cmd"
Push-Location (Join-Path $work "pyinstaller-$ver\bootloader")
& $py waf --clang --target-arch=64bit distclean configure all
Pop-Location
$out = Join-Path $work "pyinstaller-$ver\PyInstaller\bootloader\Windows-64bit-intel"
New-Item -ItemType Directory -Force (Join-Path $PSScriptRoot "bootloader") | Out-Null
Copy-Item (Join-Path $out "run.exe"), (Join-Path $out "runw.exe") (Join-Path $PSScriptRoot "bootloader") -Force
Write-Output "Bootloader rebuilt for PyInstaller $ver"
