# GameTranslator 처음 설정: 폴더 오른쪽 클릭 메뉴 등록 + 게임 폴더에 넣을 "번역설치.bat" 버튼 만들기
# 관리자 권한 필요 없음 (현재 사용자에게만 등록)
param([switch]$Remove)

$ErrorActionPreference = 'Stop'
$ToolDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Main    = Join-Path $ToolDir 'GameTranslator.ps1'
Add-Type -AssemblyName System.Windows.Forms
function Say([string]$t, [string]$icon = 'Information') { [System.Windows.Forms.MessageBox]::Show($t, 'GameTranslator', 'OK', $icon) | Out-Null }

$keys = @('HKCU:\Software\Classes\Directory\shell\GameTranslator',
          'HKCU:\Software\Classes\Directory\Background\shell\GameTranslator')

if ($Remove) {
    foreach ($k in $keys) { if (Test-Path -LiteralPath $k) { Remove-Item -LiteralPath $k -Recurse -Force } }
    Say '오른쪽 클릭 메뉴를 제거했습니다.'
    exit 0
}

if (-not (Test-Path -LiteralPath $Main)) { Say "GameTranslator.ps1 이 없습니다.`n$ToolDir" 'Error'; exit 1 }

# 인터넷에서 받은 파일 표시(차단) 해제
Get-ChildItem -LiteralPath $ToolDir -Recurse -File | Unblock-File -ErrorAction SilentlyContinue

# 1) 오른쪽 클릭 메뉴: 폴더 위에서(%1), 폴더 빈 곳에서(%V)
$ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$argMap = @{ $keys[0] = '%1'; $keys[1] = '%V' }
foreach ($k in $keys) {
    New-Item -Path $k -Force | Out-Null
    Set-ItemProperty -LiteralPath $k -Name '(default)' -Value '한국어 번역 설치 (GameTranslator)'
    Set-ItemProperty -LiteralPath $k -Name 'Icon' -Value "$ps,0"
    New-Item -Path "$k\command" -Force | Out-Null
    $cmd = "`"$ps`" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Main`" `"$($argMap[$k])`""
    Set-ItemProperty -LiteralPath "$k\command" -Name '(default)' -Value $cmd
}

# 2) 게임 폴더에 복사해 넣는 버튼: 자기 폴더를 대상으로 GameTranslator.ps1 실행
#    cmd 는 bat 파일을 시스템 코드페이지(한국어 Windows = CP949)로 읽으므로 그 인코딩으로 저장
$bat = "@echo off`r`n" +
       "start `"`" `"$ps`" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Main`" `"%~dp0.`"`r`n"
$batPath = Join-Path $ToolDir '번역설치.bat'
[IO.File]::WriteAllText($batPath, $bat, [Text.Encoding]::Default)

Say ("설정 완료!`n`n" +
     "방법 1) 게임 폴더를 오른쪽 클릭 → '한국어 번역 설치 (GameTranslator)'`n" +
     "        (Windows 11은 '더 많은 옵션 표시' 안에 있습니다)`n`n" +
     "방법 2) 이 폴더에 생긴 '번역설치.bat' 을 게임 폴더에 복사해 넣고 더블클릭`n`n" +
     "주의: GameTranslator 폴더를 다른 곳으로 옮기면 Setup.bat 을 다시 실행해야 합니다.")
