# GameTranslator v3 (스크립트판) - Unity(Mono): BepInEx+XUnity 설치 / RPG Maker MV·MZ: 데이터 직접 번역
# 게임 폴더의 엔진을 판별하고, Unity(Mono)면 BepInEx 5.4.22 + XUnity.AutoTranslator 5.6.3 을 설치해서
# 원래 게임 exe 를 그대로 실행해도 인게임 일본어가 한국어로 번역되게 한다.
# 이 스크립트는 공식 배포 파일(payload 폴더)을 게임 폴더로 복사만 한다.
param([string]$Target = '', [switch]$Auto)   # -Auto: GameShelf 가 압축 풀기 직후 호출 (확인 질문 생략)

$ErrorActionPreference = 'Stop'
$ToolDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Payload = Join-Path $ToolDir 'payload'
$Title   = 'GameTranslator'
$Marker  = 'BepInEx\GameTranslator_installed.txt'

# ------------------------------------------------------------------ 대화상자

if ($env:GT_TEST) {
    # 테스트용: 창 대신 로그에 기록하고 GT_ANSWERS 의 답을 차례로 사용
    $script:answers = @(); if ($env:GT_ANSWERS) { $script:answers = $env:GT_ANSWERS -split ',' }
    function Show-Msg([string]$text, [string]$buttons = 'OK', [string]$icon = 'Information') {
        Add-Content -LiteralPath $env:GT_TEST -Value "[BOX:$buttons] $text`n----" -Encoding UTF8
        if ($buttons -eq 'OK') { return 'OK' }
        if ($script:answers.Count -gt 0) { $a = $script:answers[0]; $script:answers = @($script:answers | Select-Object -Skip 1); return $a }
        return 'Yes'
    }
    function Pick-Folder { return '' }
} else {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.Application]::EnableVisualStyles()
    function Show-Msg([string]$text, [string]$buttons = 'OK', [string]$icon = 'Information') {
        $owner = New-Object System.Windows.Forms.Form -Property @{ TopMost = $true }
        $r = [System.Windows.Forms.MessageBox]::Show($owner, $text, $Title, $buttons, $icon)
        $owner.Dispose()
        return $r.ToString()
    }
    function Pick-Folder {
        $d = New-Object System.Windows.Forms.FolderBrowserDialog
        $d.Description = '번역을 적용할 게임 폴더(게임 exe가 있는 폴더)를 고르세요'
        if ($d.ShowDialog() -eq 'OK') { return $d.SelectedPath }
        return ''
    }
}

function Fail([string]$text) { Show-Msg $text 'OK' 'Error' | Out-Null; exit 1 }

# ------------------------------------------------------------------ 유틸

function Test-Ascii([string]$s) { return ($s -cmatch '^[\x20-\x7E]*$') }
function Has-File([string]$dir, [string]$rel) { return Test-Path -LiteralPath (Join-Path $dir $rel) -PathType Leaf }
function Has-Dir([string]$dir, [string]$rel)  { return Test-Path -LiteralPath (Join-Path $dir $rel) -PathType Container }
function Has-Match([string]$dir, [string]$sub, [string]$filter) {
    $d = if ($sub) { Join-Path $dir $sub } else { $dir }
    if (-not (Test-Path -LiteralPath $d -PathType Container)) { return $false }
    return [bool](Get-ChildItem -LiteralPath $d -Filter $filter -File -ErrorAction SilentlyContinue | Select-Object -First 1)
}
function Stamp { return (Get-Date).ToString('yyyyMMdd_HHmmss') }

function Get-PeBits([string]$path) {
    try {
        $fs = [IO.File]::OpenRead($path); $buf = New-Object byte[] 4096; $n = $fs.Read($buf, 0, 4096); $fs.Close()
        if ($n -lt 64 -or $buf[0] -ne 0x4D -or $buf[1] -ne 0x5A) { return 0 }
        $off = [BitConverter]::ToInt32($buf, 0x3C)
        if ($off + 6 -gt $n) { return 0 }
        $m = [BitConverter]::ToUInt16($buf, $off + 4)
        if ($m -eq 0x8664) { return 64 }
        if ($m -eq 0x14C)  { return 32 }
    } catch {}
    return 0
}

function Same-File([string]$a, [string]$b) {
    if (-not (Test-Path -LiteralPath $a) -or -not (Test-Path -LiteralPath $b)) { return $false }
    return (Get-FileHash -LiteralPath $a -Algorithm SHA256).Hash -eq (Get-FileHash -LiteralPath $b -Algorithm SHA256).Hash
}

