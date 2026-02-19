$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

Write-Host "Preparing EasyOCR models..."
& "$projectRoot\\prepare_easyocr_models.ps1"

Write-Host "Installing dependencies..."
python -m pip install -r requirements.txt

Write-Host "Installing PyInstaller..."
python -m pip install --upgrade pyinstaller

Write-Host "Building single-file EXE (this can take a while)..."
pyinstaller --onefile --console --name LudoTitan `
  --splash "splash.png" `
  --add-data "popup_template.png;." `
  --add-data "easyocr_models\\model;easyocr_models\\model" `
  --collect-all easyocr `
  --collect-all torch `
  --collect-all torchvision `
  --exclude-module torchaudio `
  ludo_bot.py

Write-Host "Build complete. Output: dist\\LudoTitan.exe"
