$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$targetDir = Join-Path $projectRoot "easyocr_models\\model"
$sourceDir = Join-Path $env:USERPROFILE ".EasyOCR\\model"

if (!(Test-Path $targetDir)) {
    New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
}

if (!(Test-Path $sourceDir)) {
    Write-Host "EasyOCR models not found. Downloading required models (first run)..."
    python -c "import easyocr; easyocr.Reader(['en'], gpu=False)"
}

if (!(Test-Path $sourceDir)) {
    Write-Host "Failed to locate EasyOCR models at $sourceDir"
    exit 1
}

Write-Host "Copying EasyOCR models from $sourceDir to $targetDir"
Copy-Item -Path (Join-Path $sourceDir "*") -Destination $targetDir -Recurse -Force

Write-Host "Done. Models are ready for bundling."