# src 폴더 내용을 dest 로 덮어쓰기 복사 (하위 폴더 포함)
function Copy-Tree([string]$src, [string]$dest) {
    foreach ($f in Get-ChildItem -LiteralPath $src -Recurse -File) {
        $rel = $f.FullName.Substring($src.Length).TrimStart('\', '/')
        $to  = Join-Path $dest $rel
        $toDir = Split-Path -Parent $to
        if (-not (Test-Path -LiteralPath $toDir)) { [IO.Directory]::CreateDirectory($toDir) | Out-Null }
        try { Copy-Item -LiteralPath $f.FullName -Destination $to -Force }
        catch { Fail "파일을 복사하지 못했습니다:`n$to`n`n게임이 실행 중이면 끄고 다시 해주세요.`n($($_.Exception.Message))" }
    }
}

# ------------------------------------------------------------------ 엔진 판별

function Find-UnityExe([string]$dir) {
    return @(Get-ChildItem -LiteralPath $dir -Filter *.exe -File -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -notmatch '^UnityCrashHandler' -and (Has-Dir $dir ($_.BaseName + '_Data'))
    })
}

function Get-Engine([string]$dir) {
    $u = Find-UnityExe $dir
    if ($u.Count -gt 0 -or (Has-File $dir 'UnityPlayer.dll')) {
        if ((Has-File $dir 'GameAssembly.dll') -or ($u.Count -eq 1 -and (Has-Dir $dir ($u[0].BaseName + '_Data\il2cpp_data')))) { return 'UnityIL2CPP' }
        return 'UnityMono'
    }
    if (Has-File $dir 'www\js\rpg_core.js') { return 'RPGMV' }
    if ((Has-File $dir 'js\rmmz_core.js') -or (Has-File $dir 'www\js\rmmz_core.js')) { return 'RPGMZ' }
    if ((Has-Match $dir '' '*.rgss3a') -or (Has-Match $dir '' '*.rgss2a') -or (Has-Match $dir '' '*.rgssad') -or (Has-Match $dir '' 'RGSS*.dll') -or (Has-Match $dir 'System' 'RGSS*.dll')) { return 'RGSS' }
    if ((Has-Match $dir '' '*.wolf') -or (Has-Match $dir 'Data' '*.wolf') -or (Has-File $dir 'GuruguruSMF4.dll')) { return 'Wolf' }
    if (Has-Match $dir '' '*.xp3') { return 'KiriKiri' }
    if ((Has-Dir $dir 'renpy') -or (Has-Match $dir 'game' '*.rpa') -or (Has-Match $dir 'game' '*.rpyc')) { return 'RenPy' }
    if (Has-Dir $dir 'Engine\Binaries') { return 'Unreal' }
    if (Has-Match $dir '' '*.pck') { return 'Godot' }
    if ((Has-File $dir 'resources\app.asar') -or (Has-Dir $dir 'resources\app')) { return 'Electron' }
    return 'Unknown'
}

$EngineName = @{
    UnityMono = 'Unity (Mono)'; UnityIL2CPP = 'Unity (IL2CPP)'; RPGMV = 'RPG Maker MV'; RPGMZ = 'RPG Maker MZ'
    RGSS = 'RPG Maker XP/VX/VX Ace'; Wolf = 'WOLF RPG Editor'; KiriKiri = 'KiriKiri (吉里吉里)'; RenPy = "Ren'Py"
    Unreal = 'Unreal Engine'; Godot = 'Godot'; Electron = 'HTML5 / Electron 계열 (TyranoScript 등)'; Unknown = '알 수 없음'
}

function Get-Advice([string]$e) {
    switch ($e) {
        'UnityIL2CPP' { return "이 버전은 Unity Mono 게임만 자동 설치합니다.`nIL2CPP는 BepInEx 6 + XUnity(IL2CPP판)가 필요해서 다음 버전에서 지원할 예정입니다.`n`n지금은 LunaTranslator를 쓰세요:`nhttps://github.com/HIllya51/LunaTranslator/releases" }
        { $_ -in 'RGSS', 'Wolf' } { return "이 엔진은 게임 파일 속 대사를 뽑아 번역한 뒤 다시 넣는 방식이 맞습니다.`n`nTranslator++ :`nhttps://dreamsavior.net/translator-plusplus/" }
        'KiriKiri' { return "KiriKiri는 LunaTranslator의 HOOK + 내장 번역(embed)을 먼저 시도하세요.`n`nhttps://github.com/HIllya51/LunaTranslator/releases`nhttps://docs.lunatranslator.org/ko/" }
        'RenPy'    { return "Ren'Py는 엔진 자체 번역 기능(SDK → Generate Translations)으로 tl\korean 을 만들어 채우는 방식입니다.`n`nhttps://www.renpy.org/latest.html" }
        default    { return "자동 설치를 지원하지 않는 엔진입니다.`nLunaTranslator(HOOK 또는 OCR)를 쓰세요:`n`nhttps://github.com/HIllya51/LunaTranslator/releases" }
    }
}

