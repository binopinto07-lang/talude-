# Talude Studio DEV Portatil - preparacao e execucao a partir de fontes.
# Sem PyInstaller, sem GitHub Actions, sem alterar o motor V1/V2.
[CmdletBinding()]
param(
    [ValidateSet('run', 'prepare', 'package', 'selftest', 'tests')]
    [string]$Action = 'run'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Dev = Join-Path $Repo '.talude_dev'
$Runtime = Join-Path $Dev 'runtime'
$Cache = Join-Path $Dev 'cache'
$LogDir = Join-Path $Dev 'logs'
$Python = Join-Path $Runtime 'python.exe'
$VendorScript = Join-Path $Repo 'studio\scripts\bootstrap_vendor.ps1'
$Marker = Join-Path $Dev 'DEPENDENCIAS_OK.json'
$ReleaseRoot = Join-Path $Repo 'DEV_RELEASES'
$PythonUrl = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip'
$GetPipUrl = 'https://bootstrap.pypa.io/get-pip.py'

foreach ($folder in @($Dev, $Cache, $LogDir)) {
    New-Item -Path $folder -ItemType Directory -Force | Out-Null
}
$LogFile = Join-Path $LogDir ('DEV_{0:yyyyMMdd_HHmmss}.log' -f (Get-Date))

function Write-Status([string]$Message) {
    Write-Host ('[TALUDE DEV] ' + $Message)
    Add-Content -LiteralPath $LogFile -Value ('[{0:yyyy-MM-dd HH:mm:ss}] {1}' -f (Get-Date), $Message) -Encoding UTF8
}

function Invoke-External([string]$Description, [scriptblock]$Command) {
    Write-Status $Description
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw ('{0} falhou (codigo {1}).' -f $Description, $LASTEXITCODE)
    }
}

function Assert-Source {
    if ($env:OS -ne 'Windows_NT' -or -not [Environment]::Is64BitOperatingSystem) {
        throw 'Este pacote necessita de Windows x64.'
    }
    foreach ($relative in @(
        'talude_studio.py', 'requirements.txt',
        'studio\desktop\main.py', 'studio\viewer\index.html',
        'studio\scripts\bootstrap_vendor.ps1',
        'src\talude_v1\engine.py', 'src\talude_v2\global_auto.py',
        'core\terrain_face.py', 'localbuild\SOURCE_REVISION.txt'
    )) {
        if (-not (Test-Path -LiteralPath (Join-Path $Repo $relative))) {
            throw ('Codigo-fonte incompleto: falta ' + $relative + '. Descarregue o ZIP integral do repositorio/branch.')
        }
    }
}

function Ensure-Python {
    if (-not (Test-Path -LiteralPath $Python)) {
        Write-Status 'Python portatil ausente; a preparar Python oficial embeddable 3.12.10 (so na primeira vez).'
        $archive = Join-Path $Cache 'python-3.12.10-embed-amd64.zip'
        if (-not (Test-Path -LiteralPath $archive)) {
            Invoke-WebRequest -UseBasicParsing -Uri $PythonUrl -OutFile $archive
        }
        $temporary = Join-Path $Dev 'runtime_prepare'
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item -LiteralPath $temporary -Recurse -Force
        }
        New-Item -Path $temporary -ItemType Directory -Force | Out-Null
        Expand-Archive -LiteralPath $archive -DestinationPath $temporary -Force
        if (-not (Test-Path -LiteralPath (Join-Path $temporary 'python.exe'))) {
            throw 'O ZIP do Python nao contem python.exe.'
        }
        if (Test-Path -LiteralPath $Runtime) {
            Remove-Item -LiteralPath $Runtime -Recurse -Force
        }
        Move-Item -LiteralPath $temporary -Destination $Runtime
    }

    New-Item -Path (Join-Path $Runtime 'Lib\site-packages') -ItemType Directory -Force | Out-Null
    # O Python embeddable ignora PYTHONPATH; as entradas explicitas sao relativas
    # ao python.exe, incluindo no ZIP final: .talude_dev/runtime -> raiz do repo.
    $pth = Join-Path $Runtime 'python312._pth'
    $paths = "python312.zip`r`n.`r`nLib\site-packages`r`n..\..`r`n..\..\src`r`nimport site`r`n"
    [System.IO.File]::WriteAllText($pth, $paths, (New-Object System.Text.UTF8Encoding($false)))
    Invoke-External 'Validar runtime Python 3.12' { & $Python -c 'import sys; assert sys.version_info[:2] == (3, 12); print(sys.version)' }
}

function Ensure-Pip {
    & $Python -m pip --version *> $null
    if ($LASTEXITCODE -eq 0) { return }
    Write-Status 'pip ainda nao existe no Python embeddable; a prepara-lo.'
    $bootstrap = Join-Path $Cache 'get-pip.py'
    if (-not (Test-Path -LiteralPath $bootstrap)) {
        Invoke-WebRequest -UseBasicParsing -Uri $GetPipUrl -OutFile $bootstrap
    }
    Invoke-External 'Preparar pip portatil' { & $Python $bootstrap --disable-pip-version-check --no-warn-script-location }
}

