# GameTranslator - 번역 엔진 모듈
# GameShelf ⚙설정에서 고른 엔진을 GameTranslator\engine.json 에서 읽어 사용한다.
# (파일이 없으면 Google 번역 — 예전과 동일하게 동작)
#   google : Google 번역 (키 없이)
#   bing   : Microsoft Bing 번역 (키 없이)
#   papago : 네이버 Papago (RPG Maker: 네이버 클라우드 키 필요 / Unity: XUnity 내장 방식, 키 없이)
#   deepl  : DeepL (API 키 필요 — Unity 는 키가 없으면 XUnity 내장 웹 방식)
#   local  : PC 에서 돌리는 AI (Ollama / LM Studio 등 OpenAI 호환 서버)
# GameTranslator.ps1 / RpgMaker.ps1 이 dot-source 해서 사용한다.

$script:EngineDir = $PSScriptRoot

# JSON (PowerShell 5.1 / 7 둘 다) — Unity 경로에서도 쓰이도록 RpgMaker.ps1 과 별도로 둠
function GT-ReadJson([string]$text) {
    if ($PSVersionTable.PSVersion.Major -ge 6) { return ConvertFrom-Json -InputObject $text -AsHashtable -Depth 100 -NoEnumerate }
    if (-not $script:GTSer) {
        Add-Type -AssemblyName System.Web.Extensions
        $script:GTSer = New-Object System.Web.Script.Serialization.JavaScriptSerializer
        $script:GTSer.MaxJsonLength = [int]::MaxValue
    }
    return $script:GTSer.DeserializeObject($text)
}
function GT-WriteJson($obj) {
    # 요청 본문(작은 해시테이블)용 — 5.1 / 7 모두 ConvertTo-Json 으로 충분
    return ConvertTo-Json -InputObject $obj -Depth 20 -Compress
}

function Get-GTConfig {
    if ($script:GTConfig) { return $script:GTConfig }
    $cfg = @{ engine = 'google'; deepl_key = ''; papago_id = ''; papago_secret = '';
              local_url = 'http://localhost:11434'; local_model = ''; bridge_port = 5577 }
    $p = Join-Path $script:EngineDir 'engine.json'
    if (Test-Path -LiteralPath $p) {
        try {
            $j = GT-ReadJson ([IO.File]::ReadAllText($p, (New-Object Text.UTF8Encoding $false)))
            foreach ($k in @($j.Keys)) { if ($null -ne $j[$k] -and "$($j[$k])" -ne '') { $cfg[$k] = $j[$k] } }
        } catch { }
    }
    if ($env:GT_ENGINE) { $cfg.engine = $env:GT_ENGINE }     # 테스트용
    $cfg.engine = ([string]$cfg.engine).ToLower()
    $script:GTConfig = $cfg
    return $cfg
}

function Get-EngineLabel {
    $c = Get-GTConfig
    switch ($c.engine) {
        'bing'   { return 'Bing 번역' }
        'papago' { return 'Papago 번역' }
        'deepl'  { return 'DeepL 번역' }
        'local'  { $m = if ($c.local_model) { " · $($c.local_model)" } else { '' }; return "PC의 AI 모델$m" }
        default  { return '구글 번역' }
    }
}

# ------------------------------------------------------------------ Unity(XUnity) 설정

function Get-XUnityEndpoint {
    $c = Get-GTConfig
    switch ($c.engine) {
        'bing'   { return 'BingTranslate' }
        'papago' { return 'PapagoTranslate' }
        'deepl'  { if ($c.deepl_key) { return 'DeepLTranslateLegitimate' } else { return 'DeepLTranslate' } }
        'local'  { return 'CustomTranslate' }
        default  { return 'GoogleTranslateV2' }
    }
}

function Set-XUnityEngine([string[]]$lines) {
    # Set-IniValue (GameTranslator.ps1) 를 사용
    $c = Get-GTConfig
    $lines = Set-IniValue $lines 'Service' 'Endpoint' (Get-XUnityEndpoint) $false
    if ($c.engine -eq 'local') {
        $lines = Set-IniValue $lines 'Custom' 'Url' "http://127.0.0.1:$($c.bridge_port)/translate" $false
    }
    if ($c.engine -eq 'deepl' -and $c.deepl_key) {
        $lines = Set-IniValue $lines 'DeepLLegitimate' 'ApiKey' $c.deepl_key $false
        $free = if (([string]$c.deepl_key).EndsWith(':fx')) { 'True' } else { 'False' }
        $lines = Set-IniValue $lines 'DeepLLegitimate' 'Free' $free $false
    }
    return ,$lines
}

