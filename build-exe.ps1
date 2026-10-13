$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$version = (Select-String -Path (Join-Path $project "vector1a\__init__.py") -Pattern '__version__ = "(.+)"').Matches.Groups[1].Value
$exeName = "Vector1A-$version"
$stage = Join-Path $project "dist\Vector1A-$version-exe"
$zip = Join-Path $project "dist\Vector1A-$version-windows-exe.zip"
$work = Join-Path $project "build\pyinstaller"
$distdir = Join-Path $project "build\pyinstaller-dist"
$entry = Join-Path $project "build\vector1a_entry.py"
$yaml = @(
    "event_definitions.yml",
    "director_event_allowlist.yml"
)
foreach ($name in $yaml) {
    $source = Join-Path $project "vector1a\$name"
    if (-not (Test-Path -LiteralPath $source)) {
        throw "Missing required release file: vector1a\$name"
    }
}
if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
New-Item -ItemType Directory -Path (Join-Path $project "build") -Force | Out-Null
python -c "import PyInstaller"
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is missing. Run: python -m pip install pyinstaller"
}
$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($entry, "from vector1a.app import main`r`n`r`nmain()`r`n", $utf8)
$definitions = Join-Path $project "vector1a\event_definitions.yml"
$allowlist = Join-Path $project "vector1a\director_event_allowlist.yml"
python -m PyInstaller --noconfirm --clean --onefile --console --name $exeName `
    --distpath $distdir --workpath $work --specpath $work `
    --paths $project `
    --add-data "$definitions;vector1a" `
    --add-data "$allowlist;vector1a" `
    $entry
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
New-Item -ItemType Directory -Path $stage -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $distdir "$exeName.exe") -Destination $stage
foreach ($name in $yaml) {
    Copy-Item -LiteralPath (Join-Path $project "vector1a\$name") -Destination $stage
}
Compress-Archive -LiteralPath $stage -DestinationPath $zip
Write-Host "Created $zip"
