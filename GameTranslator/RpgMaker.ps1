# GameTranslator - RPG Maker MV / MZ 모듈
# www\data (MV) 또는 data (MZ) 의 JSON 에서 대사·선택지·아이템·스킬·용어 등을 뽑아 일본어→한국어로 번역해 다시 쓴다.
#  - 원본 data 폴더는 처음 한 번 data_GTbackup 으로 복사해 두고, 번역은 항상 이 원본에서 새로 만든다.
#  - 번역 결과는 게임 폴더의 GameTranslator_RPG\translations.tsv 에 저장 (직접 고친 뒤 '다시 적용' 하면 반영)
# GameTranslator.ps1 이 dot-source 해서 사용한다. (Show-Msg, Fail 함수 필요)

$script:JpRegex   = '[\u3040-\u30FF\u3400-\u9FFF\uFF66-\uFF9F]'
$script:KoRegex   = '[\uAC00-\uD7A3]'    # 한글이 들어 있는 문장은 이미 번역된 것으로 보고 건드리지 않음
$script:CodeRegex = '\\[A-Za-z]+\[[^\]]*\]|\\[A-Za-z]+<|(?<=\\[A-Za-z]+<[^<>]*)>|\\[A-Za-z]+|\\[{}$.|!><^\\]|%\d'

# ------------------------------------------------------------------ JSON 읽기/쓰기 (PS 5.1 / 7 둘 다)

function New-JsonSerializer {
    Add-Type -AssemblyName System.Web.Extensions
    $s = New-Object System.Web.Script.Serialization.JavaScriptSerializer
    $s.MaxJsonLength = [int]::MaxValue
    $s.RecursionLimit = 1000
    return $s
}
function Read-JsonText([string]$text) {
    if ($PSVersionTable.PSVersion.Major -ge 6) { return ConvertFrom-Json -InputObject $text -AsHashtable -Depth 100 -NoEnumerate }
    if (-not $script:Ser) { $script:Ser = New-JsonSerializer }
    return $script:Ser.DeserializeObject($text)
}
function Write-JsonText($obj) {
    if ($PSVersionTable.PSVersion.Major -ge 6) { return ConvertTo-Json -InputObject $obj -Depth 100 -Compress }
    if (-not $script:Ser) { $script:Ser = New-JsonSerializer }
    return $script:Ser.Serialize($obj)
}
$script:Utf8 = New-Object Text.UTF8Encoding $false

# ------------------------------------------------------------------ 번역 캐시 (translations.tsv)

