# -*- coding: utf-8 -*-
"""
GameShelf — 로컬 게임 폴더를 스캔해서 DLsite / VNDB / Steam 에서
게임 정보와 대표 이미지를 가져와 보여주는 개인 게임 라이브러리.

- 선택한 폴더와 하위 폴더를 재귀 탐색 (.zip / .7z 압축파일은 무시)
- 폴더 이름에 RJ/VJ/BJ/RE 코드가 있으면 DLsite에서 코드로 바로 조회
- 코드가 없으면 폴더 이름을 정리해서 DLsite → VNDB → Steam 순으로 이름 검색
- 정보와 이미지는 GameShelf_data 폴더에 저장 (다음 실행 때 다시 받지 않음)
"""
import os
import re
import sys
import json
import time
import html
import difflib
import hashlib
import shutil
import glob
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import random
import csv
import zipfile
import tempfile
import threading
import subprocess
import unicodedata
from collections import OrderedDict
from pathlib import Path
from urllib.parse import quote

import requests
import gs_shrink
from PySide6.QtCore import (Qt, QObject, Signal, QRunnable, QThreadPool, QTimer,
                            QSize, QUrl, QFileInfo, QRectF, QPointF)
from PySide6.QtGui import (QPixmap, QIcon, QPainter, QColor, QFont, QPalette, QPainterPath,
                           QDesktopServices, QAction, QImageReader, QPen,
                           QPolygonF)
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QListWidget,
                               QListWidgetItem, QLabel, QPushButton, QLineEdit,
                               QComboBox, QSlider, QHBoxLayout, QVBoxLayout,
                               QSplitter, QFileDialog, QMessageBox, QMenu, QDialog,
                               QDialogButtonBox, QProgressBar, QToolBar,
                               QFileIconProvider, QScrollArea, QListView,
                               QAbstractItemView, QInputDialog, QToolButton,
                               QGridLayout, QTableWidget, QTableWidgetItem, QTabWidget,
                               QHeaderView)

# ---------------------------------------------------------------- 경로/상수
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "GameShelf_data"
COVER_DIR = DATA_DIR / "covers"
LIB_FILE = DATA_DIR / "library.json"
COVER_DIR.mkdir(parents=True, exist_ok=True)

CODE_RE = re.compile(r"(?<![A-Za-z0-9])(RJ|RE|VJ|BJ)(\d{8}|\d{6})(?!\d)", re.I)
ARCHIVE_EXT = {".zip", ".7z"}
SKIP_DIRS = {"$recycle.bin", "system volume information", "__macosx"}
EXE_BLACKLIST = re.compile(
    r"(unins|setup|install|crash|helper|vcredist|vc_redist|dxsetup|dxwebsetup|"
    r"directx|dotnet|redist|notification|updater|7z|winrar|rtp)", re.I)
MATCH_THRESHOLD = 0.6      # 이름 검색 자동 채택 기준 (0~1)

# GameTranslator (게임 번역 도구) — GameShelf 폴더 안의 GameTranslator 폴더
TRANSLATOR_DIR = BASE_DIR / "GameTranslator"
TRANSLATOR_PS1 = TRANSLATOR_DIR / "GameTranslator.ps1"
TRANSLATOR_AUTO_LIMIT = 20 * 1024 * 1024   # 번역기 툴이 이보다 크면 압축 풀기 후 자동 설치 안 함

# GameCheat (RPG 쯔꾸르 MV/MZ 치트) — GameShelf 폴더 안의 GameCheat 폴더
CHEAT_DIR = BASE_DIR / "GameCheat"
CHEAT_JS = CHEAT_DIR / "GS_Cheat.js"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# ---------------------------------------------------------------- HTTP
_tls = threading.local()


def sess():
    s = getattr(_tls, "s", None)
    if s is None:
        s = requests.Session()
        s.headers.update({"User-Agent": UA,
                          "Accept-Language": "ja,ko;q=0.8,en;q=0.6"})
        s.cookies.set("adultchecked", "1", domain=".dlsite.com")
        s.cookies.set("locale", "ja-jp", domain=".dlsite.com")
        _tls.s = s
    return s


def dbg(msg):
    lst = getattr(_tls, "dbg", None)
    if lst is not None:
        lst.append(msg)


def _abs(u):
    if not u:
        return ""
    return "https:" + u if u.startswith("//") else u


# ---------------------------------------------------------------- 텍스트 유틸
def find_code(text):
    m = CODE_RE.search(text or "")
    return (m.group(1) + m.group(2)).upper() if m else ""


def norm(s):
    s = unicodedata.normalize("NFKC", s or "").lower()
    return re.sub(r"[\W_]+", "", s)


def sim(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    r = difflib.SequenceMatcher(None, a, b).ratio()
    if min(len(a), len(b)) >= 4 and (a in b or b in a):
        r = max(r, 0.85)
    return r


BRACKETS = re.compile(r"\[[^\]]*\]|【[^】]*】|\([^)]*\)|（[^）]*）|［[^］]*］|"
                      r"〔[^〕]*〕|<[^>]*>|＜[^＞]*＞")
VER = re.compile(r"(?i)\b(?:ver(?:sion)?\.?\s*|v)?\d+(?:[._]\d+)+[a-z]?\b|"
                 r"\bver\.?\s*\d+\b|\b(?:ver|version|final|fix(?:ed)?)\b")


def clean_name(name):
    s = CODE_RE.sub(" ", name or "")
    s = BRACKETS.sub(" ", s)
    s = s.replace("_", " ").replace("『", " ").replace("』", " ")
    s = s.replace("「", " ").replace("」", " ")
    s = VER.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip(" -.~+")
    return s or (name or "").strip()


JA_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uff66-\uff9f]")
HANGUL_RE = re.compile(r"[\uac00-\ud7a3]")
BAD_CHARS = re.compile(r'[\\/:*?"<>|]')


ENGINES = [("google", "Google 번역 — 키 없음, 무료"),
           ("bing", "Bing 번역 — 키 없음, 무료"),
           ("papago", "Papago — 네이버 클라우드 키 필요"),
           ("deepl", "DeepL — API 키 필요 (무료 월 50만 자)"),
           ("local", "PC의 AI 모델 — Ollama / LM Studio 등")]
ENGINE_SHORT = {"google": "Google", "bing": "Bing", "papago": "Papago", "deepl": "DeepL", "local": "PC AI"}
LANG_NAME = {"ja": "Japanese", "ko": "Korean", "en": "English"}


def local_chat(cfg, system, user, timeout=180):
    """PC에서 돌리는 AI에 질문. URL이 /v1로 끝나면 OpenAI 호환(LM Studio 등), 아니면 Ollama."""
    base = (cfg.get("local_url") or "http://localhost:11434").rstrip("/")
    model = cfg.get("local_model") or ""
    if not model:
        raise RuntimeError("⚙설정에서 PC AI 모델을 골라주세요")
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    s = requests.Session()
    s.trust_env = False                       # localhost 요청이 프록시로 새지 않게
    if base.endswith("/v1"):
        r = s.post(f"{base}/chat/completions", timeout=timeout,
                   json={"model": model, "messages": msgs, "temperature": 0.2, "stream": False})
        r.raise_for_status()
        out = r.json()["choices"][0]["message"]["content"]
    else:
        r = s.post(f"{base}/api/chat", timeout=timeout,
                   json={"model": model, "messages": msgs, "stream": False, "think": False,
                         "options": {"temperature": 0.2}})
        r.raise_for_status()
        out = r.json()["message"]["content"]
    return re.sub(r"(?s)<think>.*?</think>", "", out or "").strip()


def local_models(url):
    """PC AI 서버에 설치된 모델 이름 목록"""
    base = (url or "http://localhost:11434").rstrip("/")
    s = requests.Session()
    s.trust_env = False
    if base.endswith("/v1"):
        r = s.get(f"{base}/models", timeout=5)
        r.raise_for_status()
        return [m.get("id", "") for m in r.json().get("data", [])]
    r = s.get(f"{base}/api/tags", timeout=5)
    r.raise_for_status()
    return [m.get("name", "") for m in r.json().get("models", [])]


def local_translate(cfg, texts, sl, tl):
    """여러 줄을 번호를 붙여 한 번에 번역, 빠진 줄은 하나씩 다시"""
    src, dst = LANG_NAME.get(sl, sl), LANG_NAME.get(tl, tl)
    system = (f"You are a professional {src}-to-{dst} translator for game titles and game text. "
              f"Translate naturally into {dst}. Keep placeholders like {{0}} and symbols unchanged. "
              "Output only the translation, no explanations.")
    if len(texts) == 1:
        return [local_chat(cfg, system, texts[0]).replace("\n", " ")]
    user = ("Translate each numbered line. Reply with the same numbers, one line each, "
            "in the form [n] translation.\n\n" + "\n".join(f"[{i + 1}] {t}" for i, t in enumerate(texts)))
    got = {}
    for ln in local_chat(cfg, system, user).splitlines():
        m = re.match(r"^\s*\[(\d+)\]\s?(.*)$", ln)
        if m and m.group(2).strip():
            got[int(m.group(1))] = m.group(2).strip()
    return [got.get(i + 1) or local_chat(cfg, system, t).replace("\n", " ") for i, t in enumerate(texts)]


class Translator:
    """제목 번역. ⚙설정에서 고른 엔진으로, 요청을 한 줄로 세워 여러 제목을 묶어서 보낸다."""
    lock = threading.Lock()
    last = 0.0
    blocked_until = 0.0
    cfg = {}
    engine = "google"
    status = ""
    GAP = 1.5          # Google 요청 사이 최소 간격(초)
    bing_token, bing_until = "", 0.0

    @classmethod
    def configure(cls, st):
        cls.cfg = dict(st or {})
        cls.engine = cls.cfg.get("title_engine") or ("deepl" if cls.cfg.get("deepl_key") else "google")
        cls.blocked_until = 0
        cls.status = ""

    @classmethod
    def _google_chunk(cls, texts, sl, tl):
        with cls.lock:
            if time.time() < cls.blocked_until:
                return None
            wait = cls.GAP - (time.time() - cls.last)
            if wait > 0:
                time.sleep(wait)
            cls.last = time.time()
            try:
                r = sess().get("https://translate.googleapis.com/translate_a/single",
                               params={"client": "gtx", "sl": sl, "tl": tl, "dt": "t",
                                       "q": "\n".join(texts)}, timeout=20)
                j = r.json() if r.ok else None
            except Exception:
                j = None
            if not j:
                cls.blocked_until = time.time() + 1800
                cls.status = "Google 번역이 요청 과다로 일시 차단됨 — 몇 시간 뒤 다시 시도하거나 ⚙설정에서 다른 번역 엔진 선택"
                dbg("Google 번역 차단됨")
                return None
            cls.status = ""
            lines = "".join(seg[0] for seg in j[0] if seg and seg[0]).split("\n")
            lines = [x.strip() for x in lines]
            return lines if len(lines) == len(texts) else None

    @classmethod
    def _deepl(cls, texts, sl, tl):
        key = (cls.cfg.get("deepl_key") or "").strip()
        if not key:
            raise RuntimeError("DeepL API 키가 없습니다 (⚙설정)")
        host = "api-free.deepl.com" if key.endswith(":fx") else "api.deepl.com"
        r = sess().post(f"https://{host}/v2/translate", timeout=30,
                        headers={"Authorization": f"DeepL-Auth-Key {key}"},
                        data=[("text", t) for t in texts] +
                             [("source_lang", sl.upper()), ("target_lang", tl.upper())])
        if not r.ok:
            raise RuntimeError(f"DeepL 오류 {r.status_code} (키 확인 / 월 사용량 초과)")
        return [x.get("text", "") for x in r.json().get("translations", [])]

    @classmethod
    def _bing(cls, texts, sl, tl):
        if not cls.bing_token or time.time() > cls.bing_until:
            r = sess().get("https://edge.microsoft.com/translate/auth", timeout=15)
            r.raise_for_status()
            cls.bing_token, cls.bing_until = r.text.strip(), time.time() + 480
        r = sess().post("https://api-edge.cognitive.microsofttranslator.com/translate",
                        params={"from": sl, "to": tl, "api-version": "3.0"}, timeout=30,
                        headers={"Authorization": "Bearer " + cls.bing_token},
                        json=[{"Text": t} for t in texts])
        if r.status_code == 401:
            cls.bing_token = ""
        if not r.ok:
            raise RuntimeError(f"Bing 번역 오류 {r.status_code}")
        return [x["translations"][0]["text"] for x in r.json()]

    @classmethod
    def _papago(cls, texts, sl, tl):
        cid, sec = cls.cfg.get("papago_id", ""), cls.cfg.get("papago_secret", "")
        if not cid or not sec:
            raise RuntimeError("Papago 키(Client ID / Secret)가 없습니다 (⚙설정)")
        r = sess().post("https://papago.apigw.ntruss.com/nmt/v1/translation", timeout=30,
                        headers={"X-NCP-APIGW-API-KEY-ID": cid, "X-NCP-APIGW-API-KEY": sec},
                        data={"source": sl, "target": tl, "text": "\n".join(texts)})
        if not r.ok:
            raise RuntimeError(f"Papago 오류 {r.status_code} (키 확인 / 사용량)")
        out = [x.strip() for x in r.json()["message"]["result"]["translatedText"].split("\n")]
        return out if len(out) == len(texts) else [cls._papago([t], sl, tl)[0] for t in texts]

    @classmethod
    def many(cls, texts, sl, tl):
        """texts 순서대로 번역 결과 목록 (실패한 칸은 빈 문자열)"""
        out = [""] * len(texts)
        eng = cls.engine
        size = {"bing": 50, "deepl": 40, "papago": 15, "local": 15}.get(eng, 25)
        i = 0
        while i < len(texts):
            if eng == "google":
                chunk, n = [], 0
                while i + len(chunk) < len(texts) and len(chunk) < 25 and n < 1500:
                    chunk.append(texts[i + len(chunk)])
                    n += len(chunk[-1]) + 1
                res = cls._google_chunk(chunk, sl, tl)
                if res is None and len(chunk) > 1 and time.time() >= cls.blocked_until:
                    # 줄 수가 어긋난 경우만 하나씩 다시
                    res = [(cls._google_chunk([t], sl, tl) or [""])[0] for t in chunk]
                if not res and time.time() < cls.blocked_until:
                    break
            else:
                chunk = texts[i:i + size]
                try:
                    res = {"bing": cls._bing, "deepl": cls._deepl, "papago": cls._papago,
                           "local": lambda t, a, b: local_translate(cls.cfg, t, a, b)}[eng](chunk, sl, tl)
                    cls.status = ""
                except Exception as ex:
                    msg = str(ex) if isinstance(ex, RuntimeError) else f"{type(ex).__name__}"
                    if eng == "local" and isinstance(ex, requests.ConnectionError):
                        msg = "PC AI 서버에 연결 못 함 — Ollama / LM Studio가 켜져 있는지 확인"
                    cls.status = f"{ENGINE_SHORT.get(eng, eng)}: {msg}"
                    dbg(cls.status)
                    break
            if res:
                out[i:i + len(chunk)] = res[:len(chunk)]
            i += len(chunk)
        return out