# ------------------------------------------------------------------ 예전 AsciiFix 흔적 되돌리기

function Undo-AsciiFix([string]$dir) {
    $nameFile = Join-Path $dir 'AsciiFix_original_name.txt'
    $realExe  = Join-Path $dir 'UnityGame.exe'
    if (-not (Test-Path -LiteralPath $nameFile) -or -not (Test-Path -LiteralPath $realExe)) { return }
    $orig = [IO.File]::ReadAllText($nameFile, [Text.Encoding]::Unicode).Trim([char]0, ' ')
    if (-not $orig) { Fail 'AsciiFix 기록 파일이 비어 있어 되돌리지 못했습니다.' }
    if (-not $Auto -and (Show-Msg "이 폴더에 예전 AsciiFix 설정이 남아 있습니다.`n(UnityGame.exe ↔ $orig.exe)`n`n원래대로 되돌리고 계속할까요?" 'YesNo' 'Question') -ne 'Yes') { exit 0 }
    $origExe = Join-Path $dir "$orig.exe"
    try {
        if (Test-Path -LiteralPath $origExe) { Remove-Item -LiteralPath $origExe -Force }
        Rename-Item -LiteralPath $realExe -NewName "$orig.exe"
        $rd = Join-Path $dir 'UnityGame_Data'
        if (Test-Path -LiteralPath $rd) { Rename-Item -LiteralPath $rd -NewName "${orig}_Data" }
        foreach ($n in 'AsciiFix_original_name.txt', 'AsciiFix.exe', 'asciifix.c', 'AsciiLaunch.bat') {
            $p = Join-Path $dir $n; if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Force }
        }
    } catch { Fail "AsciiFix 설정을 되돌리지 못했습니다. 게임을 끄고 다시 해주세요.`n($($_.Exception.Message))" }
}

# ------------------------------------------------------------------ AutoTranslatorConfig.ini

function Set-IniValue([string[]]$lines, [string]$section, [string]$key, [string]$value, [bool]$onlyIfEmpty) {
    $out = New-Object System.Collections.Generic.List[string]
    $inSec = $false; $sawSec = $false; $done = $false
    foreach ($line in $lines) {
        $t = $line.Trim().TrimStart([char]0xFEFF)
        if ($t.StartsWith('[')) {
            if ($inSec -and -not $done) { $out.Add("$key=$value"); $done = $true }
            $inSec = ($t -ieq "[$section]"); if ($inSec) { $sawSec = $true }
        } elseif ($inSec -and -not $done -and $t -match "^$([regex]::Escape($key))\s*=(.*)$") {
            $done = $true
            if (-not $onlyIfEmpty -or $Matches[1].Trim() -eq '') { $out.Add("$key=$value"); continue }
        }
        $out.Add($line)
    }
    if (-not $done) {
        if (-not $sawSec) { $out.Add(''); $out.Add("[$section]") }
        $out.Add("$key=$value")
    }
    return ,$out.ToArray()
}

function Write-XUnityConfig([string]$dir) {
    $p = Join-Path $dir 'BepInEx\config\AutoTranslatorConfig.ini'
    $lines = @()
    if (Test-Path -LiteralPath $p) { $lines = [IO.File]::ReadAllLines($p) }
    else { [IO.Directory]::CreateDirectory((Split-Path -Parent $p)) | Out-Null }
    $lines = Set-XUnityEngine $lines       # GameShelf ⚙설정에서 고른 엔진 (Engines.ps1)
    $lines = Set-IniValue $lines 'General' 'Language' 'ko' $false
    $lines = Set-IniValue $lines 'General' 'FromLanguage' 'ja' $false
    [IO.File]::WriteAllLines($p, $lines, (New-Object Text.UTF8Encoding $false))
}

