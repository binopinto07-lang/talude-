"""Static safety contract for the source-driven portable dev launcher.

This does not claim that a Windows runtime was installed or Qt launched.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_dev_entrypoint_exists_and_does_not_use_pyinstaller():
    bat = (REPO / "INICIAR_TALUDE_DEV.bat").read_text(encoding="utf-8")
    ps1 = (REPO / "scripts" / "dev_portable.ps1").read_text(encoding="utf-8")
    assert "dev_portable.ps1" in bat
    assert "-Action run" in bat
    assert '& $Python -B talude_studio.py' in ps1
    assert 'PyInstaller.__main__' not in ps1


def test_portable_runtime_resolves_source_paths():
    ps1 = (REPO / "scripts" / "dev_portable.ps1").read_text(encoding="utf-8")
    for expected in ("python312._pth", "Lib\\site-packages", "..\\..\\src", 
                     "requirements.txt", "studio\\scripts\\bootstrap_vendor.ps1"):
        assert expected in ps1


def test_complete_offline_package_checks_selftest_and_whitelists_sources():
    ps1 = (REPO / "scripts" / "dev_portable.ps1").read_text(encoding="utf-8")
    assert "TALUDE_V1_SELF_TEST=OK" in ps1
    assert "ZipFile]::CreateFromDirectory" in ps1
    assert "'src', 'core', 'studio', 'scripts'" in ps1
    assert "'.git'" not in ps1.split("foreach ($part in @(", 1)[1].split('))', 1)[0]
    assert "DEV_RELEASES" in ps1


def test_first_run_missing_pip_is_handled_by_exit_code_not_ps_stderr():
    ps1 = (REPO / "scripts" / "dev_portable.ps1").read_text(encoding="utf-8")
    assert "function Test-PythonImports" in ps1
    assert "function Test-PythonModule" in ps1
    assert "if (Test-PythonModule 'pip')" in ps1
    assert "if (-not (Test-PythonModule 'pytest'))" in ps1
    assert "-m pip --version *> $null" not in ps1
    assert "-m pytest --version *> $null" not in ps1
    assert "$ErrorActionPreference = 'Continue'" in ps1
    assert "$code = $LASTEXITCODE" in ps1