function Esc([string]$s)   { return $s.Replace('\', '\\').Replace("`t", '\t').Replace("`r", '').Replace("`n", '\n') }
function Unesc([string]$s) {
    $sb = New-Object Text.StringBuilder
    for ($i = 0; $i -lt $s.Length; $i++) {
        $c = $s[$i]
        if ($c -eq '\' -and $i + 1 -lt $s.Length) {
            $n = $s[$i + 1]
            if ($n -eq 'n') { [void]$sb.Append("`n"); $i++; continue }
            if ($n -eq 't') { [void]$sb.Append("`t"); $i++; continue }
            if ($n -eq '\') { [void]$sb.Append('\'); $i++; continue }
        }
        [void]$sb.Append($c)
    }
    return $sb.ToString()
}
function Load-Cache([string]$path) {
    $h = New-Object 'System.Collections.Generic.Dictionary[string,string]'
    if (Test-Path -LiteralPath $path) {
        foreach ($line in [IO.File]::ReadAllLines($path, $script:Utf8)) {
            if (-not $line -or $line.StartsWith('#')) { continue }
            $i = $line.IndexOf("`t"); if ($i -lt 1) { continue }
            $k = Unesc $line.Substring(0, $i); $v = Unesc $line.Substring($i + 1)
            $h[$k] = $v
        }
    }
    return ,$h
}
function Save-Cache($h, [string]$path) {
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add('# 원문<TAB>번역  (번역 칸을 고친 뒤 GameTranslator 에서 "다시 적용" 하면 게임에 반영됩니다. \n = 줄바꿈)')
    foreach ($k in $h.Keys) { $lines.Add((Esc $k) + "`t" + (Esc $h[$k])) }
    [IO.File]::WriteAllLines($path, $lines, $script:Utf8)
}

# ------------------------------------------------------------------ 제어문자 보호 (\C[1], \N[1], %1 ...)

function Protect([string]$s, [ref]$codes) {
    $list = New-Object System.Collections.Generic.List[string]
    $out = [regex]::Replace($s, $script:CodeRegex, { param($m) $list.Add($m.Value); return '{' + ($list.Count - 1) + '}' })
    $codes.Value = $list
    return $out
}
function Unprotect([string]$s, $codes) {
    $used = @{}
    $out = [regex]::Replace($s, '\{\s*(\d+)\s*\}', {
        param($m) $i = [int]$m.Groups[1].Value
        if ($i -lt $codes.Count) { $used[$i] = $true; return $codes[$i] } else { return '' }
    })
    for ($i = 0; $i -lt $codes.Count; $i++) { if (-not $used[$i]) { $out += $codes[$i] } }   # 번역기가 지운 코드는 끝에 붙임
    return $out
}

# ------------------------------------------------------------------ 번역기 (엔진은 Engines.ps1 / GameShelf ⚙설정)

. (Join-Path $PSScriptRoot 'Engines.ps1')

function Translate-Batch([string[]]$items) {
    if ($env:GT_TEST -and -not $env:GT_ENGINE) { return ,@($items | ForEach-Object { '[KO]' + $_ }) }   # 테스트용 가짜 번역
    $last = ''
    for ($try = 0; $try -lt 4; $try++) {
        try {
            $parts = Translate-ByEngine $items          # @() 로 감싸면 배열이 한 겹 더 싸여서 개수가 1이 됨
            if ($null -ne $parts -and @($parts).Count -eq $items.Count) { return ,@($parts) }
            if ($items.Count -eq 1) { return ,@(($parts -join ' ')) }
            # 줄 수가 어긋나면 하나씩 다시
            return ,@($items | ForEach-Object { (Translate-Batch @($_))[0] })
        } catch {
            $last = Get-WebErrorText $_.Exception
            if ($last -match '필요합니다|모델 이름') { throw $last }     # 설정 문제는 다시 시도해도 같음
            if ($last -match '^HTTP 40[13]') { throw "$(Get-EngineLabel) 인증 실패 (키를 확인해 주세요): $last" }
            if ($last -match '^HTTP 456') { throw "$(Get-EngineLabel) 이번 달 무료 사용량을 다 썼습니다." }
            Start-Sleep -Seconds ([math]::Pow(2, $try + 1))
        }
    }
    throw "$(Get-EngineLabel) 서버에 연결하지 못했습니다. 인터넷(또는 PC AI 프로그램) 상태를 확인하거나 잠시 뒤 다시 해주세요.`n($last)"
}

# ------------------------------------------------------------------ 줄바꿈 (한글 1칸, 영문·숫자·공백 0.5칸)

function Text-Width([string]$s) {
    $plain = [regex]::Replace($s, $script:CodeRegex, '')
    $w = 0.0
    foreach ($ch in $plain.ToCharArray()) { if ([int]$ch -ge 0x2E80) { $w += 1 } else { $w += 0.5 } }
    return $w
}
# 폭 $width 를 넘기 직전 위치 (제어문자 \C[2] 같은 덩어리 중간은 피함)
function Find-Cut([string]$s, [double]$width) {
    $spans = @{}
    foreach ($m in [regex]::Matches($s, $script:CodeRegex)) { $spans[$m.Index] = $m.Length }
    $w = 0.0; $i = 0
    while ($i -lt $s.Length) {
        if ($spans.ContainsKey($i)) { $i += $spans[$i]; continue }
        $cw = if ([int]$s[$i] -ge 0x2E80) { 1 } else { 0.5 }
        if ($w + $cw -gt $width -and $i -gt 0) { return $i }
        $w += $cw; $i++
    }
    return $s.Length
}
function Wrap-Text([string]$s, [double]$width) {
    $lines = New-Object System.Collections.Generic.List[string]
    $cur = ''
    foreach ($word in ($s -split ' ')) {
        if ($word -eq '') { continue }
        $cand = if ($cur) { "$cur $word" } else { $word }
        if ((Text-Width $cand) -le $width -or -not $cur) { $cur = $cand }
        else { $lines.Add($cur); $cur = $word }
        while ((Text-Width $cur) -gt $width * 1.3) {    # 띄어쓰기 없이 너무 긴 덩어리는 강제로 자름 (제어문자는 쪼개지 않음)
            $cut = Find-Cut $cur $width
            if ($cut -le 0 -or $cut -ge $cur.Length) { break }
            $lines.Add($cur.Substring(0, $cut)); $cur = $cur.Substring($cut)
        }
    }
    if ($cur) { $lines.Add($cur) }
    if ($lines.Count -eq 0) { $lines.Add('') }
    return ,$lines.ToArray()
}

# ------------------------------------------------------------------ 수집 / 적용 공통

# Collect 모드: 번역할 원문을 모은다 / Apply 모드: 번역문을 돌려준다
function Tx([string]$s) {
    if (-not $s) { return $s }
    if ($script:Mode -eq 'count') { if ($s -match $script:KoRegex) { $script:KoCount++ } elseif ($s -match $script:JpRegex) { $script:JpCount++ }; return $s }
    if ($s -notmatch $script:JpRegex -or $s -match $script:KoRegex) { return $s }
    if ($script:Mode -eq 'collect') { $script:Need[$s] = $true; return $s }
    $t = $null
    if ($script:Cache.TryGetValue($s, [ref]$t) -and $t) { return $t }
    return $s
}

# 여러 줄 메시지 덩어리: 원문 키 = 줄들을 \n 으로 이은 것, 번역은 한 문장으로 이어서 하고 다시 줄바꿈
function Tx-Block([string[]]$lines, [double]$minWidth) {
    $key = $lines -join "`n"
    if ($script:Mode -eq 'count') { if ($key -match $script:KoRegex) { $script:KoCount++ } elseif ($key -match $script:JpRegex) { $script:JpCount++ }; return ,$lines }
    if ($key -notmatch $script:JpRegex -or $key -match $script:KoRegex) { return ,$lines }
    if ($script:Mode -eq 'collect') { $script:NeedBlock[$key] = $true; return ,$lines }
    $t = $null
    if (-not ($script:Cache.TryGetValue($key, [ref]$t) -and $t)) { return ,$lines }
    $w = $minWidth
    foreach ($l in $lines) { $lw = Text-Width $l; if ($lw -gt $w) { $w = $lw } }
    # 번역문 안의 줄바꿈(화자 이름 줄, 또는 직접 나눈 줄)은 유지하고, 각 줄이 너무 길면 다시 줄바꿈
    $res = New-Object System.Collections.Generic.List[string]
    foreach ($piece in ($t -split "`n")) { foreach ($l in (Wrap-Text $piece $w)) { $res.Add($l) } }
    return ,$res.ToArray()
}

# 번역 엔진에 넘길 문장 만들기 (블록은 줄을 이어 붙임. 첫 줄이 화자 이름이면 따로)
function Block-Source([string]$key) {
    $lines = $key -split "`n"
    if ($lines.Count -ge 2 -and $lines[0].Length -le 12 -and $lines[1] -match '^\s*[「『（(]' -and $lines[0] -notmatch '[。、！？!?」』]$') {
        return @($lines[0], (($lines | Select-Object -Skip 1) -join ''))
    }
    return @(($lines -join ''))
}

# ------------------------------------------------------------------ 이벤트 명령 처리

function Process-CommandList($list) {
    if ($null -eq $list) { return $list }
    $out = New-Object System.Collections.Generic.List[object]
    $i = 0; $n = $list.Count
    while ($i -lt $n) {
        $c = $list[$i]; $code = [int]$c['code']; $p = $c['parameters']
        if ($code -eq 101 -or $code -eq 105) {
            # 101 = 메시지 시작(MZ 는 5번째 값이 화자 이름), 105 = 스크롤 텍스트. 뒤따르는 401/405 줄들을 한 덩어리로 번역
            $lineCode = if ($code -eq 101) { 401 } else { 405 }
            if ($code -eq 101 -and $p.Count -ge 5 -and $p[4] -is [string]) { $p[4] = Tx $p[4] }
            $w = 30
            if ($code -eq 101) { $w = if ($p.Count -ge 1 -and [string]$p[0]) { 20 } else { 26 } }   # 얼굴 그림이 있으면 폭이 좁음
            $out.Add($c); $i++
            $j = $i; $lines = New-Object System.Collections.Generic.List[string]
            while ($j -lt $n -and [int]$list[$j]['code'] -eq $lineCode) { $lines.Add([string]$list[$j]['parameters'][0]); $j++ }
            if ($lines.Count -gt 0) {
                $indent = $list[$i]['indent']
                foreach ($l in (Tx-Block $lines.ToArray() $w)) { $out.Add([ordered]@{ code = $lineCode; indent = $indent; parameters = @($l) }) }
            }
            $i = $j
            continue
        }
        if ($code -eq 102) { $arr = $p[0]; for ($k = 0; $k -lt $arr.Count; $k++) { $arr[$k] = Tx ([string]$arr[$k]) } }   # 선택지
        elseif (($code -eq 402 -or $code -eq 320 -or $code -eq 324 -or $code -eq 325) -and $p.Count -ge 2 -and $p[1] -is [string]) {
            $p[1] = Tx $p[1]    # 402 선택지 분기 표시 / 320 이름 / 324 별명 / 325 프로필 변경
        }
        $out.Add($c); $i++
    }
    return ,$out.ToArray()
}

function Process-Pages($pages) {
    if ($null -eq $pages) { return }
    foreach ($pg in $pages) { if ($pg) { $pg['list'] = Process-CommandList $pg['list'] } }
}

function Process-Fields($arr, [string[]]$fields, [string[]]$multiline) {
    if ($null -eq $arr) { return }
    foreach ($o in $arr) {
        if ($null -eq $o) { continue }
        foreach ($f in $fields) { if ($o[$f] -is [string]) { $o[$f] = Tx $o[$f] } }
        foreach ($f in $multiline) {
            if ($o[$f] -is [string] -and $o[$f]) {
                $lines = $o[$f] -split "`r?`n"
                $o[$f] = ((Tx-Block $lines 26) -join "`n")
            }
        }
    }
}

function Process-StringArray($arr) {
    if ($null -eq $arr) { return }
    for ($k = 0; $k -lt $arr.Count; $k++) { if ($arr[$k] -is [string]) { $arr[$k] = Tx $arr[$k] } }
}

function Process-File([string]$name, $data) {
    switch -Regex ($name) {
        '^Map\d+\.json$' {
            if ($data['displayName'] -is [string]) { $data['displayName'] = Tx $data['displayName'] }
            foreach ($e in $data['events']) { if ($e) { Process-Pages $e['pages'] } }
        }
        '^CommonEvents\.json$' { foreach ($e in $data) { if ($e) { $e['list'] = Process-CommandList $e['list'] } } }
        '^Troops\.json$'       { foreach ($t in $data) { if ($t) { Process-Pages $t['pages'] } } }
        '^Actors\.json$'       { Process-Fields $data @('name', 'nickname') @('profile') }
        '^Classes\.json$'      { Process-Fields $data @('name') @() }
        '^Skills\.json$'       { Process-Fields $data @('name', 'message1', 'message2') @('description') }
        '^(Items|Weapons|Armors)\.json$' { Process-Fields $data @('name') @('description') }
        '^Enemies\.json$'      { Process-Fields $data @('name') @() }
        '^States\.json$'       { Process-Fields $data @('name', 'message1', 'message2', 'message3', 'message4') @() }
        '^System\.json$' {
            foreach ($f in 'gameTitle', 'currencyUnit') { if ($data[$f] -is [string]) { $data[$f] = Tx $data[$f] } }
            foreach ($f in 'elements', 'skillTypes', 'weaponTypes', 'armorTypes', 'equipTypes') { Process-StringArray $data[$f] }
            $terms = $data['terms']
            if ($terms) {
                foreach ($f in 'basic', 'commands', 'params') { Process-StringArray $terms[$f] }
                $msg = $terms['messages']
                if ($msg) { foreach ($k in @($msg.Keys)) { if ($msg[$k] -is [string]) { $msg[$k] = Tx $msg[$k] } } }
            }
        }
    }
}

$script:RpgFiles = '^(Map\d+|CommonEvents|Troops|Actors|Classes|Skills|Items|Weapons|Armors|Enemies|States|System)\.json$'

# ------------------------------------------------------------------ 진행 창

function Show-Progress([string]$text, [int]$pct) {
    if ($env:GT_TEST) { return }
    if (-not $script:PForm) {
        $script:PForm = New-Object System.Windows.Forms.Form -Property @{ Text = 'GameTranslator'; Width = 460; Height = 140; FormBorderStyle = 'FixedDialog'; MaximizeBox = $false; StartPosition = 'CenterScreen'; TopMost = $true }
        $script:PLabel = New-Object System.Windows.Forms.Label -Property @{ Left = 12; Top = 12; Width = 420; Height = 36 }
        $script:PBar = New-Object System.Windows.Forms.ProgressBar -Property @{ Left = 12; Top = 56; Width = 420; Height = 22; Maximum = 100 }
        $script:PForm.Controls.AddRange(@($script:PLabel, $script:PBar))
        $script:PForm.Show()
    }
    $script:PLabel.Text = $text
    $script:PBar.Value = [math]::Max(0, [math]::Min(100, $pct))
    [System.Windows.Forms.Application]::DoEvents()
}
function Close-Progress { if ($script:PForm) { $script:PForm.Close(); $script:PForm = $null } }

# ------------------------------------------------------------------ 메인 흐름

function Get-RpgDataDir([string]$dir) {
    foreach ($rel in 'www\data', 'data') {
        $p = Join-Path $dir $rel
        if (Test-Path -LiteralPath (Join-Path $p 'System.json')) { return $p }
    }
    return $null
}

# 조각이 모두 번역된 작업을 캐시에 넣기 (화자 이름 + 대사로 나눈 블록은 줄을 나눠 저장)
function Store-Done($jobs) {
    foreach ($j in $jobs) {
        if ($null -eq $j.out -or ($j.out -contains $null) -or $script:Cache.ContainsKey($j.key)) { continue }
        $script:Cache[$j.key] = if ($j.block) { $j.out -join "`n" } else { $j.out -join ' ' }
    }
}

function Invoke-RpgTranslate([string]$dir, [string]$engineName, [bool]$auto = $false) {
    $data = Get-RpgDataDir $dir
    if (-not $data) { Fail "data 폴더(System.json)를 찾지 못했습니다.`n데이터가 암호화되었거나 exe 하나로 묶인 게임일 수 있습니다." }
    $backup = "${data}_GTbackup"
    $work   = Join-Path $dir 'GameTranslator_RPG'
    $tsv    = Join-Path $work 'translations.tsv'

    if (Test-Path -LiteralPath $backup) {
        if ($auto) { return }    # 이미 번역됨
        $r = Show-Msg "[$engineName] 이미 번역이 적용된 게임입니다.`n`n예 = 다시 적용 (translations.tsv 수정 반영 + 새 문장 번역)`n아니요 = 원본(일본어)으로 복구`n취소 = 닫기`n`n번역 파일: $tsv" 'YesNoCancel' 'Question'
        if ($r -eq 'No') {
            Copy-Tree $backup $data
            Show-Msg "원본 일본어 데이터로 복구했습니다.`n(번역 파일은 남겨 두었으니 '다시 적용'으로 언제든 되돌릴 수 있습니다)" | Out-Null
            return
        }
        if ($r -ne 'Yes') { return }
    } else {
        # 이미 한국어 패치가 된 게임인지 확인 (대사·용어 중 한글 문장 비율)
        $script:Mode = 'count'; $script:KoCount = 0; $script:JpCount = 0
        foreach ($f in @(Get-ChildItem -LiteralPath $data -File | Where-Object { $_.Name -match $script:RpgFiles })) {
            Process-File $f.Name (Read-JsonText ([IO.File]::ReadAllText($f.FullName, $script:Utf8))) | Out-Null
        }
        $krExe = @(Get-ChildItem -LiteralPath $dir -Filter *.exe -File | Where-Object { $_.Name -match '(?i)(_kr|kor|korean|한글|한국어)' } | ForEach-Object Name)
        $total = $script:KoCount + $script:JpCount
        if ($total -gt 0 -and $script:KoCount -ge 0.3 * $total) {
            if ($auto) { exit 5 }   # 자동 모드: 이미 한글화된 게임은 건드리지 않음
            $q = "이미 한국어로 번역된 게임 같습니다.`n(한국어 문장 $($script:KoCount)개 / 일본어 문장 $($script:JpCount)개)`n`n남은 일본어 문장만 번역할까요?`n이미 한국어인 문장은 건드리지 않고, 원본은 백업합니다."
            if ((Show-Msg $q 'YesNo' 'Question') -ne 'Yes') { return }
        } elseif ($krExe.Count -gt 0 -and -not $auto) {
            $q = "이 폴더에 한국어판으로 보이는 실행 파일이 있습니다: $($krExe -join ', ')`n그 exe는 자체 데이터를 쓰는 경우가 많아 이 번역은 Game.exe 쪽에만 적용됩니다.`n`n계속할까요?"
            if ((Show-Msg $q 'YesNo' 'Question') -ne 'Yes') { return }
        }
        $q = "폴더: $dir`n엔진: $engineName`n`n게임 데이터(대사·선택지·아이템·스킬·메뉴 용어)를 일본어 → 한국어로 번역해 바꿔 넣습니다. ($(Get-EngineLabel))`n`n- 원본은 $(Split-Path -Leaf $backup) 폴더에 백업합니다.`n- 문장이 많으면 몇 분 걸릴 수 있습니다.`n`n진행할까요?"
        if (-not $auto -and (Show-Msg $q 'YesNo' 'Question') -ne 'Yes') { return }
        try { [IO.Directory]::CreateDirectory($backup) | Out-Null; Copy-Tree $data $backup }
        catch { Fail "원본 백업을 만들지 못했습니다.`n($($_.Exception.Message))" }
    }
    if (-not (Test-Path -LiteralPath $work)) { [IO.Directory]::CreateDirectory($work) | Out-Null }

    $files = @(Get-ChildItem -LiteralPath $backup -File | Where-Object { $_.Name -match $script:RpgFiles })

    # 1) 원문 수집
    $script:Mode = 'collect'
    $script:Need = @{}; $script:NeedBlock = @{}
    $k = 0
    foreach ($f in $files) {
        $k++; Show-Progress "문장 모으는 중... $($f.Name)" ([int](10 * $k / $files.Count))
        $obj = Read-JsonText ([IO.File]::ReadAllText($f.FullName, $script:Utf8))
        Process-File $f.Name $obj | Out-Null
    }

    # 2) 캐시에 없는 것만 번역
    $script:Cache = Load-Cache $tsv
    $jobs = New-Object System.Collections.Generic.List[object]    # @{ key; parts(번역할 조각들) }
    foreach ($s in $script:Need.Keys)      { if (-not $script:Cache.ContainsKey($s)) { $jobs.Add(@{ key = $s; parts = @($s); block = $false }) } }
    foreach ($s in $script:NeedBlock.Keys) { if (-not $script:Cache.ContainsKey($s)) { $jobs.Add(@{ key = $s; parts = @(Block-Source $s); block = $true }) } }

    $pending = New-Object System.Collections.Generic.List[object]   # 조각 단위
    foreach ($j in $jobs) {
        $j.out = New-Object string[] $j.parts.Count
        for ($x = 0; $x -lt $j.parts.Count; $x++) {
            $codes = $null; $src = Protect $j.parts[$x] ([ref]$codes)
            $pending.Add(@{ job = $j; idx = $x; src = $src.Replace("`n", ' '); codes = $codes })
        }
    }
    try {
        $pos = 0; $round = 0
        while ($pos -lt $pending.Count) {
            # 3500자 / 60문장 단위로 묶어서 요청
            $batch = New-Object System.Collections.Generic.List[object]; $blen = 0
            while ($pos -lt $pending.Count -and ($batch.Count -eq 0 -or ($blen + $pending[$pos].src.Length -le 3500 -and $batch.Count -lt 60))) {
                $batch.Add($pending[$pos]); $blen += $pending[$pos].src.Length; $pos++
            }
            $res = Translate-Batch @($batch | ForEach-Object { $_.src })
            for ($y = 0; $y -lt $batch.Count; $y++) { $b = $batch[$y]; $b.job.out[$b.idx] = Unprotect ([string]$res[$y]).Trim() $b.codes }
            Store-Done $jobs
            $round++
            if ($round % 10 -eq 0) { Save-Cache $script:Cache $tsv }     # 중간 저장: 끊겨도 다음에 이어서
            Show-Progress "번역 중... $pos / $($pending.Count)" (10 + [int](80 * $pos / $pending.Count))
            if (-not $env:GT_TEST) { Start-Sleep -Milliseconds 300 }
        }
    } catch {
        Close-Progress
        Store-Done $jobs
        Save-Cache $script:Cache $tsv
        Fail "번역 도중 멈췄습니다: $($_.Exception.Message)`n`n지금까지 번역한 문장은 저장했습니다. 다시 실행하면 이어서 번역합니다."
    }
    Store-Done $jobs
    Save-Cache $script:Cache $tsv

    # 3) 원본에서 새로 만들어 data 에 쓰기
    $script:Mode = 'apply'
    $k = 0
    foreach ($f in $files) {
        $k++; Show-Progress "게임 데이터에 쓰는 중... $($f.Name)" (90 + [int](10 * $k / $files.Count))
        $obj = Read-JsonText ([IO.File]::ReadAllText($f.FullName, $script:Utf8))
        Process-File $f.Name $obj | Out-Null
        try { [IO.File]::WriteAllText((Join-Path $data $f.Name), (Write-JsonText $obj), $script:Utf8) }
        catch { Close-Progress; Fail "파일을 쓰지 못했습니다: $($f.Name)`n게임이 실행 중이면 끄고 다시 해주세요." }
    }
    Close-Progress
    Show-Msg "번역 완료!`n`n문장 $($script:Need.Count + $script:NeedBlock.Count)개 (새로 번역 $($jobs.Count)개)`n이제 게임을 그대로 실행하면 됩니다.`n`n어색한 번역은 아래 파일에서 직접 고친 뒤, 같은 버튼 → '다시 적용' 하세요.`n$tsv" | Out-Null
}