# ------------------------------------------------------------------ 설치 / 제거 / 점검

function Install-Translator([string]$dir, [int]$bits) {
    $arch = if ($bits -eq 32) { 'BepInEx_x86' } else { 'BepInEx_x64' }
    $core = Join-Path $dir 'BepInEx\core'
    if (Test-Path -LiteralPath $core) {
        try { Rename-Item -LiteralPath $core -NewName ("core_old_" + (Stamp)) }
        catch { Fail "기존 BepInEx\core 폴더를 백업하지 못했습니다. 게임을 끄고 다시 해주세요." }
    }
    foreach ($n in 'winhttp.dll', 'doorstop_config.ini') {
        $cur = Join-Path $dir $n; $new = Join-Path (Join-Path $Payload $arch) $n; $bak = "$cur.gt_backup"
        if ((Test-Path -LiteralPath $cur) -and -not (Same-File $cur $new) -and -not (Test-Path -LiteralPath $bak)) { Copy-Item -LiteralPath $cur -Destination $bak }
    }
    Copy-Tree (Join-Path $Payload $arch) $dir
    Copy-Tree (Join-Path $Payload 'XUnity') $dir
    try { Write-XUnityConfig $dir } catch { Fail "번역 설정 파일(AutoTranslatorConfig.ini)을 쓰지 못했습니다.`n($($_.Exception.Message))" }
    Set-Content -LiteralPath (Join-Path $dir $Marker) -Value "GameTranslator v3: BepInEx 5.4.22 + XUnity.AutoTranslator 5.6.3 (ja -> ko, $(Get-XUnityEndpoint))" -Encoding UTF8
}

function Uninstall-Translator([string]$dir) {
    try {
        foreach ($n in 'winhttp.dll', 'doorstop_config.ini') {
            $p = Join-Path $dir $n; $bak = "$p.gt_backup"
            if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Force }
            if (Test-Path -LiteralPath $bak) { Rename-Item -LiteralPath $bak -NewName $n }
        }
        $b = Join-Path $dir 'BepInEx'
        if (Test-Path -LiteralPath $b) { Rename-Item -LiteralPath $b -NewName ("BepInEx_removed_" + (Stamp)) }
    } catch { Fail "제거하지 못했습니다. 게임을 끄고 다시 해주세요.`n($($_.Exception.Message))" }
}

function Get-Diagnosis([string]$dir) {
    $since = (Get-Item -LiteralPath (Join-Path $dir $Marker)).LastWriteTime
    if (-not (Has-File $dir 'winhttp.dll')) { return "winhttp.dll 이 없습니다. 백신이 지웠을 수 있습니다.`nWindows 보안 → 보호 기록을 확인한 뒤 재설치하세요." }

    $pre = Get-ChildItem -LiteralPath $dir -Filter 'preloader_*.log' -File -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -gt $since } | Sort-Object LastWriteTime | Select-Object -Last 1
    if ($pre) {
        if (-not (Test-Ascii $dir)) { return "BepInEx가 시작하다 실패했습니다.`n`n원인: 게임 경로에 한글/일본어가 있습니다.`n$dir`n`n경로의 모든 폴더 이름을 영문/숫자로 바꿔주세요." }
        $t = Get-Content -LiteralPath $pre.FullName -Raw
        if ($t -match 'IL Compile Error') { return "BepInEx가 시작하다 Harmony 오류로 실패했습니다.`n($($pre.FullName))`n`n경로는 영문이므로 다른 원인입니다. 이 로그 파일을 Claude에게 보여주세요." }
        return "BepInEx가 시작하다 실패했습니다.`n`n로그: $($pre.FullName)`n이 파일을 Claude에게 보여주세요."
    }
    $log = Join-Path $dir 'BepInEx\LogOutput.log'
    if (-not (Test-Path -LiteralPath $log) -or (Get-Item -LiteralPath $log).LastWriteTime -le $since) {
        return "설치는 되어 있지만 아직 게임을 실행한 기록이 없습니다.`n`n게임 exe를 한 번 실행하고, 일본어가 나오는 화면까지 간 뒤 다시 점검하세요."
    }
    $t = Get-Content -LiteralPath $log -Raw
    $xu  = $t -match 'Loaded XUnity\.AutoTranslator'
    $err = ($t -match '\[Error\s*:XUnity') -or ($t -match '\[Fatal')
    if ($xu -and -not $err) { return "정상입니다. BepInEx와 XUnity.AutoTranslator가 로드되었습니다.`n`n게임 안에서:`n  Alt+T  원문/번역 전환`n  Alt+0  XUnity 설정 창`n`n한글이 □□로 나오면 그 화면을 Claude에게 보여주세요(글꼴 설정 필요)." }
    if ($xu) { return "XUnity는 로드됐지만 로그에 오류가 있습니다.`n`n$log`n이 파일을 Claude에게 보여주세요." }
    return "BepInEx는 실행됐지만 XUnity.AutoTranslator가 로드되지 않았습니다.`n`n$log`n이 파일을 Claude에게 보여주세요."
}

