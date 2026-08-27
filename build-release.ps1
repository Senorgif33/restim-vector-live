$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$version = (Select-String -Path (Join-Path $project "vector1a\__init__.py") -Pattern '__version__ = "(.+)"').Matches.Groups[1].Value
$stage = Join-Path $project "dist\Vector1A-$version"
$zip = Join-Path $project "dist\Vector1A-$version-windows.zip"
if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
New-Item -ItemType Directory -Path $stage -Force | Out-Null
$files = @(
    "vector1a",
    "restim_tcode_proxy",
    "tests",
    "mfp-plugin",
    "README.md",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "pyproject.toml",
    "start-vector1a.bat",
    "start-tcode-proxy.bat",
    "build-release.ps1",
    "build-release.bat"
)
foreach ($item in $files) {
    $source = Join-Path $project $item
    if (-not (Test-Path -LiteralPath $source)) {
        throw "Missing required release file: $item"
    }
    Copy-Item -LiteralPath $source -Destination $stage -Recurse
}
Get-ChildItem -LiteralPath $project -Filter "RELEASE_NOTES_*.md" |
    ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $stage }
Get-ChildItem -LiteralPath $stage -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
Compress-Archive -LiteralPath $stage -DestinationPath $zip
Write-Host "Created $zip"
