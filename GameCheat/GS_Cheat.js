//=============================================================================
// GS_Cheat.js  -  GameShelf RPG Maker MV/MZ cheat panel
//=============================================================================
/*:
 * @target MZ
 * @plugindesc [GameShelf] In-game cheat panel (` key). MV/MZ common.
 * @author GameShelf
 *
 * @param mainKey
 * @text Panel key (keyCode)
 * @desc 192 = ` (backtick). Shift + key opens the item sidebar.
 * @default 192
 *
 * @help
 * ` (backtick)        : open / close the cheat panel
 * Shift + `           : open / close the item sidebar
 * Esc                 : close
 *
 * Korean names are read from js/plugins/GS_Cheat_ko.json
 * (GameShelf creates it when installing). Without it the
 * original names are shown.
 *
 * No network access, no external libraries.
 */
/*:ko
 * @target MZ
 * @plugindesc [GameShelf] 게임 화면 위에 뜨는 치트 창 (` 키). MV/MZ 공용
 * @author GameShelf
 *
 * @param mainKey
 * @text 치트 창 키 (keyCode)
 * @desc 192 = ` 키. Shift + 키는 아이템 사이드바
 * @default 192
 */

(function () {
    'use strict';

    var PN = 'GS_Cheat';
    var P = (typeof PluginManager !== 'undefined' && PluginManager.parameters(PN)) || {};
    var KEY_MAIN = Number(P.mainKey || 192);
    var IS_MZ = (typeof Utils !== 'undefined' && Utils.RPGMAKER_NAME === 'MZ');

    //-------------------------------------------------------------------------
    // 설정 / 상태
    //-------------------------------------------------------------------------
    var GS = window.GS_Cheat = {
        version: '1.0.0',
        cfg: {
            speed: 0,             // 0 = 끔, 1/2/3/5 = 달리기 속도의 N배
            ghost: false,         // 벽 통과 + 이벤트 무시
            ghostAction: false,   // 고스트 중에도 결정키로 말 걸기 허용
            noEncounter: false,   // 랜덤 인카운터 끔
            battleMode: 'off',    // off / win / lose / escape
            battleOnce: true,     // 다음 전투 1회만
            defeatFallback: true  // 패배 분기 없는 전투는 도망으로 대체
        },
        dict: {},
        mapDisplay: {},
        open: false,
        tab: 'stats',
        sidebar: false
    };

    function loadCfg() {
        try {
            var s = window.localStorage && localStorage.getItem('GS_Cheat_cfg');
            if (s) {
                var o = JSON.parse(s);
                for (var k in o) if (GS.cfg.hasOwnProperty(k)) GS.cfg[k] = o[k];
            }
        } catch (e) {}
        GS.cfg.ghost = false; // 고스트는 매번 꺼진 상태로 시작
    }
    function saveCfg() {
        try { localStorage.setItem('GS_Cheat_cfg', JSON.stringify(GS.cfg)); } catch (e) {}
    }
    loadCfg();

    //-------------------------------------------------------------------------
    // 값 고정 (골드·HP·변수·아이템 개수·게임 고유 수치)
    //   floor = 줄지 않게 (늘어나는 건 허용, 늘어난 값이 새 기준)
    //   exact = 그 값 그대로
    //-------------------------------------------------------------------------
    GS.locks = {};
    var lockDirty = false, lockFrame = 0;
    function lockStoreKey() {
        var t = ($dataSystem && $dataSystem.gameTitle) || document.title || 'game';
        return 'GS_Cheat_locks_' + t;
    }
    function saveLocks() {
        try { localStorage.setItem(lockStoreKey(), JSON.stringify(GS.locks)); } catch (e) {}
        lockDirty = false;
    }
    function loadLocks() {
        try {
            var s = localStorage.getItem(lockStoreKey());
            GS.locks = s ? JSON.parse(s) : {};
        } catch (e) { GS.locks = {}; }
    }
    function lockId(win, path) { return win + '|' + path.join('.'); }
    function resolveLock(lk) {
        var o;
        if (lk.win.charAt(0) === '@') {        // 전역 이름이 없는 세이브 데이터 (let/const 로 만든 경우)
            try { o = DataManager.makeSaveContents()[lk.win.slice(1)]; } catch (e) { o = null; }
        } else {
            o = window[lk.win];
        }
        for (var i = 0; i < lk.path.length - 1; i++) {
            if (!o || typeof o !== 'object') return null;
            o = o[lk.path[i]];
        }
        return (o && typeof o === 'object') ? { o: o, k: lk.path[lk.path.length - 1] } : null;
    }
    function applyLocks() {
        for (var id in GS.locks) {
            if (!GS.locks.hasOwnProperty(id)) continue;
            var lk = GS.locks[id], r = resolveLock(lk);
            if (!r) continue;
            var v = r.o[r.k];
            if (typeof v !== 'number') {
                if (v === undefined && lk.inv) r.o[r.k] = lk.value;   // 아이템을 다 써서 칸이 지워진 경우
                continue;
            }
            if (lk.mode === 'exact') {
                if (v !== lk.value) r.o[r.k] = lk.value;
            } else if (v < lk.value) {
                r.o[r.k] = lk.value;
            } else if (v > lk.value) {
                lk.value = v; lockDirty = true;
            }
        }
        if (lockDirty && ++lockFrame % 300 === 0) saveLocks();
    }
    function isLocked(win, path) { return !!GS.locks[lockId(win, path)]; }
    function toggleLock(win, path, label, inv) {
        var id = lockId(win, path);
        if (GS.locks[id]) {
            delete GS.locks[id];
        } else {
            var r = resolveLock({ win: win, path: path }), v = r ? r.o[r.k] : undefined;
            if (typeof v !== 'number') v = 0;
            GS.locks[id] = { win: win, path: path, value: v, mode: 'floor', label: label, inv: !!inv };
        }
        saveLocks();
        return !!GS.locks[id];
    }
    // 🔒 버튼
    function lockBtn(win, path, label, inv) {
        var b = btn('', function () {
            var on = toggleLock(win, path, label, inv);
            paint(on);
            toast((on ? '🔒 고정: ' : '고정 해제: ') + label);
        }, 'sm');
        function paint(on) {
            b.textContent = on ? '🔒고정' : '고정';
            b.className = 'gs-btn sm' + (on ? ' on' : '');
            b.title = on ? '고정 중 — 누르면 해제 ("고정 목록" 탭에서 방식 변경)' : '이 값이 줄어들지 않게 고정';
        }
        paint(isLocked(win, path));
        return b;
    }
    // 매 프레임 적용
    var _smUpdateScene = SceneManager.updateScene;
    SceneManager.updateScene = function () {
        _smUpdateScene.apply(this, arguments);
        try { applyLocks(); } catch (e) {}
    };
    // HP는 맞는 순간에 막아야 죽지 않음
    var _bbSetHp = Game_BattlerBase.prototype.setHp;
    Game_BattlerBase.prototype.setHp = function (hp) {
        if (this.isActor && this.isActor()) {
            var lk = GS.locks[lockId('$gameActors', ['_data', this.actorId(), '_hp'])];
            if (lk) hp = lk.mode === 'exact' ? lk.value : Math.max(hp, lk.value);
        }
        return _bbSetHp.call(this, hp);
    };
    // 새 게임 / 불러오기 때 이 게임의 고정 목록을 읽음
    var _dmSetupNew = DataManager.setupNewGame;
    DataManager.setupNewGame = function () { _dmSetupNew.apply(this, arguments); loadLocks(); };
    var _dmExtract = DataManager.extractSaveContents;
    DataManager.extractSaveContents = function () { _dmExtract.apply(this, arguments); loadLocks(); };

    //-------------------------------------------------------------------------
    // 번역 사전
    //-------------------------------------------------------------------------
    function loadJSON(url, ok, ng) {
        try {
            var xhr = new XMLHttpRequest();
            xhr.open('GET', url);
            if (xhr.overrideMimeType) xhr.overrideMimeType('application/json');
            xhr.onload = function () {
                if (xhr.status < 400) {
                    var j = null;
                    try { j = JSON.parse(xhr.responseText); } catch (e) { if (ng) ng(e); return; }
                    ok(j);
                } else if (ng) ng(xhr.status);
            };
            xhr.onerror = function () { if (ng) ng('error'); };
            xhr.send();
        } catch (e) { if (ng) ng(e); }
    }
    // 창을 열 때마다 다시 읽음 (GameShelf가 게임 실행 중에 번역을 끝내도 바로 반영)
    function loadDict() {
        loadJSON('js/plugins/GS_Cheat_ko.json', function (j) {
            var n = Object.keys((j && j.t) || {}).length;
            if (n === GS.dictSize) return;
            GS.dict = (j && j.t) || {};
            GS.mapDisplay = (j && j.mapDisplay) || {};
            GS.dictSize = n;
            if (GS.open) render();
            if (GS.sidebar) renderSide();
        });
    }
    GS.dictSize = -1;
    loadDict();

    function stripCodes(s) {
        return String(s || '').replace(/\\[A-Za-z]+\[[^\]]*\]/g, '').replace(/\\[{}.|!><^$]/g, '').trim();
    }
    function tr(s) {
        if (s === undefined || s === null) return '';
        var k = String(s);
        if (GS.dict[k]) return GS.dict[k];
        var k2 = k.trim();
        if (GS.dict[k2]) return GS.dict[k2];
        var k3 = stripCodes(k);
        if (GS.dict[k3]) return GS.dict[k3];
        return k3 || k;
    }
    GS.tr = tr;

    //-------------------------------------------------------------------------
    // 4. 이동 속도 (달리기 속도 × N)
    //-------------------------------------------------------------------------
    function speedDistance(ch) {
        var base = (ch === $gamePlayer) ? ch.moveSpeed() : $gamePlayer.moveSpeed();
        return Math.min(1, Math.pow(2, base + 1) / 256 * GS.cfg.speed);
    }
    function isPartyChar(ch) {
        return ch === $gamePlayer ||
            (typeof Game_Follower !== 'undefined' && ch instanceof Game_Follower);
    }
    var _cbDpf = Game_CharacterBase.prototype.distancePerFrame;
    Game_CharacterBase.prototype.distancePerFrame = function () {
        if (GS.cfg.speed > 0 && isPartyChar(this)) return speedDistance(this);
        return _cbDpf.call(this);
    };
    // 다른 플러그인이 Game_Player 쪽에 따로 정의한 경우도 덮음
    if (Game_Player.prototype.hasOwnProperty('distancePerFrame')) {
        var _plDpf = Game_Player.prototype.distancePerFrame;
        Game_Player.prototype.distancePerFrame = function () {
            if (GS.cfg.speed > 0) return speedDistance(this);
            return _plDpf.call(this);
        };
    }
    if (typeof Game_Follower !== 'undefined' && Game_Follower.prototype.hasOwnProperty('distancePerFrame')) {
        var _foDpf = Game_Follower.prototype.distancePerFrame;
        Game_Follower.prototype.distancePerFrame = function () {
            if (GS.cfg.speed > 0) return speedDistance(this);
            return _foDpf.call(this);
        };
    }

    //-------------------------------------------------------------------------
    // 5. 고스트 모드 (벽 통과 + 이벤트 무시)
    //-------------------------------------------------------------------------
    var _plThrough = Game_Player.prototype.isThrough;
    Game_Player.prototype.isThrough = function () {
        if (GS.cfg.ghost) return true;
        return _plThrough.call(this);
    };
    var _plStartMapEvent = Game_Player.prototype.startMapEvent;
    Game_Player.prototype.startMapEvent = function (x, y, triggers, normal) {
        if (GS.cfg.ghost) {
            if (!GS.cfg.ghostAction) return;
            var t = [];
            for (var i = 0; i < triggers.length; i++) if (triggers[i] === 0) t.push(0);
            if (!t.length) return;
            triggers = t;
        }
        return _plStartMapEvent.call(this, x, y, triggers, normal);
    };
    var _evTouch = Game_Event.prototype.checkEventTriggerTouch;
    Game_Event.prototype.checkEventTriggerTouch = function (x, y) {
        if (GS.cfg.ghost) return;
        return _evTouch.call(this, x, y);
    };
    var _plCanEnc = Game_Player.prototype.canEncounter;
    Game_Player.prototype.canEncounter = function () {
        if (GS.cfg.ghost || GS.cfg.noEncounter) return false;
        return _plCanEnc.call(this);
    };

    //-------------------------------------------------------------------------
    // 2. 전투 결과 예약 (승리 / 패배 / 도망)
    //-------------------------------------------------------------------------
    var _bmSetup = BattleManager.setup;
    BattleManager.setup = function () {
        _bmSetup.apply(this, arguments);
        this._gsPreset = GS.cfg.battleMode !== 'off' ? GS.cfg.battleMode : null;
        this._gsWait = 0;
    };

    function battleScene() {
        var s = SceneManager._scene;
        return (s && s instanceof Scene_Battle) ? s : null;
    }
    function canEndBattle() {
        return !!battleScene() && BattleManager._phase && BattleManager._phase !== 'battleEnd' &&
            BattleManager._phase !== 'init';
    }

    function endBattle(mode) {
        if (!canEndBattle()) return false;
        if (mode === 'lose' && !BattleManager.canLose() && GS.cfg.defeatFallback) mode = 'escape';
        var sc = battleScene();
        try { if (sc && sc.endCommandSelection) sc.endCommandSelection(); } catch (e) {}
        if (mode === 'win') {
            $gameTroop.members().forEach(function (e) {
                if (e.isAlive()) {
                    e.addNewState(e.deathStateId());
                    if (e.performCollapse) e.performCollapse();
                }
            });
            BattleManager.processVictory();
        } else if (mode === 'lose') {
            $gameParty.battleMembers().forEach(function (a) {
                if (a.isAlive()) a.addNewState(a.deathStateId());
            });
            BattleManager.processDefeat();
        } else if (mode === 'escape') {
            $gameParty.performEscape();
            SoundManager.playEscape();
            if (BattleManager.onEscapeSuccess) {
                BattleManager.onEscapeSuccess();             // MZ
            } else {
                BattleManager.displayEscapeSuccessMessage(); // MV
                BattleManager._escaped = true;
                BattleManager.processAbort();
            }
        }
        return true;
    }
    GS.endBattle = endBattle;

    var _sbUpdate = Scene_Battle.prototype.update;
    Scene_Battle.prototype.update = function () {
        _sbUpdate.call(this);
        var bm = BattleManager, m = bm._gsPreset;
        if (!m) return;
        var ph = bm._phase;
        if (!ph || ph === 'init' || ph === 'battleEnd') return;
        bm._gsWait = (bm._gsWait || 0) + 1;
        if ($gameMessage.isBusy()) return;
        // 등장 메시지가 끝나고 입력/턴 단계가 될 때까지 대기 (최대 4초)
        if (ph !== 'input' && ph !== 'turn' && bm._gsWait < 240) return;
        bm._gsPreset = null;
        if (GS.cfg.battleOnce) { GS.cfg.battleMode = 'off'; saveCfg(); }
        endBattle(m);
    };

    //-------------------------------------------------------------------------
    // 6. 순간이동 (맵 파일을 읽어 통행 가능한 칸을 찾음)
    //-------------------------------------------------------------------------
    function tilePassable(map, flags, x, y) {
        var w = map.width, h = map.height, d = map.data;
        for (var z = 3; z >= 0; z--) {
            var id = d[(z * h + y) * w + x] || 0;
            var f = flags[id];
            if (f === undefined) continue;
            if ((f & 0x10) !== 0) continue;        // ☆ 타일은 무시
            if ((f & 0x0f) === 0) return true;     // 사방 통행
            if ((f & 0x0f) === 0x0f) return false; // 통행 불가
        }
        return false;
    }

    function findSpot(map) {
        var ts = $dataTilesets[map.tilesetId];
        var flags = (ts && ts.flags) || [];
        var w = map.width, h = map.height;
        var blocked = {};
        (map.events || []).forEach(function (ev) { if (ev) blocked[ev.x + ',' + ev.y] = true; });
        var cx = Math.floor(w / 2), cy = Math.floor(h / 2);
        var cand = [];
        for (var y = 0; y < h; y++) for (var x = 0; x < w; x++) {
            if (!blocked[x + ',' + y] && tilePassable(map, flags, x, y)) {
                cand.push([x, y, (x - cx) * (x - cx) + (y - cy) * (y - cy)]);
            }
        }
        if (!cand.length) return null;
        cand.sort(function (a, b) { return a[2] - b[2]; });
        // 벽 속 고립된 칸을 피하려고 주변에 걸어갈 칸이 충분한 곳을 고름
        var best = cand[0], bestN = -1;
        for (var i = 0; i < cand.length && i < 150; i++) {
            var n = floodCount(map, flags, cand[i][0], cand[i][1], 40);
            if (n > bestN) { best = cand[i]; bestN = n; }
            if (n >= 40) break;
        }
        return { x: best[0], y: best[1] };
    }
    function floodCount(map, flags, sx, sy, limit) {
        var seen = {}, q = [[sx, sy]], n = 0;
        seen[sx + ',' + sy] = 1;
        while (q.length && n < limit) {
            var p = q.shift(); n++;
            var nb = [[1, 0], [-1, 0], [0, 1], [0, -1]];
            for (var i = 0; i < 4; i++) {
                var x = p[0] + nb[i][0], y = p[1] + nb[i][1], k = x + ',' + y;
                if (x < 0 || y < 0 || x >= map.width || y >= map.height || seen[k]) continue;
                seen[k] = 1;
                if (tilePassable(map, flags, x, y)) q.push([x, y]);
            }
        }
        return n;
    }

    function mapFile(id) { return 'data/Map' + ('000' + id).slice(-3) + '.json'; }

    function teleport(mapId, fx, fy, done) {
        if (!(SceneManager._scene instanceof Scene_Map) || $gameParty.inBattle()) {
            done('맵 화면에서만 순간이동할 수 있습니다.');
            return;
        }
        loadJSON(mapFile(mapId), function (map) {
            var spot;
            if (fx !== null && fy !== null) {
                spot = { x: Math.max(0, Math.min(map.width - 1, fx)), y: Math.max(0, Math.min(map.height - 1, fy)) };
            } else {
                spot = findSpot(map);
            }
            if (!spot) {
                spot = { x: Math.floor(map.width / 2), y: Math.floor(map.height / 2) };
                GS.cfg.ghost = true;
            }
            $gamePlayer.reserveTransfer(mapId, spot.x, spot.y, $gamePlayer.direction(), 0);
            done(null, spot);
        }, function () { done('맵 파일을 읽지 못했습니다: ' + mapFile(mapId)); });
    }

    //-------------------------------------------------------------------------
    // 1. 변수 분석 (이벤트 코드를 훑어서 성장 수치로 쓰이는 변수를 찾음)
    //-------------------------------------------------------------------------
    var VA = GS.varAnalysis = { done: false, running: false, maps: 0, mapsTotal: 0, v: {}, s: {} };

    function vref(id) {
        return VA.v[id] || (VA.v[id] = { add: 0, set: 0, cond: 0, shown: 0, srcs: {} });
    }
    function sref(id) {
        return VA.s[id] || (VA.s[id] = { set: 0, cond: 0 });
    }
    function scanList(list, src) {
        if (!list) return;
        for (var i = 0; i < list.length; i++) {
            var c = list[i]; if (!c) continue;
            var p = c.parameters || [];
            switch (c.code) {
                case 122: // 변수 조작
                    for (var id = p[0]; id <= p[1]; id++) {
                        var r = vref(id);
                        if (p[2] === 1 || p[2] === 2) r.add++; else r.set++;
                        r.srcs[src] = 1;
                    }
                    break;
                case 111: // 조건 분기
                    if (p[0] === 1) { vref(p[1]).cond++; vref(p[1]).srcs[src] = 1; if (p[2] === 1) vref(p[3]).cond++; }
                    if (p[0] === 0) sref(p[1]).cond++;
                    break;
                case 121: // 스위치 조작
                    for (var sid = p[0]; sid <= p[1]; sid++) sref(sid).set++;
                    break;
                case 101: case 401: case 102: case 402: case 405: case 108: case 408:
                    var txt = JSON.stringify(p), m, re = /\\\\V\[(\d+)\]/gi;
                    while ((m = re.exec(txt))) vref(Number(m[1])).shown++;
                    break;
            }
        }
    }
    function scanPages(pages, src) {
        (pages || []).forEach(function (pg) {
            if (!pg) return;
            if (pg.conditions) {
                if (pg.conditions.variableValid) vref(pg.conditions.variableId).cond++;
                if (pg.conditions.switch1Valid) sref(pg.conditions.switch1Id).cond++;
                if (pg.conditions.switch2Valid) sref(pg.conditions.switch2Id).cond++;
            }
            if (pg.condition && pg.condition.switchValid) sref(pg.condition.switchId).cond++;
            scanList(pg.list, src);
        });
    }
    function analyzeVars(onUpdate) {
        if (VA.running || VA.done) return;
        VA.running = true;
        ($dataCommonEvents || []).forEach(function (ce) { if (ce) scanList(ce.list, 'CE'); });
        ($dataTroops || []).forEach(function (t) { if (t) scanPages(t.pages, 'TR'); });
        var ids = [];
        ($dataMapInfos || []).forEach(function (mi) { if (mi) ids.push(mi.id); });
        VA.mapsTotal = ids.length;
        var idx = 0;
        function next() {
            if (idx >= ids.length) { VA.running = false; VA.done = true; onUpdate(); return; }
            var id = ids[idx++];
            loadJSON(mapFile(id), function (map) {
                (map.events || []).forEach(function (ev) { if (ev) scanPages(ev.pages, 'M' + id); });
                VA.maps++; if (VA.maps % 10 === 0) onUpdate();
                setTimeout(next, 0);
            }, function () { VA.maps++; setTimeout(next, 0); });
        }
        next();
    }
    function varScore(r) {
        if (!r) return 0;
        return r.add * 3 + r.cond * 2 + r.shown * 4 + r.set;
    }

    //-------------------------------------------------------------------------
    // 능력치 편집
    //-------------------------------------------------------------------------
    function setParamTarget(actor, pid, target) {
        target = Math.max(0, Number(target) || 0);
        for (var i = 0; i < 4; i++) {
            var cur = actor.param(pid);
            if (cur === target) break;
            var rate = (actor.paramRate(pid) || 1) * (actor.paramBuffRate ? actor.paramBuffRate(pid) : 1) || 1;
            var d = Math.round((target - cur) / rate);
            if (d === 0) d = target > cur ? 1 : -1;
            actor.addParam(pid, d);
        }
        actor.refresh();
    }
    function setLevel(actor, lv) {
        lv = Math.max(1, Math.min(actor.maxLevel(), Number(lv) || 1));
        actor.changeLevel(lv, false);
        actor.refresh();
    }

    //-------------------------------------------------------------------------
    // 입력 차단 (창이 열려 있으면 게임이 키/마우스를 받지 않게)
    //-------------------------------------------------------------------------
    var _inKeyDown = Input._onKeyDown;
    Input._onKeyDown = function (event) {
        if (GS.open || GS.sidebar) return;
        return _inKeyDown.call(this, event);
    };
    var touchFns = ['_onMouseDown', '_onTouchStart', '_onPointerDown', '_onWheel', '_onRightButtonDown', '_onLeftButtonDown'];
    touchFns.forEach(function (fn) {
        if (typeof TouchInput[fn] === 'function') {
            var orig = TouchInput[fn];
            TouchInput[fn] = function () {
                if (GS.open || GS.sidebar) return;
                return orig.apply(this, arguments);
            };
        }
    });

    function isHotkey(e) {
        if (e.code === 'Backquote' && KEY_MAIN === 192) return true;
        return e.keyCode === KEY_MAIN;
    }
    window.addEventListener('keydown', function (e) {
        if (isHotkey(e)) {
            e.preventDefault(); e.stopImmediatePropagation();
            if (e.repeat) return;
            if (e.shiftKey) toggleSidebar(); else toggleMain();
            return;
        }
        if (e.keyCode === 27 && (GS.open || GS.sidebar)) {
            e.preventDefault(); e.stopImmediatePropagation();
            closeAll();
        }
    }, true);

    //-------------------------------------------------------------------------
    // UI (게임 화면 위에 띄우는 HTML 오버레이, 외부 라이브러리 없음)
    //-------------------------------------------------------------------------
    var root = null, mainBox = null, sideBox = null, toastBox = null;

    var CSS = '' +
        '#gs-root{position:fixed;left:0;top:0;right:0;bottom:0;z-index:99999;font-family:"Malgun Gothic","맑은 고딕","Apple SD Gothic Neo",sans-serif;font-size:13px;color:#e8e6f0;display:none}' +
        '#gs-root *{box-sizing:border-box}' +
        '#gs-back{position:absolute;left:0;top:0;right:0;bottom:0;background:rgba(0,0,0,.25)}' +
        '.gs-win{position:absolute;background:rgba(22,20,32,.96);border:1px solid #5b4f8a;border-radius:8px;box-shadow:0 6px 30px rgba(0,0,0,.6);display:flex;flex-direction:column;overflow:hidden}' +
        '#gs-main{left:50%;top:50%;transform:translate(-50%,-50%);width:min(760px,94vw);height:min(560px,92vh)}' +
        '#gs-side{right:8px;top:8px;bottom:8px;width:min(400px,60vw)}' +
        '.gs-head{display:flex;align-items:center;gap:6px;padding:8px 10px;background:#2c2545;border-bottom:1px solid #5b4f8a}' +
        '.gs-title{font-weight:bold;font-size:14px;margin-right:auto;color:#ffd479}' +
        '.gs-tabs{display:flex;gap:2px;padding:6px 8px 0;background:#221d36;flex-wrap:wrap}' +
        '.gs-tab{padding:6px 11px;border-radius:6px 6px 0 0;cursor:pointer;color:#b8b2d0;background:#2a2442}' +
        '.gs-tab.on{background:#3d3463;color:#fff;font-weight:bold}' +
        '.gs-body{flex:1;overflow:auto;padding:10px}' +
        '.gs-row{display:flex;align-items:center;gap:6px;padding:4px 2px;border-bottom:1px solid rgba(255,255,255,.06)}' +
        '.gs-row:hover{background:rgba(255,255,255,.04)}' +
        '.gs-name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
        '.gs-orig{color:#8a84a3;font-size:11px;margin-left:5px}' +
        '.gs-btn{background:#4a3f78;color:#fff;border:1px solid #6b5da8;border-radius:5px;padding:3px 9px;cursor:pointer;font:inherit;white-space:nowrap}' +
        '.gs-btn:hover{background:#5d5196}' +
        '.gs-btn.on{background:#d9a52b;border-color:#ffd479;color:#1d1a2a;font-weight:bold}' +
        '.gs-btn.red{background:#7a3045;border-color:#a8495f}' +
        '.gs-btn.sm{padding:2px 6px;font-size:12px}' +
        '.gs-in{background:#141220;color:#fff;border:1px solid #5b4f8a;border-radius:5px;padding:3px 6px;font:inherit;width:90px}' +
        '.gs-in.wide{width:100%}' +
        '.gs-sec{margin:12px 0 6px;color:#ffd479;font-weight:bold}' +
        '.gs-sec:first-child{margin-top:0}' +
        '.gs-note{color:#9a94b5;font-size:12px;margin:4px 0 8px;line-height:1.5}' +
        '.gs-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:4px 14px}' +
        '.gs-cnt{min-width:34px;text-align:right;color:#ffd479}' +
        '.gs-icon{width:24px;height:24px;flex:none;background-repeat:no-repeat;image-rendering:pixelated}' +
        '.gs-tag{font-size:11px;padding:0 5px;border-radius:4px;background:#3a3358;color:#cfc8ee;margin-left:4px}' +
        '.gs-tag.hot{background:#7a5a1d;color:#ffe2a0}' +
        '.gs-cur{background:rgba(217,165,43,.18)}' +
        '#gs-toast{position:absolute;left:50%;bottom:30px;transform:translateX(-50%);background:#2c2545;border:1px solid #ffd479;padding:8px 16px;border-radius:6px;display:none}' +
        '.gs-chip{display:inline-block;padding:1px 6px;margin-left:6px;border-radius:4px;background:#d9a52b;color:#1d1a2a;font-size:11px;font-weight:bold}';

    function el(tag, attrs, kids) {
        var e = document.createElement(tag);
        if (attrs) for (var k in attrs) {
            if (k === 'text') e.textContent = attrs[k];
            else if (k === 'cls') e.className = attrs[k];
            else if (k === 'style') e.style.cssText = attrs[k];
            else if (k.indexOf('on') === 0) e.addEventListener(k.slice(2), attrs[k]);
            else e.setAttribute(k, attrs[k]);
        }
        (kids || []).forEach(function (c) {
            if (c === null || c === undefined) return;
            e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
        });
        return e;
    }
    function btn(label, fn, cls) {
        return el('button', { cls: 'gs-btn ' + (cls || ''), text: label, onclick: function (ev) { ev.stopPropagation(); fn(ev); } });
    }
    function input(val, cls) {
        return el('input', { cls: 'gs-in ' + (cls || ''), value: String(val) });
    }
    function nameNode(orig) {
        var ko = tr(orig), o = stripCodes(orig);
        var n = el('span', { cls: 'gs-name', title: o });
        n.appendChild(document.createTextNode(ko || '(이름 없음)'));
        if (ko !== o && o) n.appendChild(el('span', { cls: 'gs-orig', text: o }));
        return n;
    }
    function iconNode(index) {
        var d = el('span', { cls: 'gs-icon' });
        if (index > 0) {
            var x = (index % 16) * 32, y = Math.floor(index / 16) * 32;
            d.style.backgroundImage = 'url(img/system/IconSet.png)';
            d.style.backgroundSize = '384px auto';
            d.style.backgroundPosition = (-x * 0.75) + 'px ' + (-y * 0.75) + 'px';
        }
        return d;
    }
    function toast(msg) {
        if (!toastBox) return;
        toastBox.textContent = msg;
        toastBox.style.display = 'block';
        clearTimeout(toast._t);
        toast._t = setTimeout(function () { toastBox.style.display = 'none'; }, 2200);
    }
    GS.toast = toast;

    function buildRoot() {
        if (root) return;
        var st = el('style', { text: CSS });
        document.head.appendChild(st);
        root = el('div', { id: 'gs-root' });
        var back = el('div', { id: 'gs-back', onmousedown: function () { closeAll(); } });
        mainBox = el('div', { id: 'gs-main', cls: 'gs-win' });
        sideBox = el('div', { id: 'gs-side', cls: 'gs-win' });
        toastBox = el('div', { id: 'gs-toast' });
        root.appendChild(back); root.appendChild(mainBox); root.appendChild(sideBox); root.appendChild(toastBox);
        // 창 안의 입력이 게임으로 새지 않게 막음
        ['keydown', 'keyup', 'keypress', 'mousedown', 'mouseup', 'mousemove', 'click', 'wheel',
         'touchstart', 'touchmove', 'touchend', 'pointerdown', 'pointerup', 'pointermove', 'contextmenu'].forEach(function (t) {
            root.addEventListener(t, function (e) { e.stopPropagation(); }, false);
        });
        root.addEventListener('contextmenu', function (e) { e.preventDefault(); });
        document.body.appendChild(root);
    }

    function updateVisibility() {
        buildRoot();
        root.style.display = (GS.open || GS.sidebar) ? 'block' : 'none';
        mainBox.style.display = GS.open ? 'flex' : 'none';
        sideBox.style.display = GS.sidebar ? 'flex' : 'none';
        if (GS.open && GS.sidebar) mainBox.style.left = 'calc(50% - 200px)'; else mainBox.style.left = '50%';
        if (GS.open || GS.sidebar) Input.clear();
    }
    function toggleMain() { GS.open = !GS.open; if (GS.open) { render(); loadDict(); } updateVisibility(); }
    function toggleSidebar() { GS.sidebar = !GS.sidebar; if (GS.sidebar) { renderSide(); loadDict(); } updateVisibility(); }
    function closeAll() { GS.open = false; GS.sidebar = false; updateVisibility(); }
    GS.toggle = toggleMain; GS.toggleItems = toggleSidebar; GS.close = closeAll;

    //---------------------------------------------------------------- 메인 창
    var TABS = [
        ['stats', '능력치'],
        ['deep', '고유 수치·숫자 찾기'],
        ['vars', '변수·스위치'],
        ['battle', '전투'],
        ['move', '이동'],
        ['tele', '순간이동'],
        ['locks', '고정 목록']
    ];
    var bodyScroll = {};

    function render() {
        buildRoot();
        var oldBody = mainBox.querySelector('.gs-body');
        if (oldBody) bodyScroll[GS.tab] = oldBody.scrollTop;
        mainBox.innerHTML = '';
        var head = el('div', { cls: 'gs-head' }, [
            el('span', { cls: 'gs-title', text: 'GameShelf 치트  ( ` 키로 열고 닫기 )' }),
            btn('아이템 사이드바', function () { toggleSidebar(); }, GS.sidebar ? 'on' : ''),
            btn('닫기', closeAll, 'red')
        ]);
        var tabs = el('div', { cls: 'gs-tabs' });
        TABS.forEach(function (t) {
            tabs.appendChild(el('div', {
                cls: 'gs-tab' + (GS.tab === t[0] ? ' on' : ''), text: t[1],
                onclick: function () { GS.tab = t[0]; render(); }
            }));
        });
        var body = el('div', { cls: 'gs-body' });
        try {
            ({ stats: tabStats, deep: tabDeep, vars: tabVars, battle: tabBattle, move: tabMove, tele: tabTele, locks: tabLocks })[GS.tab](body);
        } catch (e) {
            body.appendChild(el('div', { cls: 'gs-note', text: '이 화면을 그리는 중 오류: ' + e.message }));
        }
        mainBox.appendChild(head); mainBox.appendChild(tabs); mainBox.appendChild(body);
        body.scrollTop = bodyScroll[GS.tab] || 0;
    }
    GS.render = render;

    function termParam(i) {
        var t = $dataSystem.terms.params || [];
        var def = ['최대 HP', '최대 MP', '공격력', '방어력', '마법력', '마법 방어', '민첩성', '운'];
        return t[i] ? tr(t[i]) : def[i];
    }
    function termBasic(i, def) {
        var t = $dataSystem.terms.basic || [];
        return t[i] ? tr(t[i]) : def;
    }

    //---- 능력치
    function tabStats(body) {
        if (!$gameParty || !$gameParty.members) { body.appendChild(el('div', { cls: 'gs-note', text: '게임 시작 후 사용할 수 있습니다.' })); return; }
        var cr = customRoots();
        if (cr.length) {
            body.appendChild(el('div', { cls: 'gs-row', style: 'background:rgba(217,165,43,.15);border-radius:6px;padding:6px 8px;margin-bottom:6px' }, [
                el('span', { cls: 'gs-name', style: 'white-space:normal', text: '이 게임은 제작자가 따로 만든 수치(' + cr.map(function (r) { return r.name; }).join(', ') + ')를 씁니다. 화면에 보이는 진짜 능력치는 아래 기본 능력치가 아니라 이쪽에 있을 가능성이 큽니다.' }),
                btn('고유 수치 보기', function () { GS.tab = 'deep'; render(); })
            ]));
        }
        var gIn = input($gameParty.gold());
        body.appendChild(el('div', { cls: 'gs-sec', text: '소지금 (' + tr($dataSystem.currencyUnit || '') + ')' }));
        body.appendChild(el('div', { cls: 'gs-row' }, [
            el('span', { cls: 'gs-name', text: '현재 ' + $gameParty.gold() }), gIn,
            btn('적용', function () { $gameParty.gainGold((Number(gIn.value) || 0) - $gameParty.gold()); render(); }),
            btn('최대', function () { $gameParty.gainGold($gameParty.maxGold()); render(); }),
            lockBtn('$gameParty', ['_gold'], '소지금')
        ]));
        body.appendChild(el('div', { cls: 'gs-note', text: '능력치 이름은 게임 데이터의 용어 설정에서 읽습니다. 게임이 이름을 바꿨어도(예: 体力, 精力) 내부 번호로 찾기 때문에 같은 값을 고칩니다. 게임이 변수로 만든 수치(호감도 등)는 "변수·스위치" 탭에 있습니다.' }));
        $gameParty.members().forEach(function (a) {
            body.appendChild(el('div', { cls: 'gs-sec' }, [tr(a.name()) + '  ', el('span', { cls: 'gs-orig', text: tr(a.currentClass() ? a.currentClass().name : '') })]));
            var grid = el('div', { cls: 'gs-grid' });
            function row(label, cur, apply, lockKey) {
                var i = input(cur);
                grid.appendChild(el('div', { cls: 'gs-row' }, [
                    el('span', { cls: 'gs-name', text: label + '  (' + cur + ')' }), i,
                    btn('적용', function () { apply(Number(i.value) || 0); render(); }, 'sm'),
                    lockKey ? lockBtn('$gameActors', ['_data', a.actorId(), lockKey], tr(a.name()) + ' ' + label) : null
                ]));
            }
            row(termBasic(0, '레벨'), a.level, function (v) { setLevel(a, v); });
            row(termBasic(8, '경험치') + ' EXP', a.currentExp(), function (v) { a.changeExp(Math.max(0, v), false); a.refresh(); });
            row(termBasic(2, 'HP') + ' 현재', a.hp, function (v) { a.setHp(v); }, '_hp');
            row(termBasic(4, 'MP') + ' 현재', a.mp, function (v) { a.setMp(v); }, '_mp');
            row(termBasic(6, 'TP') + ' 현재', a.tp, function (v) { a.setTp(v); }, '_tp');
            for (var p = 0; p < 8; p++) (function (pid) {
                row(termParam(pid), a.param(pid), function (v) {
                    setParamTarget(a, pid, v);
                    if (pid === 0) a.setHp(a.mhp);
                    if (pid === 1) a.setMp(a.mmp);
                });
            })(p);
            body.appendChild(grid);
            body.appendChild(el('div', { cls: 'gs-row' }, [
                el('span', { cls: 'gs-name' }),
                btn('완전 회복', function () { a.recoverAll(); render(); }, 'sm'),
                btn('레벨 최대', function () { setLevel(a, a.maxLevel()); render(); }, 'sm'),
                btn('레벨 +10', function () { setLevel(a, a.level + 10); render(); }, 'sm')
            ]));
        });
    }

    //---- 변수·스위치
    var varFilter = '', varMode = 'score', swFilter = '';
    //---- 회상 전부 해금
    var RECOLL_NAME_RE = /回想|鑑賞|ＣＧ|(^|[^A-Za-z])CG|ギャラリー|gallery|recollect|メモリー|おまけ|閲覧|回顧|シーン解放|scene ?unlock/i;
    // '회상 중', '회상 모드', '등록 처리' 같은 진행용 스위치는 켜면 이벤트가 꼬일 수 있어서 제외
    var RECOLL_SKIP_RE = /中|モード|mode|フラグ|flag|登録|禁止|判定|処理|開始|終了|実行|用$/i;
    var ALLOPEN_RE = /^(on)?(all_?open|open_?all|unlock_?all|all_?unlock|release_?all|all_?release|全開放|全解放)$/i;

    function collectSwitchIds(obj, out, depth) {
        if (!obj || typeof obj !== 'object' || depth > 6) return;
        for (var k in obj) {
            if (!obj.hasOwnProperty(k)) continue;
            var v = obj[k];
            if (/switch/i.test(k) && /^\d+$/.test(String(v)) && Number(v) > 0) out.push(Number(v));
            else if (typeof v === 'string' && v.charAt(0) === '{' || typeof v === 'string' && v.charAt(0) === '[') {
                try { collectSwitchIds(JSON.parse(v), out, depth + 1); } catch (e) {}
            } else if (typeof v === 'object') collectSwitchIds(v, out, depth + 1);
        }
    }
    function uniq(a) {
        var o = [], seen = {};
        a.forEach(function (x) { if (!seen[x]) { seen[x] = 1; o.push(x); } });
        return o.sort(function (x, y) { return x - y; });
    }
    function detectRecollection() {
        var r = { plugin: null, funcs: [], named: [] };
        // 1) RecollectionMode (rinne_grid) 및 회상 플러그인 설정에 들어 있는 스위치
        var ids = [], names = [];
        if (window.rngd_recollection_mode_settings) {
            collectSwitchIds(window.rngd_recollection_mode_settings, ids, 0);
            names.push('RecollectionMode');
        }
        (window.$plugins || []).forEach(function (pl) {
            if (pl && pl.status && /recollect|回想|memory|gallery|omake|extra/i.test(pl.name) && pl.name !== 'GS_Cheat') {
                var before = ids.length;
                collectSwitchIds(pl.parameters, ids, 0);
                if (ids.length > before && names.indexOf(pl.name) < 0) names.push(pl.name);
            }
        });
        ids = uniq(ids).filter(function (id) { return id < ($dataSystem.switches || []).length; });
        if (ids.length) r.plugin = { names: names, ids: ids };
        // 2) 게임에 들어 있는 '전체 개방' 기능
        Object.keys(window).forEach(function (k) {
            var obj;
            try { obj = window[k]; } catch (e) { return; }
            if (!obj || (typeof obj !== 'function' && typeof obj !== 'object')) return;
            if (!/^(Scene_|Window_|Game_|\$game|[A-Z])/.test(k)) return;
            [obj, obj.prototype].forEach(function (o, isProto) {
                if (!o) return;
                var keys;
                try { keys = Object.getOwnPropertyNames(o); } catch (e) { return; }
                keys.forEach(function (m) {
                    if (!ALLOPEN_RE.test(m)) return;
                    var f;
                    try { f = o[m]; } catch (e) { return; }
                    var src = '';
                    try { src = Function.prototype.toString.call(f); } catch (e) {}
                    if (!/setValue|for\s*\(|forEach|save|\[[^\]]+\]\s*=\s*true/.test(src)) return;   // 값만 돌려주는 함수는 제외
                    if (typeof f === 'function') r.funcs.push({ label: k + (isProto ? '.prototype.' : '.') + m, fn: f, self: isProto ? null : o });
                });
            });
        });
        // 3) 이름으로 찾은 회상 스위치
        var sn = $dataSystem.switches || [];
        for (var i = 1; i < sn.length; i++) {
            if (sn[i] && RECOLL_NAME_RE.test(sn[i]) && !RECOLL_SKIP_RE.test(sn[i]) && (!r.plugin || r.plugin.ids.indexOf(i) < 0)) r.named.push(i);
        }
        return r;
    }
    function makeDummy() {
        if (typeof Proxy === 'undefined') return {};
        var p, target = function () {};
        p = new Proxy(target, {
            get: function (t, k) {
                if (typeof Symbol !== 'undefined' && k === Symbol.toPrimitive) return function () { return 0; };
                if (k === 'length') return 0;
                return p;
            },
            set: function () { return true; },
            apply: function () { return p; }
        });
        return p;
    }
    function backupSaveFiles() {
        try {
            var fs = require('fs'), path = require('path');
            var dir = StorageManager.fileDirectoryPath ? StorageManager.fileDirectoryPath() :
                StorageManager.localFileDirectoryPath ? StorageManager.localFileDirectoryPath() : '';
            if (!dir || !fs.existsSync(dir)) return '';
            var d = new Date(), pad = function (n) { return ('0' + n).slice(-2); };
            var dst = path.join(dir, 'gs_backup_' + d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate()) + '_' + pad(d.getHours()) + pad(d.getMinutes()) + pad(d.getSeconds()));
            fs.mkdirSync(dst);
            fs.readdirSync(dir).forEach(function (f) {
                var sp = path.join(dir, f);
                if (fs.statSync(sp).isFile()) fs.writeFileSync(path.join(dst, f), fs.readFileSync(sp));
            });
            return dst;
        } catch (e) { return ''; }
    }
    GS.detectRecollection = detectRecollection;
    function recollBox(body) {
        var r = detectRecollection();
        var box = el('div', { style: 'border:1px solid #6b5da8;border-radius:6px;padding:8px;margin-bottom:10px;background:rgba(217,165,43,.08)' });
        box.appendChild(el('div', { cls: 'gs-sec', style: 'margin-top:0', text: '🔓 회상 전부 해금' }));
        var any = false;
        function done(msg) {
            var bk = GS._recBackup;
            GS._recMsg = '✔ ' + msg + (bk ? ' · 세이브 백업: ' + bk : '') +
                ' — 다른 세이브나 회상 화면에도 남기려면 지금 세이브를 한 번 해주세요.';
            toast(msg);
            render();
        }
        function backupOnce() {
            if (!GS._recBackup) GS._recBackup = backupSaveFiles();
        }
        if (r.plugin) {
            any = true;
            var on = r.plugin.ids.filter(function (id) { return $gameSwitches.value(id); }).length;
            box.appendChild(el('div', { cls: 'gs-row' }, [
                el('span', { cls: 'gs-name', style: 'white-space:normal', text: '회상 플러그인 (' + r.plugin.names.join(', ') + '): 장면 스위치 ' + r.plugin.ids.length + '개 중 ' + on + '개 열림' }),
                btn('전부 켜기', function () {
                    backupOnce();
                    r.plugin.ids.forEach(function (id) { $gameSwitches.setValue(id, true); });
                    done('회상 스위치 ' + r.plugin.ids.length + '개를 켰습니다');
                })
            ]));
        }
        r.funcs.forEach(function (f) {
            any = true;
            box.appendChild(el('div', { cls: 'gs-row' }, [
                el('span', { cls: 'gs-name', style: 'white-space:normal', text: '게임 자체 전체 개방 기능: ' + f.label }),
                btn('실행', function () {
                    backupOnce();
                    try {
                        f.fn.call(f.self || makeDummy());
                        done('게임의 전체 개방 기능을 실행했습니다 (' + f.label + ')');
                    } catch (e) {
                        toast('실행 중 오류: ' + e.message);
                    }
                })
            ]));
        });
        if (r.named.length) {
            any = true;
            var sn = $dataSystem.switches;
            var nm = el('span', { cls: 'gs-name', style: 'white-space:normal',
                text: '이름에 회상·CG가 들어간 스위치 ' + r.named.length + '개: ' + r.named.slice(0, 6).map(function (i) { return i + ' ' + tr(sn[i]); }).join(', ') + (r.named.length > 6 ? ' …' : '') });
            nm.title = r.named.map(function (i) { return i + ' ' + tr(sn[i]) + (tr(sn[i]) !== sn[i] ? ' (' + sn[i] + ')' : ''); }).join('\n');
            box.appendChild(el('div', { cls: 'gs-row' }, [nm,
                btn('전부 켜기', function () {
                    backupOnce();
                    r.named.forEach(function (id) { $gameSwitches.setValue(id, true); });
                    done('스위치 ' + r.named.length + '개를 켰습니다');
                })
            ]));
        }
        if (!any) {
            box.appendChild(el('div', { cls: 'gs-note', text: '자동으로 찾지 못했습니다. 아래 스위치 목록에서 회상 관련 스위치를 직접 켜거나, "고유 수치·숫자 찾기" 탭에서 게임이 만든 회상 기록(참/거짓)을 찾아 켜 보세요.' }));
        } else {
            box.appendChild(el('div', { cls: 'gs-note', style: 'margin-bottom:0', text: GS._recMsg ||
                '누르기 전에 세이브 폴더를 자동으로 백업합니다(save\\gs_backup_날짜). 회상이 여러 세이브를 훑어서 열리는 게임은 해금 후 세이브를 한 번 해야 회상 화면에 반영됩니다.' }));
        }
        body.appendChild(box);
    }

    function tabVars(body) {
        if (!$gameVariables) return;
        try { recollBox(body); } catch (e) { body.appendChild(el('div', { cls: 'gs-note', text: '회상 해금 확인 중 오류: ' + e.message })); }
        if (!VA.done && !VA.running) analyzeVars(function () { if (GS.open && GS.tab === 'vars') render(); });
        body.appendChild(el('div', { cls: 'gs-note', text:
            '이름만 보지 않고 게임 이벤트 코드를 전부 훑어서, 값이 자주 더해지거나(증가) 조건 분기에 쓰이거나 대사·화면에 표시되는(표시) 변수를 위로 올립니다. ' +
            (VA.done ? '분석 완료 (맵 ' + VA.maps + '개).' : '분석 중… 맵 ' + VA.maps + ' / ' + VA.mapsTotal) }));
        var f = input(varFilter, 'wide'); f.placeholder = '변수 검색 (번호·이름)';
        f.addEventListener('input', function () { varFilter = f.value; redraw(); });
        body.appendChild(el('div', { cls: 'gs-row' }, [f,
            btn('중요도순', function () { varMode = 'score'; render(); }, varMode === 'score' ? 'on' : ''),
            btn('번호순', function () { varMode = 'id'; render(); }, varMode === 'id' ? 'on' : '')]));
        var list = el('div');
        body.appendChild(list);
        var names = $dataSystem.variables || [];
        function redraw() {
            list.innerHTML = '';
            var ids = [];
            for (var i = 1; i < names.length; i++) {
                var nm = names[i] || '';
                var r = VA.v[i];
                if (!nm && !r && !$gameVariables.value(i)) continue;
                if (varFilter) {
                    var q = varFilter.toLowerCase();
                    if (String(i) !== q && tr(nm).toLowerCase().indexOf(q) < 0 && nm.toLowerCase().indexOf(q) < 0) continue;
                }
                ids.push(i);
            }
            if (varMode === 'score') ids.sort(function (a, b) { return varScore(VA.v[b]) - varScore(VA.v[a]) || a - b; });
            ids.slice(0, 400).forEach(function (id) {
                var r = VA.v[id], tags = [];
                if (r && r.add) tags.push(el('span', { cls: 'gs-tag hot', text: '증가 ' + r.add }));
                if (r && r.shown) tags.push(el('span', { cls: 'gs-tag hot', text: '표시 ' + r.shown }));
                if (r && r.cond) tags.push(el('span', { cls: 'gs-tag', text: '조건 ' + r.cond }));
                var v = $gameVariables.value(id);
                var i = input(typeof v === 'number' ? v : JSON.stringify(v));
                var nm = el('span', { cls: 'gs-name' }, [('000' + id).slice(-4) + '  ' + (tr(names[id]) || '(이름 없음)')]);
                if (names[id] && tr(names[id]) !== names[id]) nm.appendChild(el('span', { cls: 'gs-orig', text: names[id] }));
                tags.forEach(function (t) { nm.appendChild(t); });
                list.appendChild(el('div', { cls: 'gs-row' }, [nm, i,
                    btn('적용', function () {
                        var n = Number(i.value);
                        $gameVariables.setValue(id, isNaN(n) ? i.value : n);
                        toast('변수 ' + id + ' = ' + $gameVariables.value(id));
                    }, 'sm'),
                    btn('+100', function () { $gameVariables.setValue(id, (Number($gameVariables.value(id)) || 0) + 100); i.value = $gameVariables.value(id); }, 'sm'),
                    lockBtn('$gameVariables', ['_data', id], '변수 ' + id + ' ' + (tr(names[id]) || ''))
                ]));
            });
            if (ids.length > 400) list.appendChild(el('div', { cls: 'gs-note', text: '400개까지만 표시합니다. 검색으로 좁혀 주세요.' }));
        }
        redraw();

        body.appendChild(el('div', { cls: 'gs-sec', text: '스위치 (퀘스트·진행 상태)' }));
        var sf = input(swFilter, 'wide'); sf.placeholder = '스위치 검색';
        body.appendChild(sf);
        var sl = el('div');
        body.appendChild(sl);
        var sn = $dataSystem.switches || [];
        function redrawSw() {
            sl.innerHTML = '';
            var n = 0;
            for (var i = 1; i < sn.length && n < 300; i++) {
                var nm = sn[i] || '';
                if (!nm && !VA.s[i]) continue;
                if (swFilter) {
                    var q = swFilter.toLowerCase();
                    if (String(i) !== q && tr(nm).toLowerCase().indexOf(q) < 0 && nm.toLowerCase().indexOf(q) < 0) continue;
                }
                n++;
                (function (id) {
                    var on = $gameSwitches.value(id);
                    var b = btn(on ? 'ON' : 'OFF', function () {
                        $gameSwitches.setValue(id, !$gameSwitches.value(id));
                        b.textContent = $gameSwitches.value(id) ? 'ON' : 'OFF';
                        b.className = 'gs-btn sm ' + ($gameSwitches.value(id) ? 'on' : '');
                    }, 'sm ' + (on ? 'on' : ''));
                    var nmEl = el('span', { cls: 'gs-name' }, [('000' + id).slice(-4) + '  ' + (tr(nm) || '(이름 없음)')]);
                    if (nm && tr(nm) !== nm) nmEl.appendChild(el('span', { cls: 'gs-orig', text: nm }));
                    sl.appendChild(el('div', { cls: 'gs-row' }, [nmEl, b]));
                })(i);
            }
        }
        sf.addEventListener('input', function () { swFilter = sf.value; redrawSw(); });
        redrawSw();
    }

    //---- 전투
    function tabBattle(body) {
        var c = GS.cfg;
        body.appendChild(el('div', { cls: 'gs-sec', text: '전투 결과 예약' }));
        body.appendChild(el('div', { cls: 'gs-note', text: '전투가 시작되면 바로 정해 둔 결과로 끝냅니다. 이벤트 전투라면 그 결과에 맞는 승리/패배/도망 분기 이벤트로 이어집니다.' }));
        var modes = [['off', '끔'], ['win', '승리'], ['lose', '패배'], ['escape', '도망']];
        var r = el('div', { cls: 'gs-row' });
        modes.forEach(function (m) {
            r.appendChild(btn(m[1], function () { c.battleMode = m[0]; saveCfg(); render(); }, c.battleMode === m[0] ? 'on' : ''));
        });
        body.appendChild(r);
        body.appendChild(el('div', { cls: 'gs-row' }, [
            el('span', { cls: 'gs-name', text: '적용 범위' }),
            btn('다음 전투 1번만', function () { c.battleOnce = true; saveCfg(); render(); }, c.battleOnce ? 'on' : ''),
            btn('모든 전투', function () { c.battleOnce = false; saveCfg(); render(); }, !c.battleOnce ? 'on' : '')
        ]));
        body.appendChild(el('div', { cls: 'gs-row' }, [
            el('span', { cls: 'gs-name', text: '패배 분기가 없는 전투에서 "패배"를 고르면' }),
            btn('도망으로 대체 (게임오버 방지)', function () { c.defeatFallback = true; saveCfg(); render(); }, c.defeatFallback ? 'on' : ''),
            btn('그대로 패배', function () { c.defeatFallback = false; saveCfg(); render(); }, !c.defeatFallback ? 'on' : '')
        ]));
        body.appendChild(el('div', { cls: 'gs-sec', text: '지금 진행 중인 전투' }));
        if (canEndBattle()) {
            body.appendChild(el('div', { cls: 'gs-row' }, [
                el('span', { cls: 'gs-name', text: '패배 분기 ' + (BattleManager.canLose() ? '있음' : '없음 (지면 게임오버)') + ' · 도망 ' + (BattleManager.canEscape() ? '가능' : '불가') }),
                btn('즉시 승리', function () { closeAll(); endBattle('win'); }),
                btn('즉시 패배', function () { closeAll(); endBattle('lose'); }),
                btn('즉시 도망', function () { closeAll(); endBattle('escape'); })
            ]));
        } else {
            body.appendChild(el('div', { cls: 'gs-note', text: '전투 중이 아닙니다.' }));
        }
    }

    //---- 이동
    function tabMove(body) {
        var c = GS.cfg;
        body.appendChild(el('div', { cls: 'gs-sec', text: '이동 속도 (달리기 속도 기준)' }));
        var r = el('div', { cls: 'gs-row' });
        [[0, '기본'], [1, '1배'], [2, '2배'], [3, '3배'], [5, '5배']].forEach(function (s) {
            r.appendChild(btn(s[1], function () { c.speed = s[0]; saveCfg(); render(); }, c.speed === s[0] ? 'on' : ''));
        });
        body.appendChild(r);
        body.appendChild(el('div', { cls: 'gs-note', text: '"1배"는 걷기만 해도 항상 달리기 속도입니다. 동료(따라오는 캐릭터)도 같은 속도로 움직입니다.' }));

        body.appendChild(el('div', { cls: 'gs-sec', text: '고스트 모드' }));
        body.appendChild(el('div', { cls: 'gs-row' }, [
            el('span', { cls: 'gs-name', text: '벽 통과 + 밟거나 닿는 이벤트 무시 + 랜덤 전투 없음' }),
            btn(c.ghost ? '켜짐' : '꺼짐', function () { c.ghost = !c.ghost; saveCfg(); render(); }, c.ghost ? 'on' : '')
        ]));
        body.appendChild(el('div', { cls: 'gs-row' }, [
            el('span', { cls: 'gs-name', text: '고스트 중에도 결정키로 말 걸기·조사 허용' }),
            btn(c.ghostAction ? '허용' : '막음', function () { c.ghostAction = !c.ghostAction; saveCfg(); render(); }, c.ghostAction ? 'on' : '')
        ]));
        body.appendChild(el('div', { cls: 'gs-note', text: '자동 실행·병렬 처리 이벤트(컷신 등)는 게임 진행에 필요해서 막지 않습니다.' }));

        body.appendChild(el('div', { cls: 'gs-sec', text: '기타' }));
        body.appendChild(el('div', { cls: 'gs-row' }, [
            el('span', { cls: 'gs-name', text: '랜덤 인카운터 끄기' }),
            btn(c.noEncounter ? '켜짐' : '꺼짐', function () { c.noEncounter = !c.noEncounter; saveCfg(); render(); }, c.noEncounter ? 'on' : '')
        ]));
    }

    //---- 고유 수치 · 숫자 찾기
    // 게임이 세이브 파일에 저장하는 데이터(제작자가 만든 객체 포함)를 훑어서 숫자를 찾고 고친다.
    var STD_SAVE = { system: 1, screen: 1, timer: 1, switches: 1, variables: 1, selfSwitches: 1,
        actors: 1, party: 1, map: 1, player: 1 };
    var STD_GLOBALS = { $gameTemp: 1, $gameSystem: 1, $gameScreen: 1, $gameTimer: 1, $gameMessage: 1,
        $gameSwitches: 1, $gameVariables: 1, $gameSelfSwitches: 1, $gameActors: 1, $gameParty: 1,
        $gameTroop: 1, $gameMap: 1, $gamePlayer: 1 };
    var WORDS = {
        level: '레벨', lv: '레벨', exp: '경험치', gold: '돈', money: '돈', coin: '코인', point: '포인트', points: '포인트',
        hp: 'HP', mp: 'MP', tp: 'TP', max: '최대', min: '최소', now: '현재', current: '현재', value: '값', values: '값들',
        count: '횟수', num: '횟수', times: '횟수', time: '시간', day: '일', days: '일수', turn: '턴',
        love: '애정', affection: '호감도', favor: '호감도', like: '호감', lust: '음란도', lewd: '음란도', ero: '에로',
        heat: '발정', addiction: '중독', train: '조교', trains: '조교', training: '훈련', bust: '가슴', breast: '가슴',
        cum: '사정', semen: '정액', amount: '양', milk: '모유', pleasure: '쾌감', shame: '수치심', stress: '스트레스',
        stamina: '체력', energy: '기력', health: '건강', mood: '기분', fatigue: '피로', hunger: '배고픔',
        str: '힘', strength: '힘', atk: '공격', def: '방어', agi: '민첩', luk: '운', int: '지력', charm: '매력',
        rank: '랭크', grade: '등급', step: '단계', steps: '단계', stage: '단계', bonus: '보너스', rate: '배율',
        efficiency: '효율', gauge: '게이지', item: '아이템', items: '아이템', used: '사용', state: '상태', states: '상태',
        research: '연구', researched: '연구됨', fame: '명성', reputation: '평판', sales: '매출', debt: '빚',
        tit: '파이', fuck: '즈리', kiss: '키스', sex: '섹스', orgasm: '절정', climax: '절정', sensitivity: '감도',
        corruption: '타락', obedience: '복종', submission: '복종', trust: '신뢰', exposure: '노출'
    };
    function keyHint(k) {
        var w = String(k).replace(/^_+/, '').replace(/([a-z0-9])([A-Z])/g, '$1 $2').toLowerCase().split(/[ _]+/);
        var out = [], hit = false;
        for (var i = 0; i < w.length; i++) {
            if (WORDS[w[i]]) { out.push(WORDS[w[i]]); hit = true; } else if (w[i]) out.push(w[i]);
        }
        return hit ? out.join(' ') : '';
    }
    function isPlain(v) {
        if (!v || typeof v !== 'object') return false;
        if (typeof Bitmap !== 'undefined' && v instanceof Bitmap) return false;
        if (typeof PIXI !== 'undefined' && v instanceof PIXI.DisplayObject) return false;
        if (typeof Game_Map !== 'undefined' && v instanceof Game_Map) return false;
        if (typeof Game_Interpreter !== 'undefined' && v instanceof Game_Interpreter) return false;
        if (v.nodeType) return false;
        return true;
    }
    function customRoots() {
        var roots = [], seen = [];
        var wins = Object.keys(window).filter(function (k) { return k.charAt(0) === '$'; });
        function winOf(obj) {
            for (var i = 0; i < wins.length; i++) { try { if (window[wins[i]] === obj) return wins[i]; } catch (e) {} }
            return '';
        }
        function add(name, obj, win) {
            if (!isPlain(obj) || seen.indexOf(obj) >= 0) return;
            seen.push(obj); roots.push({ name: name, obj: obj, win: win || winOf(obj), pre: [] });
        }
        try {
            var c = DataManager.makeSaveContents();
            for (var k in c) if (c.hasOwnProperty(k) && !STD_SAVE[k]) add(k, c[k], winOf(c[k]) || '@' + k);
        } catch (e) {}
        wins.forEach(function (k) {
            if (/^\$game/.test(k) && !STD_GLOBALS[k]) {
                try { add(k.replace(/^\$game/, '').replace(/^./, function (m) { return m.toLowerCase(); }), window[k], k); } catch (e) {}
            }
        });
        return roots;
    }
    // 숫자(와 참/거짓) 칸을 전부 모음
    function collectNums(roots, maxDepth, withBool) {
        var out = [], stack = [], root = null;
        function walk(o, path, depth) {
            if (out.length > 40000 || stack.indexOf(o) >= 0) return;
            stack.push(o);
            var keys = Object.keys(o);
            for (var i = 0; i < keys.length && i < 3000; i++) {
                var k = keys[i], v;
                try { v = o[k]; } catch (e) { continue; }
                if (typeof v === 'number' && isFinite(v)) out.push({ o: o, k: k, path: path.concat([k]), win: root.win, pre: root.pre || [] });
                else if (withBool && typeof v === 'boolean') out.push({ o: o, k: k, path: path.concat([k]), bool: true });
                else if (depth < maxDepth && isPlain(v)) walk(v, path.concat([k]), depth + 1);
            }
            stack.pop();
        }
        roots.forEach(function (r) { root = r; if (isPlain(r.obj)) walk(r.obj, [r.name], 0); });
        return out;
    }
    function pathText(path) {
        var t = path[0];
        for (var i = 1; i < path.length; i++) {
            t += /^\d+$/.test(String(path[i])) ? '[' + path[i] + ']' : ' › ' + String(path[i]).replace(/^_+/, '');
        }
        return t;
    }
    function lastHint(path) {
        for (var i = path.length - 1; i >= 1; i--) {
            if (!/^\d+$/.test(String(path[i]))) return keyHint(path[i]);
        }
        return '';
    }
    function searchRoots() {
        var r = customRoots();
        r.push({ name: '변수', obj: $gameVariables._data, win: '$gameVariables', pre: ['_data'] });
        r.push({ name: '파티', obj: $gameParty, win: '$gameParty', pre: [] });
        r.push({ name: '캐릭터', obj: $gameActors._data, win: '$gameActors', pre: ['_data'] });
        return r;
    }
    function numRow(c, onChange) {
        var cur = c.o[c.k];
        var hint = lastHint(c.path);
        var nm = el('span', { cls: 'gs-name', title: pathText(c.path) }, [pathText(c.path)]);
        if (hint) nm.appendChild(el('span', { cls: 'gs-tag hot', text: hint }));
        if (c.bool) {
            var b = btn(cur ? '참(ON)' : '거짓(OFF)', function () {
                c.o[c.k] = !c.o[c.k];
                b.textContent = c.o[c.k] ? '참(ON)' : '거짓(OFF)';
            }, 'sm');
            return el('div', { cls: 'gs-row' }, [nm, b]);
        }
        var i = input(cur);
        return el('div', { cls: 'gs-row' }, [nm, i,
            btn('적용', function () {
                var n = Number(i.value);
                if (isNaN(n)) return;
                c.o[c.k] = n;
                toast(pathText(c.path) + ' = ' + n);
                if (onChange) onChange();
            }, 'sm'),
            btn('×10', function () { c.o[c.k] = (Number(c.o[c.k]) || 0) * 10 || 10; i.value = c.o[c.k]; }, 'sm'),
            c.win ? lockBtn(c.win, (c.pre || []).concat(c.path.slice(1)), pathText(c.path)) : null
        ]);
    }
    var deepFilter = '', finder = { list: null, last: '' };
    function tabDeep(body) {
        if (!$gameParty || !$gameVariables) { body.appendChild(el('div', { cls: 'gs-note', text: '게임 시작 후 사용할 수 있습니다.' })); return; }
        // A. 게임 고유 데이터
        var roots = customRoots();
        body.appendChild(el('div', { cls: 'gs-sec', text: '게임이 직접 만든 데이터' }));
        if (!roots.length) {
            body.appendChild(el('div', { cls: 'gs-note', text: '이 게임은 기본 데이터(변수·능력치)만 씁니다. "능력치", "변수·스위치" 탭을 보세요.' }));
        } else {
            body.appendChild(el('div', { cls: 'gs-note', text:
                '제작자가 RPG 쯔꾸르 기본 능력치 대신 따로 만들어 세이브에 저장하는 수치입니다 (' +
                roots.map(function (r) { return r.name; }).join(', ') + '). 노란 표시는 이름을 보고 추측한 뜻입니다. ' +
                '게임이 최대치를 따로 정해 두면 그 이상은 다시 깎일 수 있습니다.' }));
            var f = input(deepFilter, 'wide'); f.placeholder = '이름으로 거르기 (예: level, bust, 레벨)';
            body.appendChild(f);
            var list = el('div');
            body.appendChild(list);
            var all = collectNums(roots, 4, true);
            var redraw = function () {
                list.innerHTML = '';
                var q = deepFilter.toLowerCase(), n = 0;
                all.forEach(function (c) {
                    if (n >= 300) return;
                    var t = (pathText(c.path) + ' ' + lastHint(c.path)).toLowerCase();
                    if (q && t.indexOf(q) < 0) return;
                    n++;
                    list.appendChild(numRow(c));
                });
                if (n >= 300) list.appendChild(el('div', { cls: 'gs-note', text: '300개까지만 표시합니다. 이름으로 걸러 주세요.' }));
            };
            f.addEventListener('input', function () { deepFilter = f.value; redraw(); });
            redraw();
        }
        // B. 값으로 찾기
        body.appendChild(el('div', { cls: 'gs-sec', text: '화면에 보이는 숫자로 찾기' }));
        body.appendChild(el('div', { cls: 'gs-note', text:
            '① 게임 화면에 보이는 수치(예: 레벨 3)를 넣고 "처음 찾기" → ② 게임에서 그 수치를 바꾼 뒤(창 닫고 플레이) 다시 열어 새 값을 넣고 "좁히기" → 후보가 몇 개 남으면 고치면 됩니다. 변수·파티·캐릭터·게임 고유 데이터를 모두 뒤집니다.' }));
        var vIn = input(finder.last); vIn.placeholder = '숫자';
        var resBox = el('div');
        function showRes() {
            resBox.innerHTML = '';
            if (!finder.list) return;
            resBox.appendChild(el('div', { cls: 'gs-note', text: '후보 ' + finder.list.length + '개' + (finder.list.length > 100 ? ' (100개까지 표시, 더 좁혀 주세요)' : '') }));
            finder.list.slice(0, 100).forEach(function (c) { resBox.appendChild(numRow(c)); });
        }
        function cur(c) { try { return c.o[c.k]; } catch (e) { return undefined; } }
        body.appendChild(el('div', { cls: 'gs-row' }, [vIn,
            btn('처음 찾기', function () {
                var v = Number(vIn.value); if (vIn.value === '' || isNaN(v)) { toast('숫자를 넣어 주세요'); return; }
                finder.last = vIn.value;
                finder.list = collectNums(searchRoots(), 6, false).filter(function (c) { return cur(c) === v; });
                finder.list.forEach(function (c) { c.prev = cur(c); });
                showRes();
            }),
            btn('이 값으로 좁히기', function () {
                if (!finder.list) return;
                var v = Number(vIn.value); if (vIn.value === '' || isNaN(v)) return;
                finder.last = vIn.value;
                finder.list = finder.list.filter(function (c) { return cur(c) === v; });
                finder.list.forEach(function (c) { c.prev = cur(c); });
                showRes();
            }),
            btn('커진 것', function () {
                if (!finder.list) return;
                finder.list = finder.list.filter(function (c) { return cur(c) > c.prev; });
                finder.list.forEach(function (c) { c.prev = cur(c); }); showRes();
            }, 'sm'),
            btn('작아진 것', function () {
                if (!finder.list) return;
                finder.list = finder.list.filter(function (c) { return cur(c) < c.prev; });
                finder.list.forEach(function (c) { c.prev = cur(c); }); showRes();
            }, 'sm'),
            btn('초기화', function () { finder.list = null; finder.last = ''; vIn.value = ''; showRes(); }, 'sm red')
        ]));
        body.appendChild(resBox);
        showRes();
    }
    GS.customRoots = customRoots; GS.collectNums = collectNums;

    //---- 고정 목록
    function tabLocks(body) {
        var ids = Object.keys(GS.locks);
        body.appendChild(el('div', { cls: 'gs-note', text:
            '각 탭의 "고정" 버튼으로 추가합니다. "줄지 않게" = 쓰거나 사거나 맞아도 안 줄고, 늘어나는 건 그대로 늘어납니다 (돈·아이템에 추천). ' +
            '"이 값 그대로" = 늘지도 줄지도 않습니다. 고정 목록은 게임마다 따로 기억합니다.' }));
        if (!ids.length) { body.appendChild(el('div', { cls: 'gs-note', text: '고정한 값이 없습니다.' })); return; }
        ids.forEach(function (id) {
            var lk = GS.locks[id], r = resolveLock(lk);
            var cur = r ? r.o[r.k] : undefined;
            var vIn = input(lk.value);
            body.appendChild(el('div', { cls: 'gs-row' }, [
                el('span', { cls: 'gs-name', title: lk.win + ' ' + lk.path.join('.') },
                    ['🔒 ' + (lk.label || lk.path.join('.')) + '  (지금 ' + (cur === undefined ? '-' : cur) + ')']),
                vIn,
                btn('값 바꾸기', function () {
                    var n = Number(vIn.value); if (isNaN(n)) return;
                    lk.value = n; if (r) r.o[r.k] = n; saveLocks(); render();
                }, 'sm'),
                btn('줄지 않게', function () { lk.mode = 'floor'; saveLocks(); render(); }, 'sm' + (lk.mode !== 'exact' ? ' on' : '')),
                btn('이 값 그대로', function () { lk.mode = 'exact'; saveLocks(); render(); }, 'sm' + (lk.mode === 'exact' ? ' on' : '')),
                btn('해제', function () { delete GS.locks[id]; saveLocks(); render(); }, 'sm red')
            ]));
        });
        body.appendChild(el('div', { cls: 'gs-row' }, [el('span', { cls: 'gs-name' }),
            btn('전부 해제', function () { GS.locks = {}; saveLocks(); render(); }, 'red')]));
    }

    //---- 순간이동
    var teleFilter = '';
    function mapLabel(mi) {
        var disp = GS.mapDisplay[mi.id];
        var nm = tr(mi.name);
        if (disp && tr(disp) && tr(disp) !== nm) return tr(disp) + '  (' + nm + ')';
        return nm;
    }
    function tabTele(body) {
        body.appendChild(el('div', { cls: 'gs-note', text: '맵을 누르면 그 맵의 걸을 수 있는 칸(가운데에서 가장 가까운 곳)으로 이동합니다. 좌표를 직접 넣으면 그 칸으로 갑니다. 막힌 곳에 떨어지면 고스트 모드를 켜고 빠져나오세요.' }));
        var f = input(teleFilter, 'wide'); f.placeholder = '맵 검색';
        var xi = input(''), yi = input('');
        xi.placeholder = 'X (자동)'; yi.placeholder = 'Y (자동)';
        body.appendChild(el('div', { cls: 'gs-row' }, [f]));
        body.appendChild(el('div', { cls: 'gs-row' }, [el('span', { cls: 'gs-name', text: '좌표 지정 (비우면 자동)' }), xi, yi]));
        var list = el('div');
        body.appendChild(list);
        var infos = ($dataMapInfos || []).filter(function (m) { return !!m; });
        var kids = {};
        infos.forEach(function (m) { (kids[m.parentId || 0] = kids[m.parentId || 0] || []).push(m); });
        for (var k in kids) kids[k].sort(function (a, b) { return a.order - b.order; });
        var ordered = [];
        (function walk(pid, depth) {
            (kids[pid] || []).forEach(function (m) { ordered.push([m, depth]); walk(m.id, depth + 1); });
        })(0, 0);
        var curId = $gameMap ? $gameMap.mapId() : 0;
        function redraw() {
            list.innerHTML = '';
            var q = teleFilter.toLowerCase();
            ordered.forEach(function (it) {
                var m = it[0], label = mapLabel(m);
                if (q && label.toLowerCase().indexOf(q) < 0 && String(m.name).toLowerCase().indexOf(q) < 0 && String(m.id) !== q) return;
                var row = el('div', { cls: 'gs-row' + (m.id === curId ? ' gs-cur' : ''), style: 'cursor:pointer;padding-left:' + (4 + (q ? 0 : it[1] * 16)) + 'px' }, [
                    el('span', { cls: 'gs-name' }, [('00' + m.id).slice(-3) + '  ' + label,
                        m.id === curId ? el('span', { cls: 'gs-chip', text: '현재 위치' }) : null])
                ]);
                row.addEventListener('click', function () {
                    var x = xi.value === '' ? null : Number(xi.value), y = yi.value === '' ? null : Number(yi.value);
                    teleport(m.id, x, y, function (err, spot) {
                        if (err) { toast(err); return; }
                        closeAll();
                        toast(label + ' (' + spot.x + ', ' + spot.y + ') 로 이동');
                    });
                });
                list.appendChild(row);
            });
        }
        f.addEventListener('input', function () { teleFilter = f.value; redraw(); });
        redraw();
    }

    //---------------------------------------------------------------- 아이템 사이드바
    var sideTab = 'item', sideFilter = '';
    var SIDE_TABS = [['item', '아이템'], ['weapon', '무기'], ['armor', '방어구'], ['key', '중요·퀘스트']];

    function sideList() {
        if (sideTab === 'weapon') return ($dataWeapons || []).filter(function (x) { return x && x.name; });
        if (sideTab === 'armor') return ($dataArmors || []).filter(function (x) { return x && x.name; });
        var key = sideTab === 'key';
        return ($dataItems || []).filter(function (x) {
            return x && x.name && (key ? x.itypeId !== 1 : x.itypeId === 1);
        });
    }
    function renderSide() {
        buildRoot();
        var oldBody = sideBox.querySelector('.gs-body');
        var keep = oldBody ? oldBody.scrollTop : 0;
        sideBox.innerHTML = '';
        sideBox.appendChild(el('div', { cls: 'gs-head' }, [
            el('span', { cls: 'gs-title', text: '아이템  (Shift + `)' }),
            btn('닫기', function () { toggleSidebar(); }, 'red')
        ]));
        var tabs = el('div', { cls: 'gs-tabs' });
        SIDE_TABS.forEach(function (t) {
            tabs.appendChild(el('div', {
                cls: 'gs-tab' + (sideTab === t[0] ? ' on' : ''), text: t[1],
                onclick: function () { sideTab = t[0]; keep = 0; renderSide(); }
            }));
        });
        sideBox.appendChild(tabs);
        var body = el('div', { cls: 'gs-body' });
        if (!$gameParty || !$dataItems) {
            body.appendChild(el('div', { cls: 'gs-note', text: '게임 시작 후 사용할 수 있습니다.' }));
            sideBox.appendChild(body); return;
        }
        var f = input(sideFilter, 'wide'); f.placeholder = '이름 검색 (한국어·원문)';
        body.appendChild(f);
        body.appendChild(el('div', { cls: 'gs-row' }, [
            el('span', { cls: 'gs-name', text: '이 탭 전부' }),
            btn('1개씩', function () { sideList().forEach(function (it) { if ($gameParty.numItems(it) < 1) $gameParty.gainItem(it, 1); }); renderSide(); }, 'sm'),
            btn('최대', function () { sideList().forEach(function (it) { $gameParty.gainItem(it, $gameParty.maxItems(it)); }); renderSide(); }, 'sm')
        ]));
        var list = el('div');
        body.appendChild(list);
        function redraw() {
            list.innerHTML = '';
            var q = sideFilter.toLowerCase();
            sideList().forEach(function (it) {
                if (q && tr(it.name).toLowerCase().indexOf(q) < 0 && it.name.toLowerCase().indexOf(q) < 0) return;
                var cnt = el('span', { cls: 'gs-cnt', text: String($gameParty.numItems(it)) });
                function add(n) {
                    if (n < 0 && $gameParty.numItems(it) === 0) return;
                    $gameParty.gainItem(it, n, false);
                    cnt.textContent = String($gameParty.numItems(it));
                }
                var nm = nameNode(it.name);
                if (it.description) nm.title = stripCodes(tr(it.description)) + '\n' + stripCodes(it.name);
                list.appendChild(el('div', { cls: 'gs-row' }, [
                    iconNode(it.iconIndex), nm, cnt,
                    btn('-1', function () { add(-1); }, 'sm'),
                    btn('+1', function () { add(1); }, 'sm'),
                    btn('+10', function () { add(10); }, 'sm'),
                    btn('최대', function () { add($gameParty.maxItems(it)); }, 'sm'),
                    lockBtn('$gameParty', [sideTab === 'weapon' ? '_weapons' : sideTab === 'armor' ? '_armors' : '_items', it.id], tr(it.name), true)
                ]));
            });
        }
        f.addEventListener('input', function () { sideFilter = f.value; redraw(); });
        redraw();
        sideBox.appendChild(body);
        body.scrollTop = keep;
    }
    GS.renderSide = renderSide;
})();