# ------------------------------------------------------------------ main

. (Join-Path $ToolDir 'Engines.ps1')

if (-not (Test-Path -LiteralPath (Join-Path $Payload 'BepInEx_x64\winhttp.dll'))) {
    Fail "payload 폴더의 파일이 없습니다.`n$Payload`n`nGameTranslator 폴더를 통째로 다시 풀어주세요. (백신이 지웠다면 보호 기록에서 복원)"
}

$dir = $Target.Trim().Trim('"')
if ($dir -and (Test-Path -LiteralPath $dir -PathType Leaf)) { $dir = Split-Path -Parent $dir }   # exe 를 넘겨도 됨
if (-not $dir) { $dir = Pick-Folder }
if (-not $dir) { exit 0 }
if (-not (Test-Path -LiteralPath $dir -PathType Container)) { Fail "폴더를 찾지 못했습니다:`n$dir" }
$dir = (Resolve-Path -LiteralPath $dir).ProviderPath.TrimEnd('\')

Undo-AsciiFix $dir

$engine = Get-Engine $dir
if ($engine -eq 'Unknown') {
    # DLsite 처럼 게임 본체가 한두 단계 아래 폴더에 있는 경우 찾아 들어가기
    $found = @()
    foreach ($d1 in Get-ChildItem -LiteralPath $dir -Directory -ErrorAction SilentlyContinue) {
        if ((Get-Engine $d1.FullName) -ne 'Unknown') { $found += $d1.FullName; continue }
        foreach ($d2 in Get-ChildItem -LiteralPath $d1.FullName -Directory -ErrorAction SilentlyContinue) {
            if ((Get-Engine $d2.FullName) -ne 'Unknown') { $found += $d2.FullName }
        }
    }
    if ($found.Count -eq 1) { $dir = $found[0]; $engine = Get-Engine $dir }
}
if ($engine -eq 'RPGMV' -or $engine -eq 'RPGMZ') {
    . (Join-Path $ToolDir 'RpgMaker.ps1')
    Invoke-RpgTranslate $dir $EngineName[$engine] $Auto
    exit 0
}
if ($engine -ne 'UnityMono') {
    if ($Auto) { exit 3 }    # 자동 모드: 지원하지 않는 엔진은 조용히 끝냄 (오른쪽 클릭으로 실행하면 안내가 뜸)
    Show-Msg "폴더: $dir`n엔진: $($EngineName[$engine])`n`n$(Get-Advice $engine)" | Out-Null
    exit 0
}

$exes = Find-UnityExe $dir
if ($exes.Count -ne 1) { Fail $(if ($exes.Count -eq 0) { 'Unity 게임 exe를 찾지 못했습니다.' } else { 'Unity 게임 exe 후보가 여러 개라 고를 수 없습니다: ' + (($exes | ForEach-Object Name) -join ', ') }) }
$exe  = $exes[0].Name
$bits = Get-PeBits $exes[0].FullName; if ($bits -eq 0) { $bits = 64 }

# 이미 설치됨 → 점검 / 재설치 / 제거
if (Has-File $dir $Marker) {
    if ($Auto) { exit 0 }
    $r = Show-Msg "[$exe] 상태 점검`n`n$(Get-Diagnosis $dir)`n`n────────────`n예 = 재설치    아니요 = 제거    취소 = 닫기" 'YesNoCancel' 'Information'
    if ($r -eq 'No') {
        if ((Show-Msg "번역(BepInEx + XUnity)을 제거할까요?`nBepInEx 폴더는 지우지 않고 BepInEx_removed_날짜 로 이름만 바꿉니다." 'YesNo' 'Question') -eq 'Yes') {
            Uninstall-Translator $dir
            Show-Msg '제거했습니다. 게임이 원래대로 실행됩니다.' | Out-Null
        }
        exit 0
    }
    if ($r -ne 'Yes') { exit 0 }
}

if (-not (Test-Ascii $dir)) {
    if ($Auto) { Show-Msg "[$exe] 한국어 번역기를 설치하지 않았습니다.`n`n게임 경로에 한글/일본어가 있어 Unity 번역(BepInEx)이 동작하지 않습니다:`n$dir`n`n폴더 이름을 영문/숫자로 바꾼 뒤 GameShelf에서 오른쪽 클릭 → '한국어 번역 설치'를 하세요." 'OK' 'Warning' | Out-Null; exit 4 }
    if ((Show-Msg "게임 경로에 한글/일본어가 있습니다:`n$dir`n`n이 상태에서는 BepInEx가 시작하다 실패합니다.`n경로의 모든 폴더 이름을 영문/숫자로 바꾼 뒤 다시 실행하세요.`n`n그래도 설치만 해둘까요?" 'YesNo' 'Warning') -ne 'Yes') { exit 0 }
}
# 이미 다른 번역 패치나 모드가 깔린 게임인지 확인
$existing = @()
$plug = Join-Path $dir 'BepInEx\plugins'
if (Test-Path -LiteralPath $plug) {
    $others = @(Get-ChildItem -LiteralPath $plug -ErrorAction SilentlyContinue | Where-Object { $_.Name -notmatch '^(XUnity\.|README)' } | ForEach-Object Name)
    if ($others.Count) { $existing += "BepInEx 플러그인: " + ($others -join ', ') }
}
$tl = Join-Path $dir 'BepInEx\Translation'
if (Test-Path -LiteralPath $tl) {
    $manual = @(Get-ChildItem -LiteralPath $tl -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -notmatch '^_(AutoGenerated|Pre|Post|Substitutions)' -and $_.Length -gt 0 })
    if ($manual.Count) { $existing += "직접 만든 번역 파일 $($manual.Count)개 (BepInEx\Translation)" }
}
if ($existing.Count) {
    if ($Auto) { exit 5 }   # 자동 모드: 이미 패치/모드가 있는 게임은 건드리지 않음
    $q = "이미 번역 패치나 모드가 설치된 게임 같습니다:`n- " + ($existing -join "`n- ") + "`n`n계속하면 BepInEx/XUnity만 교체하고 위 파일들은 그대로 둡니다.`n(교체 전 core 폴더와 winhttp.dll 은 백업)`n버전이 바뀌어 기존 패치가 동작하지 않을 수도 있습니다. 계속할까요?"
    if ((Show-Msg $q 'YesNo' 'Warning') -ne 'Yes') { exit 0 }
}
if ((Has-Dir $dir 'MelonLoader') -or (Has-File $dir 'version.dll')) {
    if ($Auto) { exit 4 }
    if ((Show-Msg "MelonLoader 또는 다른 모드 로더(version.dll)가 있습니다.`nBepInEx와 충돌할 수 있습니다. 계속할까요?" 'YesNo' 'Warning') -ne 'Yes') { exit 0 }
}

