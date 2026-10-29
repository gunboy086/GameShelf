# GameShelf 배포용 zip 만들기
# 1) GameShelf.exe 를 새로 만들고
# 2) 개인 정보(게임 목록, API 키, 내 PC 경로)가 들어간 파일을 뺀 채로
# 3) GameShelf_배포.zip 을 만든다.
param([switch]$NoExplorer)
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
Set-Location -LiteralPath $here

Write-Host '[1/3] GameShelf.exe 만드는 중...'
python -m pip install --quiet PySide6 requests pyinstaller
$iconArgs = @()
if (Test-Path -LiteralPath (Join-Path $here 'GameShelf.ico')) {
    $iconArgs = @('--icon', (Join-Path $here 'GameShelf.ico'), '--add-data', ((Join-Path $here 'GameShelf.ico') + ';.'))
}
python -m PyInstaller --noconfirm --noconsole --onefile --name GameShelf @iconArgs (Join-Path $here 'GameShelf.py')
$exe = Join-Path $here 'dist\GameShelf.exe'
if (-not (Test-Path -LiteralPath $exe)) { Write-Host '[실패] exe를 만들지 못했습니다. 위 메시지를 확인하세요.'; exit 1 }

Write-Host '[2/3] 배포용 폴더 정리 중 (개인 파일 제외)...'
$stage = Join-Path $here 'release\GameShelf'
if (Test-Path -LiteralPath (Join-Path $here 'release')) { Remove-Item -LiteralPath (Join-Path $here 'release') -Recurse -Force }
New-Item -ItemType Directory -Path $stage | Out-Null
Copy-Item -LiteralPath $exe -Destination $stage
Copy-Item -LiteralPath (Join-Path $here 'GameTranslator') -Destination $stage -Recurse
foreach ($f in @('읽어주세요.txt', 'LICENSE', 'licenses')) {
    $p = Join-Path $here $f
    if (Test-Path -LiteralPath $p) { Copy-Item -LiteralPath $p -Destination $stage -Recurse }
}
# 개인 정보가 들어 있는 파일은 절대 넣지 않음
#  - engine.json : 번역 API 키 / 번역설치.bat : 내 PC 경로 / *.bak : 백업
Get-ChildItem -LiteralPath $stage -Recurse -File |
    Where-Object { $_.Name -in @('engine.json', '번역설치.bat') -or $_.Name -like '*.bak*' } |
    Remove-Item -Force
# GameShelf_data(게임 목록·API 키)는 처음부터 복사하지 않음

Write-Host '[3/3] zip 만드는 중...'
$zip = Join-Path $here 'GameShelf_배포.zip'
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
Compress-Archive -Path $stage -DestinationPath $zip
Remove-Item -LiteralPath (Join-Path $here 'release') -Recurse -Force
Write-Host ''
Write-Host "완료: $zip"
Write-Host '이 zip 파일 하나만 올리면 됩니다. (GameShelf_data 폴더는 들어 있지 않습니다)'
if (-not $NoExplorer) { Start-Process explorer.exe "/select,`"$zip`"" }
