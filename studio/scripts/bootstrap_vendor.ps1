param(
  [switch]$Force
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$V2Root = Split-Path -Parent $ScriptDir
$Vendor = Join-Path $V2Root "vendor"
$PotreeTarget = Join-Path $Vendor "potree"
$ConverterTarget = Join-Path $Vendor "potreeconverter"
$Temp = Join-Path $env:TEMP ("talude_vendor_" + [guid]::NewGuid().ToString("N"))

New-Item -ItemType Directory -Force -Path $Vendor | Out-Null
New-Item -ItemType Directory -Force -Path $Temp | Out-Null

function Get-ReleaseAsset {
  param(
    [string]$Repo,
    [string]$Tag,
    [scriptblock]$Selector
  )

  $release = Invoke-RestMethod -Headers @{"User-Agent"="Talude"} -Uri "https://api.github.com/repos/$Repo/releases/tags/$Tag"
  $asset = $release.assets | Where-Object $Selector | Select-Object -First 1

  if (-not $asset) {
    throw "Nao encontrei asset compativel em $Repo tag $Tag"
  }

  return $asset
}

try {
  $PotreeJs = Join-Path $PotreeTarget "build\potree\potree.js"

  if ($Force -or -not (Test-Path $PotreeJs)) {
    Write-Host "A obter Potree 1.8.2..." -ForegroundColor Cyan

    $asset = Get-ReleaseAsset "potree/potree" "1.8.2" {
      $_.name -match "\.zip$"
    }

    $zip = Join-Path $Temp $asset.name
    Invoke-WebRequest -UseBasicParsing -Uri $asset.browser_download_url -OutFile $zip

    $extract = Join-Path $Temp "potree"
    Expand-Archive -Path $zip -DestinationPath $extract -Force

    $found = Get-ChildItem -Path $extract -Recurse -File -Filter "potree.js" |
      Where-Object {
        $_.FullName -match "[\\/]build[\\/]potree[\\/]potree\.js$"
      } |
      Select-Object -First 1

    if (-not $found) {
      throw "O pacote Potree nao contem build/potree/potree.js"
    }

    $root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $found.FullName))

    if (Test-Path $PotreeTarget) {
      Remove-Item -Recurse -Force $PotreeTarget
    }

    New-Item -ItemType Directory -Force -Path $PotreeTarget | Out-Null
    Copy-Item -Path (Join-Path $root "*") -Destination $PotreeTarget -Recurse -Force

    Write-Host "Potree 1.8.2 pronto." -ForegroundColor Green
  }

  $ConverterExe = Join-Path $ConverterTarget "PotreeConverter.exe"

  if ($Force -or -not (Test-Path $ConverterExe)) {
    Write-Host "A obter PotreeConverter 2.1..." -ForegroundColor Cyan

    $release = Invoke-RestMethod -Headers @{"User-Agent"="Talude"} -Uri "https://api.github.com/repos/potree/PotreeConverter/releases/tags/2.1"

    $asset = $release.assets |
      Where-Object {
        $_.name -match "(?i)(windows|win|x64).*\.zip$"
      } |
      Select-Object -First 1

    if (-not $asset) {
      $asset = $release.assets |
        Where-Object { $_.name -match "\.zip$" } |
        Select-Object -First 1
    }

    if (-not $asset) {
      throw "Nao encontrei ZIP de PotreeConverter 2.1."
    }

    $zip = Join-Path $Temp $asset.name
    Invoke-WebRequest -UseBasicParsing -Uri $asset.browser_download_url -OutFile $zip

    $extract = Join-Path $Temp "converter"
    Expand-Archive -Path $zip -DestinationPath $extract -Force

    $exe = Get-ChildItem -Path $extract -Recurse -File -Filter "PotreeConverter.exe" |
      Select-Object -First 1

    if (-not $exe) {
      throw "PotreeConverter.exe nao encontrado no pacote."
    }

    $root = Split-Path -Parent $exe.FullName

    if (Test-Path $ConverterTarget) {
      Remove-Item -Recurse -Force $ConverterTarget
    }

    New-Item -ItemType Directory -Force -Path $ConverterTarget | Out-Null
    Copy-Item -Path (Join-Path $root "*") -Destination $ConverterTarget -Recurse -Force

    Write-Host "PotreeConverter 2.1 pronto." -ForegroundColor Green
  }

  Write-Host ""
  Write-Host "Vendor V2 concluido:" -ForegroundColor Green
  Write-Host "  Potree:          $PotreeTarget"
  Write-Host "  PotreeConverter: $ConverterTarget"
}
finally {
  if (Test-Path $Temp) {
    Remove-Item -Recurse -Force $Temp -ErrorAction SilentlyContinue
  }
}