$q = "폴더: $dir`n엔진: Unity (Mono), ${bits}비트`n게임: $exe`n`n다음을 설치합니다:`n- BepInEx 5.4.22 (모드 로더)`n- XUnity.AutoTranslator 5.6.3 (일본어 → 한국어, $(Get-EngineLabel))$(if ((Get-GTConfig).engine -eq 'local') { "`n  ※ PC AI 번역은 GameShelf가 켜져 있을 때만 동작합니다" })`n`n기존 plugins / 번역 기록 / 설정은 유지하고, 바꾸는 파일은 백업합니다.`n진행할까요?"
if (-not $Auto -and (Show-Msg $q 'YesNo' 'Question') -ne 'Yes') { exit 0 }

Install-Translator $dir $bits

$localNote = if ((Get-GTConfig).engine -eq 'local') { "`n※ PC AI 번역: GameShelf를 켜 둔 상태에서 게임을 실행하세요.`n" } else { '' }
Show-Msg "설치 완료! ($(Get-EngineLabel))`n$localNote`n이제 '$exe' 를 그대로 실행하세요.`n처음 보는 문장은 잠깐 일본어로 보였다가 한국어로 바뀝니다.`n`n게임 안에서  Alt+T = 원문/번역 전환`n`n한 번 플레이한 뒤 같은 버튼을 다시 누르면 정상 동작 여부를 점검해 줍니다." | Out-Null
exit 0
