# TALUDE STUDIO — MODO DEV PORTÁTIL (Windows x64)

**Objetivo:** abrir o Talude Studio diretamente a partir dos ficheiros Python, sem PyInstaller e sem instalações globais. Os algoritmos já integrados em `src/` e `core/`, o editor, a interface Classic UX e o Safe Core mantêm-se intactos.

## Duplo clique

1. Descarregar o **ZIP integral** da branch `v2-experimental-raw-tin-mst` do repositório `binopinto07-lang/talude-` e extrair para uma pasta curta, por exemplo `C:\TALUDE_STUDIO_DEV`. Não executar o BAT dentro do visualizador de ZIP.
2. Abrir `INICIAR_TALUDE_DEV.bat`.
3. **Só na primeira execução de um ZIP de código-fonte**, com Internet, o preparador obtém o Python 3.12.10 embeddable para Windows x64, prepara pip de forma local, instala **wheels pré-compilados** de `requirements.txt` e prepara o Potree/PotreeConverter com o `studio/scripts/bootstrap_vendor.ps1` original. Não compila o Talude nem instala Python no Windows. Isto não faz parte de um ZIP de código-fonte normal do GitHub.
4. Nas execuções seguintes, desde que não se alterem as dependências, basta duplo clique, mesmo sem Internet. O arranque invoca `python.exe -B talude_studio.py`, diretamente do código-fonte, e não o `.exe` PyInstaller.

### O ZIP que JÁ contém todas as dependências

Após a preparação inicial no **Windows x64**, executar uma vez `CRIAR_ZIP_DEV_COMPLETO.bat`. O preparador realiza também o `--self-test` original na pasta de origem **e na cópia independente**, e cria:

`DEV_RELEASES\TALUDE_STUDIO_DEV_WINDOWS_X64_COMPLETO.zip`

Esse ZIP contém o runtime Windows Python 3.12.10, `Lib/site-packages`, Potree, PotreeConverter, o código-fonte, os algoritmos já integrados, scripts de arranque, documentação e manifesto com versões. Basta distribuí-lo, extrair e abrir `INICIAR_TALUDE_DEV.bat` em outro PC Windows x64 compatível. Não precisa de repetir a preparação nem de acesso à Internet **se o ZIP tiver sido criado e validado com sucesso**.

> **Não confundir:** o ZIP automático **Code → Download ZIP** do GitHub contém o código-fonte e os dois BATs, mas não contém as dependências binárias (`.talude_dev/` e `studio/vendor/` estão deliberadamente excluídos do Git). Para um ZIP pronto a abrir completamente offline, utilizar o ZIP em `DEV_RELEASES` gerado no Windows e colocá-lo numa Release ou pasta partilhada. Não carregar as DLLs/wheels no histórico do Git.

## Estrutura depois de preparar

```text
TALUDE_STUDIO_DEV/
├── INICIAR_TALUDE_DEV.bat
├── CRIAR_ZIP_DEV_COMPLETO.bat
├── talude_studio.py
├── .talude_dev/
│   ├── runtime/
│   │   ├── python.exe
│   │   ├── python312._pth
│   │   └── Lib/site-packages/    ← dependências pré-instaladas
│   ├── DEPENDENCIAS_OK.json
│   ├── cache/                  ← apenas no pacote de preparação, não no ZIP final
│   └── logs/
├── src/talude_v1/               ← baseline funcional em código
├── src/talude_v2/               ← motor R20 e experiências independentes
├── core/
├── studio/
│   ├── desktop/
│   ├── backend/
│   ├── viewer/                ← Classic UX
│   └── vendor/
│       ├── potree/
│       └── potreeconverter/
└── scripts/dev_portable.ps1
```

## Desenvolvimento normal

- Alterar os ficheiros `.py` / `.js` / `.css`, guardar e voltar a abrir o BAT. Um novo EXE não é necessário.
- As dependências são reutilizadas enquanto o SHA-256 de `requirements.txt` se mantiver e os imports passarem.
- R19/R20 permanecem **EXPERIMENTAIS**, não ligados ao AUTO de produção. Pode continuar a chamar manualmente o preview `scripts/preview_r20_independent.py` sem alterar `global_auto.py`.
- O Source Guard R18, a baseline 1.1.7, o Safe Core, o editor, as exportações e o Local Build Manager não são alterados pelo novo iniciador.
- O BAT é independente do `LocalBuildManager__TALUDE_STUDIO`. O LBM continua disponível para a compilação final.

## Diagnóstico e operações opcionais

```powershell
# Na raiz do projeto, em PowerShell:
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\dev_portable.ps1 -Action prepare
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\dev_portable.ps1 -Action selftest
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\dev_portable.ps1 -Action tests
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\dev_portable.ps1 -Action package
```

- Logs: `.talude_dev\logs\DEV_YYYYMMDD_HHMMSS.log`.
- Relatório de validação original: `TALUDE_V1_SELF_TEST.txt`.
- Ao atualizar o código pelo GitHub, **não eliminar `.talude_dev/` ou `studio/vendor/`**: são as dependências e assets locais já preparados.
- Se o primeiro download falhar, confirmar Internet/firewall; pode apagar apenas o download corrompido em `.talude_dev/cache`, sem tocar nos motores.
- Utilizar uma pasta curta, sem extrair por cima de um projeto com dados pessoais de levantamentos.
- A cópia em `DEV_RELEASES` é uma distribuição; os dados LAS/LAZ, projetos locais, pasta `.git` e extrações de terceiros não são incluídos automaticamente.

**Limite de validação:** o iniciador foi preparado com inspeção estática. A instalação efetiva das rodas Windows, a inicialização QtWebEngine num PC Windows e a geração do ZIP completo exigem executar os BATs nesse ambiente. Não considerar essa validação realizada até obter os logs do PC.
