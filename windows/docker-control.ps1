# ============================================================
# docker-control.ps1 - HTTP listener on port 2376
# Hardened: try/finally listener cleanup (readiness handled by Node-RED)
# ============================================================

$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add("http://+:2376/")

try {
    $listener.Start()
    Write-Host "Docker control server listening on port 2376..."

    while ($listener.IsListening) {
        $context = $listener.GetContext()
        $path = $context.Request.Url.AbsolutePath
        $response = $context.Response
        switch ($path) {
            "/start-ditto" {
                Start-Process "docker" -ArgumentList "start tts_server" -WindowStyle Hidden
                Start-Process "docker" -ArgumentList "start talking_head_server" -WindowStyle Hidden
                $msg = "started"
            }
            "/stop-ditto" {
                Start-Process "docker" -ArgumentList "stop talking_head_server" -WindowStyle Hidden
                Start-Process "docker" -ArgumentList "stop tts_server" -WindowStyle Hidden
                $msg = "stopped"
            }
            "/start-ditto-voicebox" {
                Start-Process "docker" -ArgumentList "start voicebox" -WindowStyle Hidden
                Start-Sleep -Seconds 2
                Start-Process "docker" -ArgumentList "start talking_head_server" -WindowStyle Hidden
                $msg = "started (voicebox)"
            }
            "/stop-ditto-voicebox" {
                Start-Process "docker" -ArgumentList "stop talking_head_server" -WindowStyle Hidden
                Start-Process "docker" -ArgumentList "stop voicebox" -WindowStyle Hidden
                $msg = "stopped (voicebox)"
            }
            "/start-birdwatch" {
                Start-Process "docker" -ArgumentList "start birdwatch" -WindowStyle Hidden
                $msg = "started"
            }
            "/stop-birdwatch" {
                Start-Process "docker" -ArgumentList "stop birdwatch" -WindowStyle Hidden
                $msg = "stopped"
            }
            "/copy-video" {
                $log = "C:\docker_desktop\copy-video.log"
                function CvLog($m) { "$(Get-Date -Format 'HH:mm:ss') $m" | Out-File -FilePath $log -Append -Encoding utf8 }
                CvLog "=== /copy-video START ==="
                # Authenticate to HA Samba share using stored credential file.
                # Credential file format (plain text, 2 lines): line1=username, line2=password
                CvLog "authenticating to 192.168.1.10..."
                try {
                    $credFile = "C:\docker_desktop\nas-cred.txt"
                    if (Test-Path $credFile) {
                        $c = Get-Content $credFile
                        $nasUser = $c[0]
                        $nasPass = $c[1]
                        # Drop any existing session to this host, then reconnect with creds (non-blocking)
                        cmd /c "net use \\192.168.1.10\config /delete /y" 2>$null | Out-Null
                        $r = cmd /c "net use \\192.168.1.10\config $nasPass /user:$nasUser /persistent:no" 2>&1
                        CvLog "auth result: $r"
                    } else {
                        CvLog "WARNING: cred file not found at $credFile"
                    }
                } catch {
                    CvLog "auth step error: $_"
                }
                CvLog "listing output dir..."
                $outputDir = "C:\docker_desktop\talking-head-ditto\output"
                $latest = Get-ChildItem $outputDir -Filter "*.mp4" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
                $dst1 = "\\192.168.1.10\config\www\Ai-Assistant\summary.mp4"
                $copyOk = 0
                $copyFail = 0
                if ($latest) {
                    CvLog "latest = $($latest.Name)"
                    $dst2 = "\\192.168.1.10\media\AI-Assistant\" + $latest.Name
                    CvLog "copying to www..."
                    try {
                        Copy-Item -Path $latest.FullName -Destination $dst1 -Force
                        CvLog "www copy OK"
                        Write-Host "Copied $($latest.Name) to www as summary.mp4"
                        $copyOk++
                    } catch {
                        CvLog "www copy FAILED: $_"
                        Write-Host "Failed to copy to www: $_"
                        $copyFail++
                    }
                    CvLog "copying to media..."
                    try {
                        Copy-Item -Path $latest.FullName -Destination $dst2 -Force
                        CvLog "media copy OK"
                        Write-Host "Copied $($latest.Name) to media"
                        $copyOk++
                    } catch {
                        CvLog "media copy FAILED: $_"
                        Write-Host "Failed to copy to media: $_"
                        $copyFail++
                    }
                    if ($copyFail -eq 0) {
                        $msg = "copied"
                    } elseif ($copyOk -gt 0) {
                        $msg = "partial: $copyOk ok, $copyFail failed"
                    } else {
                        $msg = "copy failed"
                    }
                } else {
                    CvLog "no mp4 found"
                    Write-Host "No mp4 files found in $outputDir"
                    $msg = "no file found"
                }
                CvLog "=== /copy-video END ($msg) ==="
            }
            default { $msg = "unknown command" }
        }
        Write-Host "$path -> $msg"
        $buffer = [System.Text.Encoding]::UTF8.GetBytes($msg)
        $response.ContentLength64 = $buffer.Length
        $response.OutputStream.Write($buffer, 0, $buffer.Length)
        $response.OutputStream.Close()
    }
}
finally {
    # Always release the HTTP listener cleanly so it can't orphan the
    # port 2376 registration (prevents zombie HttpListener processes).
    if ($listener) {
        try { $listener.Stop() } catch {}
        try { $listener.Close() } catch {}
    }
    Write-Host "Listener stopped and disposed."
}
