# watch-and-copy.ps1
# Polls the Ditto output folder every 5 seconds and copies new videos to HA media share
# Also copies latest video to HA www folder as summary.mp4 for dashboard card
# Deletes files older than 1 day from both folders
# Run: powershell -WindowStyle Hidden -File C:\docker_desktop\talking-head-ditto\watch-and-copy.ps1

$sourceFolder = "C:\docker_desktop\talking-head-ditto\output"
$destFolder   = "Z:\AI-Assistant"               # mapped NAS media share
$wwwFolder    = "\\192.168.1.10\config\www\Ai-Assistant"

function Remove-OldFiles {
    $cutoff = (Get-Date).AddDays(-1)
    # Clean source folder
    Get-ChildItem -Path $sourceFolder -Filter "*.mp4" | Where-Object { $_.LastWriteTime -lt $cutoff } | ForEach-Object {
        Remove-Item $_.FullName -Force
        Write-Host "$(Get-Date -Format 'HH:mm:ss') Deleted old file from output: $($_.Name)"
    }
    # Clean NAS folder
    Get-ChildItem -Path $destFolder -Filter "*.mp4" | Where-Object { $_.LastWriteTime -lt $cutoff } | ForEach-Object {
        Remove-Item $_.FullName -Force
        Write-Host "$(Get-Date -Format 'HH:mm:ss') Deleted old file from NAS: $($_.Name)"
    }
}

Write-Host "$(Get-Date -Format 'HH:mm:ss') Starting - syncing existing files..."

# On startup, copy any files not already on NAS
# Also copy the newest file to www as summary.mp4
$newestFile = Get-ChildItem -Path $sourceFolder -Filter "*.mp4" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Get-ChildItem -Path $sourceFolder -Filter "*.mp4" | ForEach-Object {
    $dest = Join-Path $destFolder $_.Name
    if (-not (Test-Path $dest)) {
        Copy-Item -Path $_.FullName -Destination $dest -Force -Verbose
    }
}
if ($newestFile) {
    Copy-Item -Path $newestFile.FullName -Destination "$wwwFolder\summary.mp4" -Force
    Write-Host "$(Get-Date -Format 'HH:mm:ss') Copied $($newestFile.Name) to www as summary.mp4 on startup"
}

# Clean old files on startup
Remove-OldFiles

Write-Host "$(Get-Date -Format 'HH:mm:ss') Watching $sourceFolder for new videos..."

$counter = 0
while ($true) {
    Get-ChildItem -Path $sourceFolder -Filter "*.mp4" | ForEach-Object {
        $dest = Join-Path $destFolder $_.Name
        if (-not (Test-Path $dest)) {
            Start-Sleep -Seconds 2
            Copy-Item -Path $_.FullName -Destination $dest -Force -Verbose
            Write-Host "$(Get-Date -Format 'HH:mm:ss') Copied $($_.Name) to Z:\AI-Assistant"
            # Copy to www as summary.mp4 for HA dashboard card
            Copy-Item -Path $_.FullName -Destination "$wwwFolder\summary.mp4" -Force
            Write-Host "$(Get-Date -Format 'HH:mm:ss') Copied $($_.Name) to www as summary.mp4"
        }
    }
    # Run cleanup every 60 cycles (~5 minutes)
    $counter++
    if ($counter -ge 60) {
        Remove-OldFiles
        $counter = 0
    }
    Start-Sleep -Seconds 5
}