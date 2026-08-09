setup-issue-templates.ps1
Creates .github\ISSUE_TEMPLATE\ in the LoopClip repo and moves the three
downloaded template files into it.
Run this from C:\LoopClip (or edit $repoPath below).
Assumes bug_report.yml, feature_request.yml, and config.yml are in your
Downloads folder - edit $downloadsPath if they are somewhere else.
$repoPath = "C:\LoopClip" $downloadsPath = "$env:USERPROFILE\Downloads" $templateFolder = Join-Path $repoPath ".github\ISSUE_TEMPLATE"
Write-Host "Creating folder: $templateFolder" -ForegroundColor Cyan New-Item -ItemType Directory -Path $templateFolder -Force | Out-Null
$files = @("bug_report.yml", "feature_request.yml", "config.yml")
foreach ($file in $files) { $source = Join-Path $downloadsPath $file $dest = Join-Path $templateFolder $file
if (Test-Path -LiteralPath $source) {
    Move-Item -LiteralPath $source -Destination $dest -Force
    Write-Host "  Moved: $file" -ForegroundColor Green
} else {
    Write-Host "  Not found in Downloads: $file (skipped)" -ForegroundColor Yellow
}
}
Write-Host "" Write-Host "Done. Contents of ${templateFolder}:" -ForegroundColor Cyan Get-ChildItem -LiteralPath $templateFolder
Write-Host "" Write-Host "Next steps:" -ForegroundColor Cyan Write-Host "  cd $repoPath" Write-Host "  git add .github" Write-Host "  git commit -m ""Add GitHub issue templates""" Write-Host "  git push"

