# Local Build Manager V0.1.11

Aplicação desktop Windows criada para substituir o uso diário de GitHub Actions no desenvolvimento do `Cloud_to_lines`.

## O que faz

- Mantém o GitHub apenas como repositório/versionamento.
- Executa localmente verificação de ambiente, testes, build, self-test, packaging ZIP e publicação de release.
- Mostra logs em tempo real.
- Guarda logs persistentes em `<repo>\logs\local_build_manager`.
- Permite repetir a etapa falhada ou continuar da etapa seguinte.
- Mantém builds em `<repo>\builds\latest` e arquivo em `<repo>\builds\archive`.
- Não grava tokens no código; a publicação usa a sessão autenticada do `gh` CLI.
- Suporta novos projetos através de ficheiros JSON em `projects\`.

## Arranque mais simples

1. Extrair esta pasta para uma localização permanente, por exemplo `C:\LocalBuildManager`.
2. Executar `START_LOCAL_BUILD_MANAGER.bat`.
3. Selecionar o projeto `Cloud_to_lines`.
4. Clicar `Selecionar pasta...` e escolher o clone local do repositório.
5. Clicar `Verificar ambiente`.
6. Quando estiver tudo necessário OK, usar `BUILD + TESTES`.

## Ferramentas esperadas no Windows

Obrigatórias para o pipeline Cloud_to_lines atual:

- Python 3.12
- Git
- Node.js
- PowerShell
- 7-Zip (`7z.exe` no PATH)
- GitHub CLI (`gh`) para publicação de releases

Opcionais/futuras:

- PDAL
- Open3D
- NVIDIA/CUDA

## Saídas

Build validado mais recente:

`<repo>\builds\latest\Cloud_to_lines_V2_Windows_x64.zip`

Arquivo histórico:

`<repo>\builds\archive\Cloud_to_lines_V2_YYYYMMDD_HHMMSS.zip`

Logs:

`<repo>\logs\local_build_manager\build_YYYY-MM-DD_HHMMSS.log`

## Segurança GitHub

A aplicação não inclui tokens. Para publicar releases, autenticar uma vez no Windows com:

`gh auth login`

A aplicação usa depois essa sessão local.

## Criar EXE do próprio Local Build Manager

Executar:

`BUILD_LOCAL_BUILD_MANAGER_EXE.bat`

O resultado ficará em:

`dist\LocalBuildManager\LocalBuildManager.exe`

## Nota

Os workflows existentes em `.github/workflows` no `Cloud_to_lines` podem permanecer como fallback. O Local Build Manager não precisa deles para testar/buildar localmente.


## Alterações V0.1.1

- Corrigido o build V2 do PyInstaller quando `--specpath` aponta para `builds\local_work`: `v2/viewer`, `v2/vendor` e `v2/desktop/main.py` passam a ser fornecidos por caminho absoluto do repositório.
- Ambientes Python separados automaticamente: `test` para `requirements-dev.txt` e `v2` para `v2/requirements-v2.txt`.
- Os ambientes do Local Build Manager ficam fora do repositório, em `%LOCALAPPDATA%\LocalBuildManager\venvs`, evitando alterações indesejadas no Git.
- `BUILD + TESTES` executa testes no ambiente `test` e faz o packaging no ambiente `v2`, sem trocar versões de PySide6/laspy no mesmo `.venv`.


## Alterações V0.1.2

- Corrigido erro do `pip` no Windows ao instalar PySide6 em máquinas sem **Long Path support**.
- Os ambientes Python deixam de usar o caminho comprido `%LOCALAPPDATA%\LocalBuildManager\venvs\...`.
- Nova raiz curta por defeito: `%LOCALAPPDATA%\LBM\v\<repo-hash>\test` e `%LOCALAPPDATA%\LBM\v\<repo-hash>\v2`.
- O comprimento do caminho do ambiente passa a ser mostrado no log para diagnóstico.
- Pode definir uma raiz ainda mais curta com `LOCAL_BUILD_MANAGER_VENV_ROOT`, por exemplo `C:\LBM`.
- Os ambientes antigos da V0.1.1 não são reutilizados. Podem ser apagados manualmente depois de confirmar que a V0.1.2 funciona.
- Continua a não ser obrigatório alterar o Registo do Windows nem ativar Long Paths para o pipeline normal do Cloud_to_lines.


## V0.1.3 — seleção automática de projeto

Ao escolher uma pasta Git, o Local Build Manager lê `origin` e muda automaticamente para o perfil correspondente. Isto evita executar o pipeline Cloud_to_lines dentro do repositório Talude V1. O placeholder `%DIST%` também deixou de estar fixo em `Cloud_to_lines_V2` e passa a respeitar `dist_dir_name` do perfil.

## V0.1.4 — ambiente do próprio gestor em caminho curto

- Corrigido o arranque do próprio Local Build Manager em pastas extraídas com caminhos longos.
- O ambiente Python do gestor deixa de ser criado em `.venv` ao lado do ZIP/pasta.
- Novo ambiente fixo curto: `%LOCALAPPDATA%\LBM\m`.
- TEMP/TMP usados durante `pip install` passam para `%LOCALAPPDATA%\LBM\t`.
- Instalações `pip` do gestor usam `--no-cache-dir`, reduzindo caminhos temporários profundos do PySide6.
- O instalador coloca a aplicação em `%LOCALAPPDATA%\LBM\app`.
- Não é necessário ativar Windows Long Paths para o fluxo normal.
- Uma `.venv` antiga dentro da pasta extraída pode ser apagada; a V0.1.4 já não a utiliza.


## V0.1.5 — Talude Studio 3D

- Atualizado o perfil `Talude V1` para `Talude Studio V1 — Crista + Pé`.
- O build Talude inclui agora PySide6 + QtWebEngine, Potree 1.8.2 e PotreeConverter 2.1.
- O pipeline executa o bootstrap `studio/scripts/bootstrap_vendor.ps1` antes do PyInstaller.
- O PyInstaller inclui `studio/viewer` e `studio/vendor` no EXE portátil.
- O self-test valida QtWebEngine, Potree, PotreeConverter e o BREAKLINE_ENGINE_V1.
- O pipeline completo volta a instalar explicitamente as dependências do ambiente de build antes de criar o EXE.

## V0.1.6 — perfil do repositório é autoritativo

Ao selecionar uma pasta Git, o gestor procura `localbuild/<project_id>.json` dentro do próprio repositório. Se existir, essa configuração substitui automaticamente a cópia incluída no Local Build Manager. Assim, um `Git Pull` que altere o pipeline passa a ter efeito imediato sem ser necessário descarregar outra versão do gestor.

Para `talude-`, o gestor usa `localbuild/talude_v1.json` do repositório e valida os caminhos absolutos do PyInstaller antes do build.


## V0.1.7 — catálogo V2 e descoberta por pasta

- Adicionado um terceiro projeto separado no seletor: `Talude Studio V2 — Fases 6–9 · Editor + Export`.
- `Talude Studio V1 — Crista + Pé` continua disponível e não é substituído.
- O gestor descobre automaticamente todos os perfis `localbuild/*.json` existentes numa pasta selecionada, mesmo quando essa pasta veio de um ZIP do GitHub e não contém `.git`.
- Corrigido o caso em que V1 e V2 usam o mesmo `repo_url`: selecionar V2 já não é revertido automaticamente para V1.
- Quando o V2 é introduzido numa atualização do gestor, a pasta anteriormente guardada para V1 pode ser reutilizada automaticamente.
- Compatibilidade transitória com branches V2 antigas que ainda guardavam o perfil como `localbuild/talude_v1.json`.
- Perfis instalados junto ao EXE são atualizados automaticamente quando a versão incluída no novo gestor tem `config_revision` superior.
- O perfil V2 exige os ficheiros críticos `scripts/build_windows.py`, `studio/viewer/vector_editor.js`, `studio/backend/vector_export.py` e `localbuild/talude_v2.json`; se a pasta de código for antiga, o build é bloqueado antes da pipeline com uma mensagem clara.
- O perfil V2 inclui o `builder-preflight` e o builder em caminho curto `%LOCALAPPDATA%\LBM\TaludeStudioBuild`.

### Resultado esperado no seletor

```text
Cloud_to_lines
Talude Studio V1 — Crista + Pé
Talude Studio V2 — Fases 6–9 · Editor + Export
```


## V0.1.8 — SOURCE GUARD

Esta versão impede TEST/BUILD sobre uma pasta ZIP antiga do Talude Studio V2.

O perfil V2 exige agora `localbuild/SOURCE_REVISION.txt` com a revisão esperada.
Se a pasta selecionada não corresponder à revisão atual, a pipeline é bloqueada
antes do pytest/PyInstaller e o log mostra `SOURCE GUARD`.

Isto evita o caso em que o Local Build Manager está atualizado mas a pasta do
repositório foi extraída de um ZIP antigo e contém testes/código já ultrapassados.

## V0.1.9 — Python 3.12 automático

- O Local Build Manager consegue arrancar numa máquina Windows sem `python` nem `py` no PATH.
- `START_LOCAL_BUILD_MANAGER.bat` descarrega o instalador oficial Python 3.12.10 de `python.org` quando necessário.
- O SHA-256 do instalador é validado antes da instalação.
- O Python privado fica em `%LOCALAPPDATA%\LBM\py312` e não é adicionado ao PATH do Windows.
- Perfis com `"auto_bootstrap_python": true` podem pedir ao motor para provisionar o runtime automaticamente antes de criar os venvs de TEST/BUILD.
- A deteção passa a reconhecer `py -3.12`, o runtime privado do LBM, a instalação por utilizador padrão e `python`.


## V0.1.10 — deteção real do runtime + Git opcional em ZIP

- A verificação de ambiente deixa de depender de `python` no PATH e reconhece o runtime privado `%LOCALAPPDATA%\LBM\py312`, o Python base do próprio gestor, `py -3.12` e instalações comuns do Windows.
- Perfis antigos que ainda usam um comando PowerShell `python --version` são tratados semanticamente como verificação Python do LBM.
- Uma pasta extraída de ZIP do GitHub já não é marcada como erro por não ter Git; BUILD/TESTES continuam disponíveis e apenas as operações Git deixam de se aplicar.
- O perfil Talude V2 incluído ativa `auto_bootstrap_python` e usa as verificações `@lbm:python312` e `@lbm:git`.


## V0.1.11 — integrado no Talude Studio

- O Local Build Manager é agora versionado no próprio repositório `talude-`, em `LocalBuildManager__TALUDE_STUDIO/`.
- Para Talude Studio V2, o perfil autoritativo é sempre `localbuild/talude_v2.json` da raiz selecionada.
- A cópia embutida `projects/talude_v2.json` é sincronizada com a mesma revisão para arranque por ZIP.
- Ao descarregar a branch V2 em ZIP já não é necessário descarregar separadamente o gestor de `BINO_APOIO`.
- Fluxo: extrair o ZIP, executar `LocalBuildManager__TALUDE_STUDIO/START_LOCAL_BUILD_MANAGER.bat` e selecionar a raiz do mesmo ZIP.
