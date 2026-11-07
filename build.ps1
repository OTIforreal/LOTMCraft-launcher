Set-Location $PSScriptRoot
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --noupx --name LOTMCraft --collect-all customtkinter --collect-all minecraft_launcher_lib --add-data "assets;assets" main.py
if (Test-Path dist\LOTMCraft) {
    Remove-Item -Recurse -Force dist\LOTMCraft -ErrorAction SilentlyContinue
}