# ------------------------------------------------------------------ HTTP

function New-GTClient {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $wc = New-Object Net.WebClient
    $wc.Encoding = [Text.Encoding]::UTF8
    $wc.Headers.Add('User-Agent', 'Mozilla/5.0')
    return $wc
}

function Get-WebErrorText($ex) {
    $e = $ex
    while ($e.InnerException -and -not ($e -is [Net.WebException])) { $e = $e.InnerException }
    if ($e -is [Net.WebException] -and $e.Response) {
        $code = [int]$e.Response.StatusCode
        try { $body = (New-Object IO.StreamReader($e.Response.GetResponseStream())).ReadToEnd() } catch { $body = '' }
        if ($body.Length -gt 200) { $body = $body.Substring(0, 200) }
        return "HTTP $code $body"
    }
    return $ex.Message
}

# ------------------------------------------------------------------ 엔진별 번역 (items: 문장 배열 → 같은 개수의 번역 배열)

function Translate-Google([string[]]$items) {
    $text = $items -join "`n"
    $url  = 'https://translate.googleapis.com/translate_a/single?client=gtx&sl=ja&tl=ko&dt=t'
    $wc = New-GTClient
    $wc.Headers.Add('Content-Type', 'application/x-www-form-urlencoded;charset=UTF-8')
    $res = $wc.UploadString($url, 'POST', 'q=' + [uri]::EscapeDataString($text))
    $j = GT-ReadJson $res
    $sb = New-Object Text.StringBuilder
    foreach ($seg in $j[0]) { if ($seg -and $seg[0]) { [void]$sb.Append([string]$seg[0]) } }
    if ($items.Count -eq 1) { return ,@($sb.ToString().Replace("`n", ' ')) }
    return ,($sb.ToString() -split "`n")
}

function Get-BingToken {
    if ($script:BingToken -and ((Get-Date) -lt $script:BingTokenUntil)) { return $script:BingToken }
    $wc = New-GTClient
    $script:BingToken = $wc.DownloadString('https://edge.microsoft.com/translate/auth').Trim()
    $script:BingTokenUntil = (Get-Date).AddMinutes(8)
    return $script:BingToken
}

function Translate-Bing([string[]]$items) {
    $wc = New-GTClient
    $wc.Headers.Add('Authorization', 'Bearer ' + (Get-BingToken))
    $wc.Headers.Add('Content-Type', 'application/json; charset=UTF-8')
    $body = GT-WriteJson @($items | ForEach-Object { @{ Text = $_ } })
    $res = $wc.UploadString('https://api-edge.cognitive.microsofttranslator.com/translate?from=ja&to=ko&api-version=3.0', 'POST', $body)
    $j = GT-ReadJson $res
    return ,@($j | ForEach-Object { [string]$_['translations'][0]['text'] })
}

function Translate-Papago([string[]]$items) {
    $c = Get-GTConfig
    if (-not $c.papago_id -or -not $c.papago_secret) {
        throw "Papago 로 RPG Maker 게임을 번역하려면 네이버 클라우드 Papago 키(Client ID / Secret)가 필요합니다.`nGameShelf ⚙설정에 넣거나, 게임 번역 엔진을 다른 것으로 바꿔주세요."
    }
    $wc = New-GTClient
    $wc.Headers.Add('X-NCP-APIGW-API-KEY-ID', $c.papago_id)
    $wc.Headers.Add('X-NCP-APIGW-API-KEY', $c.papago_secret)
    $wc.Headers.Add('Content-Type', 'application/x-www-form-urlencoded; charset=UTF-8')
    $res = $wc.UploadString('https://papago.apigw.ntruss.com/nmt/v1/translation', 'POST',
                            'source=ja&target=ko&text=' + [uri]::EscapeDataString(($items -join "`n")))
    $t = [string](GT-ReadJson $res)['message']['result']['translatedText']
    if ($items.Count -eq 1) { return ,@($t.Replace("`n", ' ')) }
    return ,($t -split "`n")
}