function Ensure-Dependencies {
    $requirements = Join-Path $Repo 'requirements.txt'
    $hash = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash
    $needInstall = $true
    if (Test-Path -LiteralPath $Marker) {
        try {
            $previous = Get-Content -LiteralPath $Marker -Raw | ConvertFrom-Json
            $needInstall = ($previous.requirements_sha256 -ne $hash)
        } catch {
            $needInstall = $true
        }
    }
    # Um marcador sem bibliotecas e considerado incompleto.
    if (-not $needInstall) {
        & $Python -c 'import numpy, scipy, laspy, lazrs, ezdxf, fastapi, uvicorn, pyproj; from PySide6 import QtWebEngineWidgets, QtWebEngineCore, QtWebChannel' *> $null
        $needInstall = ($LASTEXITCODE -ne 0)
    }
    if ($needInstall) {
        Ensure-Pip
        Invoke-External 'Instalar wheels Windows x64; nunca compilar bibliotecas' {
            & $Python -m pip install --disable-pip-version-check --no-warn-script-location --only-binary=:all: -r $requirements
        }
        Invoke-External 'Verificar imports das dependencias' {
            & $Python -c 'import numpy, scipy, laspy, lazrs, ezdxf, fastapi, uvicorn, pyproj; from PySide6 import QtWebEngineWidgets, QtWebEngineCore, QtWebChannel; print("DEPENDENCIAS=OK")'
        }
        @{ requirements_sha256 = $hash; created_utc = [DateTime]::UtcNow.ToString('o'); python = '3.12.10-embed-amd64' } |
            ConvertTo-Json | Set-Content -LiteralPath $Marker -Encoding UTF8
    } else {
        Write-Status 'Todas as dependencias ja estao instaladas: sem downloads nem pip.'
    }
}

function Ensure-Vendor {
    $potree = Join-Path $Repo 'studio\vendor\potree\build\potree\potree.js'
    $converter = Join-Path $Repo 'studio\vendor\potreeconverter\PotreeConverter.exe'
    if ((Test-Path -LiteralPath $potree) -and (Test-Path -LiteralPath $converter)) {
        Write-Status 'Potree e PotreeConverter disponiveis localmente.'
        return
    }
    if (-not (Test-Path -LiteralPath $VendorScript)) {
        throw ('Script vendor nao encontrado: ' + $VendorScript)
    }
    Write-Status 'A obter Potree/PotreeConverter com o bootstrap ORIGINAL do projeto.'
    & $VendorScript
    if (-not ((Test-Path -LiteralPath $potree) -and (Test-Path -LiteralPath $converter))) {
        throw 'Bootstrap vendor incompleto. Confirmar Potree e PotreeConverter no studio/vendor.'
    }
}

function Ensure-Ready {
    Assert-Source
    Ensure-Python
    Ensure-Dependencies
    Ensure-Vendor
    $env:PYTHONNOUSERSITE = '1'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:PYTHONPATH = "${Repo};$(Join-Path $Repo 'src')"
    $env:TALUDE_DEV_PORTABLE = '1'
}

function Invoke-SelfTest {
    Push-Location $Repo
    try {
        Invoke-External 'Self-test original: QtWebEngine, Potree, motor e vector export' {
            & $Python -B talude_studio.py --self-test
        }
        $report = Join-Path $Repo 'TALUDE_V1_SELF_TEST.txt'
        if (-not (Test-Path -LiteralPath $report)) {
            throw 'O self-test original nao gerou TALUDE_V1_SELF_TEST.txt.'
        }
        $first = Get-Content -LiteralPath $report -TotalCount 1
        if ($first -ne 'TALUDE_V1_SELF_TEST=OK') {
            throw ('Self-test reprovado: ' + $report)
        }
        Write-Status ('Self-test OK: ' + $report)
    } finally {
        Pop-Location
    }
}

function Invoke-Tests {
    & $Python -m pytest --version *> $null
    if ($LASTEXITCODE -ne 0) {
        Ensure-Pip
        Invoke-External 'Preparar pytest sem PyInstaller' {
            & $Python -m pip install --disable-pip-version-check --no-warn-script-location --only-binary=:all: 'pytest>=8,<9'
        }
    }
    Push-Location $Repo
    try {
        Invoke-External 'Testes completos de codigo-fonte (sem build)' {
            & $Python -B -m pytest -q
        }
    } finally {
        Pop-Location
    }
}

function Copy-Checked([string]$Relative, [string]$TargetRoot) {
    $source = Join-Path $Repo $Relative
    if (-not (Test-Path -LiteralPath $source)) { return }
    $destination = Join-Path $TargetRoot $Relative
    $parent = Split-Path -Parent $destination
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -Path $parent -ItemType Directory -Force | Out-Null
    }
    Copy-Item -LiteralPath $source -Destination $destination -Recurse -Force
}

