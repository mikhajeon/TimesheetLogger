# Screenshots the overlay, one run per tab, cropped to the window.
# Uses System.Drawing from the Windows .NET Framework - nothing to install.
param(
    [string]$OutDir = "shots",
    [int]$X = 140,
    [int]$Y = 90,
    [int]$Width = 470,
    [int]$Height = 700
)

Add-Type -AssemblyName System.Drawing
$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$tempRoot = [System.IO.Path]::GetTempPath()
$env:TIMESHEET_HOME = Join-Path $tempRoot "tslog-demo"
$ready = Join-Path $tempRoot "tslog-ready"

foreach ($tab in @("Today", "Week", "Export", "Setup")) {
    if (Test-Path $ready) { Remove-Item -Force $ready }
    $proc = Start-Process -FilePath "python" `
        -ArgumentList "tests\shot_driver.py", $tab, $X, $Y, $Width, $Height `
        -PassThru -WindowStyle Hidden

    # The driver touches the ready file once the tab is painted.
    $waited = 0
    while (-not (Test-Path $ready) -and $waited -lt 15000) {
        Start-Sleep -Milliseconds 200
        $waited += 200
    }
    Start-Sleep -Milliseconds 500

    $bmp = New-Object System.Drawing.Bitmap($Width, $Height)
    $gfx = [System.Drawing.Graphics]::FromImage($bmp)
    $gfx.CopyFromScreen($X, $Y, 0, 0, $bmp.Size)
    $path = Join-Path $OutDir "$tab.png"
    $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
    $gfx.Dispose(); $bmp.Dispose()
    Write-Output "captured $path"

    if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 400
}