# ---------------------------------------------------------------- GameTranslator 엔진 설정 / PC AI 다리
def write_gt_engine(st):
    """GameTranslator가 읽는 engine.json 저장 (폴더 오른쪽 클릭으로 실행해도 같은 엔진 사용)"""
    if not TRANSLATOR_DIR.is_dir():
        return
    data = {"engine": st.get("game_engine", "google"),
            "deepl_key": st.get("deepl_key", ""), "papago_id": st.get("papago_id", ""),
            "papago_secret": st.get("papago_secret", ""),
            "local_url": st.get("local_url", "http://localhost:11434"),
            "local_model": st.get("local_model", ""), "bridge_port": int(st.get("bridge_port", 5577))}
    try:
        (TRANSLATOR_DIR / "engine.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def set_ini_value(lines, section, key, value):
    out, in_sec, saw, done = [], False, False, False
    for line in lines:
        t = line.strip().lstrip("\ufeff")
        if t.startswith("["):
            if in_sec and not done:
                out.append(f"{key}={value}")
                done = True
            in_sec = t.lower() == f"[{section}]".lower()
            saw = saw or in_sec
        elif in_sec and not done and re.match(rf"^{re.escape(key)}\s*=", t, re.I):
            out.append(f"{key}={value}")
            done = True
            continue
        out.append(line)
    if not done:
        if not saw:
            out += ["", f"[{section}]"]
        out.append(f"{key}={value}")
    return out


def xunity_apply_engine(cfg_path, st):
    """이미 설치된 Unity 게임의 XUnity 설정을 고른 엔진으로 바꿈"""
    eng = st.get("game_engine", "google")
    ep = {"bing": "BingTranslate", "papago": "PapagoTranslate", "local": "CustomTranslate",
          "deepl": "DeepLTranslateLegitimate" if st.get("deepl_key") else "DeepLTranslate"}.get(eng, "GoogleTranslateV2")
    try:
        lines = cfg_path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        lines = []
    lines = set_ini_value(lines, "Service", "Endpoint", ep)
    if eng == "local":
        lines = set_ini_value(lines, "Custom", "Url", f"http://127.0.0.1:{int(st.get('bridge_port', 5577))}/translate")
    if eng == "deepl" and st.get("deepl_key"):
        lines = set_ini_value(lines, "DeepLLegitimate", "ApiKey", st["deepl_key"])
        lines = set_ini_value(lines, "DeepLLegitimate", "Free", "True" if st["deepl_key"].endswith(":fx") else "False")
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TranslateBridge:
    """Unity 게임(XUnity CustomTranslate) ↔ PC AI 다리. GameShelf가 켜져 있는 동안만 동작.
    XUnity가 http://127.0.0.1:포트/translate?from=ja&to=ko&text=... 로 물어보면 번역문을 돌려준다."""

    def __init__(self):
        self.server, self.cfg, self.cache = None, {}, {}
        self.lock = threading.Lock()

    def start(self, port, cfg):
        self.cfg = dict(cfg)
        if self.server and self.server.server_address[1] == port:
            return ""
        self.stop()
        bridge = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                text = q.get("text", "")
                if u.path.rstrip("/") != "/translate" or not text:
                    self.send_response(404)
                    self.end_headers()
                    return
                key = (q.get("from", "ja"), q.get("to", "ko"), text)
                try:
                    with bridge.lock:          # AI는 한 번에 한 문장씩 (그래픽카드 메모리 보호)
                        res = bridge.cache.get(key)
                        if res is None:
                            res = local_translate(bridge.cfg, [text], key[0], key[1])[0]
                            bridge.cache[key] = res
                    body, code = res.encode("utf-8"), 200
                except Exception as ex:
                    body, code = f"{type(ex).__name__}: {ex}".encode("utf-8"), 500
                self.send_response(code)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", port), H)
        except OSError as ex:
            self.server = None
            return f"PC AI 번역 다리(포트 {port})를 열지 못했습니다: {ex}"
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return ""

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None


def translate(text, sl, tl):
    return Translator.many([text], sl, tl)[0] if text else ""


def translate_ko(text):
    """일본어(가나·한자)가 들어 있으면 한국어로 번역. 영어 등은 번역하지 않음."""
    if not text or not JA_RE.search(text):
        return ""
    return translate(text, "ja", "ko")


def display_name(e):
    """표시 이름: (DLsite 코드) + 번역된 제목. 영어 제목은 그대로."""
    if e.get("manual_title"):
        return e.get("title") or e.get("name", "")
    if e.get("status") != "found":
        return e.get("title") or e.get("name", "")
    t = e.get("title_ko") or e.get("title") or e.get("name", "")
    code = find_code(e.get("code", ""))
    return f"{code} {t}" if code and code not in t else t


def _og(text, prop):
    for pat in (rf'<meta[^>]+property="{prop}"[^>]+content="([^"]*)"',
                rf'<meta[^>]+content="([^"]*)"[^>]+property="{prop}"'):
        m = re.search(pat, text)
        if m:
            return html.unescape(m.group(1))
    return ""


# ---------------------------------------------------------------- DLsite
PREFIX_SITES = {
    "RJ": ["maniax", "home", "aix", "girls", "bl", "appx"],
    "VJ": ["pro", "soft"],
    "BJ": ["books", "comic"],
    "RE": ["ecchi-eng", "eng"],
}
KIND = {"RJ": "doujin", "RE": "doujin", "VJ": "professional", "BJ": "books"}


def dlsite_guess_image(code):
    """DLsite 대표 이미지 주소 규칙: 번호를 1000 단위로 올림한 폴더 아래 *_img_main.jpg"""
    pre, num = code[:2], code[2:]
    folder = pre + str(((int(num) + 999) // 1000) * 1000).zfill(len(num))
    return (f"https://img.dlsite.jp/modpub/images2/work/{KIND.get(pre, 'doujin')}"
            f"/{folder}/{code}_img_main.jpg")


def dlsite_guess_ana_image(code):
    """판매 예고(予告) 작품 이미지 주소"""
    pre, num = code[:2], code[2:]
    folder = pre + str(((int(num) + 999) // 1000) * 1000).zfill(len(num))
    return (f"https://img.dlsite.jp/modpub/images2/ana/{KIND.get(pre, 'doujin')}"
            f"/{folder}/{code}_ana_img_main.jpg")


def _parse_dlsite_html(t):
    title = _og(t, "og:title")
    title = re.sub(r"\s*\|\s*DLsite.*$", "", title)
    title = re.sub(r"\s*\[[^\]]*\]\s*$", "", title).strip()
    m = re.search(r'class="maker_name"[^>]*>\s*<a[^>]*>([^<]+)</a>', t)
    return title, (html.unescape(m.group(1)).strip() if m else ""), _abs(_og(t, "og:image"))


def _get_image(url, referer=None):
    try:
        r = sess().get(url, timeout=20, headers={"Referer": referer} if referer else None)
        if r.ok and r.headers.get("content-type", "").startswith("image") and len(r.content) > 1000:
            return r.content
    except Exception:
        pass
    return None


def wayback_dlsite(code, sites):
    """판매 종료로 DLsite 페이지가 사라진 작품 → 인터넷 보관소(Wayback Machine)에 남은 페이지에서 정보 얻기"""
    s = sess()
    for site in sites[:2]:
        for kind in ("work", "announce"):
            page = f"www.dlsite.com/{site}/{kind}/=/product_id/{code}.html"
            try:
                r = s.get("https://archive.org/wayback/available", params={"url": page}, timeout=20)
                snap = (r.json().get("archived_snapshots") or {}).get("closest") or {}
                if not snap.get("available"):
                    continue
                ts = snap.get("timestamp", "2020")
                r = s.get(snap["url"], timeout=30)
                if not r.ok:
                    continue
                title, maker, img = _parse_dlsite_html(r.text)
                if not title:
                    continue
                img = re.sub(r"^https?://web\.archive\.org/web/[^/]+/", "", img or "")
                img = _abs(img) or dlsite_guess_image(code)
                data = _get_image(img) or _get_image(f"https://web.archive.org/web/{ts}im_/{img}")
                return dict(source="DLsite 보관본", code=code, title=title, maker=maker, date="",
                            genres=["판매 종료"], image_url=img,
                            url=f"https://web.archive.org/web/{ts}/https://{page}",
                            _img_bytes=data)
            except Exception:
                pass
    return None


def dlsite_by_code(code):
    s = sess()
    sites = PREFIX_SITES.get(code[:2], ["maniax"])
    page = lambda site: f"https://www.dlsite.com/{site}/work/=/product_id/{code}.html"

    # 1) product.json (가장 정보가 많음)
    for site in sites:
        try:
            r = s.get(f"https://www.dlsite.com/{site}/api/=/product.json",
                      params={"workno": code}, timeout=15)
            if not r.ok:
                dbg(f"product.json({site})={r.status_code}")
            j = r.json() if r.ok else None
            if isinstance(j, list) and j and j[0].get("work_name"):
                w = j[0]
                img = w.get("image_main")
                img = img.get("url") if isinstance(img, dict) else img
                genres = [g.get("name") for g in (w.get("genres") or [])
                          if isinstance(g, dict) and g.get("name")]
                return dict(source="DLsite", code=code, title=w.get("work_name", ""),
                            maker=w.get("maker_name") or "",
                            date=str(w.get("regist_date") or "")[:10],
                            genres=genres[:12],
                            image_url=_abs(img) or dlsite_guess_image(code),
                            url=page(w.get("site_id") or site))
            break   # 응답은 정상인데 작품이 없으면 다른 사이트도 같은 결과
        except Exception as ex:
            dbg(f"product.json({site}) 오류: {type(ex).__name__}")
    # 2) ajax 정보
    for site in sites:
        try:
            r = s.get(f"https://www.dlsite.com/{site}/product/info/ajax",
                      params={"product_id": code, "cdn_cache_min": 1}, timeout=15)
            if not r.ok:
                dbg(f"ajax({site})={r.status_code}")
            j = r.json() if r.ok else None
            info = j.get(code) if isinstance(j, dict) else None
            if info and info.get("work_name"):
                return dict(source="DLsite", code=code, title=info["work_name"],
                            maker=info.get("maker_name") or "",
                            date=str(info.get("regist_date") or "")[:10], genres=[],
                            image_url=_abs(info.get("work_image")) or dlsite_guess_image(code),
                            url=page(info.get("site_id") or site))
            break
        except Exception as ex:
            dbg(f"ajax({site}) 오류: {type(ex).__name__}")
    # 3) 상품 페이지 / 판매 예고 페이지 HTML
    for kind in ("work", "announce"):
        url = f"https://www.dlsite.com/{sites[0]}/{kind}/=/product_id/{code}.html"
        try:
            r = s.get(url, timeout=20)
            if r.ok and code in r.url:
                title, maker, img = _parse_dlsite_html(r.text)
                if title:
                    guess = dlsite_guess_image(code) if kind == "work" else dlsite_guess_ana_image(code)
                    return dict(source="DLsite" if kind == "work" else "DLsite 예고", code=code,
                                title=title, maker=maker, date="",
                                genres=[] if kind == "work" else ["발매 예정"],
                                image_url=img or guess, url=r.url)
        except Exception:
            pass
    # 4) 판매 종료 작품 → 인터넷 보관소
    info = wayback_dlsite(code, sites)
    if info:
        return info
    # 5) 이미지 주소 규칙으로만 시도 (페이지는 없어도 이미지 서버에 남아 있는 경우)
    for img in (dlsite_guess_image(code), dlsite_guess_ana_image(code)):
        data = _get_image(img, "https://www.dlsite.com/")
        if data:
            return dict(source="DLsite", code=code, title="", maker="", date="",
                        genres=[], image_url=img, url=page(sites[0]), _img_bytes=data)
    return None


TOKEN_RE = re.compile(r"[A-Za-z0-9]{2,}|[\u30a0-\u30ffー]{2,}|[\u4e00-\u9fff々]{2,}|[\u3040-\u309f]{4,}")


def dlsite_ajax_bulk(ids):
    """여러 작품 정보를 한 번에 조회 (최대 30개씩)"""
    out = {}
    for i in range(0, len(ids), 30):
        chunk = ids[i:i + 30]
        try:
            r = sess().get("https://www.dlsite.com/maniax/product/info/ajax?product_id="
                           + ",".join(chunk) + "&cdn_cache_min=1", timeout=20)
            j = r.json() if r.ok else {}
            if isinstance(j, dict):
                out.update(j)
            elif not r.ok:
                dbg(f"ajax 일괄={r.status_code}")
        except Exception as ex:
            dbg(f"ajax 일괄 오류: {type(ex).__name__}")
    return out


def dlsite_search_ids(words, site):
    # DLsite 검색어는 단어 사이를 '+'로 이어야 AND 검색이 된다 (%20 이면 결과 0)
    path = "+".join(quote(w, safe="") for w in words if w)
    try:
        r = sess().get(f"https://www.dlsite.com/{site}/fsr/=/keyword/{path}/per_page/30/", timeout=20)
        if not r.ok:
            dbg(f"검색({site})={r.status_code}")
            return []
        return list(dict.fromkeys(re.findall(r'data-list_item_product_id="((?:RJ|VJ|BJ|RE)\d{6,8})"', r.text)
                                  or re.findall(r"product_id/((?:RJ|VJ|BJ|RE)\d{6,8})\.html", r.text)))
    except Exception as ex:
        dbg(f"검색({site}) 오류: {type(ex).__name__}")
        return []


def dlsite_keyword(q, translated=False):
    """핵심 단어로 좁혀가며 검색. 일반(maniax) 먼저, 못 찾을 때만 전연령(home)·AI(aix)."""
    q = unicodedata.normalize("NFKC", q or "").strip()
    if not q:
        return []
    # 번역문은 히라가나(조사·어미)가 원제와 달라지기 쉬우므로 영문·가타카나·한자 단어를 우선
    toks = sorted(set(TOKEN_RE.findall(q)),
                  key=lambda t: (bool(re.fullmatch(r"[\u3040-\u309f]+", t)), -len(t)))
    plans = [] if translated else [q.split()]      # 번역문은 원제와 똑같을 리 없으니 전체 문장 검색 생략
    if len(toks) >= 3:
        plans.append(toks[:3])
    if len(toks) >= 2:
        plans.append(toks[:2])
    if toks:
        plans.append(toks[:1])
    if not plans:
        plans = [q.split()]
    seen, out = set(), []

    def add(ids):
        ids = [i for i in dict.fromkeys(ids) if i not in seen][:40]
        seen.update(ids)
        for code, info in dlsite_ajax_bulk(ids).items():
            if not isinstance(info, dict) or not info.get("work_name"):
                continue
            site = info.get("site_id") or "maniax"
            out.append(dict(source="DLsite", code=code, title=info["work_name"], maker="",
                            date=str(info.get("regist_date") or "")[:10], genres=[],
                            image_url=_abs(info.get("work_image")) or dlsite_guess_image(code),
                            url=f"https://www.dlsite.com/{site}/work/=/product_id/{code}.html",
                            score=sim(q, info["work_name"]), _need_resolve=True))
        return max((c["score"] for c in out), default=0)

    for site in ("maniax", "home", "aix"):          # 사이트 하나를 끝까지 본 뒤 다음 사이트로
        for words in plans:
            ids = dlsite_search_ids(words, site)
            if ids and add(ids) >= 0.75:
                out.sort(key=lambda c: c["score"], reverse=True)
                return out[:15]
            if len(ids) >= 30:                     # 결과가 이미 많으면 더 좁은(짧은) 검색은 의미 없음
                break
        if max((c["score"] for c in out), default=0) >= MATCH_THRESHOLD:
            break
    out.sort(key=lambda c: c["score"], reverse=True)
    return out[:15]


# ---------------------------------------------------------------- VNDB / Steam
def vndb_search(q):
    out = []
    try:
        r = sess().post("https://api.vndb.org/kana/vn", timeout=15, json={
            "filters": ["search", "=", q],
            "fields": "title,alttitle,released,image.url,developers.name",
            "results": 6})
        for it in (r.json().get("results") or []) if r.ok else []:
            score = max(sim(q, it.get("title")), sim(q, it.get("alttitle") or ""))
            out.append(dict(source="VNDB", code=it.get("id", ""),
                            title=it.get("alttitle") or it.get("title") or "",
                            maker=", ".join(d.get("name", "") for d in it.get("developers") or []),
                            date=it.get("released") or "", genres=[],
                            image_url=(it.get("image") or {}).get("url", ""),
                            url=f"https://vndb.org/{it.get('id', '')}", score=score))
    except Exception:
        pass
    return out


def steam_search(q):
    out = []
    try:
        r = sess().get("https://store.steampowered.com/api/storesearch/",
                       params={"term": q, "l": "japanese", "cc": "JP"}, timeout=15)
        for it in (r.json().get("items") or []) if r.ok else []:
            aid = it.get("id")
            out.append(dict(source="Steam", code=f"steam{aid}", title=it.get("name", ""),
                            maker="", date="", genres=[],
                            image_url=f"https://cdn.cloudflare.steamstatic.com/steam/apps/{aid}/header.jpg",
                            image_url2=it.get("tiny_image", ""),
                            url=f"https://store.steampowered.com/app/{aid}",
                            score=sim(q, it.get("name", ""))))
    except Exception:
        pass
    return out


def _search_once(cq, translated=False):
    cands = dlsite_keyword(cq, translated)
    if max((c["score"] for c in cands), default=0) < 0.5:   # DLsite에 비슷한 것조차 없을 때만
        cands += vndb_search(cq)
        if max((c["score"] for c in cands), default=0) < 0.5:
            cands += steam_search(cq)
    return cands


def search_candidates(q):
    q = (q or "").strip()
    code = find_code(q)
    if code:
        info = dlsite_by_code(code)
        if info:
            info["score"] = 1.0
            return [info]
        return []
    cq = clean_name(q)
    cands = []
    if HANGUL_RE.search(cq):
        # 한국어 제목 → 일본어로 번역해서 먼저 검색 (DLsite는 한국어 제목으로는 검색이 안 됨)
        jq = translate(cq, "ko", "ja")
        if not jq:
            dbg("번역 실패")
        if jq and norm(jq) != norm(cq):
            cands = _search_once(jq, translated=True)
            for c in cands:
                c["via"] = jq
        if max((c["score"] for c in cands), default=0) < MATCH_THRESHOLD:
            cands += steam_search(cq)      # DLsite는 한국어 검색이 안 되므로 Steam(한국어 제목 있음)만
    else:
        cands = _search_once(cq)
    seen, out = set(), []
    for c in sorted(cands, key=lambda c: c["score"], reverse=True):
        k = (c["source"], c.get("code"))
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out[:25]


def resolve(c):
    if not c:
        return None
    c = dict(c)
    if c.get("_need_resolve") and c.get("code"):
        full = dlsite_by_code(c["code"])
        if full:
            if not full.get("title"):
                full["title"] = c.get("title", "")
            return full
    return c


def auto_lookup(code, name):
    if code:
        return dlsite_by_code(code)
    cands = search_candidates(name)
    if cands:
        c = cands[0]
        need = 0.5 if c.get("via") else MATCH_THRESHOLD   # 번역 검색은 표현이 조금 달라도 인정
        if c["score"] >= need:
            return resolve(c)
    return None


def download_cover(url, key, data=None):
    if not url:
        return ""
    try:
        if data is None:
            headers = None
            if "dlsite" in url:
                headers = {"Referer": "https://www.dlsite.com/"}
            elif "namu.la" in url or "arca.live" in url:
                headers = {"Referer": "https://arca.live/"}
            elif "kone" in url:
                headers = {"Referer": "https://kone.gg/"}
            r = sess().get(url, timeout=25, headers=headers)
            if not r.ok or not r.headers.get("content-type", "").startswith("image"):
                return ""
            data = r.content
        low = url.lower().split("?")[0]
        ext = ".png" if low.endswith(".png") else ".webp" if low.endswith(".webp") else ".jpg"
        fn = hashlib.md5(key.encode("utf-8")).hexdigest()[:16] + ext
        for old in COVER_DIR.glob(fn[:16] + ".*"):
            old.unlink(missing_ok=True)
        (COVER_DIR / fn).write_bytes(data)
        return fn
    except Exception:
        return ""


# ---------------------------------------------------------------- 폴더 스캔
def find_exe(folder, max_depth=2):
    found = []
    base = Path(folder)
    for root, dirs, files in os.walk(folder):
        depth = len(Path(root).relative_to(base).parts)
        if depth >= max_depth:
            dirs[:] = []
        for f in files:
            if f.lower().endswith(".exe") and not EXE_BLACKLIST.search(f):
                p = os.path.join(root, f)
                try:
                    size = os.path.getsize(p)
                except OSError:
                    size = 0
                found.append((0 if f.lower() == "game.exe" else 1, depth, -size, p))
    return min(found)[3] if found else ""


def scan_root(root, max_depth=8):
    """게임 폴더 + 압축파일(.zip/.7z, 안은 열지 않음) 경로 목록"""
    games = []

    def visit(d, depth):
        try:
            entries = list(os.scandir(d))
        except OSError:
            return
        name = os.path.basename(d.rstrip("\\/")) or d
        files = [e for e in entries if e.is_file(follow_symlinks=False)]
        dirs = [e for e in entries if e.is_dir(follow_symlinks=False)
                and e.name.lower() not in SKIP_DIRS and not e.name.startswith(".")]
        code = find_code(name)
        has_exe = any(f.name.lower().endswith(".exe") and not EXE_BLACKLIST.search(f.name)
                      for f in files)
        sub_codes = {find_code(x.name) for x in dirs} - {"", code}

        if code and not sub_codes:        # RJ 코드 폴더 = 게임 하나
            games.append(d)
            return
        if has_exe and not code:          # exe가 있는 폴더 = 게임 하나
            games.append(d)
            return
        for f in files:                   # 이 폴더에 있는 압축파일 각각 = 게임 하나
            if os.path.splitext(f.name)[1].lower() in ARCHIVE_EXT and not is_clear_archive(f.name):
                games.append(f.path)
        if depth < max_depth:
            for sub in dirs:
                visit(sub.path, depth + 1)

    visit(root, 0)
    return games


def translator_size():
    total = 0
    for root, _, files in os.walk(TRANSLATOR_DIR):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def run_translator(folder, auto=False):
    """GameTranslator.ps1 실행 (엔진 판별 → Unity 설치 / RPG Maker 번역). 창과 질문은 번역기가 직접 띄움"""
    ps = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                      "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    args = [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
            "-File", str(TRANSLATOR_PS1), folder]
    if auto:
        args.append("-Auto")
    return subprocess.Popen(args, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


# ---------------------------------------------------------------- RPG 쯔꾸르 치트 (GameCheat/GS_Cheat.js)
CHEAT_NAME = "GS_Cheat"
PLUGINS_RE = re.compile(r"(\$plugins\s*=\s*)(\[.*\])", re.S)
RPG_CODE_RE = re.compile(r"\\[A-Za-z]+\[[^\]]*\]")
RPG_CODE2_RE = re.compile(r"\\[{}.|!><^$]")
DISPLAY_NAME_RE = re.compile(r'"displayName"\s*:\s*("(?:[^"\\]|\\.)*")')
RPG_SKIP_DIRS = {"img", "audio", "movies", "locales", "save", "fonts", "icon", "swiftshader",
                 "node_modules", "effects", "css", "data", "js", "gametranslator_rpg"}


class CheatError(Exception):
    pass


def rpg_find(folder, max_depth=3):
    """RPG 쯔꾸르 MV/MZ 게임이면 (js 폴더, data 폴더, 'MV'/'MZ'), 아니면 None"""
    if not folder or not os.path.isdir(folder):
        return None

    def visit(d, depth):
        js, data = os.path.join(d, "js"), os.path.join(d, "data")
        if os.path.isfile(os.path.join(js, "plugins.js")) and os.path.isfile(os.path.join(data, "System.json")):
            if os.path.isfile(os.path.join(js, "rmmz_core.js")):
                return Path(js), Path(data), "MZ"
            if os.path.isfile(os.path.join(js, "rpg_core.js")):
                return Path(js), Path(data), "MV"
        if depth >= max_depth:
            return None
        try:
            subs = [x.path for x in os.scandir(d) if x.is_dir() and not x.name.startswith(".")
                    and x.name.lower() not in RPG_SKIP_DIRS and not x.name.lower().endswith("_gtbackup")]
        except OSError:
            return None
        subs.sort(key=lambda x: os.path.basename(x).lower() != "www")
        for sub in subs:
            r = visit(sub, depth + 1)
            if r:
                return r
        return None

    return visit(folder, 0)


def _plugins_load(js_dir):
    p = js_dir / "plugins.js"
    text = p.read_text(encoding="utf-8-sig")
    m = PLUGINS_RE.search(text)
    if not m:
        raise CheatError("plugins.js 형식을 알 수 없어서 치트를 넣지 못했습니다.")
    try:
        arr = json.loads(m.group(2))
    except ValueError as ex:
        raise CheatError(f"plugins.js를 읽지 못했습니다 ({ex}).")
    return p, text, m, arr


def _plugins_save(p, text, m, arr):
    body = "[\n" + ",\n".join(json.dumps(x, ensure_ascii=False) for x in arr) + "\n]"
    p.write_text(text[:m.start(2)] + body + text[m.end(2):], encoding="utf-8")


def cheat_installed(info):
    js = info[0]
    try:
        return (js / "plugins" / "GS_Cheat.js").is_file() and \
            f'"{CHEAT_NAME}"' in (js / "plugins.js").read_text(encoding="utf-8-sig")
    except OSError:
        return False


def install_cheat(folder):
    """plugins.js 백업(처음 한 번) → js/plugins/GS_Cheat.js 복사 → plugins.js 맨 끝에 등록"""
    info = rpg_find(folder)
    if not info:
        raise CheatError("RPG 쯔꾸르 MV/MZ 게임 파일(js/plugins.js, data/System.json)을 찾지 못했습니다.\n"
                         "(게임이 exe 파일 하나로 묶여 있는 경우에는 넣을 수 없습니다)")
    if not CHEAT_JS.is_file():
        raise CheatError(f"치트 파일이 없습니다:\n{CHEAT_JS}\n\nGameCheat 폴더를 GameShelf 폴더 안에 넣어주세요.")
    js = info[0]
    p, text, m, arr = _plugins_load(js)
    bk = js / "plugins.js.gs_backup"
    if not bk.exists():
        shutil.copy2(p, bk)
    (js / "plugins").mkdir(exist_ok=True)
    shutil.copyfile(CHEAT_JS, js / "plugins" / "GS_Cheat.js")
    arr = [x for x in arr if not (isinstance(x, dict) and x.get("name") == CHEAT_NAME)]
    arr.append({"name": CHEAT_NAME, "status": True,
                "description": "[GameShelf] 게임 화면 위 치트 창 (` 키)", "parameters": {"mainKey": "192"}})
    _plugins_save(p, text, m, arr)
    return info


def uninstall_cheat(folder):
    info = rpg_find(folder)
    if not info:
        raise CheatError("RPG 쯔꾸르 MV/MZ 게임 파일을 찾지 못했습니다.")
    js = info[0]
    p, text, m, arr = _plugins_load(js)
    keep = [x for x in arr if not (isinstance(x, dict) and x.get("name") == CHEAT_NAME)]
    if len(keep) != len(arr):
        _plugins_save(p, text, m, keep)
    for f in (js / "plugins" / "GS_Cheat.js", js / "plugins" / "GS_Cheat_ko.json", js / "plugins.js.gs_backup"):
        try:
            f.unlink()
        except OSError:
            pass
    return info


def rpg_strip(s):
    return RPG_CODE2_RE.sub("", RPG_CODE_RE.sub("", s)).strip()


def _tsv_unesc(s):
    return re.sub(r"\\([nt\\])", lambda m: {"n": "\n", "t": "\t", "\\": "\\"}[m.group(1)], s)


def cheat_names(data_dir):
    """치트 창에 나오는 일본어 이름 (아이템·무기·방어구·설명·맵·변수·스위치·능력치 용어)"""
    out, disp = [], {}

    def add(x):
        if isinstance(x, str):
            k = rpg_strip(x)
            if k and JA_RE.search(k):
                out.append(k)

    def load(name):
        try:
            return json.loads((data_dir / name).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return None

    for name in ("Items.json", "Weapons.json", "Armors.json"):
        for x in load(name) or []:
            if isinstance(x, dict):
                add(x.get("name"))
                add(x.get("description"))
    for name in ("Actors.json", "Classes.json"):
        for x in load(name) or []:
            if isinstance(x, dict):
                add(x.get("name"))
    sysd = load("System.json") or {}
    for key in ("variables", "switches"):
        for x in sysd.get(key) or []:
            add(x)
    add(sysd.get("currencyUnit"))
    for key in ("params", "basic"):
        for x in (sysd.get("terms") or {}).get(key) or []:
            add(x)
    for mi in load("MapInfos.json") or []:
        if not isinstance(mi, dict):
            continue
        add(mi.get("name"))
        try:   # 맵 표시 이름만 빠르게 (맵 전체를 읽지 않음)
            mt = DISPLAY_NAME_RE.search((data_dir / ("Map%03d.json" % mi["id"])).read_text(encoding="utf-8-sig"))
            dn = json.loads(mt.group(1)) if mt else ""
        except (OSError, ValueError, KeyError, TypeError):
            dn = ""
        if dn:
            disp[str(mi["id"])] = dn
            add(dn)
    return list(dict.fromkeys(out)), disp


def build_cheat_dict(folder):
    """GS_Cheat_ko.json 만들기. 번역기(GameTranslator) 번역 파일에 있는 건 그대로 쓰고 나머지만 번역.
    반환: (전체 이름 수, 번역 못 한 수)"""
    info = rpg_find(folder)
    if not info:
        raise CheatError("RPG 쯔꾸르 게임이 아닙니다.")
    js, data, _ = info
    names, disp = cheat_names(data)
    out_p = js / "plugins" / "GS_Cheat_ko.json"
    t = {}
    try:
        t = dict(json.loads(out_p.read_text(encoding="utf-8")).get("t") or {})
    except (OSError, ValueError, AttributeError):
        pass
    for base in (data.parent, data.parent.parent, Path(folder)):
        tsv = base / "GameTranslator_RPG" / "translations.tsv"
        if not tsv.is_file():
            continue
        try:
            for line in tsv.read_text(encoding="utf-8-sig").splitlines():
                if line.startswith("#") or "\t" not in line:
                    continue
                k, v = line.split("\t", 1)
                k, v = rpg_strip(_tsv_unesc(k)), _tsv_unesc(v).strip()
                if k in names and v and not t.get(k):
                    t[k] = v
        except OSError:
            pass
    todo = [k for k in names if not t.get(k)]
    for i in range(0, len(todo), 50):
        chunk = todo[i:i + 50]
        res = Translator.many([re.sub(r"\s*\n\s*", " ", c) for c in chunk], "ja", "ko")
        for k, v in zip(chunk, res):
            if v:
                t[k] = v
        if not any(res):
            break
    out_p.parent.mkdir(exist_ok=True)
    out_p.write_text(json.dumps({"t": t, "mapDisplay": disp}, ensure_ascii=False), encoding="utf-8")
    return len(names), sum(1 for k in names if not t.get(k))


SAVE_DIR = DATA_DIR / "saves"
SAVE_NAMES = {"save", "saves", "savedata", "save_data", "savefile", "savefiles"}


def find_save_dirs(folder, max_depth=4):
    """게임 폴더 안의 세이브 폴더들 (RPG 쯔꾸르 save, WOLF Save, 렌파이 saves 등)"""
    out = []

    def visit(d, depth):
        try:
            ents = list(os.scandir(d))
        except OSError:
            return
        for x in ents:
            if not x.is_dir() or x.name == gs_shrink.BACKUP_DIR:
                continue
            if x.name.lower() in SAVE_NAMES:
                try:
                    if any(True for _ in os.scandir(x.path)):
                        out.append(x.path)
                except OSError:
                    pass
            elif depth < max_depth:
                visit(x.path, depth + 1)
    visit(folder, 0)
    return out


def save_store(e):
    return SAVE_DIR / re.sub(r'[\\/:*?"<>|]+', "_", e.get("code") or e.get("name") or "game")[:80]


def backup_saves(e, folder):
    """세이브 폴더를 GameShelf_data/saves/<게임>/ 에 복사. 반환: 복사한 폴더 수"""
    n = 0
    for sd in find_save_dirs(folder):
        dst = save_store(e) / os.path.relpath(sd, folder)
        shutil.copytree(sd, dst, dirs_exist_ok=True)
        n += 1
    return n


def restore_saves(e, folder):
    """압축을 다시 풀었을 때 보관해 둔 세이브를 제자리에 (이미 있는 파일은 덮어쓰지 않음)"""
    src = save_store(e)
    if not src.is_dir():
        return 0
    n = 0
    for root, _, files in os.walk(src):
        for f in files:
            sp = os.path.join(root, f)
            dp = os.path.join(folder, os.path.relpath(sp, src))
            if not os.path.exists(dp):
                os.makedirs(os.path.dirname(dp), exist_ok=True)
                shutil.copy2(sp, dp)
                n += 1
    return n


def shrink_mark(e):
    """용량 줄이기 상태: 'pending'(확인 대기, 원본 백업 있음) / 'done'(확정) / None"""
    sh = e.get("shrink") or {}
    return sh.get("state") if sh.get("state") in ("pending", "done") else None


def reveal(path):
    """탐색기에서 파일/폴더 위치 열기"""
    if sys.platform.startswith("win"):
        if os.path.isfile(path):
            subprocess.Popen(f'explorer /select,"{os.path.normpath(path)}"')
        else:
            os.startfile(path)
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(path if os.path.isdir(path) else os.path.dirname(path)))


# ---------------------------------------------------------------- 휴지통 삭제
def send_to_recycle_bin(path):
    """Windows 휴지통으로 보내기 (되살릴 수 있음). 휴지통에 안 들어가는 크기면 Windows가 경고창을 띄움."""
    if not sys.platform.startswith("win"):
        raise OSError("휴지통 삭제는 Windows에서만 지원합니다")
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE, FOF_ALLOWUNDO, FOF_NOCONFIRMATION, FOF_WANTNUKEWARNING = 3, 0x40, 0x10, 0x4000
    op = SHFILEOPSTRUCTW(None, FO_DELETE, os.path.abspath(path) + "\0\0", None,
                         FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_WANTNUKEWARNING, False, None, None)
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc != 0 or op.fAnyOperationsAborted:
        raise OSError(f"삭제 실패 (코드 {rc}{', 취소됨' if op.fAnyOperationsAborted else ''}) — 게임 실행 중이거나 파일 사용 중일 수 있음")
    if os.path.exists(path):
        raise OSError("삭제 후에도 파일이 남아 있음 (사용 중인 파일이 있을 수 있음)")


# ---------------------------------------------------------------- 보안 검사
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_mpcmdrun():
    """Windows Defender 명령줄 검사기 위치 (최신 플랫폼 버전 우선)"""
    cands = []
    base = r"C:\ProgramData\Microsoft\Windows Defender\Platform"
    try:
        vers = sorted(os.listdir(base),
                      key=lambda v: [int(x) if x.isdigit() else 0 for x in re.split(r"[.-]", v)],
                      reverse=True)
        cands += [os.path.join(base, v, "MpCmdRun.exe") for v in vers]
    except OSError:
        pass
    cands.append(os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                              "Windows Defender", "MpCmdRun.exe"))
    return next((p for p in cands if os.path.isfile(p)), "")


def defender_scan(mp, path):
    """폴더/파일 하나를 Defender로 검사. 찾아도 지우거나 격리하지 않음(-DisableRemediation)."""
    today = time.strftime("%Y-%m-%d")
    try:
        r = subprocess.run([mp, "-Scan", "-ScanType", "3", "-File", path, "-DisableRemediation"],
                           capture_output=True, timeout=3600, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        return {"def": "error", "def_msg": "검사 시간 초과(1시간)", "def_date": today}
    except OSError as ex:
        return {"def": "error", "def_msg": str(ex), "def_date": today}
    raw = (r.stdout or b"") + (r.stderr or b"")
    try:
        out = raw.decode("cp949")
    except UnicodeDecodeError:
        out = raw.decode("utf-8", "replace")
    if r.returncode == 0:
        return {"def": "clean", "threats": [], "threat_files": [], "def_date": today}
    if r.returncode == 2:
        names = sorted({m.strip() for m in re.findall(r"Threat\s*:\s*(.+)", out)})
        files = [f.strip() for f in re.findall(r"file:(.+)", out)]
        return {"def": "threat", "threats": names or ["(이름 확인 불가)"],
                "threat_files": files[:10], "def_date": today}
    tail = " ".join(x.strip() for x in out.strip().splitlines()[-2:])[:200]
    return {"def": "error", "def_msg": f"코드 {r.returncode}: {tail}", "def_date": today}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def vt_lookup(sha, key):
    """해시로 VirusTotal 기록 조회 (파일은 올리지 않음)"""
    try:
        r = requests.get(f"https://www.virustotal.com/api/v3/files/{sha}",
                         headers={"x-apikey": key}, timeout=30)
    except Exception as ex:
        return "error", {"state": "error", "msg": type(ex).__name__}
    today = time.strftime("%Y-%m-%d")
    if r.status_code == 200:
        a = (r.json().get("data") or {}).get("attributes") or {}
        st = a.get("last_analysis_stats") or {}
        total = sum(st.get(k, 0) for k in ("malicious", "suspicious", "undetected", "harmless"))
        label = (a.get("popular_threat_classification") or {}).get("suggested_threat_label", "")
        return "ok", {"state": "ok", "mal": st.get("malicious", 0), "sus": st.get("suspicious", 0),
                      "total": total, "label": label, "date": today}
    if r.status_code == 404:
        return "ok", {"state": "unknown", "date": today}
    if r.status_code in (401, 403):
        return "badkey", {}
    if r.status_code == 429:
        return "quota", {}
    return "error", {"state": "error", "msg": f"HTTP {r.status_code}"}


EXEC_EXT = {".exe", ".dll", ".scr", ".com", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".jse",
            ".wsf", ".hta", ".msi", ".jar", ".lnk", ".cpl", ".ocx", ".sys"}
VT_MAX = 650 * 1024 * 1024          # VirusTotal 업로드 최대 크기
VT_SMALL = 32 * 1024 * 1024         # 이보다 크면 전용 업로드 주소 필요


class VTRate:
    """무료 키 분당 4건 → 모든 VirusTotal 요청(조회·업로드)이 이 간격을 함께 지킴"""
    lock = threading.Lock()
    last = 0.0
    GAP = 15.5

    @classmethod
    def wait(cls, stop):
        with cls.lock:
            w = cls.GAP - (time.time() - cls.last)
            if w > 0 and stop.wait(w):
                return False
            cls.last = time.time()
            return True


def vt_upload(name, data, key, stop):
    """파일을 VirusTotal에 올려 실제 검사 요청 (결과는 몇 분 뒤 해시 조회로 확인)"""
    hdr = {"x-apikey": key}
    try:
        url = "https://www.virustotal.com/api/v3/files"
        if len(data) > VT_SMALL:
            if not VTRate.wait(stop):
                return "stopped"
            r = requests.get("https://www.virustotal.com/api/v3/files/upload_url", headers=hdr, timeout=30)
            if r.status_code in (401, 403):
                return "badkey"
            if r.status_code == 429:
                return "quota"
            url = r.json().get("data", url)
        if not VTRate.wait(stop):
            return "stopped"
        r = requests.post(url, headers=hdr, files={"file": (os.path.basename(name), data)}, timeout=900)
    except Exception as ex:
        return f"error:{type(ex).__name__}"
    if r.ok:
        return "ok"
    return {401: "badkey", 403: "badkey", 429: "quota", 413: "toolarge"}.get(r.status_code, f"error:HTTP {r.status_code}")


def find_archiver(prefer="7z"):
    """압축 프로그램 찾기. prefer="bz"면 반디집 우선(일본어 zip 이름 인코딩을 잘 처리)"""
    if prefer == "bz":
        for b in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                  os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            p = os.path.join(b, "Bandizip", "bz.exe")
            if os.path.isfile(p):
                return "bz", p
    bases = [os.environ.get("ProgramFiles", r"C:\Program Files"),
             os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
             os.environ.get("ProgramW6432", r"C:\Program Files")]
    for b in bases:
        p = os.path.join(b, "7-Zip", "7z.exe")
        if os.path.isfile(p):
            return "7z", p
    w = shutil.which("7z") or shutil.which("7za")
    if w:
        return "7z", w
    for b in bases:
        p = os.path.join(b, "Bandizip", "bz.exe")
        if os.path.isfile(p):
            return "bz", p
    w = shutil.which("bz")
    if w:
        return "bz", w
    return None, None


def _run_tool(args, timeout=600):
    r = subprocess.run(args, capture_output=True, stdin=subprocess.DEVNULL,
                       timeout=timeout, creationflags=NO_WINDOW)
    raw = (r.stdout or b"") + b"\n" + (r.stderr or b"")
    try:
        out = raw.decode("utf-8")
    except UnicodeDecodeError:
        out = raw.decode("cp949", "replace")
    return r.returncode, out


def archive_list(kind, exe, path):
    """[(이름, 크기 또는 -1)] — 암호가 걸려 열 수 없으면 PermissionError"""
    if kind == "7z":
        code, out = _run_tool([exe, "l", "-slt", "-sccUTF-8", "-p", path], 120)
        if "Wrong password" in out or "encrypted" in out.lower() and code != 0:
            raise PermissionError
        body = out.split("\n----------", 1)[-1]
        items = []
        for block in re.split(r"\r?\n\s*\r?\n", body):
            kv = dict(re.findall(r"^(\w[\w ]*?) = (.*?)\r?$", block, re.M))
            if not kv.get("Path") or kv.get("Folder") == "+" or kv.get("Attributes", "").startswith("D"):
                continue
            if kv.get("Encrypted") == "+":
                raise PermissionError
            try:
                size = int(kv.get("Size") or -1)
            except ValueError:
                size = -1
            items.append((kv["Path"], size))
        return items
    code, out = _run_tool([exe, "l", "-list:s", path], 120)
    return [(ln.strip(), -1) for ln in out.splitlines()
            if os.path.splitext(ln.strip())[1].lower() in EXEC_EXT]


def archive_extract(kind, exe, path, names, tmpdir):
    if kind == "7z":
        lf = os.path.join(tmpdir, "_gameshelf_list.txt")
        with open(lf, "w", encoding="utf-8") as f:
            f.write("\n".join(names))
        _run_tool([exe, "x", path, f"-o{tmpdir}", "-y", "-p", "-scsUTF-8", "-sccUTF-8", f"@{lf}"])
        os.remove(lf)
    else:
        _run_tool([exe, "x", f"-o:{tmpdir}", "-y", "-aoa", "-p:x", path] + names)


def zip_name(info):
    """UTF-8 표시가 없는 zip 이름은 일본어(cp932) → 한국어(cp949) 순으로 다시 해석"""
    if info.flag_bits & 0x800:
        return info.filename
    raw = info.filename.encode("cp437", "replace")
    for enc in ("cp932", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return info.filename


def _safe_rel(name):
    parts = [p for p in re.split(r"[\\/]", name) if p not in ("", ".")]
    if not parts or ".." in parts or re.match(r"^[A-Za-z]:", parts[0]):
        return None
    return os.path.join(*parts)


def extract_archive(path, pw="", name=None, parent=None):
    """압축파일 옆에 '압축파일 이름' 폴더로 풀기. 반환: (풀린 폴더, 오류 메시지)
    압축 안이 폴더 하나로만 감싸져 있으면 그 껍데기를 벗겨 이름 폴더 바로 아래에 파일이 오게 함.
    name/parent 를 주면 그 이름·위치로 품 (클리어본을 원래 게임 폴더 이름으로 풀 때)"""
    parent = parent or os.path.dirname(path)
    name = name or os.path.splitext(os.path.basename(path))[0].rstrip(". ") or "game"
    tmp = os.path.join(parent, f".{name}.gameshelf_tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    msg = ""
    kind, exe = find_archiver(prefer="bz")
    try:
        if kind == "bz":
            code, out = _run_tool([exe, "x", f"-o:{tmp}", "-y", "-aoa", f"-p:{pw or 'x'}", path], 7200)
        elif kind == "7z":
            code, out = _run_tool([exe, "x", path, f"-o{tmp}", "-y", f"-p{pw}" if pw else "-p"], 7200)
        elif path.lower().endswith(".zip"):
            code, out = 0, ""
            with zipfile.ZipFile(path) as zf:
                for i in zf.infolist():
                    rel = _safe_rel(zip_name(i))
                    if not rel:
                        continue
                    dst = os.path.join(tmp, rel)
                    if i.is_dir():
                        os.makedirs(dst, exist_ok=True)
                        continue
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    with zf.open(i, pwd=pw.encode() if pw else None) as src, open(dst, "wb") as f:
                        shutil.copyfileobj(src, f, 1 << 20)
        else:
            shutil.rmtree(tmp, ignore_errors=True)
            return None, "7z 파일을 풀려면 반디집 또는 7-Zip이 필요합니다 (https://www.7-zip.org/)"
    except RuntimeError as ex:      # zipfile: 암호 필요
        code, out = 1, f"password {ex}"
    except Exception as ex:
        code, out = 1, f"{type(ex).__name__}: {ex}"
    entries = [x for x in os.listdir(tmp)] if os.path.isdir(tmp) else []
    if code != 0 or not entries:
        tail = " ".join(x.strip() for x in out.strip().splitlines()[-2:])[:200]
        shutil.rmtree(tmp, ignore_errors=True)
        pw_hint = re.search(r"password|암호|encrypt|Wrong", out, re.I)
        return None, ("암호" if pw_hint else "") + f"|코드 {code}: {tail}"
    src = tmp
    if len(entries) == 1 and os.path.isdir(os.path.join(tmp, entries[0])):
        src = os.path.join(tmp, entries[0])               # 껍데기 폴더 벗기기
    dest = os.path.join(parent, name)
    if os.path.isdir(dest) and not os.listdir(dest):
        os.rmdir(dest)
    n = 2
    while os.path.exists(dest):
        dest = os.path.join(parent, f"{name} ({n})")
        n += 1
    os.replace(src, dest)
    shutil.rmtree(tmp, ignore_errors=True)
    return dest, msg


CLEAR_TAG = "_클리어본"           # 용량 줄인 압축파일 이름 꼬리표 (예: RJ432234_클리어본.7z)


def is_clear_archive(path):
    """클리어본·이전 버전 압축파일 (스캔할 때 새 게임으로 잡지 않음)"""
    stem = os.path.splitext(os.path.basename(path))[0]
    return stem.endswith(CLEAR_TAG) or (CLEAR_TAG + " 이전 ") in stem or " (이전 압축파일 " in stem


STORE_EXT = {".png", ".rpgmvp", ".png_", ".jpg", ".jpeg", ".webp", ".ogg", ".rpgmvo", ".ogg_", ".m4a",
             ".rpgmvm", ".m4a_", ".mp4", ".webm", ".zip", ".7z", ".rar", ".pak", ".dat", ".bin"}


def _count_files(folder):
    n = 0
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d != gs_shrink.BACKUP_DIR]
        n += len(files)
    return n


def repack_archive(folder, archive, progress=None):
    """압축 푼(용량 줄인) 폴더를 세이브까지 통째로 다시 압축해서 archive 를 새로 만듦.
    같은 이름·형식(.7z/.zip)으로 만들어 목록 연결은 그대로. 새 파일을 먼저 만들고 확인한 뒤 바꿔치기.
    반환: (새 압축파일 크기, 옛 압축파일 임시 경로) — 옛 파일 처리는 부르는 쪽에서"""
    ext = os.path.splitext(archive)[1].lower()
    if ext not in (".7z", ".zip"):
        raise RuntimeError(f"{ext} 형식은 다시 만들 수 없습니다 (.7z/.zip만 가능)")
    if os.path.isdir(os.path.join(folder, gs_shrink.BACKUP_DIR)):
        raise RuntimeError("용량 줄이기가 아직 확인 대기 중입니다. 먼저 확정해주세요.")
    parent, name = os.path.dirname(folder), os.path.basename(folder.rstrip("\\/"))
    new = archive + ".gs_new" + ext
    for f in (new,):
        if os.path.exists(f):
            os.remove(f)
    total = _count_files(folder)
    kind, exe = find_archiver(prefer="7z")
    if kind == "7z":
        code, out = _run_tool([exe, "a", "-t" + ext[1:], "-mx=1", "-y", "-ssw", "-scsUTF-8",
                               f"-xr!{gs_shrink.BACKUP_DIR}", new, os.path.join(parent, name)], 7200)
    elif kind == "bz":
        code, out = _run_tool([exe, "c", "-y", "-l:1", f"-fmt:{ext[1:]}", new, folder], 7200)
    elif ext == ".zip":
        code, out = 0, ""
        with zipfile.ZipFile(new, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
            n = 0
            for root, dirs, files in os.walk(folder):
                dirs[:] = [d for d in dirs if d != gs_shrink.BACKUP_DIR]
                for f in files:
                    fp = os.path.join(root, f)
                    arc = os.path.join(name, os.path.relpath(fp, folder))
                    ct = zipfile.ZIP_STORED if os.path.splitext(f)[1].lower() in STORE_EXT else zipfile.ZIP_DEFLATED
                    zf.write(fp, arc, compress_type=ct)
                    n += 1
                    if progress and n % 50 == 0:
                        progress(n, total)
    else:
        raise RuntimeError("7z 압축파일을 만들려면 반디집 또는 7-Zip이 필요합니다 (https://www.7-zip.org/)")
    if code != 0 or not os.path.isfile(new) or os.path.getsize(new) == 0:
        try:
            os.remove(new)
        except OSError:
            pass
        raise RuntimeError("압축파일을 만들지 못했습니다: " + " ".join(out.strip().splitlines()[-2:])[:200])
    # 확인: 들어간 파일 수
    if kind == "7z":
        c2, o2 = _run_tool([exe, "l", "-slt", "-sccUTF-8", new], 600)
        listed = len(re.findall(r"^Attributes = [^D\r\n]", o2, re.M))
    elif kind == "bz":
        c2, o2 = _run_tool([exe, "l", "-list:s", new], 600)
        listed = sum(1 for ln in o2.splitlines() if ln.strip() and not ln.strip().endswith(("\\", "/")))
    else:
        with zipfile.ZipFile(new) as zf:
            listed = sum(1 for i in zf.infolist() if not i.is_dir())
    if listed < total:
        os.remove(new)
        raise RuntimeError(f"새 압축파일 확인 실패 (파일 {total}개 중 {listed}개만 들어감). 원본은 그대로 둡니다.")
    old = archive + ".gs_old" + ext
    if os.path.exists(old):                 # 예전에 중간에 멈춘 흔적 — 원본일 수 있으니 휴지통으로
        send_to_recycle_bin(old)
    if os.path.exists(archive):
        os.replace(archive, old)
    os.replace(new, archive)
    return os.path.getsize(archive), (old if os.path.exists(old) else "")


def exec_members(path, is_archive, tmpdir, limit=25):
    """검사할 실행 파일 목록 [(이름, 크기, 내용 읽는 함수)], 메모(암호 압축 등)"""
    out, note = [], ""
    rank = lambda n: (0 if os.path.basename(n).lower() == "game.exe" else
                      1 if n.lower().endswith(".exe") else 2)
    low = path.lower()
    if not is_archive:
        for root, _, files in os.walk(path):
            for f in files:
                if os.path.splitext(f)[1].lower() in EXEC_EXT:
                    p = os.path.join(root, f)
                    try:
                        out.append((os.path.relpath(p, path), os.path.getsize(p),
                                    lambda p=p: open(p, "rb").read()))
                    except OSError:
                        pass
    elif low.endswith(".zip"):
        zf = zipfile.ZipFile(path)
        for i in zf.infolist():
            if i.is_dir() or os.path.splitext(i.filename)[1].lower() not in EXEC_EXT:
                continue
            if i.flag_bits & 0x1:
                note = "암호 걸린 압축파일 — 안을 열 수 없음"
                continue
            out.append((i.filename, i.file_size, lambda i=i: zf.read(i)))
    elif low.endswith(".7z"):
        kind, exe = find_archiver()
        if not kind:
            return [], "7z 안을 보려면 7-Zip 또는 반디집이 필요 (https://www.7-zip.org/)"
        try:
            # 경로 조작 방지: '..'·드라이브·절대경로가 들어간 이름은 꺼내지 않음
            unsafe = lambda n: (".." in re.split(r"[\\/]", n) or os.path.isabs(n)
                                or re.match(r"^[A-Za-z]:", n) or n.startswith(("\\", "/")))
            names = [(n, sz) for n, sz in archive_list(kind, exe, path)
                     if os.path.splitext(n)[1].lower() in EXEC_EXT and not unsafe(n)]
        except PermissionError:
            return [], "암호 걸린 압축파일 — 안을 열 수 없음"
        except Exception as ex:
            return [], f"7z 목록 읽기 실패: {type(ex).__name__}"
        names.sort(key=lambda x: rank(x[0]))
        if len(names) > limit:
            note = f"실행 파일 {len(names)}개 중 {limit}개만 검사"
            names = names[:limit]
        big = [(n, sz) for n, sz in names if sz > VT_MAX]
        want = [n for n, sz in names if sz <= VT_MAX]
        for n, sz in big:                                 # 650MB 초과는 꺼내지도 않음
            out.append((n, sz, lambda: b""))
        if want:
            try:
                archive_extract(kind, exe, path, want, tmpdir)
            except Exception as ex:
                return out, f"7z 풀기 실패: {type(ex).__name__}"
            missing = 0
            for n in want:
                p = os.path.join(tmpdir, *re.split(r"[\\/]", n))
                if os.path.isfile(p):
                    out.append((n, os.path.getsize(p), lambda p=p: open(p, "rb").read()))
                else:
                    missing += 1
            if missing:
                note = (note + " · " if note else "") + f"{missing}개 파일을 꺼내지 못함 (암호 압축일 수 있음)"
    out.sort(key=lambda x: (rank(x[0]), -x[1]))
    if len(out) > limit:
        note = (note + " · " if note else "") + f"실행 파일 {len(out)}개 중 {limit}개만 검사"
    return out[:limit], note


def archive_main_exe_hash(path):
    """압축 안의 메인 실행 파일만 임시로 꺼내 해시 계산 (게임 자체는 풀지 않음). 반환 (sha, 이름) 또는 (None, 메모)"""
    with tempfile.TemporaryDirectory(prefix="gameshelf_") as tmp:
        members, note = exec_members(path, True, tmp, limit=5)
        exes = [m for m in members if m[0].lower().endswith(".exe") and 0 < m[1] <= VT_MAX]
        if not exes:
            return None, note or "압축 안에 실행 파일이 없음"
        name, _, read = exes[0]
        return hashlib.sha256(read()).hexdigest(), name


def sec_state(e):
    """썸네일 배지: red(위험) / yellow(확인 필요) / green(이상 없음) / err / None(검사 안 함)"""
    sc = e.get("scan") or {}
    d, vt = sc.get("def"), sc.get("vt") or {}
    mal = vt.get("mal", 0) if vt.get("state") == "ok" else 0
    sus = vt.get("sus", 0) if vt.get("state") == "ok" else 0
    up = [f for f in ((sc.get("vtu") or {}).get("files") or []) if f.get("state") == "ok"]
    mal = max([mal] + [f.get("mal", 0) for f in up])
    sus = max([sus] + [f.get("sus", 0) for f in up])
    if up and not d and vt.get("state") != "ok":
        vt = {"state": "ok"}
    if d == "threat" or mal >= 3:
        return "red"
    if mal >= 1 or sus >= 1:
        return "yellow"
    if d == "clean" or vt.get("state") == "ok":
        return "green"
    if d == "error":
        return "err"
    return None


# ---------------------------------------------------------------- 저장
def load_lib():
    try:
        with open(LIB_FILE, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        d = {}
    d.setdefault("roots", [])
    d.setdefault("games", {})
    for e in d["games"].values():
        if e.get("status") == "pending":
            e["status"] = "new"
    return d


def fmt_secs(sec):
    sec = int(sec or 0)
    if sec < 60:
        return f"{sec}초" if sec else "-"
    h, m = divmod(sec // 60, 60)
    return f"{h}시간 {m}분" if h else f"{m}분"


def fmt_time(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "-"


def key_of(path):
    return os.path.normcase(os.path.abspath(path))


# ---------------------------------------------------------------- 작업 스레드
class Signals(QObject):
    looked_up = Signal(str, dict)
    candidates = Signal(int, list)
    scan_done = Signal(object)
    translated = Signal(dict, str)
    sec_result = Signal(str, dict)
    sec_progress = Signal(str, str)
    deleted = Signal(str, str)
    engine_scan = Signal(list)
    precheck = Signal(str, dict)
    extracted = Signal(str, str, str)
    cheat_msg = Signal(str)
    repacked = Signal(str, str, str)      # key, 오류, 다음 할 일


class FuncTask(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.setAutoDelete(True)

    def run(self):
        self.fn()


def make_lookup_task(key, entry, sig, stop_evt, cand=None):
    code, name = entry.get("code", ""), entry.get("name", "")

    def job():
        if stop_evt.is_set():
            sig.looked_up.emit(key, {"status": "cancelled"})
            return
        res = {"status": "notfound"}
        _tls.dbg = []
        try:
            info = resolve(cand) if cand else auto_lookup(code, name)
            if info:
                res = {k: v for k, v in info.items() if not k.startswith("_") and k != "score"}
                res["status"] = "found"
                res["cover"] = download_cover(info.get("image_url"), key, info.get("_img_bytes"))
                if not res["cover"] and info.get("image_url2"):
                    res["cover"] = download_cover(info["image_url2"], key)
            else:
                res["debug"] = " · ".join(_tls.dbg[-6:]) or "검색 결과 없음 / 일치도 낮음"
            time.sleep(0.25)  # 사이트에 부담 주지 않도록 약간 쉬기
        except Exception as ex:
            res = {"status": "error", "error": str(ex)}
        sig.looked_up.emit(key, res)

    return FuncTask(job)


# ---------------------------------------------------------------- 평점 (별 5개, 반 개 단위)
RATING_CATS = ["거유", "료나", "게임성", "그림체"]
STAR_GOLD = "#f5b301"
STAR_LINE = "#c99a1c"


def rating_total(e):
    r = e.get("rating") or {}
    return sum(float(r.get(c, 0) or 0) for c in RATING_CATS) if r else None


def fmt_score(v):
    return f"{v:.1f}".rstrip("0").rstrip(".") if v is not None else ""


def star_path(cx, cy, r):
    import math
    path = QPainterPath()
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.45
        pt = QPointF(cx + rr * math.cos(ang), cy + rr * math.sin(ang))
        path.moveTo(pt) if i == 0 else path.lineTo(pt)
    path.closeSubpath()
    return path


def paint_stars(p, x, y, size, value, gap=3):
    """별 5개: 채운 별 + 반 별(왼쪽 반만 채움) + 남은 별은 윤곽선"""
    p.setRenderHint(QPainter.Antialiasing)
    for i in range(5):
        cx, cy = x + i * (size + gap) + size / 2, y + size / 2
        path = star_path(cx, cy, size / 2)
        fill = max(0.0, min(1.0, value - i))
        if fill > 0:
            p.save()
            p.setClipRect(QRectF(cx - size / 2, y, size * fill, size))
            p.fillPath(path, QColor(STAR_GOLD))
            p.restore()
        p.setPen(QPen(QColor(STAR_LINE), max(1.2, size / 14)))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)


class StarWidget(QWidget):
    """누르거나 끌어서 0.5 단위로 점수 고르기 (별의 왼쪽 반 = .5). 오른쪽 클릭 = 0점"""
    changed = Signal(float)

    def __init__(self, value=0.0, size=30, parent=None):
        super().__init__(parent)
        self.value, self.size, self.gap = float(value), size, 4
        self.hover = None
        self.setMouseTracking(True)
        self.setFixedSize(5 * size + 4 * self.gap + 2, size + 2)
        self.setCursor(Qt.PointingHandCursor)

    def val_at(self, x):
        step = self.size + self.gap
        i = int(x // step)
        frac = (x - i * step) / self.size
        v = i + (0.5 if frac < 0.5 else 1.0)
        return max(0.5, min(5.0, v))

    def paintEvent(self, ev):
        p = QPainter(self)
        paint_stars(p, 1, 1, self.size, self.hover if self.hover is not None else self.value, self.gap)
        p.end()

    def mouseMoveEvent(self, ev):
        x = ev.position().x() if hasattr(ev, "position") else ev.x()
        if ev.buttons() & Qt.LeftButton:
            self.set_value(self.val_at(x))
        else:
            self.hover = self.val_at(x)
            self.update()

    def mousePressEvent(self, ev):
        x = ev.position().x() if hasattr(ev, "position") else ev.x()
        self.set_value(0.0 if ev.button() == Qt.RightButton else self.val_at(x))

    def leaveEvent(self, ev):
        self.hover = None
        self.update()

    def set_value(self, v):
        self.value = float(v)
        self.hover = None
        self.update()
        self.changed.emit(self.value)


def rating_pixmap(rating, width=340):
    """오른쪽 정보 영역용: 항목별 별 + 점수 + 합계"""
    row_h, size = 26, 18
    h = row_h * (len(RATING_CATS) + 1) + 6
    pm = QPixmap(width, h)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    f = QFont()
    f.setPixelSize(13)
    p.setFont(f)
    total = 0.0
    for i, c in enumerate(RATING_CATS):
        v = float(rating.get(c, 0) or 0)
        total += v
        y = i * row_h + 3
        p.setPen(QColor("#999999"))
        p.drawText(QRectF(0, y, 60, size), Qt.AlignVCenter | Qt.AlignLeft, c)
        paint_stars(p, 64, y, size, v)
        p.setPen(QColor("#8a8a8a"))
        p.drawText(QRectF(64 + 5 * size + 20 + 6, y, 70, size), Qt.AlignVCenter | Qt.AlignLeft, f"{fmt_score(v)} / 5")
    y = len(RATING_CATS) * row_h + 3
    f.setBold(True)
    f.setPixelSize(15)
    p.setFont(f)
    p.setPen(QColor(STAR_GOLD))
    p.drawText(QRectF(0, y, width, row_h), Qt.AlignVCenter | Qt.AlignLeft,
               f"합계  {fmt_score(total)} / 20   (평균 ★ {fmt_score(round(total / 4 * 2) / 2)})")
    p.end()
    return pm


class RatingDialog(QDialog):
    def __init__(self, win, e, reason=""):
        super().__init__(win)
        self.setWindowTitle("⭐ 평점 남기기 — " + display_name(e))
        v = QVBoxLayout(self)
        t = QLabel(f"<b>{html.escape(display_name(e))}</b><br>"
                   + (f"<span style='color:#999'>{html.escape(reason)}</span><br>" if reason else "")
                   + "<span style='color:#999'>평점을 남기면 '🏆 클리어한 게임' 목록에 저장됩니다. "
                     "별의 왼쪽 반을 누르면 반 개(.5), 오른쪽 클릭은 0점.</span>")
        t.setWordWrap(True)
        v.addWidget(t)
        old = e.get("rating") or {}
        g = QGridLayout()
        self.stars, self.lbls = {}, {}
        for i, c in enumerate(RATING_CATS):
            lb = QLabel(c)
            f = lb.font()
            f.setPointSize(f.pointSize() + 2)
            lb.setFont(f)
            sw = StarWidget(old.get(c, 0))
            sc = QLabel()
            sw.changed.connect(self.update_total)
            g.addWidget(lb, i, 0)
            g.addWidget(sw, i, 1)
            g.addWidget(sc, i, 2)
            self.stars[c], self.lbls[c] = sw, sc
        v.addLayout(g)
        self.total = QLabel()
        f = self.total.font()
        f.setPointSize(f.pointSize() + 3)
        f.setBold(True)
        self.total.setFont(f)
        v.addWidget(self.total)
        bb = QDialogButtonBox()
        bb.addButton("저장", QDialogButtonBox.AcceptRole)
        bb.addButton("건너뛰기", QDialogButtonBox.RejectRole)
        self.cleared = False
        if old:
            b = bb.addButton("평점 지우기", QDialogButtonBox.DestructiveRole)
            b.clicked.connect(self.clear)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.update_total()

    def clear(self):
        self.cleared = True
        self.accept()

    def update_total(self, *_):
        tot = 0.0
        for c in RATING_CATS:
            val = self.stars[c].value
            tot += val
            self.lbls[c].setText(f"{fmt_score(val)} / 5")
        self.total.setText(f"합계 {fmt_score(tot)} / 20")

    def result_rating(self):
        r = {c: self.stars[c].value for c in RATING_CATS}
        r["date"] = time.time()
        return r


# ---------------------------------------------------------------- 용량 줄이기 창
class ShrinkSignals(QObject):
    progress = Signal(int, int, str)
    done = Signal(str)


class ShrinkDialog(QDialog):
    """1단계 분석 · 2단계 이미지/음악 재압축(백업·되돌리기·확정) · 3단계 실행 파일 공유"""

    def __init__(self, win, key):
        super().__init__(win)
        self.win, self.key = win, key
        self.e = win.lib["games"][key]
        self.folder = win.game_dir(self.e)
        self.info = rpg_find(self.folder) if self.folder else None
        self.ffmpeg = gs_shrink.find_ffmpeg([str(BASE_DIR)])
        self.busy = False
        self.stop_flag = False
        self.sig = ShrinkSignals()
        self.sig.progress.connect(self.on_progress)
        self.sig.done.connect(self.on_done)
        self.setWindowTitle("💾 용량 줄이기 — " + display_name(self.e))
        self.resize(760, 640)
        v = QVBoxLayout(self)
        self.info_lbl = QLabel()
        self.info_lbl.setWordWrap(True)
        self.info_lbl.setTextFormat(Qt.RichText)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(self.info_lbl)
        v.addWidget(sc, 1)

        g = QGridLayout()
        self.b_ll = QPushButton("🖼 이미지 무손실 압축 (화질 그대로)")
        self.b_hq = QPushButton("🖼 이미지 고화질 압축 (훨씬 작게)")
        self.b_m4a = QPushButton("🎵 안 쓰는 중복 음악(m4a) 정리")
        self.b_aud = QPushButton("🎵 음악 압축")
        self.b_ll.clicked.connect(lambda: self.run_images("lossless"))
        self.b_hq.clicked.connect(lambda: self.run_images("hq"))
        self.b_m4a.clicked.connect(self.run_m4a)
        self.b_aud.clicked.connect(self.run_audio)
        g.addWidget(self.b_ll, 0, 0)
        g.addWidget(self.b_hq, 0, 1)
        g.addWidget(self.b_m4a, 1, 0)
        g.addWidget(self.b_aud, 1, 1)
        v.addLayout(g)

        h = QHBoxLayout()
        self.b_play = QPushButton("▶ 게임 실행해서 확인")
        self.b_commit = QPushButton("✅ 확정 (원본 백업 지우기)")
        self.b_restore = QPushButton("↩ 되돌리기 (원본으로)")
        self.b_play.clicked.connect(lambda: win.run_game(key))
        self.b_commit.clicked.connect(self.do_commit)
        self.b_restore.clicked.connect(self.do_restore)
        for b in (self.b_play, self.b_commit, self.b_restore):
            h.addWidget(b)
        v.addLayout(h)

        from PySide6.QtWidgets import QCheckBox
        self.cb_auto = QCheckBox("압축이 끝나면 확인 없이 바로 확정 (원본 백업을 바로 휴지통으로 — 휴지통을 비우기 전까지는 되살릴 수 있음)")
        self.cb_auto.setChecked(bool(win.lib.get("settings", {}).get("shrink_autocommit")))
        self.cb_auto.toggled.connect(lambda v: (win.lib.setdefault("settings", {}).__setitem__("shrink_autocommit", v),
                                                win.schedule_save()))
        v.addWidget(self.cb_auto)
        self.b_share = QPushButton("🔗 모든 게임의 같은 실행 파일 공유하기 (RPG 쯔꾸르 MV/MZ, 화질·내용 변화 없음)")
        self.b_share.clicked.connect(self.run_share)
        v.addWidget(self.b_share)

        self.prog = QProgressBar()
        self.prog.setVisible(False)
        self.stat = QLabel("")
        self.b_stop = QPushButton("중지")
        self.b_stop.setVisible(False)
        self.b_stop.clicked.connect(self.ask_stop)
        h2 = QHBoxLayout()
        h2.addWidget(self.prog, 1)
        h2.addWidget(self.b_stop)
        v.addLayout(h2)
        v.addWidget(self.stat)
        self.refresh()

    # ------------------------------------------------------------ 화면
    def refresh(self):
        H = gs_shrink.human
        if not self.folder or not os.path.isdir(self.folder):
            self.info_lbl.setText("게임 폴더가 없습니다. 압축 게임이면 먼저 압축을 풀어주세요.")
            for b in (self.b_ll, self.b_hq, self.b_m4a, self.b_aud, self.b_commit, self.b_restore, self.b_play):
                b.setEnabled(False)
            return
        a = gs_shrink.analyze(self.folder, self.info, self.ffmpeg)
        bk = gs_shrink.backup_info(self.folder)
        c, est = a["cats"], a["est"]
        rpg = bool(self.info)
        kind = f"RPG 쯔꾸르 {self.info[2]}" if rpg else "RPG 쯔꾸르 MV/MZ가 아님"
        rows = [
            ("장면·그림 이미지 (압축 가능)", c["image"]),
            ("이미 압축된 이미지", c["image_done"]),
            ("기타 이미지 (Live2D 등, 건드리지 않음)", c["image_skip"]),
            ("음악·음성", c["audio"]),
            ("중복 음악 m4a (PC에선 안 씀)", c["m4a_dup"]),
            ("영상", c["movie"]),
            ("게임 실행 프로그램 (nw.js)", c["runtime"]),
            ("대사·데이터", c["data"]),
            ("기타", c["other"]),
        ]
        t = (f"<h3 style='margin:0'>{html.escape(display_name(self.e))}</h3>"
             f"<p>{html.escape(self.folder)}<br>{kind} · 파일 {a['files']:,}개 · <b>{H(a['total'])}</b></p>"
             "<table cellspacing=0 cellpadding=3 width='100%'>"
             "<tr><th align=left>종류</th><th align=right>파일</th><th align=right>용량</th><th align=right>비율</th></tr>")
        for name, (n, sz) in rows:
            if n:
                t += (f"<tr><td>{name}</td><td align=right>{n:,}</td><td align=right>{H(sz)}</td>"
                      f"<td align=right>{sz * 100 // max(a['total'], 1)}%</td></tr>")
        t += "</table><p><b>폴더별 큰 순서</b>: " + ", ".join(
            f"{html.escape(d)} {H(sz)}" for d, sz in a["dirs"][:6]) + "</p>"
        t += "<p><b>예상 절약량</b><br>"
        if rpg and c["image"][0]:
            t += (f"· 이미지 무손실: 약 {H(est['img_lossless'])} (화질 완전히 같음)<br>"
                  f"· 이미지 고화질: 약 {H(est['img_hq'])} (눈으로 구분하기 어려움)<br>")
        if est["m4a"]:
            t += f"· 중복 m4a 정리: {H(est['m4a'])} (MV 게임은 PC에서 ogg만 씀, 소리 그대로)<br>"
        if c["audio"][0]:
            t += (f"· 음악 압축: 약 {H(est['audio'])}<br>" if self.ffmpeg else
                  "· 음악 압축: ffmpeg가 필요합니다 (아래 안내)<br>")
        if c["runtime"][1]:
            t += f"· 실행 프로그램 {H(c['runtime'][1])}: 다른 RPG 쯔꾸르 게임과 같은 버전이면 '실행 파일 공유'로 공유 가능<br>"
        if not rpg:
            t += "· 이미지·음악 압축은 RPG 쯔꾸르 MV/MZ 게임에서만 됩니다 (다른 엔진은 파일 형식이 달라 위험).<br>"
        t += "</p>"
        if self.e.get("archive") and os.path.isfile(self.e.get("path", "")):
            try:
                t += (f"<p>※ 원본 압축파일도 그대로 있습니다 ({H(os.path.getsize(self.e['path']))}). "
                      "푼 폴더와 둘 다 있으면 용량을 두 번 차지합니다.</p>")
            except OSError:
                pass
        if bk["count"]:
            t += (f"<p style='background:#fff3cd;color:#5a4500;padding:6px'><b>확인 대기 중</b> ({', '.join(bk['actions'])}) — "
                  f"바꾼 파일 {bk['count']:,}개, 줄어든 용량 <b>{H(bk['saved'])}</b>.<br>"
                  f"원본 {H(bk['size'])}은 게임 폴더의 {gs_shrink.BACKUP_DIR} 에 보관 중이라 아직 공간이 비지 않았습니다. "
                  "게임을 실행해서 장면 몇 개를 확인한 뒤 <b>확정</b>을 누르면 그만큼 비워집니다.</p>")
        if not self.ffmpeg and c["audio"][0]:
            t += ("<p>음악 압축에는 ffmpeg가 필요합니다: "
                  "<a href='https://www.gyan.dev/ffmpeg/builds/'>gyan.dev 윈도우용 ffmpeg</a> "
                  "(ffmpeg-release-essentials.zip) 을 받아 bin\\ffmpeg.exe 를 GameShelf 폴더에 넣어주세요.</p>")
        self.info_lbl.setText(t)
        self.info_lbl.setOpenExternalLinks(True)
        idle = not self.busy
        self.b_ll.setEnabled(idle and rpg and c["image"][0] > 0)
        self.b_hq.setEnabled(idle and rpg and (c["image"][0] + c["image_done"][0]) > 0)
        self.b_m4a.setEnabled(idle and est["m4a"] > 0)
        self.b_aud.setEnabled(idle and rpg and bool(self.ffmpeg) and c["audio"][0] > 0)
        self.b_commit.setEnabled(idle and bk["count"] > 0)
        self.b_restore.setEnabled(idle and bk["count"] > 0)
        self.b_share.setEnabled(idle)

    # ------------------------------------------------------------ 작업 실행
    def start(self, label, fn):
        if self.busy:
            return
        self.busy = True
        self.stop_flag = False
        self.prog.setVisible(True)
        self.prog.setValue(0)
        self.b_stop.setVisible(True)
        self.stat.setText(label + "…")
        self.refresh()

        def work():
            try:
                msg = fn()
            except Exception as ex:
                msg = f"오류: {type(ex).__name__}: {ex}"
            self.sig.done.emit(msg)
        threading.Thread(target=work, daemon=True).start()

    def prog_cb(self, i, n, name):
        self.sig.progress.emit(i, n, name)

    def on_progress(self, i, n, name):
        self.prog.setMaximum(max(n, 1))
        self.prog.setValue(i)
        self.stat.setText(f"{i:,} / {n:,}  {name}")

    def on_done(self, msg):
        self.busy = False
        self.win.sync_shrink(self.key, getattr(self, "_committed", 0))
        self._committed = 0
        if getattr(self, "_auto_next", False):
            self._auto_next = False
            bk = gs_shrink.backup_info(self.folder)
            if bk["count"] and self.cb_auto.isChecked():
                self.prog.setVisible(False)
                self.b_stop.setVisible(False)
                saved = bk["saved"]

                def fn():
                    freed = gs_shrink.commit(self.folder, send_to_recycle_bin)
                    self._committed = saved
                    return msg.split(" — ")[0] + f" → 바로 확정: 원본 {gs_shrink.human(freed)} 휴지통으로"
                self.start("백업 지우는 중", fn)
                return
        self.prog.setVisible(False)
        self.b_stop.setVisible(False)
        self.stat.setText(msg)
        self.refresh()

    def ask_stop(self):
        self.stop_flag = True
        self.stat.setText("멈추는 중… (지금 바꾸던 파일까지만 끝냄)")

    def running_warn(self):
        return QMessageBox.question(
            self, "용량 줄이기", "게임이 켜져 있으면 끈 뒤에 진행해주세요.\n계속할까요?") == QMessageBox.Yes

    def run_images(self, mode):
        if not self.running_warn():
            return
        name = "무손실" if mode == "lossless" else "고화질"

        def fn():
            n, saved, fail = gs_shrink.shrink_images(self.folder, self.info, mode, self.prog_cb, lambda: self.stop_flag)
            return (f"이미지 {name} 압축: {n:,}개 바꿈, {gs_shrink.human(saved)} 줄어듦"
                    + (f", 실패 {fail}개(원본 그대로 둠)" if fail else "")
                    + " — 게임을 실행해서 확인한 뒤 '확정'을 눌러주세요.")
        self._auto_next = True
        self.start(f"이미지 {name} 압축 중", fn)

    def run_m4a(self):
        def fn():
            n, size = gs_shrink.remove_m4a_dups(self.folder)
            return f"중복 m4a {n:,}개 ({gs_shrink.human(size)}) 를 백업으로 옮겼습니다."
        self._auto_next = True
        self.start("중복 음악 정리 중", fn)

    def run_audio(self):
        if not self.running_warn():
            return

        def fn():
            n, saved, fail = gs_shrink.shrink_audio(self.folder, self.info, self.ffmpeg, self.prog_cb, lambda: self.stop_flag)
            return f"음악 압축: {n:,}개 바꿈, {gs_shrink.human(saved)} 줄어듦" + (f", 실패 {fail}개" if fail else "")
        self._auto_next = True
        self.start("음악 압축 중", fn)

    def do_commit(self):
        bk = gs_shrink.backup_info(self.folder)
        if QMessageBox.question(
                self, "확정",
                f"게임에서 장면이 잘 나오는 걸 확인하셨나요?\n\n원본 백업 {bk['count']:,}개 ({gs_shrink.human(bk['size'])}) 를 "
                "휴지통으로 보냅니다.\n휴지통을 비우면 그때 실제로 공간이 비워지고, 비우기 전에는 휴지통에서 되살릴 수 있습니다.") != QMessageBox.Yes:
            return
        saved = bk["saved"]

        def fn():
            freed = gs_shrink.commit(self.folder, send_to_recycle_bin)
            self._committed = saved
            return f"확정: 원본 백업 {gs_shrink.human(freed)} 를 휴지통으로 보냈습니다 (휴지통을 비우면 공간이 비워짐). 이 게임 총 절약 {gs_shrink.human(saved)}"
        self.start("백업 지우는 중", fn)

    def do_restore(self):
        if QMessageBox.question(self, "되돌리기", "바꾼 파일을 모두 원본으로 되돌릴까요?") != QMessageBox.Yes:
            return
        self.start("되돌리는 중", lambda: f"원본 {gs_shrink.restore(self.folder):,}개를 제자리로 돌려놓았습니다.")

    def run_share(self):
        folders = []
        for e in self.win.lib["games"].values():
            d = self.win.game_dir(e)
            if d and os.path.isdir(d) and rpg_find(d, max_depth=2):
                folders.append(d)
        if len(folders) < 2:
            QMessageBox.information(self, "실행 파일 공유", "압축이 풀린 RPG 쯔꾸르 MV/MZ 게임이 2개 이상 있어야 합니다.")
            return
        if QMessageBox.question(
                self, "실행 파일 공유",
                f"압축이 풀린 RPG 쯔꾸르 게임 {len(folders):,}개의 실행 프로그램(nw.dll 등)을 비교해서,\n"
                "내용이 완전히 같은 파일은 디스크에 한 번만 저장되게 합니다 (하드 링크).\n\n"
                "· 게임 폴더는 겉보기에 그대로이고 게임도 그대로 실행됩니다.\n"
                "· 탐색기의 폴더 용량 표시는 그대로지만, 드라이브 남은 공간이 늘어납니다.\n"
                "· 같은 드라이브(NTFS) 안의 게임끼리만 공유됩니다.\n"
                "· 실행 중인 게임이 있으면 끄고 진행해주세요.\n\n진행할까요?") != QMessageBox.Yes:
            return

        def fn():
            r = gs_shrink.dedupe_runtime(folders, self.prog_cb, lambda: self.stop_flag)
            return (f"실행 파일 공유: {r['linked']:,}개 파일을 공유해서 {gs_shrink.human(r['saved'])} 비웠습니다"
                    f" (이미 공유 중 {r['already']:,}개" + (f", 건너뜀 {r['errors']:,}개" if r["errors"] else "") + ").")
        self.start("실행 파일 비교 중", fn)

    def closeEvent(self, ev):
        if self.busy:
            QMessageBox.information(self, "용량 줄이기", "작업이 끝날 때까지 기다리거나 '중지'를 눌러주세요.")
            ev.ignore()
            return
        super().closeEvent(ev)

    def reject(self):
        if self.busy:
            return
        super().reject()


# ---------------------------------------------------------------- 다시 찾기 창
class SearchDialog(QDialog):
    def __init__(self, parent, text, pool):
        super().__init__(parent)
        self.setWindowTitle("게임 정보 다시 찾기")
        self.resize(720, 480)
        self.pool, self.cands, self.req, self.alive = pool, [], 0, True
        self.sig = Signals()
        self.sig.candidates.connect(self.on_cands)

        v = QVBoxLayout(self)
        v.addWidget(QLabel("게임 이름이나 코드(RJ123456 등)를 입력하고, 결과에서 맞는 게임을 고르세요."))
        h = QHBoxLayout()
        self.edit = QLineEdit(text)
        btn = QPushButton("검색")
        h.addWidget(self.edit, 1)
        h.addWidget(btn)
        v.addLayout(h)
        self.listw = QListWidget()
        v.addWidget(self.listw, 1)
        self.status = QLabel("")
        v.addWidget(self.status)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        btn.clicked.connect(self.do_search)
        self.edit.returnPressed.connect(self.do_search)
        self.listw.itemDoubleClicked.connect(lambda *_: self.accept())
        QTimer.singleShot(0, self.do_search)

    def do_search(self):
        q = self.edit.text().strip()
        if not q:
            return
        self.req += 1
        rid, sig = self.req, self.sig
        self.status.setText("검색 중…")
        self.listw.clear()
        self.cands = []

        def job():
            try:
                c = search_candidates(q)
            except Exception:
                c = []
            sig.candidates.emit(rid, c)

        self.pool.start(FuncTask(job))

    def on_cands(self, rid, cands):
        if not self.alive or rid != self.req:
            return
        self.cands = cands
        self.listw.clear()
        for c in cands:
            t = f"[{c['source']}] {c.get('title') or '(제목 없음)'}"
            if c.get("maker"):
                t += f"  —  {c['maker']}"
            if c.get("code") and c["source"] != "Steam":
                t += f"  ({c['code']})"
            t += f"   · 일치도 {c.get('score', 0):.0%}"
            if c.get("via"):
                t += f"   (일본어 번역 「{c['via']}」로 검색)"
            self.listw.addItem(t)
        if cands:
            self.listw.setCurrentRow(0)
        self.status.setText(f"{len(cands)}개 찾음" if cands else
                            "결과가 없습니다. 이름을 짧게 줄이거나 원어(일본어) 제목으로 검색해 보세요.")

    def selected(self):
        r = self.listw.currentRow()
        return self.cands[r] if 0 <= r < len(self.cands) else None

    def done(self, r):
        self.alive = False
        super().done(r)


# ---------------------------------------------------------------- 메인 창
FILTERS = ["전체", "정보 찾음", "못 찾음", "숨긴 항목", "폴더 없어짐", "🛡 위험·확인 필요", "🕹 플레이한 게임",
           "💾 용량 줄인 게임", "🏆 클리어한 게임"]
SORTS = ["제목순", "추가된 순", "코드순", "제작사순", "최근 플레이순", "⭐ 평점순"]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GameShelf — 게임 라이브러리")
        self.resize(1320, 820)
        self.lib = load_lib()
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(5)
        self.sig = Signals()
        self.sig.looked_up.connect(self.on_looked_up)
        self.sig.scan_done.connect(self.on_scan_done)
        self.sig.translated.connect(self.on_translated)
        self.sig.sec_result.connect(self.on_sec_result)
        self.sig.sec_progress.connect(self.on_sec_progress)
        self.sig.deleted.connect(self.on_deleted)
        self.sig.engine_scan.connect(self.on_engine_scan)
        self.sig.precheck.connect(self.on_precheck)
        self.sig.extracted.connect(self.on_extracted)
        self.sig.cheat_msg.connect(lambda m: self.status_lbl.setText(m))
        self.sig.repacked.connect(self.on_repacked)
        self.extracting = set()
        self.running = []          # [(key, 시작 시각, Popen)]
        self.play_timer = QTimer(self)
        self.play_timer.setInterval(5000)
        self.play_timer.timeout.connect(self.poll_running)
        self.play_timer.start()
        self.rand_dlg = None
        self.deleting = False
        self.sec_prog = {}
        self.sec_stop = {"def": threading.Event(), "vt": threading.Event(), "vtu": threading.Event()}
        self.translating = False
        st0 = self.lib.setdefault("settings", {})
        Translator.configure(st0)
        self.bridge = TranslateBridge()
        write_gt_engine(st0)
        QTimer.singleShot(500, self.update_bridge)
        self.stop_evt = threading.Event()
        self.jobs_total = self.jobs_left = 0
        self.icon_cache, self.items, self.ph_cache = OrderedDict(), {}, {}
        self.lazy_timer = QTimer(self)
        self.lazy_timer.setSingleShot(True)
        self.lazy_timer.setInterval(40)
        self.lazy_timer.timeout.connect(self.lazy_load)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(250)
        self.search_timer.timeout.connect(self.refresh_list)
        self.thumb_w = int(self.lib.get("thumb_w", 200))
        self.cur_key = None
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(2000)
        self.save_timer.timeout.connect(self.save)
        self.build_ui()
        self.refresh_list()
        if not self.lib["roots"]:
            self.status_lbl.setText("‘폴더 추가’로 게임이 들어 있는 폴더를 골라 주세요.")

    # ---------- UI
    def build_ui(self):
        tb = QToolBar()
        tb.setMovable(False)
        self.addToolBar(tb)
        tb.addAction("📁 폴더 추가", self.add_root)
        self.roots_btn = QToolButton()
        self.roots_btn.setText("폴더 관리")
        self.roots_btn.setPopupMode(QToolButton.InstantPopup)
        self.roots_menu = QMenu(self)
        self.roots_menu.aboutToShow.connect(self.fill_roots_menu)
        self.roots_btn.setMenu(self.roots_menu)
        tb.addWidget(self.roots_btn)
        tb.addAction("🔄 다시 스캔", self.rescan)
        tb.addAction("🌐 못 찾은 것 정보 가져오기", lambda: self.fetch_missing(True))
        tb.addAction("⏹ 중지", self.stop_jobs)
        tb.addAction("🎲 무작위 플레이", self.random_play)
        tb.addAction("🕹 플레이 기록", self.show_history)
        tb.addAction("🈯 제목 번역", lambda: self.translate_titles())
        sec_btn = QToolButton()
        sec_btn.setText("🛡 보안 검사")
        sec_btn.setPopupMode(QToolButton.InstantPopup)
        sm = QMenu(self)
        sm.addAction("Defender + VirusTotal (아직 안 한 게임)", lambda: self.sec_run("both"))
        sm.addAction("Defender만 (아직 안 한 게임)", lambda: self.sec_run("def"))
        sm.addAction("VirusTotal만 (아직 안 한 게임)", lambda: self.sec_run("vt"))
        sm.addSeparator()
        sm.addAction("⬆ ‘기록 없음’ 게임 전부 올려서 검사 (실행 파일만)", lambda: self.upload_run(None))
        sm.addAction("🔄 업로드 결과 확인 (분석 대기 중인 것)", lambda: self.upload_recheck())
        sm.addSeparator()
        sm.addAction("전체 다시 검사 (Defender)", lambda: self.sec_run("def", force=True))
        sm.addSeparator()
        sm.addAction("🛡 위험·확인 필요 목록 보기", lambda: self.filter_cb.setCurrentIndex(5))
        sm.addAction("⏹ 보안 검사 중지", self.sec_stop_all)
        sec_btn.setMenu(sm)
        tb.addWidget(sec_btn)
        tb.addAction("📄 못 찾은 목록 내보내기", self.export_notfound)
        tb.addAction("⚙ 설정", self.open_settings)
        tb.addSeparator()
        self.search = QLineEdit()
        self.search.setPlaceholderText("제목 · 코드 · 제작사 · 장르 검색")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(280)
        self.search.textChanged.connect(lambda *_: self.search_timer.start())
        tb.addWidget(self.search)
        self.filter_cb = QComboBox()
        self.filter_cb.addItems(FILTERS)
        self.filter_cb.currentIndexChanged.connect(self.refresh_list)
        tb.addWidget(self.filter_cb)
        self.sort_cb = QComboBox()
        self.sort_cb.addItems(SORTS)
        self.sort_cb.currentIndexChanged.connect(self.refresh_list)
        tb.addWidget(self.sort_cb)
        tb.addWidget(QLabel("  크기 "))
        self.size_sl = QSlider(Qt.Horizontal)
        self.size_sl.setRange(120, 360)
        self.size_sl.setValue(self.thumb_w)
        self.size_sl.setMaximumWidth(140)
        self.size_sl.valueChanged.connect(self.set_thumb)
        tb.addWidget(self.size_sl)

        self.listw = QListWidget()
        self.listw.setViewMode(QListView.IconMode)
        self.listw.setResizeMode(QListView.Adjust)
        self.listw.setMovement(QListView.Static)
        self.listw.setUniformItemSizes(True)
        self.listw.setWordWrap(True)
        self.listw.setSpacing(6)
        self.listw.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.listw.setContextMenuPolicy(Qt.CustomContextMenu)
        self.listw.customContextMenuRequested.connect(self.context_menu)
        self.listw.currentItemChanged.connect(lambda cur, _p: self.show_detail(
            cur.data(Qt.UserRole) if cur else None))
        self.listw.itemDoubleClicked.connect(lambda it: self.run_game(it.data(Qt.UserRole)))
        self.listw.verticalScrollBar().valueChanged.connect(self.schedule_lazy)
        del_act = QAction(self.listw)
        del_act.setShortcut(Qt.Key_Delete)
        del_act.triggered.connect(lambda: self.delete_games(
            [i.data(Qt.UserRole) for i in self.listw.selectedItems()]))
        self.listw.addAction(del_act)
        self.apply_sizes()

        # 오른쪽 상세 패널
        panel = QWidget()
        pv = QVBoxLayout(panel)
        self.d_cover = QLabel()
        self.d_cover.setAlignment(Qt.AlignCenter)
        self.d_cover.setMinimumHeight(280)
        self.d_cover.setStyleSheet("background:#1b1c22;border-radius:8px;")
        self.d_title = QLabel()
        self.d_title.setWordWrap(True)
        self.d_title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        f = QFont()
        f.setPointSize(13)
        f.setBold(True)
        self.d_title.setFont(f)
        self.d_info = QLabel()
        self.d_info.setWordWrap(True)
        self.d_info.setTextFormat(Qt.RichText)
        self.d_info.setOpenExternalLinks(True)
        self.d_info.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.d_info.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        grid = QGridLayout()
        self.btns = {}
        for i, (k, label, fn) in enumerate([
                ("run", "▶ 실행", lambda: self.run_game(self.cur_key)),
                ("folder", "위치 열기", lambda: self.open_folder(self.cur_key)),
                ("web", "웹페이지", lambda: self.open_web(self.cur_key)),
                ("research", "다시 찾기…", lambda: self.research(self.cur_key)),
                ("cover", "이미지 직접 지정…", lambda: self.set_cover(self.cur_key)),
                ("rename", "제목 수정…", lambda: self.rename(self.cur_key)),
                ("websearch", "웹에서 찾기 ▾", None),
                ("paste", "📋 복사한 이미지 붙여넣기", lambda: self.paste_cover(self.cur_key)),
                ("translate", "🈂 한국어 번역 설치", lambda: self.install_translation(self.cur_key)),
                ("shrink", "💾 용량 줄이기…", lambda: self.open_shrink(self.cur_key)),
                ("rate", "⭐ 평점…", lambda: self.rate_game(self.cur_key)),
                ("del_game", "🗑 게임 삭제", lambda: self.trash_parts([self.cur_key], {"game"})),
                ("del_install", "🗑 설치 삭제", lambda: self.trash_parts([self.cur_key], {"install"})),
                ("del_clear", "🗑 클리어 삭제", lambda: self.trash_parts([self.cur_key], {"clear"}))]):
            b = QPushButton(label)
            if fn:
                b.clicked.connect(fn)
            else:
                wm = QMenu(b)
                wm.aboutToShow.connect(lambda wm=wm: self.fill_web_menu(wm, self.cur_key))
                b.setMenu(wm)
            grid.addWidget(b, i // 2, i % 2)
            self.btns[k] = b
        pv.addWidget(self.d_cover)
        pv.addWidget(self.d_title)
        pv.addLayout(grid)
        self.d_rating = QLabel()
        self.d_rating.setVisible(False)
        pv.addWidget(self.d_rating)
        pv.addWidget(self.d_info, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        scroll.setMinimumWidth(380)

        split = QSplitter()
        split.addWidget(self.listw)
        split.addWidget(scroll)
        split.setStretchFactor(0, 1)
        split.setSizes([920, 400])
        self.setCentralWidget(split)

        self.status_lbl = QLabel()
        self.prog = QProgressBar()
        self.prog.setMaximumWidth(260)
        self.prog.hide()
        self.statusBar().addWidget(self.status_lbl, 1)
        self.statusBar().addPermanentWidget(self.prog)
        self.show_detail(None)
        QTimer.singleShot(1500, self.reconcile_files)

    def apply_sizes(self):
        w = self.thumb_w
        h = int(w * 0.75)
        self.listw.setIconSize(QSize(w, h))
        self.listw.setGridSize(QSize(w + 18, h + 52))

    def set_thumb(self, v):
        self.thumb_w = v
        self.lib["thumb_w"] = v
        self.icon_cache.clear()
        self.apply_sizes()
        self.refresh_list()
        self.schedule_save()

    # ---------- 썸네일
    def placeholder_pixmap(self, kind, w, h):
        """kind: noimg(이미지 없음, 모든 게임 공통) / wait(검색 대기) / blank(로딩 전)"""
        ck = (kind, w, h)
        if ck in self.ph_cache:
            return self.ph_cache[ck]
        pm = QPixmap(w, h)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#2a2c35"))
        p.drawRoundedRect(QRectF(0, 0, w, h), 8, 8)
        if kind != "blank":
            s = min(w, h) * 0.34
            cx, cy = w / 2, h * 0.40
            fr = QRectF(cx - s * 0.7, cy - s * 0.5, s * 1.4, s)
            lw = max(2.0, w / 90)
            p.setPen(QPen(QColor("#5f6274"), lw))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(fr, 4, 4)
            if kind == "noimg":
                p.drawPolyline(QPolygonF([
                    QPointF(fr.left() + s * 0.15, fr.bottom() - s * 0.15),
                    QPointF(cx - s * 0.18, cy - s * 0.02),
                    QPointF(cx + s * 0.08, cy + s * 0.2),
                    QPointF(cx + s * 0.28, cy + s * 0.04),
                    QPointF(fr.right() - s * 0.15, fr.bottom() - s * 0.15)]))
                p.drawEllipse(QPointF(fr.right() - s * 0.32, fr.top() + s * 0.28), s * 0.09, s * 0.09)
                p.setPen(QPen(QColor("#a35d63"), lw * 1.3))
                p.drawLine(QPointF(fr.left() - s * 0.1, fr.top() - s * 0.1),
                           QPointF(fr.right() + s * 0.1, fr.bottom() + s * 0.1))
                text, color = "이미지 없음", QColor("#9a9cab")
            else:
                for i in range(3):
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor("#7d8094"))
                    p.drawEllipse(QPointF(cx + (i - 1) * s * 0.35, cy), s * 0.08, s * 0.08)
                text, color = "정보 찾는 중", QColor("#80828f")
            f = QFont()
            f.setPixelSize(max(11, int(w / 14)))
            p.setFont(f)
            p.setPen(color)
            p.drawText(QRectF(0, fr.bottom() + h * 0.07, w, h * 0.22),
                       Qt.AlignHCenter | Qt.AlignTop, text)
        p.end()
        self.ph_cache[ck] = pm
        return pm

    def draw_badge(self, p, w, badge):
        if not badge:
            return
        col, mark = {"red": ("#d64545", "✕"), "yellow": ("#e0a92e", "!"),
                     "green": ("#3aa76d", "✓"), "err": ("#7a7c88", "?")}[badge]
        r = max(18, w // 9)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor("#ffffff"), 2))
        p.setBrush(QColor(col))
        p.drawEllipse(QRectF(6, 6, r, r))
        f = QFont()
        f.setPixelSize(int(r * 0.62))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#ffffff"))
        p.drawText(QRectF(6, 6, r, r), Qt.AlignCenter, mark)

    def draw_shrink(self, p, w, h, mark):
        """용량 줄인 게임 표시: 테두리 + 오른쪽 아래 💾 딱지 (확정=청록 실선, 확인 대기=주황 점선)"""
        if not mark:
            return
        col = QColor("#20b2aa" if mark == "done" else "#ff9f1c")
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(col, 4)
        if mark == "pending":
            pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(2, 2, w - 4, h - 4), 6, 6)
        r = max(20, w // 8)
        box = QRectF(w - r - 6, h - r - 6, r, r)
        p.setPen(QPen(QColor("#ffffff"), 2))
        p.setBrush(col)
        p.drawRoundedRect(box, 5, 5)
        f = QFont()
        f.setPixelSize(int(r * 0.6))
        p.setFont(f)
        p.drawText(box, Qt.AlignCenter, "💾" if mark == "done" else "⏳")

    def draw_score(self, p, w, h, score):
        """클리어한 게임: 왼쪽 아래 ★ 합계 딱지"""
        if score is None:
            return
        f = QFont()
        f.setPixelSize(max(12, w // 13))
        f.setBold(True)
        p.setFont(f)
        txt = f"★ {fmt_score(score)}/20"
        tw = p.fontMetrics().horizontalAdvance(txt) + 12
        th = f.pixelSize() + 8
        box = QRectF(6, h - th - 6, tw, th)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 170))
        p.drawRoundedRect(box, 6, 6)
        p.setPen(QColor(STAR_GOLD))
        p.drawText(box, Qt.AlignCenter, txt)

    def ph_icon(self, kind, badge=None, mark=None, score=None):
        w, h = self.thumb_w, int(self.thumb_w * 0.75)
        ck = ("icon", kind, w, badge, mark, score)
        if ck not in self.ph_cache:
            pm = self.placeholder_pixmap(kind, w, h).copy()
            if badge or mark or score is not None:
                p = QPainter(pm)
                self.draw_badge(p, w, badge)
                self.draw_shrink(p, w, h, mark)
                self.draw_score(p, w, h, score)
                p.end()
            self.ph_cache[ck] = QIcon(pm)
        return self.ph_cache[ck]

    def cover_path(self, e):
        c = e.get("cover")
        if c:
            cp = COVER_DIR / c
            if cp.exists():
                return cp
        return None

    def make_icon(self, e, load=True):
        w, h = self.thumb_w, int(self.thumb_w * 0.75)
        cp = self.cover_path(e)
        badge = sec_state(e)
        mark = shrink_mark(e)
        score = rating_total(e)
        if cp is None:
            return self.ph_icon("wait" if e.get("status") in ("new", "pending") else "noimg", badge, mark, score)
        ck = (str(cp), os.path.getmtime(cp), w, badge, mark, score)
        if ck in self.icon_cache:
            self.icon_cache.move_to_end(ck)
            return self.icon_cache[ck]
        if not load:
            return None
        rd = QImageReader(str(cp))
        rd.setAutoTransform(True)
        sz = rd.size()
        if sz.isValid():
            rd.setScaledSize(sz.scaled(w, h, Qt.KeepAspectRatio))   # 작게 디코딩 → 빠름
        img = rd.read()
        if img.isNull():
            return self.ph_icon("noimg")
        if img.width() > w or img.height() > h:
            img = img.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        pm = QPixmap(w, h)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.drawImage((w - img.width()) // 2, (h - img.height()) // 2, img)
        self.draw_badge(p, w, badge)
        self.draw_shrink(p, w, h, mark)
        self.draw_score(p, w, h, score)
        p.end()
        icon = QIcon(pm)
        self.icon_cache[ck] = icon
        if len(self.icon_cache) > 600:
            self.icon_cache.popitem(last=False)
        return icon

    def schedule_lazy(self, *_):
        self.lazy_timer.start()

    def lazy_load(self):
        """화면에 보이는 썸네일만 불러오고, 멀리 벗어난 건 메모리에서 내림"""
        vp = self.listw.viewport().rect()
        first = next(iter(self.items.values()), None)
        if first is not None and self.listw.visualItemRect(first).isEmpty():
            QTimer.singleShot(80, self.lazy_load)      # 아직 배치 계산 전이면 잠시 후 다시
            return
        near = vp.adjusted(0, -vp.height() // 2, 0, vp.height())
        far = vp.adjusted(0, -vp.height() * 3, 0, vp.height() * 3)
        loaded = 0
        for key, it in self.items.items():
            r = self.listw.visualItemRect(it)
            state = it.data(Qt.UserRole + 1)
            if state == "img" and not r.intersects(far):
                it.setIcon(self.ph_icon("blank"))
                it.setData(Qt.UserRole + 1, "blank")
            elif state == "blank" and r.intersects(near):
                e = self.lib["games"].get(key)
                if e:
                    it.setIcon(self.make_icon(e))
                    it.setData(Qt.UserRole + 1, "img")
                    loaded += 1
                    if loaded >= 48:          # 한 번에 너무 많이 읽지 않게 나눠서
                        self.lazy_timer.start()
                        return

    def showEvent(self, ev):
        super().showEvent(ev)
        QTimer.singleShot(50, self.lazy_load)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if hasattr(self, "lazy_timer"):
            self.schedule_lazy()

    # ---------- 목록
    def visible_entries(self):
        q = norm(self.search.text())
        fi = self.filter_cb.currentIndex()
        out = []
        for key, e in self.lib["games"].items():
            hidden, missing = e.get("hidden"), e.get("missing")
            if fi == 8:
                if not e.get("rating"):
                    continue
            elif fi == 3:
                if not hidden:
                    continue
            elif fi == 4:
                if not missing:
                    continue
            else:
                if hidden or (missing and not e.get("rating")):     # 클리어한 게임은 파일이 없어도 보임
                    continue
                if fi == 1 and e.get("status") != "found":
                    continue
                if fi == 2 and e.get("status") == "found":
                    continue
                if fi == 5 and sec_state(e) not in ("red", "yellow"):
                    continue
                if fi == 6 and not e.get("plays"):
                    continue
                if fi == 7 and not shrink_mark(e):
                    continue
            if q:
                hay = norm(" ".join([display_name(e), e.get("title", ""), e.get("name", ""), e.get("code", ""),
                                     e.get("maker", ""), " ".join(e.get("genres") or [])]))
                if q not in hay:
                    continue
            out.append((key, e))
        si = self.sort_cb.currentIndex()
        if si == 0:
            out.sort(key=lambda x: norm(re.sub(r"^\S*\d{6,}\s*", "", display_name(x[1]))))
        elif si == 1:
            out.sort(key=lambda x: x[1].get("added", 0), reverse=True)
        elif si == 2:
            out.sort(key=lambda x: (x[1].get("code") or "~", norm(x[1].get("title"))))
        elif si == 3:
            out.sort(key=lambda x: (norm(x[1].get("maker")) or "~", norm(x[1].get("title"))))
        elif si == 4:
            out.sort(key=lambda x: x[1].get("last_played", 0), reverse=True)
        else:
            out.sort(key=lambda x: (-(rating_total(x[1]) or -1), norm(display_name(x[1]))))
        return out

    def item_text(self, e):
        t = display_name(e)
        return t if len(t) <= 38 else t[:36] + "…"

    def set_item_icon(self, it, e):
        icon = self.make_icon(e, load=False)
        if icon is None:
            it.setIcon(self.ph_icon("blank", None, shrink_mark(e), rating_total(e)))
            it.setData(Qt.UserRole + 1, "blank")
        else:
            it.setIcon(icon)
            it.setData(Qt.UserRole + 1, "img" if self.cover_path(e) else "ph")

    def refresh_list(self):
        keep = self.cur_key
        self.listw.setUpdatesEnabled(False)
        self.listw.clear()
        self.items = {}
        for key, e in self.visible_entries():
            it = QListWidgetItem(self.item_text(e))
            it.setData(Qt.UserRole, key)
            it.setToolTip(f"{display_name(e)}\n{e.get('path')}")
            it.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            self.set_item_icon(it, e)
            self.listw.addItem(it)
            self.items[key] = it
        self.listw.setUpdatesEnabled(True)
        if keep in self.items:
            self.listw.setCurrentItem(self.items[keep])
        self.update_status()
        self.schedule_lazy()
        QTimer.singleShot(300, self.lazy_load)

    def update_item(self, key):
        it, e = self.items.get(key), self.lib["games"].get(key)
        if it and e:
            it.setText(self.item_text(e))
            it.setToolTip(f"{display_name(e)}\n{e.get('path')}")
            r = self.listw.visualItemRect(it)
            if r.intersects(self.listw.viewport().rect().adjusted(0, -400, 0, 400)):
                it.setIcon(self.make_icon(e))
                it.setData(Qt.UserRole + 1, "img" if self.cover_path(e) else "ph")
            else:
                self.set_item_icon(it, e)
        if key == self.cur_key:
            self.show_detail(key)

    def update_status(self):
        g = [e for e in self.lib["games"].values() if not e.get("hidden") and not e.get("missing")]
        found = sum(1 for e in g if e.get("status") == "found")
        txt = f"게임 {len(g)}개 · 정보 찾음 {found} · 못 찾음 {len(g) - found}"
        cl = sum(1 for e in self.lib["games"].values() if e.get("rating"))
        if cl:
            txt += f" · 🏆 클리어 {cl}개"
        sh = [e for e in g if shrink_mark(e)]
        if sh:
            txt += (f" · 💾 용량 줄임 {len(sh)}개 ({gs_shrink.human(sum((e.get('shrink') or {}).get('saved', 0) for e in sh))} 절약"
                    + (f", 확인 대기 {sum(1 for e in sh if shrink_mark(e) == 'pending')}개" if any(shrink_mark(e) == 'pending' for e in sh) else "") + ")")
        if self.jobs_left:
            txt += f"   |   정보 가져오는 중… 남은 {self.jobs_left}개"
        for k, label in (("def", "Defender"), ("vt", "VirusTotal"), ("vtu", "VT 업로드")):
            if self.sec_prog.get(k):
                txt += f"   |   🛡 {label} {self.sec_prog[k]}"
        self.status_lbl.setText(txt)

    # ---------- 상세
    def show_detail(self, key):
        self.cur_key = key
        e = self.lib["games"].get(key) if key else None
        for b in self.btns.values():
            b.setEnabled(bool(e))
        if not e:
            self.d_cover.clear()
            self.d_title.setText("")
            self.d_info.setText("<span style='color:#888'>게임을 선택하세요.<br>"
                                "더블클릭하면 실행, 오른쪽 클릭으로 메뉴가 열립니다.</span>")
            return
        self.btns["web"].setEnabled(bool(e.get("url")))
        self.btns["translate"].setEnabled(not e.get("archive") or self.archive_ready(e))
        gd = self.game_dir(e)
        self.btns["shrink"].setEnabled(bool(gd) and os.path.isdir(gd))
        pst = self.parts_state(e)
        self.btns["del_game"].setEnabled("game" in pst)
        self.btns["del_install"].setEnabled("install" in pst)
        self.btns["del_clear"].setEnabled("clear" in pst)
        for k, part in (("del_game", "game"), ("del_install", "install"), ("del_clear", "clear")):
            self.btns[k].setToolTip(pst.get(part, "지울 파일이 없습니다"))
        if e.get("removed"):
            for k in ("run", "folder", "translate", "shrink"):
                self.btns[k].setEnabled(False)
        if e.get("rating"):
            self.d_rating.setPixmap(rating_pixmap(e["rating"]))
            self.d_rating.setVisible(True)
        else:
            self.d_rating.setVisible(False)
        if e.get("archive"):
            ready = self.archive_ready(e)
            busy = key in self.extracting
            self.btns["run"].setText("📦 푸는 중…" if busy else "▶ 실행" if ready else "📦 압축 풀기")
            self.btns["run"].setEnabled(not busy)
        else:
            self.btns["run"].setText("▶ 실행")
            self.btns["run"].setEnabled(bool(e.get("exe")))
        if not self.play_folder(e) and "clear" in pst and not (e.get("archive") and "install" in pst):
            self.btns["run"].setText("📦 클리어본 풀기")
            self.btns["run"].setEnabled(key not in self.extracting)
        elif e.get("removed"):
            self.btns["run"].setText("🗑 삭제한 게임")
            self.btns["run"].setEnabled(False)
        cp = COVER_DIR / e["cover"] if e.get("cover") else None
        pm = QPixmap(str(cp)) if cp and cp.exists() else QPixmap()
        if pm.isNull():
            kind = "wait" if e.get("status") in ("new", "pending") else "noimg"
            self.d_cover.setPixmap(self.placeholder_pixmap(kind, 360, 270))
        else:
            self.d_cover.setPixmap(pm.scaled(360, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.d_title.setText(display_name(e))
        esc = html.escape
        rows = []

        def row(k, v):
            if v:
                rows.append(f"<tr><td style='color:#999;padding-right:10px;white-space:nowrap'>{k}</td>"
                            f"<td>{v}</td></tr>")
        if e.get("title_ko") and not e.get("manual_title"):
            row("원제", esc(e.get("title", "")))
        row("코드", esc(e.get("code", "")))
        row("제작", esc(e.get("maker", "")))
        row("발매일", esc(e.get("date", "")))
        row("장르", esc(", ".join(e.get("genres") or [])))
        if e.get("url"):
            row("출처", f"<a href='{esc(e['url'])}' style='color:#7ab7ff'>{esc(e.get('source', ''))}</a>")
        st = {"found": "정보 찾음", "notfound": "못 찾음 — ‘다시 찾기’로 직접 고를 수 있습니다",
              "pending": "찾는 중…", "new": "아직 검색 안 함", "error": "오류: " + esc(e.get("error", ""))}
        row("상태", st.get(e.get("status"), ""))
        if e.get("status") == "notfound" and e.get("debug"):
            row("진단", f"<span style='color:#c99'>{esc(e['debug'])}</span>")
        row("폴더 이름", esc(e.get("name", "")))
        row("경로", esc(e.get("path", "")))
        if e.get("clear_archive"):
            row("클리어본", esc(e["clear_archive"]))
        if e.get("removed"):
            row("파일", f"<span style='color:#c99'>🗑 삭제함 ({fmt_time(e.get('removed_at'))}) — 기록·평점만 남아 있습니다</span>")
        if e.get("rating"):
            row("클리어", f"🏆 평점 {fmt_score(rating_total(e))} / 20 ({fmt_time(e['rating'].get('date'))})")
        sh = e.get("shrink") or {}
        if shrink_mark(e) == "done":
            row("용량", f"<span style='color:#20b2aa'>💾 줄임 — {gs_shrink.human(sh.get('saved', 0))} 절약"
                      f" ({esc(', '.join(sh.get('actions') or []))}, {fmt_time(sh.get('date'))})</span>")
        elif shrink_mark(e) == "pending":
            row("용량", f"<span style='color:#ff9f1c'>⏳ 확인 대기 — {gs_shrink.human(sh.get('pending', 0))} 줄어듦, "
                      "게임 실행으로 확인한 뒤 ‘용량 줄이기’ 창에서 확정해주세요</span>")
        if e.get("plays"):
            row("플레이", f"{e['plays']}회 · 총 {fmt_secs(e.get('play_secs'))} · 마지막 {fmt_time(e.get('last_played'))}")
        sc = e.get("scan") or {}
        d = sc.get("def")
        amode = "<span style='color:#aaa'> · 압축 상태로 검사</span>"
        dm = amode if sc.get("def_mode") == "archive" else ""
        if d == "clean":
            row("Defender", f"<span style='color:#5c5'>✓ 이상 없음</span> ({esc(sc.get('def_date', ''))}){dm}")
        elif d == "threat":
            files = "<br>".join(esc(x) for x in sc.get("threat_files") or [])
            row("Defender", f"<span style='color:#f66'><b>위협 발견:</b> {esc(', '.join(sc.get('threats') or []))}</span>"
                            f" ({esc(sc.get('def_date', ''))}){dm}" + (f"<br><small>{files}</small>" if files else ""))
        elif d == "error":
            row("Defender", f"<span style='color:#aaa'>검사 오류: {esc(sc.get('def_msg', ''))}</span>")
        vt = sc.get("vt") or {}
        vurl = f"https://www.virustotal.com/gui/file/{vt.get('sha', '')}"
        col = txt = ""
        if vt.get("state") == "ok":
            col = "#f66" if vt["mal"] >= 3 else "#eb5" if vt["mal"] or vt.get("sus") else "#5c5"
            txt = f"{vt['mal']} / {vt['total']} 엔진 탐지" + (f", 의심 {vt['sus']}" if vt.get("sus") else "")
            if vt.get("label"):
                txt += f" ({esc(vt['label'])})"
        vm = (amode + (f"<span style='color:#aaa'> ({esc(vt.get('member', ''))})</span>" if vt.get("member") else "")
              if vt.get("mode") == "archive" else "")
        if vt.get("state") == "ok":
            row("VirusTotal", f"<a href='{vurl}' style='color:{col}'>{txt}</a> ({esc(vt.get('date', ''))}){vm}")
        elif vt.get("state") == "unknown":
            row("VirusTotal", f"<a href='{vurl}' style='color:#aaa'>기록 없음 — 처음 보는 파일</a>{vm}")
        elif vt.get("state") == "nofile":
            row("VirusTotal", f"<span style='color:#aaa'>실행 파일이 없어 조회 안 함"
                              f"{(' — ' + esc(vt['msg'])) if vt.get('msg') else ''}</span>")
        elif vt.get("state") == "error":
            row("VirusTotal", f"<span style='color:#aaa'>오류: {esc(vt.get('msg', ''))}</span>")
        vtu = sc.get("vtu") or {}
        if vtu:
            fl = vtu.get("files") or []
            cnt = lambda st: sum(1 for f in fl if f.get("state") == st)
            bad = [f for f in fl if f.get("state") == "ok" and (f.get("mal") or f.get("sus"))]
            parts = [f"실행 파일 {len(fl)}개 검사"]
            if bad:
                parts.append(f"<span style='color:#f66'>탐지 {len(bad)}개</span>")
            elif cnt("ok"):
                parts.append("<span style='color:#5c5'>탐지 없음</span>")
            if cnt("pending"):
                parts.append(f"<span style='color:#eb5'>분석 대기 {cnt('pending')}개</span>")
            if cnt("toolarge"):
                parts.append(f"용량 초과 {cnt('toolarge')}개 → Defender 결과 참고")
            if cnt("error"):
                parts.append(f"오류 {cnt('error')}개")
            if vtu.get("note"):
                parts.append(esc(vtu["note"]))
            lines = [" · ".join(parts) + f" ({esc(vtu.get('date', ''))})"]
            for f in (bad + [f for f in fl if f not in bad])[:6]:
                url = f"https://www.virustotal.com/gui/file/{f.get('sha', '')}"
                st = f.get("state")
                tag = (f"{f.get('mal', 0)}/{f.get('total', 0)}" if st == "ok" else
                       {"pending": "분석 대기", "toolarge": "650MB 초과", "error": "오류"}.get(st, st))
                name = esc(f.get("name", ""))
                lines.append(f"<small><a href='{url}' style='color:#9bf'>{name}</a> — {tag}</small>"
                             if f.get("sha") else f"<small>{name} — {tag}</small>")
            row("VT 업로드", "<br>".join(lines))
        if e.get("archive"):
            if e.get("extracted"):
                row("종류", "압축파일 · 압축 풀림")
                row("푼 위치", esc(e["extracted"]))
                row("실행 파일", esc(os.path.basename(e.get("exe", ""))) or "(못 찾음)")
            else:
                row("종류", "압축파일 · 아직 안 풀었음 (‘📦 압축 풀기’ 또는 더블클릭)")
        else:
            row("실행 파일", esc(os.path.basename(e.get("exe", ""))) or "(못 찾음)")
        self.d_info.setText("<table>" + "".join(rows) + "</table>")

    # ---------- 폴더 / 스캔
    def add_root(self):
        d = QFileDialog.getExistingDirectory(self, "게임이 들어 있는 폴더 선택")
        if not d:
            return
        d = os.path.abspath(d)
        if d not in self.lib["roots"]:
            self.lib["roots"].append(d)
            self.schedule_save()
        self.rescan()

    def fill_roots_menu(self):
        self.roots_menu.clear()
        if not self.lib["roots"]:
            a = self.roots_menu.addAction("(등록된 폴더 없음)")
            a.setEnabled(False)
            return
        for r in self.lib["roots"]:
            sub = self.roots_menu.addMenu(r)
            sub.addAction("탐색기에서 열기", lambda r=r: QDesktopServices.openUrl(QUrl.fromLocalFile(r)))
            sub.addAction("목록에서 이 폴더 빼기", lambda r=r: self.remove_root(r))

    def remove_root(self, r):
        if QMessageBox.question(self, "폴더 빼기",
                                f"{r}\n\n이 폴더와 그 안의 게임들을 목록에서 뺄까요?\n(실제 파일은 지우지 않습니다)") \
                != QMessageBox.Yes:
            return
        self.lib["roots"].remove(r)
        rk = key_of(r)
        for k in [k for k in self.lib["games"] if k == rk or k.startswith(rk + os.sep)]:
            self.lib["games"].pop(k, None)
        self.schedule_save()
        self.refresh_list()

    def rescan(self):
        roots = list(self.lib["roots"])
        if not roots:
            self.add_root()
            return
        self.status_lbl.setText("폴더 스캔 중…")
        sig = self.sig

        def job():
            res = {}
            for r in roots:
                res[r] = scan_root(r) if os.path.isdir(r) else None
            for r, paths in res.items():
                if paths:
                    res[r] = [(p, "" if os.path.isfile(p) else find_exe(p)) for p in paths]
            sig.scan_done.emit(res)

        self.pool.start(FuncTask(job))

    def on_scan_done(self, res):
        games = self.lib["games"]
        added = 0
        ext_keys = {key_of(e["extracted"]) for e in games.values() if e.get("extracted")}
        for root, items in res.items():
            if items is None:          # 루트 폴더 자체가 없음 (외장하드 분리 등) → 그대로 둠
                continue
            rk = key_of(root)
            seen = set()
            for path, exe in items:
                k = key_of(path)
                if k in ext_keys or os.path.basename(path).endswith(".gameshelf_tmp"):
                    continue
                seen.add(k)
                e = games.get(k)
                if e is None:
                    is_arc = os.path.isfile(path)
                    name = os.path.basename(path.rstrip("\\/"))
                    if is_arc:
                        name = os.path.splitext(name)[0]
                    games[k] = dict(path=path, name=name, code=find_code(name),
                                    title=clean_name(name), exe=exe, status="new",
                                    archive=is_arc, added=time.time())
                    added += 1
                else:
                    e["missing"] = False
                    e["removed"] = False
                    e["path"] = path
                    if exe:
                        e["exe"] = exe
            for k, e in games.items():
                if (k == rk or k.startswith(rk + os.sep)) and k not in seen and k not in ext_keys:
                    e["missing"] = not any(os.path.exists(x) for x in (
                        e.get("path", ""), e.get("extracted", ""), e.get("clear_archive", "")) if x)
        self.reconcile_files()
        self.schedule_save()
        self.refresh_list()
        self.status_lbl.setText(f"스캔 완료 · 새 게임 {added}개")
        self.fetch_missing(False)

    # ---------- 정보 가져오기
    def fetch_missing(self, include_failed):
        want = ("new", "notfound", "error") if include_failed else ("new",)
        keys = [k for k, e in self.lib["games"].items()
                if e.get("status") in want and not e.get("hidden") and not e.get("missing")]
        self.start_lookup(keys)

    def start_lookup(self, keys, cand=None):
        if not keys:
            return
        if self.jobs_left == 0:
            self.stop_evt = threading.Event()
            self.jobs_total = 0
        for k in keys:
            e = self.lib["games"][k]
            e["status"] = "pending"
            self.pool.start(make_lookup_task(k, e, self.sig, self.stop_evt, cand))
            self.update_item(k)
        self.jobs_total += len(keys)
        self.jobs_left += len(keys)
        self.prog.setMaximum(self.jobs_total)
        self.prog.setValue(self.jobs_total - self.jobs_left)
        self.prog.show()
        self.update_status()

    def stop_jobs(self):
        self.stop_evt.set()
        self.pool.clear()
        for e in self.lib["games"].values():
            if e.get("status") == "pending":
                e["status"] = "new"
        self.jobs_left = 0
        self.prog.hide()
        self.icon_cache.clear()
        self.refresh_list()

    def on_looked_up(self, key, res):
        if res.get("status") == "manual_cover":
            e = self.lib["games"].get(key)
            if e and res.get("cover"):
                e["cover"], e["manual_cover"] = res["cover"], True
                self.update_item(key)
                self.schedule_save()
                self.update_status()
            else:
                QMessageBox.warning(self, "붙여넣기 실패",
                                    "그 주소에서 이미지를 받지 못했습니다 (로그인/차단된 사이트일 수 있음).\n"
                                    "주소 대신 이미지를 우클릭 → ‘이미지 복사’로 해 보세요.")
            return
        if self.jobs_left > 0:
            self.jobs_left -= 1
        e = self.lib["games"].get(key)
        if e and e.get("status") == "pending":
            st = res.get("status")
            if st == "found":
                for f in ("source", "code", "maker", "date", "genres", "image_url", "url"):
                    if res.get(f) or f not in e:
                        e[f] = res.get(f, "")
                if res.get("title") and not e.get("manual_title"):
                    if e.get("title") != res["title"]:
                        e["title_ko"] = ""          # 제목이 바뀌면 다시 번역
                    e["title"] = res["title"]
                if res.get("cover") and not e.get("manual_cover"):
                    e["cover"] = res["cover"]
                e["status"] = "found"
                e.pop("error", None)
            elif st == "cancelled":
                e["status"] = "new"
            elif st == "error":
                e["status"], e["error"] = "error", res.get("error", "")
            else:
                e["status"] = "notfound"
                e["debug"] = res.get("debug", "")
            self.update_item(key)
            self.schedule_save()
        self.prog.setValue(self.jobs_total - self.jobs_left)
        if self.jobs_left == 0:
            self.prog.hide()
            self.translate_titles()
        self.update_status()

    # ---------- 개별 동작
    def run_game(self, key):
        e = self.lib["games"].get(key) if key else None
        if not e:
            return
        if not self.play_folder(e) and e.get("clear_archive") and os.path.isfile(e["clear_archive"]) and \
                not (e.get("archive") and os.path.isfile(e.get("path", ""))):
            self.start_extract(key)          # 처음 받은 압축파일은 지웠고 클리어본만 있음 → 클리어본을 풂
            return
        if e.get("archive"):
            if not self.archive_ready(e):
                self.launch_dialog(key)
                return
            exe = e.get("exe") if e.get("exe") and os.path.isfile(e["exe"]) else find_exe(e["extracted"])
            e["exe"] = exe
            if not exe:
                QMessageBox.information(self, "실행", "압축 푼 폴더에서 실행 파일을 찾지 못해 폴더를 엽니다.")
                reveal(e["extracted"])
                return
            self.launch(key, exe)
            return
        exe = e.get("exe")
        if not exe or not os.path.exists(exe):
            exe = find_exe(e["path"])
            e["exe"] = exe
        if not exe:
            QMessageBox.information(self, "실행", "실행 파일을 찾지 못해서 폴더를 엽니다.")
            self.open_folder(key)
            return
        self.launch(key, exe)

    def launch(self, key, exe):
        e = self.lib["games"][key]
        if self.lib.get("settings", {}).get("auto_cheat", True):
            self.auto_cheat(key, exe)
        try:
            pr = subprocess.Popen([exe], cwd=os.path.dirname(exe))
        except Exception as ex:
            QMessageBox.warning(self, "실행 실패", str(ex))
            return
        now = time.time()
        e["plays"] = e.get("plays", 0) + 1
        e["last_played"] = now
        hist = self.lib.setdefault("history", [])
        hist.append({"key": key, "title": display_name(e), "start": now, "secs": None})
        del hist[:-500]
        self.running.append((key, now, pr, hist[-1]))
        self.update_item(key)
        self.schedule_save()
        self.status_lbl.setText(f"▶ {display_name(e)} 실행")

    def poll_running(self, closing=False):
        """실행한 게임이 끝났는지 5초마다 확인해서 플레이 시간 누적"""
        still = []
        for key, start, pr, rec in self.running:
            if pr.poll() is None and not closing:
                still.append((key, start, pr, rec))
                continue
            secs = time.time() - start
            e = self.lib["games"].get(key)
            # 10초 안에 끝난 건 런처가 진짜 게임을 띄우고 바로 종료한 경우라 시간 측정 불가
            if secs >= 10:
                rec["secs"] = int(secs)
                if e:
                    e["play_secs"] = e.get("play_secs", 0) + int(secs)
            if e:
                self.update_item(key)
            self.schedule_save()
        self.running = still

    def open_folder(self, key):
        e = self.lib["games"].get(key) if key else None
        if e:
            reveal(e["extracted"] if e.get("archive") and self.archive_ready(e) else e["path"])

    # ---------- 압축 풀기
    def archive_ready(self, e):
        """압축 푼 폴더가 아직 있으면 True. 지워졌거나 옮겨졌으면 연결을 끊고 '압축 풀기' 상태로"""
        ex = e.get("extracted")
        if ex and os.path.isdir(ex):
            return True
        if ex:
            e.pop("extracted", None)
            e["exe"] = ""
            self.schedule_save()
        return False

    def start_extract(self, key, pw=""):
        e = self.lib["games"].get(key)
        if not e or key in self.extracting:
            return False
        src, name, parent = self.extract_source(e)
        if not src:
            QMessageBox.warning(self, "압축 풀기", f"압축파일이 없습니다:\n{e.get('path', '')}")
            return False
        path = src
        same = os.path.join(parent, name)
        if not pw and os.path.isdir(same) and os.listdir(same):
            r = QMessageBox.question(
                self, "압축 풀기",
                f"이미 같은 이름의 폴더가 있습니다:\n{same}\n\n이 폴더를 이 게임의 압축 푼 폴더로 연결할까요?\n"
                "(아니오 → 새로 압축을 풀어 ‘(2)’가 붙은 폴더로 만듭니다)",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if r == QMessageBox.Cancel:
                return False
            if r == QMessageBox.Yes:
                self.on_extracted(key, same, "")
                return True
        self.extracting.add(key)
        self.status_lbl.setText(f"📦 압축 푸는 중… {os.path.basename(path)}")
        if key == self.cur_key:
            self.btns["run"].setEnabled(False)
            self.btns["run"].setText("📦 푸는 중…")
        sig = self.sig

        def job():
            try:
                dest, msg = extract_archive(path, pw, name, parent)
            except Exception as ex:
                dest, msg = None, f"|{type(ex).__name__}: {ex}"
            sig.extracted.emit(key, dest or "", msg)

        self.pool.start(FuncTask(job))
        return True

    def on_extracted(self, key, dest, msg):
        self.extracting.discard(key)
        e = self.lib["games"].get(key)
        if not e:
            return
        if not dest:
            hint, _, detail = msg.partition("|")
            pw, ok = QInputDialog.getText(
                self, "압축 풀기 실패",
                ("암호가 걸린 압축파일 같습니다." if hint else "압축을 풀지 못했습니다.") +
                f"\n{detail}\n\n암호가 있다면 입력하고 다시 시도하세요 (모르면 취소):",
                QLineEdit.Password)
            if ok and pw:
                self.start_extract(key, pw)
            else:
                self.status_lbl.setText("압축 풀기 실패")
                self.update_item(key)
                dlg = self.rand_dlg
                if dlg and dlg.key == key and dlg.state == "extracting":
                    dlg.info.setText(f"<span style='color:#f66'>압축 풀기 실패</span><br>{html.escape(detail)}")
                    dlg.state = "pick"
                    dlg.reset_buttons()
            return
        games = self.lib["games"]
        dk = key_of(dest)
        dup = games.pop(dk, None) if dk != key else None   # 같은 폴더가 따로 게임으로 등록돼 있었으면 합침
        if dup:
            for f in ("plays", "play_secs", "last_played"):
                if dup.get(f) and not e.get(f):
                    e[f] = dup[f]
            it = self.items.pop(dk, None)
            if it is not None:
                self.listw.takeItem(self.listw.row(it))
        if e.get("archive"):
            e["extracted"] = dest
        else:
            e["path"] = dest                # 폴더 게임을 클리어본에서 되살린 경우
        e["exe"] = find_exe(dest)
        e["removed"] = False
        e["missing"] = False
        try:
            ns = restore_saves(e, dest)
        except OSError:
            ns = 0
        self.schedule_save()
        self.update_item(key)
        self.status_lbl.setText(f"📦 압축 풀기 완료 → {dest}" +
                                ("" if e["exe"] else "  (실행 파일은 못 찾음)") +
                                (f"  · 보관해 둔 세이브 {ns}개 되돌려 놓음" if ns else ""))
        dlg = self.rand_dlg
        if dlg and dlg.key == key and dlg.state == "extracting":
            dlg.pending_tr = dest          # 번역기 설치는 보안 검사를 통과한 뒤에
        else:
            self.auto_translation(key, dest)
        if dlg and dlg.key == key and dlg.state == "extracting":
            dlg.info.setText("📦 압축 풀기 완료<br>" + dlg.checking_text)
            self.begin_check(dlg)

    # ---------- 게임 번역 (GameTranslator)
    def game_dir(self, e):
        """번역기를 적용할 폴더: 압축 게임이면 푼 폴더, 아니면 게임 폴더"""
        if e.get("archive"):
            return e["extracted"] if self.archive_ready(e) else ""
        p = e.get("path", "")
        return p if os.path.isdir(p) else os.path.dirname(p)

    def install_translation(self, key):
        e = self.lib["games"].get(key) if key else None
        if not e:
            return
        if not TRANSLATOR_PS1.is_file():
            QMessageBox.warning(self, "한국어 번역", f"번역기 툴이 없습니다:\n{TRANSLATOR_PS1}\n\n"
                                "GameTranslator 폴더를 GameShelf 폴더 안에 넣어주세요.")
            return
        d = self.game_dir(e)
        if not d:
            QMessageBox.information(self, "한국어 번역", "압축을 먼저 풀어주세요.")
            return
        run_translator(d)
        self.status_lbl.setText(f"🈂 번역기 실행 → {d}")

    def auto_translation(self, key, dest):
        """압축 풀기 직후: 번역기 툴이 20MB 이하면 바로 이어서 설치, 넘으면 안내만"""
        st = self.lib.get("settings", {})
        if not st.get("auto_translate", True) or not TRANSLATOR_PS1.is_file():
            return
        size = translator_size()
        if size > TRANSLATOR_AUTO_LIMIT:
            QMessageBox.information(
                self, "한국어 번역",
                f"번역기 툴 용량이 {size / 1048576:.1f}MB로 20MB를 넘어서 자동 설치하지 않았습니다.\n\n"
                "게임 목록에서 오른쪽 클릭 → '🈂 한국어 번역 설치'로 설치할 수 있습니다.")
            return
        proc = run_translator(dest, auto=True)
        self.status_lbl.setText(self.status_lbl.text() + "  ·  🈂 번역기 자동 설치 중")
        return proc

    def rate_game(self, key, reason=""):
        """평점 창. 저장하면 '🏆 클리어한 게임'에 들어감. 반환: 저장했으면 True"""
        e = self.lib["games"].get(key) if key else None
        if not e:
            return False
        dlg = RatingDialog(self, e, reason)
        if dlg.exec() != QDialog.Accepted:
            return False
        if dlg.cleared:
            e.pop("rating", None)
        else:
            e["rating"] = dlg.result_rating()
        self.schedule_save()
        self.update_item(key)
        self.update_status()
        return True

    def ask_rating(self, key, reason):
        e = self.lib["games"].get(key)
        if e and not e.get("rating"):
            self.rate_game(key, reason)

    def open_shrink(self, key):
        if key and key in self.lib["games"]:
            was = shrink_mark(self.lib["games"][key])
            ShrinkDialog(self, key).exec()
            self.sync_shrink(key)
            if was != "done" and shrink_mark(self.lib["games"][key]) == "done":
                self.ask_rating(key, "용량 줄이기를 마쳤습니다. 다 한 게임이라면 평점을 남겨 보세요.")
            self.offer_drop_archive(key)

    def extract_source(self, e):
        """압축 풀 때 쓸 파일: 처음 받은 압축파일이 있으면 그것, 없으면 클리어본. 반환: (파일, 풀 폴더 이름, 위치)"""
        if e.get("archive"):
            base = e.get("path", "")
            name = os.path.splitext(os.path.basename(base))[0].rstrip(". ")
            parent = os.path.dirname(base)
            if os.path.isfile(base):
                return base, name, parent
        else:
            base = e.get("path", "")
            name, parent = os.path.basename(base.rstrip("\\/")), os.path.dirname(base.rstrip("\\/"))
        ca = e.get("clear_archive", "")
        if ca and os.path.isfile(ca):
            return ca, name, parent
        return "", name, parent

    def play_folder(self, e):
        """지금 플레이하는 폴더 (압축 게임이면 푼 폴더, 폴더 게임이면 그 폴더). 없으면 \"\""""
        if e.get("archive"):
            return e["extracted"] if self.archive_ready(e) else ""
        p = e.get("path", "")
        return p if p and os.path.isdir(p) else ""

    def clear_archive_path(self, e):
        folder = self.play_folder(e)
        base = e.get("path", "") if e.get("archive") else folder
        stem = os.path.splitext(os.path.basename(base))[0] if e.get("archive") else os.path.basename(folder.rstrip("\\/"))
        kind, _ = find_archiver(prefer="7z")
        ext = ".7z" if kind else ".zip"
        old = e.get("clear_archive", "")
        if old and os.path.splitext(old)[1].lower() == ext:
            return old
        return os.path.join(os.path.dirname(base.rstrip("\\/")), stem + CLEAR_TAG + ext)

    def offer_drop_archive(self, key):
        """용량 줄이기를 확정한 게임: 세이브까지 담은 클리어본 압축파일을 따로 만들지 물어봄 (원본은 그대로)"""
        e = self.lib["games"].get(key)
        if not e or shrink_mark(e) != "done" or not self.play_folder(e):
            return
        if e.get("clear_at", 0) >= (e.get("shrink") or {}).get("date", 0):
            return
        target = self.clear_archive_path(e)
        if QMessageBox.question(
                self, "클리어본 만들기",
                f"{display_name(e)}\n\n용량을 줄인 게임 폴더를 세이브까지 통째로 압축해서 클리어본을 따로 만들까요?\n\n"
                f"새로 만들 파일: {target}\n\n"
                "처음 받은 압축파일과 지금 플레이하던 폴더는 그대로 둡니다. 다 만든 뒤에 오른쪽 버튼으로 따로 지우면 됩니다.\n"
                "· 게임 삭제 = 플레이하던 폴더\n· 설치 삭제 = 처음 받은 압축파일\n· 클리어 삭제 = 클리어본") != QMessageBox.Yes:
            return
        self.start_repack(key)

    def repack_menu(self, key):
        e = self.lib["games"].get(key)
        folder = self.play_folder(e) if e else ""
        if not folder:
            return
        if os.path.isdir(os.path.join(folder, gs_shrink.BACKUP_DIR)):
            QMessageBox.information(self, "클리어본 만들기", "용량 줄이기가 확인 대기 중입니다. 먼저 '용량 줄이기' 창에서 확정해주세요.")
            return
        target = self.clear_archive_path(e)
        saves = find_save_dirs(folder)
        if QMessageBox.question(
                self, "클리어본 만들기",
                f"{display_name(e)}\n\n지금 게임 폴더를 세이브까지 통째로 압축해서 클리어본을 만듭니다.\n"
                f"· 폴더: {folder}\n· 세이브 폴더: {len(saves)}개\n· 만들 파일: {target}\n\n"
                + ("이미 클리어본이 있으면 최신 세이브로 새로 만들고, 이전 클리어본은 휴지통으로 보냅니다.\n"
                   if os.path.isfile(target) else "")
                + "처음 받은 압축파일은 건드리지 않습니다. 게임이 켜져 있으면 끄고 진행해주세요. 진행할까요?") != QMessageBox.Yes:
            return
        self.start_repack(key)

    def start_repack(self, key, then=""):
        """클리어본(줄인 폴더 + 세이브) 압축파일 만들기 (백그라운드). 처음 받은 압축파일은 건드리지 않음"""
        e = self.lib["games"].get(key)
        folder = self.play_folder(e) if e else ""
        if not folder:
            return
        self.repacking = getattr(self, "repacking", set())
        if key in self.repacking:
            return
        self.repacking.add(key)
        target = self.clear_archive_path(e)
        try:
            backup_saves(e, folder)
        except OSError:
            pass
        sig = self.sig
        self.status_lbl.setText(f"📦 {display_name(e)}: 클리어본 만드는 중… (세이브 포함)")

        def job():
            try:
                size, old = repack_archive(folder, target)
                if old:                     # 이전 클리어본은 새 파일 확인 후 휴지통으로
                    stem, ext = os.path.splitext(target)
                    named = f"{stem} 이전 {time.strftime('%Y%m%d_%H%M')}{ext}"
                    try:
                        os.replace(old, named)
                        old = named
                    except OSError:
                        pass
                    send_to_recycle_bin(old)
                sig.repacked.emit(key, "", f"{then}|{size}|{target}")
            except Exception as err:
                sig.repacked.emit(key, f"{type(err).__name__}: {err}", then)
        self.pool.start(FuncTask(job))

    def on_repacked(self, key, err, info):
        self.repacking.discard(key)
        e = self.lib["games"].get(key)
        if not e:
            return
        parts = info.split("|")
        then = parts[0]
        if err:
            self.status_lbl.setText("📦 클리어본 만들기 실패")
            QMessageBox.warning(self, "클리어본 만들기", f"{display_name(e)}\n\n{err}\n\n원래 파일들은 그대로입니다.")
            return
        size, target = int(parts[1] or 0), parts[2]
        e["clear_archive"] = target
        e["clear_at"] = time.time()
        self.schedule_save()
        self.update_item(key)
        self.status_lbl.setText(f"📦 {display_name(e)}: 클리어본을 만들었습니다 ({gs_shrink.human(size)}) — {target}")
        if then == "trash_game":
            self.trash_parts([key], {"game"}, confirmed=True)

    def relink_to_folder(self, key):
        """압축 게임 항목을 '폴더 게임'으로 바꿈 (플레이 기록·표지·정보는 그대로)"""
        games = self.lib["games"]
        e = games.get(key)
        if not e or not e.get("extracted"):
            return key
        folder = e["extracted"]
        nk = key_of(folder)
        dup = games.pop(nk, None) if nk != key else None
        if dup:
            for f in ("plays", "play_secs", "last_played"):
                if dup.get(f) and not e.get(f):
                    e[f] = dup[f]
        games.pop(key, None)
        e["path"] = folder
        e.pop("archive", None)
        e.pop("extracted", None)
        if not e.get("exe") or not os.path.isfile(e["exe"]):
            e["exe"] = find_exe(folder)
        games[nk] = e
        self.cur_key = nk
        self.schedule_save()
        self.refresh_list()
        if nk in self.items:
            self.listw.setCurrentItem(self.items[nk])
        self.show_detail(nk)
        self.status_lbl.setText(f"💾 {display_name(e)}: 원본 압축파일을 정리하고 {folder} 로 연결했습니다.")
        return nk

    def sync_shrink(self, key, committed=0):
        """게임 폴더의 백업 상태를 읽어 목록 표시(테두리·딱지)를 맞춤"""
        e = self.lib["games"].get(key)
        if not e:
            return
        d = self.game_dir(e)
        sh = dict(e.get("shrink") or {})
        bi = gs_shrink.backup_info(d) if d and os.path.isdir(d) else {"count": 0, "saved": 0, "actions": []}
        if committed:
            sh["saved"] = sh.get("saved", 0) + committed
            sh["state"] = "done"
            sh["date"] = time.time()
        for a in bi.get("actions") or []:
            sh.setdefault("actions", [])
            if a not in sh["actions"]:
                sh["actions"].append(a)
        if bi["count"]:
            sh["state"] = "pending"
            sh["pending"] = bi["saved"]
        else:
            sh.pop("pending", None)
            sh["state"] = "done" if sh.get("saved") else None
        old = e.get("shrink")
        if sh.get("state"):
            e["shrink"] = sh
        else:
            e.pop("shrink", None)
        if old != e.get("shrink"):
            self.schedule_save()
            self.update_item(key)
            self.update_status()

    def auto_cheat(self, key, exe):
        """RPG 쯔꾸르 MV/MZ 게임을 실행할 때 치트가 없으면 넣고, 있으면 최신 파일로 바꿈 (실패해도 실행은 그대로)"""
        try:
            d = os.path.dirname(exe)
            info = rpg_find(d, max_depth=2)
            if not info or not CHEAT_JS.is_file():
                return
            target = info[0] / "plugins" / "GS_Cheat.js"
            if cheat_installed(info):
                if target.read_bytes() != CHEAT_JS.read_bytes():
                    shutil.copyfile(CHEAT_JS, target)
                if (info[0] / "plugins" / "GS_Cheat_ko.json").is_file():
                    return
            else:
                install_cheat(d)
                self.lib["games"][key]["cheat"] = True
            self.start_cheat_dict(d, display_name(self.lib["games"][key]))
        except Exception as ex:
            dbg(f"치트 자동 설치 실패: {ex}")

    def start_cheat_dict(self, folder, title):
        def work():
            try:
                total, miss = build_cheat_dict(folder)
                msg = f"🎮 {title}: 치트 창 이름 번역 {total - miss}/{total}개"
                if miss and Translator.status:
                    msg += f" — {Translator.status}"
            except Exception as ex:
                msg = f"🎮 치트 이름 번역 실패: {type(ex).__name__}: {ex}"
            self.sig.cheat_msg.emit(msg)
        threading.Thread(target=work, daemon=True).start()

    def cheat_install(self, key):
        e = self.lib["games"].get(key) if key else None
        d = self.game_dir(e) if e else ""
        if not d:
            QMessageBox.information(self, "치트", "압축을 먼저 풀어주세요.")
            return
        try:
            js, data, kind = install_cheat(d)
        except CheatError as ex:
            QMessageBox.information(self, "치트", str(ex))
            return
        except OSError as ex:
            QMessageBox.warning(self, "치트", f"파일을 쓰지 못했습니다 (게임이 실행 중이거나 권한 문제):\n{ex}")
            return
        e["cheat"] = True
        self.schedule_save()
        self.start_cheat_dict(d, display_name(e))
        self.status_lbl.setText(f"🎮 치트 설치됨 (RPG 쯔꾸르 {kind}) — 이름 번역 중…")
        QMessageBox.information(
            self, "치트 설치 완료",
            f"RPG 쯔꾸르 {kind} 게임에 치트를 넣었습니다.\n\n"
            "게임 안에서  `  키 (숫자 1 왼쪽, Esc 아래) = 치트 창 열기/닫기\n"
            "Shift + `  = 아이템 사이드바\n\n"
            "아이템·맵 이름의 한국어 번역은 지금 백그라운드에서 만들고 있고,\n"
            "게임 실행 중에 끝나도 치트 창을 다시 열면 반영됩니다.\n\n"
            f"원래 plugins.js는 {js / 'plugins.js.gs_backup'} 로 백업했습니다.")

    def cheat_remove(self, key):
        e = self.lib["games"].get(key) if key else None
        d = self.game_dir(e) if e else ""
        if not d:
            return
        try:
            uninstall_cheat(d)
        except (CheatError, OSError) as ex:
            QMessageBox.warning(self, "치트 제거", str(ex))
            return
        e["cheat"] = False
        st = self.lib.setdefault("settings", {})
        self.schedule_save()
        note = ("\n\n※ '게임 실행 시 치트 자동 설치'가 켜져 있어서 다음 실행 때 다시 들어갑니다. "
                "⚙설정에서 끌 수 있습니다." if st.get("auto_cheat", True) else "")
        QMessageBox.information(self, "치트 제거", "치트를 뺐습니다." + note)

    def open_web(self, key):
        e = self.lib["games"].get(key) if key else None
        if e and e.get("url"):
            QDesktopServices.openUrl(QUrl(e["url"]))

    def research(self, key):
        e = self.lib["games"].get(key) if key else None
        if not e:
            return
        dlg = SearchDialog(self, e.get("code") or clean_name(e.get("name", "")), self.pool)
        if dlg.exec() == QDialog.Accepted:
            c = dlg.selected()
            if c:
                e["manual_cover"] = False
                e["manual_title"] = False
                if c.get("code") and c["source"] == "DLsite":
                    e["code"] = c["code"]
                self.start_lookup([key], cand=c)

    def set_cover(self, key):
        e = self.lib["games"].get(key) if key else None
        if not e:
            return
        fn, _ = QFileDialog.getOpenFileName(self, "대표 이미지 선택", e["path"],
                                            "이미지 (*.png *.jpg *.jpeg *.webp *.bmp *.gif)")
        if not fn:
            return
        ext = os.path.splitext(fn)[1].lower()
        name = hashlib.md5(key.encode("utf-8")).hexdigest()[:16]
        for old in COVER_DIR.glob(name + ".*"):
            old.unlink(missing_ok=True)
        shutil.copyfile(fn, COVER_DIR / (name + ext))
        e["cover"], e["manual_cover"] = name + ext, True
        self.update_item(key)
        self.schedule_save()

    def fill_web_menu(self, menu, key):
        menu.clear()
        e = self.lib["games"].get(key) if key else None
        if not e:
            return
        q = e.get("code") or e.get("title") or e.get("name", "")
        g = lambda extra: "https://www.google.com/search?q=" + quote(f"{extra} {q}".strip())
        for label, url in [
                ("아카라이브에서 찾기", g("site:arca.live")),
                ("코네에서 찾기", g("site:kone.gg")),
                ("구글 이미지 검색", "https://www.google.com/search?tbm=isch&q=" + quote(q)),
                ("DLsite 보관본 (Wayback Machine)",
                 "https://web.archive.org/web/*/dlsite.com/*" + quote(e.get("code", "")) + "*")]:
            menu.addAction(label, lambda u=url: QDesktopServices.openUrl(QUrl(u)))
        menu.addSeparator()
        a = menu.addAction("찾은 이미지는: 우클릭 → ‘이미지 복사’ 후 📋 붙여넣기")
        a.setEnabled(False)

    def paste_cover(self, key):
        """클립보드의 이미지(또는 이미지 주소)를 대표 이미지로 지정"""
        e = self.lib["games"].get(key) if key else None
        if not e:
            return
        md = QApplication.clipboard().mimeData()
        name = hashlib.md5(key.encode("utf-8")).hexdigest()[:16]
        if md.hasImage():
            img = QApplication.clipboard().image()
            if not img.isNull():
                for old in COVER_DIR.glob(name + ".*"):
                    old.unlink(missing_ok=True)
                img.save(str(COVER_DIR / (name + ".png")))
                e["cover"], e["manual_cover"] = name + ".png", True
                self.update_item(key)
                self.schedule_save()
                return
        text = (md.text() or "").strip()
        if re.match(r"https?://\S+$", text):
            sig = self.sig

            def job():
                fn = download_cover(text, key)
                sig.looked_up.emit(key, {"status": "manual_cover", "cover": fn, "url_tried": text})

            self.pool.start(FuncTask(job))
            self.status_lbl.setText("이미지 주소에서 내려받는 중…")
            return
        QMessageBox.information(self, "붙여넣기",
                                "클립보드에 이미지가 없습니다.\n\n"
                                "웹페이지에서 이미지를 우클릭 → ‘이미지 복사’(또는 ‘이미지 주소 복사’)를 한 뒤 "
                                "다시 눌러 주세요.")

    def rename(self, key):
        e = self.lib["games"].get(key) if key else None
        if not e:
            return
        t, ok = QInputDialog.getText(self, "제목 수정", "표시할 제목 (비우면 자동 이름으로 되돌림):",
                                     text=display_name(e))
        if ok and not t.strip():
            e["manual_title"] = False
            self.update_item(key)
            self.schedule_save()
            return
        if ok and t.strip():
            e["title"], e["manual_title"] = t.strip(), True
            self.update_item(key)
            self.schedule_save()

    def context_menu(self, pos):
        it = self.listw.itemAt(pos)
        if not it:
            return
        keys = [i.data(Qt.UserRole) for i in self.listw.selectedItems()] or [it.data(Qt.UserRole)]
        key = it.data(Qt.UserRole)
        m = QMenu(self)
        if len(keys) == 1:
            ge = self.lib["games"][key]
            m.addAction("📦 압축 풀기" if ge.get("archive") and not self.archive_ready(ge) else "▶ 실행",
                        lambda: self.run_game(key))
            m.addAction("위치 열기", lambda: self.open_folder(key))
            a = m.addAction("🈂 한국어 번역 설치 / 점검", lambda: self.install_translation(key))
            a.setEnabled(not ge.get("archive") or self.archive_ready(ge))
            gd = self.game_dir(ge)
            ri = rpg_find(gd) if gd else None
            if ri:
                if cheat_installed(ri):
                    m.addAction("🎮 치트 다시 넣기 · 이름 다시 번역", lambda: self.cheat_install(key))
                    m.addAction("🎮 치트 빼기", lambda: self.cheat_remove(key))
                else:
                    m.addAction(f"🎮 치트 넣기 (RPG 쯔꾸르 {ri[2]}, 게임 안에서 ` 키)", lambda: self.cheat_install(key))
            if self.play_folder(ge):
                m.addAction("📦 클리어본 만들기" if not ge.get("clear_archive") else "📦 클리어본 새로 만들기",
                            lambda: self.repack_menu(key))
            a = m.addAction("💾 용량 줄이기…", lambda: self.open_shrink(key))
            a.setEnabled(bool(gd) and os.path.isdir(gd))
            a = m.addAction("웹페이지 열기", lambda: self.open_web(key))
            a.setEnabled(bool(self.lib["games"][key].get("url")))
            m.addSeparator()
            m.addAction("다시 찾기 (직접 고르기)…", lambda: self.research(key))
            m.addAction("이미지 직접 지정…", lambda: self.set_cover(key))
            m.addAction("제목 수정…", lambda: self.rename(key))
            m.addAction("📋 복사한 이미지 붙여넣기", lambda: self.paste_cover(key))
            self.fill_web_menu(m.addMenu("웹에서 찾기"), key)
        m.addAction(f"자동으로 다시 찾기 ({len(keys)}개)", lambda: self.auto_again(keys))
        m.addAction(f"🈯 제목 다시 번역 ({len(keys)}개)", lambda: self.translate_titles(keys, force=True))
        m.addAction(f"🛡 보안 검사 ({len(keys)}개, Defender + VirusTotal)",
                    lambda: self.sec_run("both", keys=keys, force=True))
        m.addAction(f"⬆ VirusTotal에 올려서 검사 ({len(keys)}개, 실행 파일만)", lambda: self.upload_run(keys))
        m.addSeparator()
        m.addAction(f"실제 폴더/파일 이름을 표시 이름으로 바꾸기 ({len(keys)}개)…",
                    lambda: self.rename_folders(keys, revert=False))
        if any(self.lib["games"][k].get("orig_name") for k in keys):
            m.addAction("원래 이름으로 되돌리기…", lambda: self.rename_folders(keys, revert=True))
        m.addSeparator()
        m.addSeparator()
        n = len(keys)
        cnt = {p: sum(1 for k in keys if p in self.parts_state(self.lib["games"][k])) for p in ("game", "install", "clear")}
        for part in ("game", "install", "clear"):
            a = m.addAction(f"🗑 {self.PART_NAME[part]}" + (f" {cnt[part]}개" if n > 1 else ""),
                            lambda part=part: self.trash_parts(keys, {part}))
            a.setEnabled(cnt[part] > 0)
        if n == 1 and not self.parts_state(self.lib["games"][key]):
            m.addAction("✖ 목록에서 지우기", lambda: self.forget_game(key))
        hidden = self.lib["games"][key].get("hidden")
        m.addAction("숨김 해제" if hidden else f"목록에서 숨기기 ({len(keys)}개)",
                    lambda: self.toggle_hidden(keys, not hidden))
        m.exec(self.listw.mapToGlobal(pos))

    def rename_folders(self, keys, revert=False):
        games = self.lib["games"]
        plans = []
        for k in keys:
            e = games.get(k)
            if not e:
                continue
            old = e["path"]
            is_file = os.path.isfile(old)
            if revert:
                new = e.get("orig_name")
            else:
                new = BAD_CHARS.sub(" ", display_name(e))
                new = re.sub(r"\s+", " ", new).strip().rstrip(". ")[:150]
                if new and is_file:
                    new += os.path.splitext(old)[1]
            if not new or os.path.basename(old.rstrip("\\/")) == new:
                continue
            plans.append((k, old, os.path.join(os.path.dirname(old.rstrip("\\/")), new)))
        if not plans:
            QMessageBox.information(self, "폴더 이름 바꾸기", "바꿀 폴더가 없습니다.")
            return
        preview = "\n".join(f"{os.path.basename(o)}\n   → {os.path.basename(d)}" for _, o, d in plans[:12])
        if len(plans) > 12:
            preview += f"\n… 외 {len(plans) - 12}개"
        if QMessageBox.question(self, "폴더 이름 바꾸기",
                                f"실제 폴더/파일 {len(plans)}개의 이름을 바꿉니다.\n"
                                f"(원래 이름은 기억해 두니 나중에 되돌릴 수 있습니다)\n\n{preview}") != QMessageBox.Yes:
            return
        fails = []
        for k, old, dst in plans:
            if os.path.exists(dst):
                fails.append(f"{os.path.basename(dst)}: 같은 이름의 폴더가 이미 있음")
                continue
            try:
                os.rename(old, dst)
            except OSError as ex:
                fails.append(f"{os.path.basename(old)}: {ex.strerror or ex}")
                continue
            e = games.pop(k)
            if revert:
                e.pop("orig_name", None)
            else:
                e.setdefault("orig_name", os.path.basename(old.rstrip("\\/")))
            if e.get("exe", "").startswith(old):
                e["exe"] = dst + e["exe"][len(old):]
            e["path"] = dst
            e["name"] = os.path.splitext(os.path.basename(dst))[0] if os.path.isfile(dst) \
                else os.path.basename(dst)
            nk = key_of(dst)
            games[nk] = e
            if self.cur_key == k:
                self.cur_key = nk
        self.schedule_save()
        self.refresh_list()
        if fails:
            QMessageBox.warning(self, "일부 실패",
                                "다음 폴더는 바꾸지 못했습니다 (게임 실행 중이거나 권한 문제일 수 있음):\n\n"
                                + "\n".join(fails[:20]))

    def auto_again(self, keys):
        for k in keys:
            self.lib["games"][k]["manual_cover"] = False
        self.start_lookup([k for k in keys if self.lib["games"][k].get("status") != "pending"])

    def toggle_hidden(self, keys, value):
        for k in keys:
            self.lib["games"][k]["hidden"] = value
        self.schedule_save()
        self.refresh_list()

    def need_translation(self, e):
        return (e.get("status") == "found" and not e.get("manual_title") and not e.get("title_ko")
                and JA_RE.search(e.get("title") or "") and not e.get("hidden"))

    def translate_titles(self, keys=None, force=False):
        """제목 번역: 한 줄로 25개씩 묶어서 보냄 (한 번에 하나의 번역 작업만)"""
        if self.translating:
            return
        games = self.lib["games"]
        if keys is None:
            keys = [k for k, e in games.items() if self.need_translation(e)]
        elif force:
            for k in keys:
                games[k]["title_ko"] = ""
                games[k]["manual_title"] = False
            keys = [k for k in keys if JA_RE.search(games[k].get("title") or "")]
        if not keys:
            self.status_lbl.setText("번역할 일본어 제목이 없습니다.")
            return
        items = [(k, games[k]["title"]) for k in keys]
        self.translating = True
        self.status_lbl.setText(f"제목 번역 중… 0 / {len(items)}")
        sig = self.sig

        def job():
            done = 0
            for i in range(0, len(items), 25):
                part = items[i:i + 25]
                res = Translator.many([t for _, t in part], "ja", "ko")
                got = {k: r for (k, _), r in zip(part, res) if r}
                done += len(part)
                sig.translated.emit(got, f"{done}/{len(items)}|{Translator.status}")
                if Translator.status and not got:
                    break
            sig.translated.emit({}, "END|" + Translator.status)

        self.pool.start(FuncTask(job))

    def on_translated(self, got, info):
        prog, _, err = info.partition("|")
        for k, ko in got.items():
            e = self.lib["games"].get(k)
            if e:
                e["title_ko"] = ko
                self.update_item(k)
        if got:
            self.schedule_save()
        if prog == "END":
            self.translating = False
            left = sum(1 for e in self.lib["games"].values() if self.need_translation(e))
            msg = "제목 번역 완료" if not left else f"번역 안 된 제목 {left}개 남음"
            self.status_lbl.setText(msg + (f"  ⚠ {err}" if err else ""))
            if err and left:
                QMessageBox.warning(self, "번역 중단", f"{err}\n\n번역 안 된 제목 {left}개는 "
                                    "나중에 ‘🈯 제목 번역’을 다시 누르면 이어서 번역합니다.")
        else:
            self.status_lbl.setText(f"제목 번역 중… {prog}" + (f"  ⚠ {err}" if err else ""))

    def update_bridge(self):
        st = self.lib.get("settings", {})
        if st.get("game_engine") == "local":
            err = self.bridge.start(int(st.get("bridge_port", 5577)), st)
            if err:
                self.status_lbl.setText("⚠ " + err)
        else:
            self.bridge.stop()

    def open_settings(self):
        st = self.lib.setdefault("settings", {})
        dlg = QDialog(self)
        dlg.setWindowTitle("설정")
        dlg.resize(680, 640)
        outer = QVBoxLayout(dlg)
        area = QScrollArea()
        area.setWidgetResizable(True)
        body = QWidget()
        v = QVBoxLayout(body)
        area.setWidget(body)
        outer.addWidget(area, 1)

        def label(html_text):
            lb = QLabel(html_text)
            lb.setOpenExternalLinks(True)
            lb.setWordWrap(True)
            v.addWidget(lb)
            return lb

        def engine_box(cur):
            cb = QComboBox()
            for k, name in ENGINES:
                cb.addItem(name, k)
            cb.setCurrentIndex(max(0, [k for k, _ in ENGINES].index(cur) if cur in dict(ENGINES) else 0))
            v.addWidget(cb)
            return cb

        label("<b>🈯 제목 번역 엔진</b> — 게임 목록의 일본어 제목 번역, 한국어 폴더 이름을 일본어로 바꿔 검색할 때")
        cb_title = engine_box(st.get("title_engine") or Translator.engine)
        label("<br><b>🎮 게임 번역 엔진</b> (GameTranslator) — RPG Maker 대사 번역 / Unity 실시간 번역<br>"
              "<small>· Unity: Papago·Bing·Google은 키 없이 동작 (XUnity 내장)<br>"
              "· RPG Maker: Papago는 네이버 클라우드 키, DeepL은 API 키 필요<br>"
              "· PC의 AI 모델 + Unity: GameShelf가 켜져 있어야 번역됩니다</small>")
        cb_game = engine_box(st.get("game_engine", "google"))

        label("<br><b>DeepL API 키</b> — 무료 키(월 50만 자): "
              "<a href='https://www.deepl.com/pro-api'>https://www.deepl.com/pro-api</a>")
        ed = QLineEdit(st.get("deepl_key", ""))
        ed.setEchoMode(QLineEdit.Password)
        v.addWidget(ed)

        label("<br><b>Papago (네이버 클라우드)</b> — Client ID / Client Secret: "
              "<a href='https://www.ncloud.com/product/aiService/papagoTranslation'>"
              "https://www.ncloud.com/product/aiService/papagoTranslation</a><br>"
              "<small>사용한 글자 수만큼 요금이 나가는 유료 서비스입니다 (가입 시 무료 크레딧)</small>")
        hp = QHBoxLayout()
        ed_pid = QLineEdit(st.get("papago_id", ""))
        ed_pid.setPlaceholderText("Client ID")
        ed_psec = QLineEdit(st.get("papago_secret", ""))
        ed_psec.setPlaceholderText("Client Secret")
        ed_psec.setEchoMode(QLineEdit.Password)
        hp.addWidget(ed_pid)
        hp.addWidget(ed_psec)
        v.addLayout(hp)

        label("<br><b>PC의 AI 모델</b> — Ollama: <a href='https://ollama.com/download'>https://ollama.com/download</a> · "
              "LM Studio: <a href='https://lmstudio.ai/'>https://lmstudio.ai/</a><br>"
              "<small>Ollama는 http://localhost:11434 · LM Studio는 http://localhost:1234/v1</small>")
        hu = QHBoxLayout()
        ed_url = QLineEdit(st.get("local_url", "http://localhost:11434"))
        cb_model = QComboBox()
        cb_model.setEditable(True)
        cb_model.setMinimumWidth(200)
        if st.get("local_model"):
            cb_model.addItem(st["local_model"])
        b_models = QPushButton("모델 불러오기")
        b_test = QPushButton("번역 테스트")
        hu.addWidget(ed_url, 2)
        hu.addWidget(cb_model, 2)
        hu.addWidget(b_models)
        hu.addWidget(b_test)
        v.addLayout(hu)
        lb_test = label("")

        class TSig(QObject):
            done = Signal(str, object)
        tsig = TSig(dlg)

        def cur_cfg():
            return {"local_url": ed_url.text().strip(), "local_model": cb_model.currentText().strip()}

        def load_models():
            lb_test.setText("모델 목록 불러오는 중…")
            cfg = cur_cfg()

            def job():
                try:
                    tsig.done.emit("models", local_models(cfg["local_url"]))
                except Exception as ex:
                    tsig.done.emit("err", f"연결 실패: {type(ex).__name__} — 프로그램이 켜져 있는지, 주소가 맞는지 확인")
            self.pool.start(FuncTask(job))

        def test():
            cfg = cur_cfg()
            lb_test.setText("번역 테스트 중… (처음에는 모델을 불러오느라 수십 초 걸릴 수 있음)")

            def job():
                try:
                    t0 = time.time()
                    r = local_translate(cfg, ["勇者はただただえっちする。", "魔王を倒せ！"], "ja", "ko")
                    tsig.done.emit("test", f"✓ {time.time() - t0:.1f}초 — " + " / ".join(r))
                except Exception as ex:
                    tsig.done.emit("err", f"실패: {type(ex).__name__}: {str(ex)[:120]}")
            self.pool.start(FuncTask(job))

        def on_done(kind, val):
            if kind == "models":
                cur = cb_model.currentText()
                cb_model.clear()
                cb_model.addItems([m for m in val if m])
                if cur:
                    cb_model.setCurrentText(cur)
                lb_test.setText(f"모델 {len(val)}개" if val else "설치된 모델이 없습니다 (예: ollama pull qwen2.5:7b)")
            else:
                lb_test.setText(html.escape(str(val)))
        tsig.done.connect(on_done)
        b_models.clicked.connect(load_models)
        b_test.clicked.connect(test)

        label("<br><b>VirusTotal API 키</b> — 보안 검사에서 실행 파일 해시를 70여 개 백신 기록과 대조합니다.<br>"
                   "무료 키(하루 500건): <a href='https://www.virustotal.com/gui/join-us'>https://www.virustotal.com/gui/join-us</a>"
                   " → 로그인 후 오른쪽 위 프로필 → API key")
        ed_vt = QLineEdit(st.get("vt_key", ""))
        ed_vt.setEchoMode(QLineEdit.Password)
        ed_vt.setPlaceholderText("VirusTotal API 키")
        v.addWidget(ed_vt)
        from PySide6.QtWidgets import QCheckBox
        cb_tr = QCheckBox("압축을 풀면 한국어 번역기(GameTranslator)를 바로 이어서 설치 (번역기 툴이 20MB 이하일 때)")
        cb_tr.setChecked(st.get("auto_translate", True))
        cb_ch = QCheckBox("RPG 쯔꾸르 MV/MZ 게임을 실행할 때 치트(게임 안에서 ` 키)를 자동으로 넣기")
        cb_ch.setChecked(st.get("auto_cheat", True))
        v.addWidget(QLabel(""))
        v.addWidget(cb_tr)
        v.addWidget(cb_ch)
        v.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("저장")
        bb.button(QDialogButtonBox.Cancel).setText("취소")
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        outer.addWidget(bb)
        if dlg.exec() != QDialog.Accepted:
            return
        old_game = (st.get("game_engine", "google"), st.get("deepl_key", ""), st.get("bridge_port", 5577))
        st["title_engine"] = cb_title.currentData()
        st["game_engine"] = cb_game.currentData()
        st["deepl_key"] = ed.text().strip()
        st["papago_id"] = ed_pid.text().strip()
        st["papago_secret"] = ed_psec.text().strip()
        st["local_url"] = ed_url.text().strip() or "http://localhost:11434"
        st["local_model"] = cb_model.currentText().strip()
        st["vt_key"] = ed_vt.text().strip()
        st["auto_translate"] = cb_tr.isChecked()
        st["auto_cheat"] = cb_ch.isChecked()
        Translator.configure(st)
        write_gt_engine(st)
        self.update_bridge()
        self.schedule_save()
        if (st["game_engine"], st["deepl_key"], st.get("bridge_port", 5577)) != old_game:
            self.offer_engine_update()

    def offer_engine_update(self):
        """게임 번역 엔진을 바꿨을 때: 이미 번역기가 설치된 Unity 게임에도 적용할지"""
        st = self.lib.get("settings", {})
        dirs = []
        for e in self.lib["games"].values():
            d = self.game_dir(e)
            if d and os.path.isdir(d):
                dirs.append(d)
        sig = self.sig

        def job():
            found = []
            for d in dirs:
                for depth in range(3):           # 본체가 한두 단계 아래 폴더에 있는 경우까지
                    pat = os.path.join(d, *(["*"] * depth), "BepInEx", "GameTranslator_installed.txt")
                    hits = glob.glob(pat)
                    if hits:
                        found += [Path(h).parent / "config" / "AutoTranslatorConfig.ini" for h in hits]
                        break
            sig.engine_scan.emit(found)

        self._engine_st = dict(st)
        self.status_lbl.setText("번역기가 설치된 Unity 게임 찾는 중…")
        self.pool.start(FuncTask(job))

    def on_engine_scan(self, found):
        st = self._engine_st
        name = ENGINE_SHORT.get(st.get("game_engine"), "")
        msg = (f"게임 번역 엔진을 {name}(으)로 바꿨습니다.\n\n"
               "• 앞으로 설치하는 게임, RPG Maker 번역, 폴더 오른쪽 클릭 GameTranslator 모두 이 엔진을 씁니다.\n"
               "• RPG Maker 게임은 이미 번역된 문장은 그대로 두고, 새 문장만 이 엔진으로 번역합니다.")
        if not found:
            self.status_lbl.setText("게임 번역 엔진 변경 완료")
            QMessageBox.information(self, "게임 번역 엔진", msg)
            return
        if QMessageBox.question(self, "게임 번역 엔진", msg + f"\n\n이미 번역기가 설치된 Unity 게임 {len(found)}개에도 "
                                f"{name}을(를) 적용할까요?\n(XUnity 설정 파일의 번역 엔진만 바꿉니다)") != QMessageBox.Yes:
            self.status_lbl.setText("게임 번역 엔진 변경 완료 (기존 Unity 게임은 그대로)")
            return
        ok = 0
        for p in found:
            try:
                xunity_apply_engine(p, st)
                ok += 1
            except OSError:
                pass
        self.status_lbl.setText(f"Unity 게임 {ok}개에 {name} 적용 완료")

    # ---------- 무작위 플레이
    def random_candidates(self):
        return [k for k, e in self.lib["games"].items()
                if not e.get("hidden") and not e.get("missing") and sec_state(e) != "red"
                and ((e.get("archive") and os.path.isfile(e["path"]))
                     or (e.get("exe") and os.path.isfile(e["exe"])))]

    def random_play(self):
        self.launch_dialog(None)

    def launch_dialog(self, fixed_key):
        """실행 전 검사 창. fixed_key가 없으면 무작위로 고름.
        안 푼 압축 게임은 '압축 풀기 → 푼 폴더 검사 → 실행' 순서."""
        if self.rand_dlg:
            self.rand_dlg.raise_()
            return
        if not fixed_key and not self.random_candidates():
            QMessageBox.information(self, "무작위 플레이", "실행할 수 있는 게임이 없습니다.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("🎲 무작위 플레이" if not fixed_key else "실행 전 검사")
        dlg.resize(480, 540)
        v = QVBoxLayout(dlg)
        dlg.cover = QLabel()
        dlg.cover.setAlignment(Qt.AlignCenter)
        dlg.cover.setMinimumHeight(300)
        dlg.cover.setStyleSheet("background:#1b1c22;border-radius:8px;")
        dlg.title = QLabel()
        dlg.title.setWordWrap(True)
        f = QFont()
        f.setPointSize(13)
        f.setBold(True)
        dlg.title.setFont(f)
        dlg.info = QLabel()
        dlg.info.setWordWrap(True)
        dlg.info.setTextFormat(Qt.RichText)
        dlg.info.setOpenExternalLinks(True)
        h = QHBoxLayout()
        dlg.b_again = QPushButton("🎲 다른 게임")
        dlg.b_go = QPushButton("🛡 검사 후 실행")
        dlg.b_go.setDefault(True)
        b_cancel = QPushButton("취소")
        if fixed_key:
            dlg.b_again.hide()
        for b in (dlg.b_again, dlg.b_go, b_cancel):
            h.addWidget(b)
        v.addWidget(dlg.cover)
        v.addWidget(dlg.title)
        v.addWidget(dlg.info, 1)
        v.addLayout(h)
        dlg.key, dlg.state, dlg.recent = fixed_key, "pick", []
        dlg.pending_tr = None
        dlg.stop = threading.Event()
        dlg.checking_text = ("🛡 Windows Defender로 게임 폴더 검사 중…<br>"
                             "🛡 VirusTotal에서 실행 파일 조회 중… (다른 VirusTotal 작업이 돌고 있으면 최대 15초 대기)")

        def needs_extract():
            e = self.lib["games"][dlg.key]
            return bool(e.get("archive")) and not self.archive_ready(e)

        def reset_buttons():
            dlg.b_go.setText("📦 압축 풀고 검사 후 실행" if needs_extract() else "🛡 검사 후 실행")
            dlg.b_go.setEnabled(True)
            dlg.b_again.setEnabled(True)

        def pick():
            dlg.pending_tr = None
            if not fixed_key:
                pool = self.random_candidates()
                fresh = [k for k in pool if k not in dlg.recent] or pool
                # 안 해 본 게임이 조금 더 잘 뽑히게 (가중치 3:1)
                weights = [3 if not self.lib["games"][k].get("plays") else 1 for k in fresh]
                dlg.key = random.choices(fresh, weights)[0]
                dlg.recent = (dlg.recent + [dlg.key])[-20:]
            e = self.lib["games"][dlg.key]
            cp = self.cover_path(e)
            pm = QPixmap(str(cp)) if cp else QPixmap()
            dlg.cover.setPixmap(pm.scaled(440, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                                if not pm.isNull() else self.placeholder_pixmap("noimg", 400, 300))
            dlg.title.setText(display_name(e))
            extra = [f"플레이 {e['plays']}회 · 마지막 {fmt_time(e.get('last_played'))}"
                     if e.get("plays") else "아직 안 해 본 게임"]
            if e.get("maker"):
                extra.append(f"제작: {html.escape(e['maker'])}")
            if needs_extract():
                extra.append("📦 압축파일 — 실행하면 먼저 압축을 풀고, 푼 폴더를 다시 검사합니다")
            dlg.info.setText("<br>".join(extra))
            dlg.state = "pick"
            reset_buttons()
            if dlg.key in self.items:
                self.listw.setCurrentItem(self.items[dlg.key])

        def go():
            if dlg.state == "pick":
                dlg.b_go.setEnabled(False)
                dlg.b_again.setEnabled(False)
                if needs_extract():
                    dlg.state = "extracting"
                    dlg.info.setText("📦 압축 푸는 중… (큰 게임은 몇 분 걸릴 수 있습니다)")
                    if not self.start_extract(dlg.key):
                        dlg.state = "pick"
                        pick()
                else:
                    dlg.info.setText(dlg.checking_text)
                    self.begin_check(dlg)
            elif dlg.state in ("ok", "warn") and getattr(dlg, "pending_tr", None):
                dest, dlg.pending_tr = dlg.pending_tr, None
                proc = self.auto_translation(dlg.key, dest)
                if proc:
                    dlg.state = "translating"
                    dlg.b_go.setEnabled(False)
                    dlg.b_again.setEnabled(False)
                    dlg.info.setText(dlg.info.text() + "<br><br>🈂 한국어 번역기 설치 중… 끝나면 바로 실행합니다"
                                     "<br><small>(RPG Maker 게임은 대사 번역에 몇 분 걸릴 수 있습니다)</small>")
                    t0 = time.time()

                    def wait_tr():
                        if self.rand_dlg is not dlg:
                            return
                        if proc.poll() is None and time.time() - t0 < 3600:
                            QTimer.singleShot(1000, wait_tr)
                            return
                        dlg.state = "ok"
                        go()
                    QTimer.singleShot(1000, wait_tr)
                    return
                go()
            elif dlg.state in ("ok", "warn"):
                e = self.lib["games"][dlg.key]
                exe = e.get("exe") if e.get("exe") and os.path.isfile(e["exe"]) else ""
                if exe:
                    self.launch(dlg.key, exe)
                else:
                    reveal(e.get("extracted") or e["path"])
                dlg.accept()

        dlg.pick, dlg.reset_buttons = pick, reset_buttons
        dlg.b_again.clicked.connect(pick)
        dlg.b_go.clicked.connect(go)
        b_cancel.clicked.connect(dlg.reject)
        dlg.finished.connect(lambda *_: (dlg.stop.set(), setattr(self, "rand_dlg", None)))
        self.rand_dlg = dlg
        pick()
        dlg.show()

    def begin_check(self, dlg):
        dlg.state = "checking"
        dlg.b_go.setEnabled(False)
        dlg.b_again.setEnabled(False)
        self.start_precheck(dlg.key, dlg.stop)

    def start_precheck(self, key, stop):
        e = self.lib["games"][key]
        path, exe = e.get("extracted") or e["path"], e.get("exe") or ""
        api_key = self.lib.get("settings", {}).get("vt_key", "")
        cache = dict(self.lib.get("vt_cache", {}))
        sig = self.sig

        def job():
            res = {}
            mp = find_mpcmdrun() if sys.platform.startswith("win") else ""
            res["def"] = defender_scan(mp, path) if mp else {"def": "error", "def_msg": "Defender를 찾지 못함"}
            res["def"]["def_mode"] = "folder"
            if stop.is_set():
                return
            if not api_key:
                res["vt"] = {"state": "nokey"}
            elif not exe or not os.path.isfile(exe):
                res["vt"] = {"state": "nofile"}
            else:
                try:
                    sha = sha256_file(exe)
                    if sha in cache and cache[sha].get("state") == "ok":
                        res["vt"] = dict(cache[sha], sha=sha, mode="folder")
                    elif VTRate.wait(stop):
                        st, data = vt_lookup(sha, api_key)
                        res["vt"] = dict(data, sha=sha, mode="folder") if st == "ok" else \
                            {"state": "error", "msg": {"quota": "오늘 한도 초과", "badkey": "API 키 오류"}.get(st, st)}
                except OSError as ex:
                    res["vt"] = {"state": "error", "msg": str(ex)}
            if not stop.is_set():
                sig.precheck.emit(key, res)

        self.pool.start(FuncTask(job))

    def on_precheck(self, key, res):
        # 결과는 게임 정보에도 저장
        if "def" in res:
            self.on_sec_result(key, res["def"])
        if res.get("vt", {}).get("state") in ("ok", "unknown"):
            self.on_sec_result(key, {"vt": res["vt"]})
        dlg = self.rand_dlg
        if not dlg or dlg.key != key:
            return
        d, vt = res.get("def", {}), res.get("vt", {})
        lines, level = [], "ok"
        if d.get("def") == "clean":
            lines.append("<span style='color:#5c5'>✓ Defender: 이상 없음</span>")
        elif d.get("def") == "threat":
            level = "danger"
            lines.append(f"<span style='color:#f66'>✕ Defender: 위협 발견 — "
                         f"{html.escape(', '.join(d.get('threats') or []))}</span>")
        else:
            level = "warn"
            lines.append(f"<span style='color:#eb5'>? Defender 검사 실패: {html.escape(d.get('def_msg', ''))}</span>")
        url = f"https://www.virustotal.com/gui/file/{vt.get('sha', '')}"
        if vt.get("state") == "ok":
            mal, sus = vt.get("mal", 0), vt.get("sus", 0)
            if mal >= 3:
                level = "danger"
                col = "#f66"
            elif mal or sus:
                level = "warn" if level == "ok" else level
                col = "#eb5"
            else:
                col = "#5c5"
            lines.append(f"<a href='{url}' style='color:{col}'>VirusTotal: {mal} / {vt.get('total', 0)} 엔진 탐지"
                         f"{f', 의심 {sus}' if sus else ''}</a>")
        elif vt.get("state") == "unknown":
            level = "warn" if level == "ok" else level
            lines.append("<span style='color:#eb5'>VirusTotal: 기록 없음 (처음 보는 파일 — Defender 결과로 판단)</span>")
        elif vt.get("state") == "nokey":
            lines.append("<span style='color:#aaa'>VirusTotal: ⚙설정에 키가 없어 생략</span>")
        elif vt.get("state") == "nofile":
            lines.append("<span style='color:#aaa'>VirusTotal: 실행 파일을 찾지 못해 생략 (확인하면 폴더를 엽니다)</span>")
        else:
            level = "warn" if level == "ok" else level
            lines.append(f"<span style='color:#eb5'>VirusTotal 조회 실패: {html.escape(vt.get('msg', ''))}</span>")
        if level == "ok":
            lines.append("<br><b>안전 확인 — 곧 실행합니다…</b>")
            dlg.info.setText("<br>".join(lines))
            dlg.state = "ok"
            QTimer.singleShot(1200, lambda: self.rand_dlg is dlg and dlg.state == "ok" and dlg.b_go.click())
            dlg.b_go.setText("▶ 실행")
            dlg.b_go.setEnabled(True)
        else:
            lines.append("<br><b style='color:#f66'>⚠ 위험 판정 — 실행하지 않는 것을 권장합니다.</b>"
                         if level == "danger" else "<br><b>확인이 필요한 결과가 있습니다. 실행할까요?</b>")
            dlg.info.setText("<br>".join(lines))
            dlg.state = "warn"
            dlg.b_go.setText("그래도 실행" if level == "danger" else "▶ 실행")
            dlg.b_go.setDefault(level != "danger")
            dlg.b_go.setEnabled(True)
            dlg.b_again.setEnabled(True)

    # ---------- 플레이 기록 창
    def show_history(self):
        games = self.lib["games"]
        played = sorted([(k, e) for k, e in games.items() if e.get("plays")],
                        key=lambda x: x[1].get("last_played", 0), reverse=True)
        hist = list(reversed(self.lib.get("history", [])))
        dlg = QDialog(self)
        dlg.setWindowTitle("🕹 플레이 기록")
        dlg.resize(860, 560)
        v = QVBoxLayout(dlg)
        total = sum(e.get("play_secs", 0) for _, e in played)
        v.addWidget(QLabel(f"플레이한 게임 {len(played)}개 · 총 실행 {sum(e['plays'] for _, e in played)}회 · "
                           f"총 플레이 시간 {fmt_secs(total)}  (더블클릭하면 목록에서 그 게임으로 이동)"))
        tabs = QTabWidget()

        def table(headers, rows, keys):
            t = QTableWidget(len(rows), len(headers))
            t.setHorizontalHeaderLabels(headers)
            t.verticalHeader().setVisible(False)
            t.setEditTriggers(QAbstractItemView.NoEditTriggers)
            t.setSelectionBehavior(QAbstractItemView.SelectRows)
            t.setSortingEnabled(False)
            for r, row in enumerate(rows):
                for c, val in enumerate(row):
                    it = QTableWidgetItem(str(val))
                    it.setData(Qt.UserRole, keys[r])
                    t.setItem(r, c, it)
            t.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
            for c in range(1, len(headers)):
                t.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeToContents)
            t.cellDoubleClicked.connect(lambda r, c: jump(t.item(r, 0).data(Qt.UserRole)))
            return t

        def jump(k):
            if k not in games:
                return
            e = games[k]
            self.search.clear()
            # 전체에 안 보이는 게임이면 보이는 필터로 (숨김 / 클리어 / 폴더 없어짐)
            fi = 3 if e.get("hidden") else 0 if not e.get("missing") or e.get("rating") else 4
            self.filter_cb.setCurrentIndex(fi)
            self.refresh_list()
            if k in self.items:
                self.listw.setCurrentItem(self.items[k])
                self.listw.scrollToItem(self.items[k])
            dlg.accept()

        tabs.addTab(table(["게임", "마지막 플레이", "횟수", "총 플레이 시간"],
                          [(display_name(e), fmt_time(e.get("last_played")), e["plays"],
                            fmt_secs(e.get("play_secs"))) for _, e in played],
                          [k for k, _ in played]), "게임별")
        tabs.addTab(table(["게임", "시작", "플레이 시간"],
                          [(games[h["key"]] and display_name(games[h["key"]]) if h["key"] in games else h["title"],
                            fmt_time(h["start"]),
                            fmt_secs(h["secs"]) if h["secs"] else "측정 안 됨" if h["secs"] is None and
                            not any(r[3] is h for r in self.running) else "플레이 중…")
                           for h in hist],
                          [h["key"] for h in hist]), "최근 기록")
        v.addWidget(tabs, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.button(QDialogButtonBox.Close).setText("닫기")
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        dlg.exec()

    def reconcile_files(self):
        """디스크의 실제 파일과 목록을 맞춤:
        · '이름_클리어본.7z' 이 있으면 클리어본으로 연결
        · 압축 게임의 푼 폴더가 따로 '폴더 게임'으로 잡혔으면 하나로 합침
        · 휴지통에서 되살린 파일이 있으면 '삭제함' 표시를 풂
        · 예전 버전(압축파일 다시 만들기)이 원본 자리에 클리어본을 만들었던 경우 이름을 바로잡을지 물어봄"""
        games = self.lib["games"]
        changed, legacy = False, []
        for k, e in list(games.items()):
            if k not in games or not e.get("archive"):
                continue
            arc = e.get("path", "")
            d = os.path.dirname(arc)
            stem, ext = os.path.splitext(os.path.basename(arc))
            if e.get("repacked_at") and os.path.isfile(arc):
                olds = sorted(glob.glob(os.path.join(glob.escape(d), glob.escape(stem) + " (이전 압축파일 *)" + ext)))
                if olds and not os.path.exists(os.path.join(d, stem + CLEAR_TAG + ext)):
                    legacy.append((k, arc, olds[-1], os.path.join(d, stem + CLEAR_TAG + ext)))
            for cext in (".7z", ".zip"):
                c = os.path.join(d, stem + CLEAR_TAG + cext)
                if os.path.isfile(c) and e.get("clear_archive") != c:
                    e["clear_archive"] = c
                    e.setdefault("clear_at", os.path.getmtime(c))
                    changed = True
            if not (e.get("extracted") and os.path.isdir(e["extracted"])):
                f = os.path.join(d, stem.rstrip(". "))
                fk = key_of(f)
                fe = games.get(fk)
                if os.path.isdir(f) and fe is not None and fe is not e and not fe.get("archive"):
                    games.pop(fk)
                    e["plays"] = (e.get("plays") or 0) + (fe.get("plays") or 0)
                    e["play_secs"] = (e.get("play_secs") or 0) + (fe.get("play_secs") or 0)
                    e["last_played"] = max(e.get("last_played") or 0, fe.get("last_played") or 0)
                    for x in ("plays", "play_secs", "last_played"):
                        if not e.get(x):
                            e.pop(x, None)
                    e["extracted"] = f
                    e["exe"] = fe.get("exe") if fe.get("exe") and os.path.isfile(fe["exe"]) else find_exe(f)
                    if self.cur_key == fk:
                        self.cur_key = k
                    changed = True
            if (e.get("removed") or e.get("missing")) and self.parts_state(e):
                e["removed"] = False
                e["missing"] = False
                changed = True
        for k, arc, old, clear in legacy:
            e = games.get(k)
            try:
                a1, a2 = gs_shrink.human(os.path.getsize(arc)), gs_shrink.human(os.path.getsize(old))
            except OSError:
                continue
            if QMessageBox.question(
                    self, "압축파일 이름 바로잡기",
                    f"{display_name(e)}\n\n예전 버전의 '압축파일 다시 만들기'로 이름이 뒤바뀐 파일이 있습니다.\n\n"
                    f"· {os.path.basename(arc)} ({a1}) = 용량 줄인 게임 + 세이브 → 클리어본\n"
                    f"· {os.path.basename(old)} ({a2}) = 처음 받은 원본\n\n"
                    f"이렇게 이름을 바꿀까요? (지우는 파일 없이 이름만 바꿉니다)\n"
                    f"· {os.path.basename(arc)} → {os.path.basename(clear)}\n"
                    f"· {os.path.basename(old)} → {os.path.basename(arc)}") != QMessageBox.Yes:
                continue
            try:
                os.rename(arc, clear)
                os.rename(old, arc)
            except OSError as err:
                QMessageBox.warning(self, "압축파일 이름 바로잡기", f"이름을 바꾸지 못했습니다:\n{err}")
                continue
            e["clear_archive"] = clear
            e["clear_at"] = e.pop("repacked_at", None) or time.time()
            e["removed"] = False
            e["missing"] = False
            changed = True
        for e in games.values():
            if e.get("repacked_at") and not e.get("archive"):
                e.pop("repacked_at", None)
        if changed:
            self.schedule_save()
            self.refresh_list()
            if self.cur_key in games:
                self.show_detail(self.cur_key)
            self.update_status()

    def parts_state(self, e):
        """지울 수 있는 것: game(플레이 폴더) / install(처음 받은 압축파일) / clear(클리어본)"""
        st = {}
        pf = self.play_folder(e)
        if pf:
            st["game"] = pf
        if e.get("archive") and os.path.isfile(e.get("path", "")):
            st["install"] = e["path"]
        ca = e.get("clear_archive", "")
        if ca and os.path.isfile(ca):
            st["clear"] = ca
        return st

    def delete_games(self, keys):
        """예전 이름 호환: 게임 삭제 = 플레이하던 폴더"""
        self.trash_parts(keys, {"game"})

    def forget_game(self, key):
        e = self.lib["games"].get(key)
        if not e:
            return
        if QMessageBox.question(self, "목록에서 지우기",
                                f"{display_name(e)}\n\n목록에서 기록·평점까지 완전히 지울까요? (파일은 건드리지 않습니다)") \
                != QMessageBox.Yes:
            return
        c = e.get("cover")
        if c and not any(x is not e and x.get("cover") == c for x in self.lib["games"].values()):
            (COVER_DIR / c).unlink(missing_ok=True)
        self.lib["games"].pop(key, None)
        if self.cur_key == key:
            self.cur_key = None
        self.schedule_save()
        self.refresh_list()

    PART_NAME = {"game": "게임 삭제", "install": "설치 삭제", "clear": "클리어 삭제"}

    def trash_parts(self, keys, parts, confirmed=False):
        """게임 삭제(플레이 폴더) · 설치 삭제(처음 받은 압축파일) · 클리어 삭제(클리어본) — 전부 휴지통으로.
        목록에는 기록·표지·평점이 남음."""
        games = self.lib["games"]
        keys = [k for k in keys if k in games]
        if not keys:
            return
        if self.deleting:
            QMessageBox.information(self, "삭제", "이전 삭제 작업이 아직 진행 중입니다.")
            return
        roots = {key_of(r) for r in self.lib["roots"]}
        jobs, lines = [], []
        for k in keys:
            e = games[k]
            st = self.parts_state(e)
            todo = [(p, st[p]) for p in ("game", "install", "clear") if p in parts and p in st]
            todo = [(p, path) for p, path in todo if key_of(path) not in roots]
            if todo:
                jobs.append((k, todo))
                lines.append(f"• {display_name(e)}\n" + "\n".join(f"   {self.PART_NAME[p]}: {path}" for p, path in todo))
        if not jobs:
            QMessageBox.information(self, "삭제", "지울 파일이 없습니다.")
            return
        title = " · ".join(self.PART_NAME[p] for p in ("game", "install", "clear") if p in parts)
        if not confirmed:
            names = "\n".join(lines[:8]) + (f"\n… 외 {len(lines) - 8}개" if len(lines) > 8 else "")
            box = QMessageBox(QMessageBox.Warning, title,
                              f"{len(jobs)}개 게임에서 아래 파일을 휴지통으로 보냅니다. 휴지통에서 되살릴 수 있습니다.\n"
                              "목록에는 기록·표지·평점이 남습니다.\n\n" + names, parent=self)
            ok_btn = box.addButton("휴지통으로", QMessageBox.DestructiveRole)
            box.addButton("취소", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is not ok_btn:
                return
            if len(jobs) == 1:
                self.ask_rating(jobs[0][0], f"{title}를 합니다. 다 한 게임이라면 평점을 남겨 보세요.")
            # 게임 삭제인데 클리어본이 있으면: 그 뒤에 더 한 세이브를 클리어본에 먼저 넣음
            if len(jobs) == 1 and [p for p, _ in jobs[0][1]] == ["game"]:
                e = games[jobs[0][0]]
                if e.get("clear_archive") and os.path.isfile(e["clear_archive"]) and \
                        shrink_mark(e) == "done" and find_save_dirs(jobs[0][1][0][1]):
                    if QMessageBox.question(self, "게임 삭제",
                                            "클리어본이 있습니다. 지우기 전에 지금 세이브로 클리어본을 새로 만들까요?\n"
                                            "(그 뒤에 플레이한 내용까지 클리어본에 남습니다)") == QMessageBox.Yes:
                        self.start_repack(jobs[0][0], then="trash_game")
                        return
        for k, todo in jobs:
            if any(p == "game" for p, _ in todo):
                try:
                    backup_saves(games[k], dict(todo)["game"])
                except OSError as err:
                    QMessageBox.warning(self, title, f"{display_name(games[k])}: 세이브를 보관하지 못해서 멈췄습니다:\n{err}")
                    return
        self.deleting = True
        self._del_mode = getattr(self, "_del_mode", {})
        for k, todo in jobs:
            self._del_mode[k] = [p for p, _ in todo]
        self.status_lbl.setText(f"{title} 중… 0 / {len(jobs)}")
        sig = self.sig

        def job():
            for k, todo in jobs:
                errs = []
                for p, path in todo:
                    try:
                        if os.path.exists(path):
                            send_to_recycle_bin(path)
                    except Exception as ex:
                        errs.append(f"{self.PART_NAME[p]}: {ex}")
                sig.deleted.emit(k, "; ".join(errs))
            sig.deleted.emit("", "END")

        self.pool.start(FuncTask(job))

    def on_deleted(self, key, err):
        if not key:
            self.deleting = False
            fails = getattr(self, "_del_fails", [])
            self._del_fails = []
            self.refresh_list()
            self.status_lbl.setText("휴지통으로 보냄" + (f" (실패 {len(fails)}개)" if fails else ""))
            if fails:
                QMessageBox.warning(self, "일부 삭제 실패", "\n".join(fails[:15]))
            return
        e = self.lib["games"].get(key)
        self._del_mode.pop(key, None)
        if err:
            self._del_fails = getattr(self, "_del_fails", []) + [f"{e and display_name(e)}: {err}"]
        if not e:
            return
        if e.get("archive") and e.get("extracted") and not os.path.isdir(e["extracted"]):
            e.pop("extracted", None)
        if e.get("clear_archive") and not os.path.isfile(e["clear_archive"]):
            e.pop("clear_archive", None)
            e.pop("clear_at", None)
        if not self.play_folder(e):
            e["exe"] = ""
        left = self.parts_state(e)
        if not left:                        # 파일이 하나도 안 남음 → 기록만 (평점 있으면 '🏆 클리어한 게임')
            e["removed"] = True
            e["removed_at"] = time.time()
            e["missing"] = True
        self.schedule_save()
        self.update_item(key)

    def sec_run(self, mode, keys=None, force=False):
        games = self.lib["games"]
        base = keys or [k for k, e in games.items() if not e.get("hidden") and not e.get("missing")]
        msgs = []
        if mode in ("def", "both"):
            ks = [k for k in base if force or (games[k].get("scan") or {}).get("def") not in ("clean", "threat")]
            msgs.append(self.start_defender(ks))
        if mode in ("vt", "both"):
            key = self.lib.get("settings", {}).get("vt_key", "")
            if not key:
                msgs.append("VirusTotal: ⚙설정에 API 키를 넣어야 합니다.")
            else:
                ks = [k for k in base if force or ((games[k].get("scan") or {}).get("vt") or {}).get("state")
                      not in ("ok", "unknown", "nofile")]
                msgs.append(self.start_vt(ks, key, force))
        msgs = [m for m in msgs if m]
        if msgs:
            QMessageBox.information(self, "보안 검사", "\n\n".join(msgs))

    def start_defender(self, keys):
        if not sys.platform.startswith("win"):
            return "Defender 검사는 Windows에서만 됩니다."
        if self.sec_prog.get("def"):
            return "Defender 검사가 이미 진행 중입니다."
        if not keys:
            return "Defender: 새로 검사할 게임이 없습니다."
        mp = find_mpcmdrun()
        if not mp:
            return "Windows Defender(MpCmdRun.exe)를 찾지 못했습니다. 다른 백신을 쓰고 있어 Defender가 꺼져 있을 수 있습니다."
        items = []
        for k in keys:
            e = self.lib["games"][k]
            if e.get("archive") and self.archive_ready(e):
                items.append((k, e["extracted"], "folder"))
            else:
                items.append((k, e["path"], "archive" if e.get("archive") else "folder"))
        stop = self.sec_stop["def"] = threading.Event()
        sig = self.sig
        self.sec_prog["def"] = f"0/{len(items)}"

        def job():
            found = 0
            for i, (k, path, mode) in enumerate(items):
                if stop.is_set():
                    break
                res = defender_scan(mp, path) if os.path.exists(path) else \
                    {"def": "error", "def_msg": "경로 없음"}
                res["def_mode"] = mode
                found += res.get("def") == "threat"
                sig.sec_result.emit(k, res)
                sig.sec_progress.emit("def", f"{i + 1}/{len(items)}")
            sig.sec_progress.emit("def", f"END|{found}")

        self.pool.start(FuncTask(job))
        self.update_status()
        return f"Defender: {len(items)}개 검사 시작 (파일을 지우거나 격리하지 않고 결과만 기록합니다)"

    def start_vt(self, keys, api_key, force=False):
        if self.sec_prog.get("vt"):
            return "VirusTotal 조회가 이미 진행 중입니다."
        if not keys:
            return "VirusTotal: 새로 조회할 게임이 없습니다."
        games = self.lib["games"]
        items = []
        for k in keys:
            e = games[k]
            if e.get("archive") and not self.archive_ready(e):
                items.append((k, e["path"], "archive"))
            else:
                items.append((k, e.get("exe", ""), "folder"))
        cache = {} if force else dict(self.lib.get("vt_cache", {}))
        stop = self.sec_stop["vt"] = threading.Event()
        sig = self.sig
        self.sec_prog["vt"] = f"0/{len(items)}"

        def job():
            end = ""
            st = ""
            for i, (k, path, mode) in enumerate(items):
                if stop.is_set():
                    break
                if not path or not os.path.isfile(path):
                    sig.sec_result.emit(k, {"vt": {"state": "nofile"}})
                    continue
                member = ""
                try:
                    if mode == "archive":
                        sha, member = archive_main_exe_hash(path)
                        if not sha:
                            sig.sec_result.emit(k, {"vt": {"state": "nofile", "msg": member, "mode": mode}})
                            continue
                    else:
                        sha = sha256_file(path)
                except Exception as ex:
                    sig.sec_result.emit(k, {"vt": {"state": "error", "msg": f"{type(ex).__name__}: {ex}"}})
                    continue
                extra = {"sha": sha, "mode": mode, "member": member}
                if sha in cache:
                    sig.sec_result.emit(k, {"vt": dict(cache[sha], **extra)})
                    sig.sec_progress.emit("vt", f"{i + 1}/{len(items)}")
                    continue
                for attempt in range(2):
                    if not VTRate.wait(stop):                  # 무료 키: 분당 4건 (업로드와 공유)
                        break
                    st, data = vt_lookup(sha, api_key)
                    if st == "quota" and attempt == 0:
                        if stop.wait(65):                      # 분당 한도일 수 있으니 1분 쉬고 한 번 더
                            break
                        continue
                    break
                if stop.is_set():
                    break
                if st == "quota":
                    end = "quota"
                    break
                if st == "badkey":
                    end = "badkey"
                    break
                sig.sec_result.emit(k, {"vt": dict(data, **extra)})
                sig.sec_progress.emit("vt", f"{i + 1}/{len(items)}")
            sig.sec_progress.emit("vt", "END|" + (end or ("stopped" if stop.is_set() else "")))

        self.pool.start(FuncTask(job))
        self.update_status()
        hours = len(items) * 15.5 / 3600
        return (f"VirusTotal: {len(items)}개 조회 시작 — 무료 한도 때문에 1개당 약 15초"
                f"{f', 전부 약 {hours:.1f}시간' if hours >= 0.2 else ''}. 하루 500건을 넘으면 멈추고, "
                f"다음 날 다시 누르면 이어서 합니다.")

    def upload_run(self, keys):
        api_key = self.lib.get("settings", {}).get("vt_key", "")
        if not api_key:
            QMessageBox.information(self, "VirusTotal 업로드", "⚙설정에 VirusTotal API 키를 먼저 넣어 주세요.")
            return
        if self.sec_prog.get("vtu"):
            QMessageBox.information(self, "VirusTotal 업로드", "업로드 검사가 이미 진행 중입니다.")
            return
        games = self.lib["games"]
        if keys is None:     # 해시 조회 결과 '기록 없음'이고 아직 올린 적 없는 게임
            keys = [k for k, e in games.items() if not e.get("hidden") and not e.get("missing")
                    and ((e.get("scan") or {}).get("vt") or {}).get("state") == "unknown"
                    and not (e.get("scan") or {}).get("vtu")]
        if not keys:
            QMessageBox.information(self, "VirusTotal 업로드",
                                    "올릴 게임이 없습니다.\n(먼저 ‘VirusTotal만’으로 해시 조회를 해서 "
                                    "‘기록 없음’이 나온 게임이 대상입니다)")
            return
        if QMessageBox.question(
                self, "VirusTotal 업로드",
                f"게임 {len(keys)}개의 실행 파일(.exe/.dll 등)을 VirusTotal에 올려 검사합니다.\n\n"
                "• 그림·음성 등 데이터 파일은 올리지 않습니다.\n"
                "• 이미 기록이 있는 파일은 해시로 확인하고 건너뜁니다.\n"
                "• 650MB가 넘는 파일은 올릴 수 없어 Defender 결과로 판단합니다.\n"
                "• 올린 파일은 VirusTotal과 보안 회사들이 볼 수 있습니다 (게임 실행 파일이면 괜찮지만 "
                "개인 파일이 섞여 있지 않은지 확인하세요).\n"
                "• 파일 1개당 약 15~30초, 결과는 올린 뒤 몇 분 지나서 나옵니다.\n\n진행할까요?") != QMessageBox.Yes:
            return
        items = [(k, games[k]["path"], bool(games[k].get("archive"))) for k in keys]
        cache = dict(self.lib.get("vt_cache", {}))
        stop = self.sec_stop["vtu"] = threading.Event()
        sig = self.sig
        self.sec_prog["vtu"] = f"0/{len(items)}"
        self.update_status()

        def job():
            end = ""
            for gi, (k, path, is_arc) in enumerate(items):
                if stop.is_set() or end:
                    break
                files, note = [], ""
                with tempfile.TemporaryDirectory(prefix="gameshelf_") as tmp:
                    try:
                        members, note = exec_members(path, is_arc, tmp)
                    except Exception as ex:
                        members, note = [], f"파일 목록 읽기 실패: {type(ex).__name__}"
                    for name, size, read in members:
                        if stop.is_set():
                            break
                        if size > VT_MAX:
                            files.append({"name": name, "state": "toolarge"})
                            continue
                        try:
                            data = read()
                        except Exception as ex:
                            files.append({"name": name, "state": "error", "msg": type(ex).__name__})
                            continue
                        sha = hashlib.sha256(data).hexdigest()
                        info = cache.get(sha)
                        if not info or info.get("state") != "ok":
                            if not VTRate.wait(stop):
                                break
                            st, info = vt_lookup(sha, api_key)
                            if st in ("quota", "badkey"):
                                end = st
                                break
                        if info and info.get("state") == "ok" and info.get("total"):
                            files.append(dict(info, name=name, sha=sha))
                            continue
                        up = vt_upload(name, data, api_key, stop)
                        if up in ("quota", "badkey"):
                            end = up
                            break
                        if up == "stopped":
                            break
                        files.append({"name": name, "sha": sha,
                                      "state": "pending" if up == "ok" else
                                      "toolarge" if up == "toolarge" else "error",
                                      "msg": "" if up == "ok" else up, "uploaded": time.time()})
                if files or note:
                    sig.sec_result.emit(k, {"vtu": {"files": files, "note": note,
                                                    "date": time.strftime("%Y-%m-%d")}})
                sig.sec_progress.emit("vtu", f"{gi + 1}/{len(items)}")
            sig.sec_progress.emit("vtu", "END|" + (end or ("stopped" if stop.is_set() else "done")))

        self.pool.start(FuncTask(job))

    def upload_recheck(self):
        """분석 대기(pending) 파일들의 결과를 해시로 다시 조회"""
        api_key = self.lib.get("settings", {}).get("vt_key", "")
        if not api_key or self.sec_prog.get("vtu"):
            QMessageBox.information(self, "업로드 결과 확인",
                                    "API 키가 없거나 업로드 작업이 진행 중입니다.")
            return
        games = self.lib["games"]
        targets = {k: json.loads(json.dumps(e["scan"]["vtu"])) for k, e in games.items()
                   if any(f.get("state") == "pending" for f in ((e.get("scan") or {}).get("vtu") or {}).get("files", []))}
        if not targets:
            QMessageBox.information(self, "업로드 결과 확인", "분석 대기 중인 파일이 없습니다.")
            return
        stop = self.sec_stop["vtu"] = threading.Event()
        sig = self.sig
        self.sec_prog["vtu"] = f"결과 확인 0/{len(targets)}"
        self.update_status()

        def job():
            end = ""
            for gi, (k, vtu) in enumerate(targets.items()):
                for f in vtu["files"]:
                    if f.get("state") != "pending" or stop.is_set() or end:
                        continue
                    if not VTRate.wait(stop):
                        break
                    st, info = vt_lookup(f["sha"], api_key)
                    if st in ("quota", "badkey"):
                        end = st
                        break
                    if info.get("state") == "ok" and info.get("total"):
                        name, sha = f["name"], f["sha"]
                        f.clear()
                        f.update(info, name=name, sha=sha)
                if not end:
                    sig.sec_result.emit(k, {"vtu": vtu})
                sig.sec_progress.emit("vtu", f"결과 확인 {gi + 1}/{len(targets)}")
            sig.sec_progress.emit("vtu", "END|" + (end or "rechecked"))

        self.pool.start(FuncTask(job))

    def on_sec_result(self, key, res):
        e = self.lib["games"].get(key)
        if not e:
            return
        sc = e.setdefault("scan", {})
        if "vtu" in res:
            sc["vtu"] = res["vtu"]
            for f in res["vtu"].get("files", []):
                if f.get("state") == "ok" and f.get("sha"):
                    self.lib.setdefault("vt_cache", {})[f["sha"]] = \
                        {k: v for k, v in f.items() if k not in ("sha", "name", "uploaded")}
        elif "vt" in res:
            sc["vt"] = res["vt"]
            if res["vt"].get("state") in ("ok", "unknown") and res["vt"].get("sha"):
                self.lib.setdefault("vt_cache", {})[res["vt"]["sha"]] = \
                    {k: v for k, v in res["vt"].items() if k not in ("sha", "mode", "member")}
        else:
            for k in ("threats", "threat_files", "def_msg"):
                sc.pop(k, None)
            sc.update(res)
        self.update_item(key)
        self.schedule_save()

    def on_sec_progress(self, kind, text):
        if not text.startswith("END|"):
            self.sec_prog[kind] = text
            self.update_status()
            return
        self.sec_prog.pop(kind, None)
        self.update_status()
        info = text[4:]
        if kind == "vtu":
            if info in ("quota", "badkey"):
                QMessageBox.information(self, "VirusTotal 업로드",
                                        "오늘 무료 한도(500건)에 도달했습니다. 내일 이어서 해 주세요."
                                        if info == "quota" else "API 키가 올바르지 않습니다.")
            pend = sum(1 for e in self.lib["games"].values()
                       for f in ((e.get("scan") or {}).get("vtu") or {}).get("files", [])
                       if f.get("state") == "pending")
            msg = {"done": "업로드 완료", "rechecked": "업로드 결과 확인 완료",
                   "stopped": "업로드 중지됨"}.get(info, "업로드 중단")
            self.status_lbl.setText(msg + (f" — 분석 대기 {pend}개: 몇 분 뒤 ‘🔄 업로드 결과 확인’을 누르세요"
                                           if pend else ""))
            return
        if kind == "def":
            n = int(info or 0)
            if n and QMessageBox.question(
                    self, "Defender 검사 완료",
                    f"위협이 발견된 게임 {n}개가 있습니다.\n‘위험·확인 필요’ 목록으로 볼까요?\n\n"
                    "※ 번역 패치·실행 도구 등은 오탐도 많으니 상세 패널의 이름을 확인하세요.") == QMessageBox.Yes:
                self.filter_cb.setCurrentIndex(5)
            elif not n:
                self.status_lbl.setText("Defender 검사 완료 — 위협 없음")
        else:
            if info == "quota":
                QMessageBox.information(self, "VirusTotal", "오늘 무료 한도(500건)에 도달했습니다.\n"
                                        "내일 ‘VirusTotal만’을 다시 누르면 남은 게임부터 이어서 조회합니다.")
            elif info == "badkey":
                QMessageBox.warning(self, "VirusTotal", "API 키가 올바르지 않습니다. ⚙설정에서 확인해 주세요.")
            else:
                self.status_lbl.setText("VirusTotal 조회 중지됨 — 다시 누르면 이어서 합니다"
                                        if info == "stopped" else "VirusTotal 조회 완료")

    def sec_stop_all(self):
        for ev in self.sec_stop.values():
            ev.set()

    def export_notfound(self):
        rows = [e for e in self.lib["games"].values()
                if e.get("status") in ("notfound", "error") and not e.get("hidden") and not e.get("missing")]
        if not rows:
            QMessageBox.information(self, "못 찾은 목록", "못 찾은 게임이 없습니다.")
            return
        rows.sort(key=lambda e: e.get("path", ""))
        out = DATA_DIR / "못찾은목록.csv"
        try:
            # utf-8-sig: 엑셀에서 한글·일본어가 깨지지 않게
            with open(out, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(["폴더/파일 이름", "코드", "검색에 쓴 이름", "종류", "진단", "경로"])
                for e in rows:
                    w.writerow([os.path.basename(e.get("path", "")), e.get("code", ""),
                                clean_name(e.get("name", "")),
                                "압축파일" if e.get("archive") else "폴더",
                                e.get("debug") or e.get("error", ""), e.get("path", "")])
        except OSError as ex:
            QMessageBox.warning(self, "내보내기 실패", f"{out}\n\n{ex}\n\n(엑셀에서 이 파일이 열려 있으면 닫고 다시 시도)")
            return
        self.status_lbl.setText(f"못 찾은 게임 {len(rows)}개 → {out}")
        reveal(str(out))

    # ---------- 저장
    def schedule_save(self):
        self.save_timer.start()

    def save(self):
        tmp = LIB_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.lib, f, ensure_ascii=False, indent=1)
        os.replace(tmp, LIB_FILE)

    def closeEvent(self, ev):
        self.bridge.stop()
        self.poll_running(closing=True)
        if self.rand_dlg:
            self.rand_dlg.stop.set()
        self.stop_evt.set()
        self.sec_stop_all()
        self.pool.clear()
        for e in self.lib["games"].values():
            if e.get("status") == "pending":
                e["status"] = "new"
        self.save()
        super().closeEvent(ev)


def dark_palette(app):
    app.setStyle("Fusion")
    p = QPalette()
    for role, c in [(QPalette.Window, "#24252c"), (QPalette.WindowText, "#e6e6ec"),
                    (QPalette.Base, "#1b1c22"), (QPalette.AlternateBase, "#24252c"),
                    (QPalette.Text, "#e6e6ec"), (QPalette.Button, "#2f313a"),
                    (QPalette.ButtonText, "#e6e6ec"), (QPalette.Highlight, "#4a6fd8"),
                    (QPalette.HighlightedText, "#ffffff"), (QPalette.ToolTipBase, "#2f313a"),
                    (QPalette.ToolTipText, "#e6e6ec"), (QPalette.Link, "#7ab7ff"),
                    (QPalette.PlaceholderText, "#80828c")]:
        p.setColor(role, QColor(c))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#6c6e78"))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor("#6c6e78"))
    app.setPalette(p)


def app_icon_path():
    """exe 안에 묶인 아이콘(_MEIPASS) 또는 GameShelf.py 옆의 아이콘"""
    for base in (getattr(sys, "_MEIPASS", ""), str(BASE_DIR)):
        p = os.path.join(base, "GameShelf.ico") if base else ""
        if p and os.path.isfile(p):
            return p
    return ""


def main():
    if sys.platform.startswith("win"):
        try:   # 작업 표시줄에 Python 아이콘 대신 GameShelf 아이콘이 뜨게 함
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("GameShelf.App")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName("GameShelf")
    ico = app_icon_path()
    if ico:
        app.setWindowIcon(QIcon(ico))
    dark_palette(app)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