function New-PortableZip {
    Ensure-Ready
    Invoke-SelfTest
    $stageParent = Join-Path $Dev 'zip_stage'
    $stage = Join-Path $stageParent 'TALUDE_STUDIO_DEV'
    if (Test-Path -LiteralPath $stageParent) {
        Remove-Item -LiteralPath $stageParent -Recurse -Force
    }
    New-Item -Path $stage -ItemType Directory -Force | Out-Null

    # Whitelist: evitar .git, LAS/LAZ enormes, dados pessoais, caches e extracoes
    # proprietarias do BINO_APOIO. Os algoritmos Python integrados em src/core
    # entram integralmente e as dependencias executaveis ficam no vendor.
    foreach ($part in @(
        'src', 'core', 'studio', 'scripts', 'packaging', 'tests', 'localbuild',
        'config', 'docs', 'LocalBuildManager__TALUDE_STUDIO', 'V2_EXPERIMENTAL.md',
        'talude_studio.py', 'talude_gui.py', 'requirements.txt',
        'requirements-dev.txt', 'pyproject.toml', 'README.md',
        'INICIAR_TALUDE_DEV.bat', 'CRIAR_ZIP_DEV_COMPLETO.bat',
        'DEV_PORTATIL_LEIA_ME.md', 'DEV_PORTATIL_COMPONENTES.md'
    )) {
        Copy-Checked $part $stage
    }
    $targetRuntime = Join-Path $stage '.talude_dev\runtime'
    New-Item -Path (Split-Path -Parent $targetRuntime) -ItemType Directory -Force | Out-Null
    Copy-Item -LiteralPath $Runtime -Destination $targetRuntime -Recurse -Force
    $runtimeMarker = Join-Path $stage '.talude_dev\DEPENDENCIAS_OK.json'
    Copy-Item -LiteralPath $Marker -Destination $runtimeMarker -Force

    $sourceRevision = (Get-Content -LiteralPath (Join-Path $Repo 'localbuild\SOURCE_REVISION.txt') -Raw).Trim()
    $gitHead = 'ZIP_SOURCE_NO_GIT'
    if (Test-Path -LiteralPath (Join-Path $Repo '.git')) {
        $gitOutput = & git -C $Repo rev-parse HEAD 2>$null
        if ($LASTEXITCODE -eq 0) { $gitHead = $gitOutput.Trim() }
    }
    $pipFreeze = & $Python -m pip freeze --all
    @{
        project = 'Talude Studio DEV Portatil'
        created_utc = [DateTime]::UtcNow.ToString('o')
        source_sha = $gitHead
        source_guard = $sourceRevision
        python = '3.12.10-embed-amd64'
        requirements_sha256 = (Get-FileHash -LiteralPath (Join-Path $Repo 'requirements.txt') -Algorithm SHA256).Hash
        packages = @($pipFreeze)
        note = 'R19/R20 apenas experimentais; nao alterar AUTO de producao.'
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $stage 'DEV_PORTABLE_MANIFEST.json') -Encoding UTF8

    # Ensaio do pacote copiado ANTES de criar o ZIP; nao executar o builder.
    Push-Location $stage
    try {
        $testPython = Join-Path $stage '.talude_dev\runtime\python.exe'
        Invoke-External 'Self-test dentro da pasta PORTATIL copiada' {
            & $testPython -B talude_studio.py --self-test
        }
        $result = Get-Content -LiteralPath (Join-Path $stage 'TALUDE_V1_SELF_TEST.txt') -TotalCount 1
        if ($result -ne 'TALUDE_V1_SELF_TEST=OK') {
            throw 'O ZIP nao sera emitido: self-test da copia falhou.'
        }
        Remove-Item -LiteralPath (Join-Path $stage 'TALUDE_V1_SELF_TEST.txt') -Force
    } finally {
        Pop-Location
    }
    New-Item -Path $ReleaseRoot -ItemType Directory -Force | Out-Null
    $zip = Join-Path $ReleaseRoot 'TALUDE_STUDIO_DEV_WINDOWS_X64_COMPLETO.zip'
    if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $stageParent, $zip, [System.IO.Compression.CompressionLevel]::Optimal, $false
    )
    $checksum = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash
    Write-Status ('ZIP completo: ' + $zip)
    Write-Status ('ZIP SHA256: ' + $checksum)
    Write-Status 'Este ZIP pode iniciar offline noutro Windows x64 compativel.'
}

try {
    Write-Status ('Acao: ' + $Action + '; raiz: ' + $Repo)
    switch ($Action) {
        'run' {
            Ensure-Ready
            Push-Location $Repo
            try {
                Invoke-External 'Abrir Talude Studio pelo codigo Python (sem compilacao)' {
                    & $Python -B talude_studio.py
                }
            } finally { Pop-Location }
        }
        'prepare' { Ensure-Ready; Invoke-SelfTest }
        'selftest' { Ensure-Ready; Invoke-SelfTest }
        'tests' { Ensure-Ready; Invoke-Tests }
        'package' { New-PortableZip }
    }
    Write-Status 'Concluido.'
    exit 0
} catch {
    $message = ($_ | Out-String)
    Add-Content -LiteralPath $LogFile -Value $message -Encoding UTF8
    Write-Host ('[ERRO TALUDE DEV] ' + $message) -ForegroundColor Red
    Write-Host ('Log: ' + $LogFile) -ForegroundColor Yellow
    exit 1
}
