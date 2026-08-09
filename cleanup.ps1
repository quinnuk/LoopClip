
# cleanup.ps1 - removes leftover/duplicate files from the LoopClip project.
# Run this FROM the C:\LoopClip folder (or it'll just find nothing to do).
#
# Safe by design:
#  - Shows you exactly what it found before touching anything
#  - Asks for a single Y/N confirmation before deleting
#  - Only ever touches the specific items listed below - never a blanket
#    wildcard delete

$itemsToDelete = @(
    "__pycache__",
    "build",
    "dist",
    "github",                          # the stray folder WITHOUT the dot - not .github
    "LoopClip.spec",
    "run.bat",
    "Run LoopClip (silent).vbs"
)

Write-Host "Looking for these items in $PWD ..." -ForegroundColor Cyan
Write-Host ""

$found = @()
foreach ($item in $itemsToDelete) {
    if (Test-Path -LiteralPath $item) {
        $found += $item
        Write-Host "  FOUND   $item"
    } else {
        Write-Host "  (not present) $item" -ForegroundColor DarkGray
    }
}

Write-Host ""

if ($found.Count -eq 0) {
    Write-Host "Nothing to delete - already clean." -ForegroundColor Green
    exit
}

$confirm = Read-Host "Delete the $($found.Count) item(s) marked FOUND above? (y/n)"
if ($confirm -ne "y") {
    Write-Host "Cancelled - nothing was deleted." -ForegroundColor Yellow
    exit
}

foreach ($item in $found) {
    try {
        Remove-Item -LiteralPath $item -Recurse -Force -ErrorAction Stop
        Write-Host "  Deleted: $item" -ForegroundColor Green
    } catch {
        Write-Host "  Could not delete: $item ($($_.Exception.Message))" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "Done." -ForegroundColor Cyan