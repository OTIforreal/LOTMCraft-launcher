Set-Location $PSScriptRoot
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
& $py -m pip install -r requirements.txt pyinstaller
Get-Process LOTMCraft -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 1
$distDir = Join-Path $PSScriptRoot "dist"
$folder = Join-Path $distDir "LOTMCraft"
if (Test-Path $folder) {
    Remove-Item -Recurse -Force $folder
}
# use the bootloader compiled from source (see build_bootloader.ps1)
$loader = Join-Path (Split-Path (& $py -c "import PyInstaller,sys;print(PyInstaller.__file__)")) "bootloader\Windows-64bit-intel"
if (Test-Path (Join-Path $PSScriptRoot "bootloader\runw.exe")) { Copy-Item (Join-Path $PSScriptRoot "bootloader\run*.exe") $loader -Force }
& $py -m PyInstaller --noconfirm --clean --distpath $distDir --workpath build\pyinstaller LOTMCraft.spec
$exe = Join-Path $folder "LOTMCraft.exe"
if (-not (Test-Path $exe)) {
    throw "Build failed: LOTMCraft.exe is missing"
}
$old = Join-Path $distDir "LOTMCraft.exe"
if (Test-Path $old) { Remove-Item $old -Force }
$zip = Join-Path $distDir "LOTMCraft.zip"
if (Test-Path $zip) {
    Remove-Item $zip -Force
}
Compress-Archive -Path $folder -DestinationPath $zip
Write-Output "Built $exe"
Write-Output "Zip $zip"


# Optional code signing: set LOTM_SIGN_PFX (path) and LOTM_SIGN_PASSWORD to sign the launcher.
if ($env:LOTM_SIGN_PFX -and (Test-Path $env:LOTM_SIGN_PFX)) {
    $pwd = ConvertTo-SecureString $env:LOTM_SIGN_PASSWORD -AsPlainText -Force
    $cert = Get-PfxCertificate -FilePath $env:LOTM_SIGN_PFX -Password $pwd
    $result = Set-AuthenticodeSignature -FilePath $exe -Certificate $cert -HashAlgorithm SHA256 -TimestampServer "http://timestamp.digicert.com"
    if ($result.Status -ne "Valid") { Write-Warning "Signing finished with status: $($result.Status)" } else { Write-Output "Signed $exe" }
    Remove-Item $zip -Force
    Compress-Archive -Path $folder -DestinationPath $zip
} else {
    Write-Output "Not signed (set LOTM_SIGN_PFX and LOTM_SIGN_PASSWORD to sign)."
}
