param([string]$PdfPath = "")
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $PdfPath) {
    $PdfPath = Join-Path $projectRoot 'tests\fixtures\private\ganggu_2025_regular.pdf'
}
if (-not (Test-Path -LiteralPath $PdfPath -PathType Leaf)) {
    throw 'Source PDF is unavailable. Pass -PdfPath with the local original PDF path.'
}
$resolvedPdf = (Resolve-Path -LiteralPath $PdfPath).Path
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$outputPath = Join-Path $projectRoot 'data\real-evaluation'
Push-Location -LiteralPath $projectRoot
try {
    & $pythonPath -m doc_understanding physical-grid --pdf $resolvedPdf --output-dir $outputPath --focus-pages '8,14,15,18'
    if ($LASTEXITCODE -ne 0) { throw 'Whole-document physical evaluation failed.' }
} finally {
    Pop-Location
}