function Translate-DeepL([string[]]$items) {
    $c = Get-GTConfig
    if (-not $c.deepl_key) { throw "DeepL 로 RPG Maker 게임을 번역하려면 DeepL API 키가 필요합니다. GameShelf ⚙설정에 넣어주세요." }
    $hostName = if (([string]$c.deepl_key).EndsWith(':fx')) { 'api-free.deepl.com' } else { 'api.deepl.com' }
    $wc = New-GTClient
    $wc.Headers.Add('Authorization', "DeepL-Auth-Key $($c.deepl_key)")
    $wc.Headers.Add('Content-Type', 'application/x-www-form-urlencoded; charset=UTF-8')
    $form = (@($items | ForEach-Object { 'text=' + [uri]::EscapeDataString($_) }) + 'source_lang=JA' + 'target_lang=KO') -join '&'
    $res = $wc.UploadString("https://$hostName/v2/translate", 'POST', $form)
    return ,@((GT-ReadJson $res)['translations'] | ForEach-Object { [string]$_['text'] })
}

function Invoke-LocalAI([string]$system, [string]$user) {
    $c = Get-GTConfig
    if (-not $c.local_model) { throw "PC AI 모델 이름이 비어 있습니다. GameShelf ⚙설정에서 모델을 골라주세요." }
    $base = ([string]$c.local_url).TrimEnd('/')
    $wc = New-GTClient
    $wc.Headers.Add('Content-Type', 'application/json; charset=UTF-8')
    $msgs = @(@{ role = 'system'; content = $system }, @{ role = 'user'; content = $user })
    if ($base -match '/v1$') {
        # LM Studio / llama.cpp / vLLM 등 OpenAI 호환 서버
        $body = GT-WriteJson @{ model = $c.local_model; messages = $msgs; temperature = 0.2; stream = $false }
        $j = GT-ReadJson ($wc.UploadString("$base/chat/completions", 'POST', $body))
        $out = [string]$j['choices'][0]['message']['content']
    } else {
        # Ollama
        $body = GT-WriteJson @{ model = $c.local_model; messages = $msgs; stream = $false; think = $false;
                                  options = @{ temperature = 0.2 } }
        $j = GT-ReadJson ($wc.UploadString("$base/api/chat", 'POST', $body))
        $out = [string]$j['message']['content']
    }
    return ([regex]::Replace($out, '(?s)<think>.*?</think>', '')).Trim()
}

$script:LocalSystem = "You are a professional Japanese-to-Korean game translator. Translate naturally into Korean as it would appear in a Korean release of the game. Keep placeholders like {0} {1} and symbols exactly as they are. Output only the translation, no explanations."

function Translate-Local([string[]]$items) {
    if ($items.Count -eq 1) { return ,@((Invoke-LocalAI $script:LocalSystem $items[0]).Replace("`n", ' ')) }
    $out = New-Object string[] $items.Count
    for ($s = 0; $s -lt $items.Count; $s += 15) {        # AI 는 한 번에 15줄씩
        $part = @($items[$s..([math]::Min($s + 14, $items.Count - 1))])
        $numbered = for ($i = 0; $i -lt $part.Count; $i++) { "[$($i + 1)] $($part[$i])" }
        $user = "Translate each numbered line. Reply with the same numbers, one line each, in the form [n] translation.`n`n" + ($numbered -join "`n")
        $reply = Invoke-LocalAI $script:LocalSystem $user
        $got = @{}
        foreach ($ln in ($reply -split "`r?`n")) {
            if ($ln -match '^\s*\[(\d+)\]\s?(.*)$') { $got[[int]$Matches[1]] = $Matches[2].Trim() }
        }
        for ($i = 0; $i -lt $part.Count; $i++) {
            if ($got.ContainsKey($i + 1) -and $got[$i + 1]) { $out[$s + $i] = $got[$i + 1] }
            else { $out[$s + $i] = (Translate-Local @($part[$i]))[0] }     # 빠진 줄만 하나씩 다시
        }
    }
    return ,$out
}

function Translate-ByEngine([string[]]$items) {
    $c = Get-GTConfig
    switch ($c.engine) {
        'bing'   { return ,(Translate-Bing $items) }
        'papago' { return ,(Translate-Papago $items) }
        'deepl'  { return ,(Translate-DeepL $items) }
        'local'  { return ,(Translate-Local $items) }
        default  { return ,(Translate-Google $items) }
    }
}
