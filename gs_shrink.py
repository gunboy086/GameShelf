"""GameShelf - 게임 용량 줄이기 (분석 / 이미지·음악 재압축 / 실행 파일 공유)

GUI 없이 쓸 수 있는 순수 로직. GameShelf.py 에서 불러 씀.
- 원본은 게임 폴더 안 _GS_backup 으로 '이동' (같은 드라이브라 복사 시간 없음)
- 되돌리기 = 백업을 제자리로, 확정 = 백업 삭제
"""
import os
import io
import json
import shutil
import hashlib
import subprocess
from concurrent.futures import ThreadPoolExecutor

BACKUP_DIR = "_GS_backup"
MANIFEST = "manifest.json"

# nw.js (RPG 쯔꾸르 MV/MZ 실행 프로그램) 파일 — 같은 버전이면 게임끼리 완전히 같음
RUNTIME_NAMES = {
    "nw.dll", "node.dll", "nw_elf.dll", "ffmpeg.dll", "ffmpegsumo.dll", "libegl.dll", "libglesv2.dll",
    "d3dcompiler_47.dll", "icudtl.dat", "resources.pak", "nw.pak", "nw_100_percent.pak",
    "nw_200_percent.pak", "v8_context_snapshot.bin", "natives_blob.bin", "snapshot_blob.bin",
    "notification_helper.exe", "credits.html", "pdf.dll", "vk_swiftshader.dll", "vulkan-1.dll",
    "vk_swiftshader_icd.json",
}
RUNTIME_DIRS = {"locales", "swiftshader"}
IMG_EXT = (".png", ".rpgmvp", ".png_")
AUD_EXT = (".ogg", ".rpgmvo", ".ogg_", ".m4a", ".rpgmvm", ".m4a_", ".wav")
MOV_EXT = (".webm", ".mp4")
SKIP_IMG_DIRS = {"live2d", "effects"}          # 자체 로더를 쓰는 폴더는 건드리지 않음
RPG_HEADER = bytes([0x52, 0x50, 0x47, 0x4D, 0x56, 0, 0, 0, 0, 3, 1, 0, 0, 0, 0, 0])
WEBP_MAX = 16383


def human(n):
    n = float(n)
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.0f} {u}" if u in ("B", "KB") else f"{n:.1f} {u}"
        n /= 1024


def _walk(folder):
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d != BACKUP_DIR]
        for f in files:
            p = os.path.join(root, f)
            try:
                yield p, os.path.getsize(p)
            except OSError:
                pass


def _is_runtime(path, folder):
    name = os.path.basename(path).lower()
    if name in RUNTIME_NAMES:
        return True
    rel = os.path.relpath(path, folder).replace("\\", "/").lower().split("/")
    return any(d in RUNTIME_DIRS for d in rel[:-1])


def _img_dir_ok(rel):
    parts = rel.replace("\\", "/").lower().split("/")
    return not any(p in SKIP_IMG_DIRS for p in parts[:-1])


def _read_head(p, n=16):
    try:
        with open(p, "rb") as f:
            return f.read(n)
    except OSError:
        return b""


def is_webp_bytes(b):
    return len(b) >= 12 and b[:4] == b"RIFF" and b[8:12] == b"WEBP"


# ------------------------------------------------------------------ 암호화 (RPG 쯔꾸르 MV/MZ)
def load_key(data_dir):
    try:
        s = json.loads(open(os.path.join(data_dir, "System.json"), encoding="utf-8-sig").read())
        k = s.get("encryptionKey") or ""
        return bytes.fromhex(k) if len(k) == 32 else None
    except (OSError, ValueError):
        return None


def decrypt(raw, key):
    if raw[:16] != RPG_HEADER or not key:
        raise ValueError("암호화 헤더가 다름")
    body = bytearray(raw[16:])
    for i in range(min(16, len(body))):
        body[i] ^= key[i]
    return bytes(body)


def encrypt(plain, key):
    body = bytearray(plain)
    for i in range(min(16, len(body))):
        body[i] ^= key[i]
    return RPG_HEADER + bytes(body)


def _is_enc_name(p):
    return p.lower().endswith((".rpgmvp", ".png_", ".rpgmvo", ".ogg_", ".rpgmvm", ".m4a_"))


# ------------------------------------------------------------------ 1단계: 분석
def analyze(folder, rpg_info=None, ffmpeg=None):
    """반환: dict (총량, 종류별, 예상 절약량)"""
    cats = {k: [0, 0] for k in ("image", "image_done", "image_skip", "audio", "m4a_dup",
                                "movie", "runtime", "data", "other")}
    by_dir = {}
    oggs = set()
    files = list(_walk(folder))
    for p, sz in files:
        if p.lower().endswith((".ogg", ".rpgmvo", ".ogg_")):
            oggs.add(os.path.splitext(p)[0].lower())
    img_root = str(rpg_info[1].parent / "img") if rpg_info else None
    key = load_key(str(rpg_info[1])) if rpg_info else None
    for p, sz in files:
        low = p.lower()
        rel = os.path.relpath(p, folder)
        top = "/".join(rel.replace("\\", "/").split("/")[:3][:-1]) or "(게임 폴더)"
        by_dir[top] = by_dir.get(top, 0) + sz
        if _is_runtime(p, folder):
            c = "runtime"
        elif low.endswith(IMG_EXT):
            if img_root and os.path.commonpath([os.path.abspath(p), img_root]) == img_root and \
                    _img_dir_ok(os.path.relpath(p, img_root)):
                head = _read_head(p, 32)
                if _is_enc_name(p):
                    try:
                        head = decrypt(head, key)
                    except ValueError:
                        head = b""
                c = "image_done" if is_webp_bytes(head) else "image"
            else:
                c = "image_skip"
        elif low.endswith((".m4a", ".rpgmvm", ".m4a_")) and os.path.splitext(p)[0].lower() in oggs:
            c = "m4a_dup"
        elif low.endswith(AUD_EXT):
            c = "audio"
        elif low.endswith(MOV_EXT):
            c = "movie"
        elif low.endswith((".json", ".js")):
            c = "data"
        else:
            c = "other"
        cats[c][0] += 1
        cats[c][1] += sz
    total = sum(v[1] for v in cats.values())
    est = {
        "img_lossless": int(cats["image"][1] * 0.50),
        "img_hq": int(cats["image"][1] * 0.85),
        "m4a": cats["m4a_dup"][1] if rpg_info and rpg_info[2] == "MV" else 0,
        "audio": int(cats["audio"][1] * 0.45) if ffmpeg else 0,
    }
    return {"total": total, "files": len(files), "cats": cats, "est": est,
            "dirs": sorted(by_dir.items(), key=lambda x: -x[1])[:12]}


# ------------------------------------------------------------------ 백업 / 되돌리기 / 확정
def _bk_root(folder):
    return os.path.join(folder, BACKUP_DIR)


def load_manifest(folder):
    try:
        return json.loads(open(os.path.join(_bk_root(folder), MANIFEST), encoding="utf-8").read())
    except (OSError, ValueError):
        return {"files": {}, "actions": []}


def save_manifest(folder, m):
    os.makedirs(_bk_root(folder), exist_ok=True)
    tmp = os.path.join(_bk_root(folder), MANIFEST + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(_bk_root(folder), MANIFEST))


def backup_info(folder):
    m = load_manifest(folder)
    size = 0
    for rel in m["files"]:
        try:
            size += os.path.getsize(os.path.join(_bk_root(folder), rel))
        except OSError:
            pass
    saved = sum(v.get("before", 0) - v.get("after", 0) for v in m["files"].values())
    return {"count": len(m["files"]), "size": size, "saved": saved, "actions": m.get("actions", [])}


def _move_to_backup(folder, path, m, before, after):
    rel = os.path.relpath(path, folder)
    dst = os.path.join(_bk_root(folder), rel)
    if rel in m["files"]:                    # 이미 백업이 있으면(두 번째 압축) 처음 원본을 유지
        m["files"][rel]["after"] = after
        return None
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    os.replace(path, dst)
    m["files"][rel] = {"before": before, "after": after}
    return dst


def restore(folder):
    """백업한 원본을 전부 제자리로. 반환: 되돌린 파일 수"""
    m = load_manifest(folder)
    n = 0
    for rel in list(m["files"]):
        src = os.path.join(_bk_root(folder), rel)
        dst = os.path.join(folder, rel)
        if os.path.exists(src):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.replace(src, dst)
            n += 1
    shutil.rmtree(_bk_root(folder), ignore_errors=True)
    return n


def commit(folder, trash=None):
    """백업 정리(확정). trash 가 있으면 그 함수로(휴지통) 보내고, 없으면 바로 지움. 반환: 정리한 용량"""
    size = backup_info(folder)["size"]
    bk = _bk_root(folder)
    if trash and os.path.isdir(bk):
        # 휴지통에서 알아보기 쉽게 이름을 바꿔서 보냄: "_GS_backup (게임폴더 이름) 용량줄이기 전 원본"
        named = os.path.join(folder, f"{BACKUP_DIR} ({os.path.basename(folder.rstrip(os.sep + '/'))}) 용량줄이기 전 원본")
        try:
            os.replace(bk, named)
            bk = named
        except OSError:
            pass
        trash(bk)
    else:
        shutil.rmtree(bk, ignore_errors=True)
    return size


# ------------------------------------------------------------------ 2단계: 이미지 → WebP
def _encode_webp(png_bytes, mode):
    from PIL import Image
    im = Image.open(io.BytesIO(png_bytes))
    im.load()
    if max(im.size) > WEBP_MAX:
        return None
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA" if ("A" in im.mode or "transparency" in im.info) else "RGB")
    b = io.BytesIO()
    if mode == "lossless":
        im.save(b, "WEBP", lossless=True, quality=100, method=4, exact=True)
    else:
        im.save(b, "WEBP", quality=90, method=4, alpha_quality=100)
    return b.getvalue()


def shrink_images(folder, rpg_info, mode, progress=None, stop=None, workers=None):
    """mode: 'lossless' | 'hq'. 반환: (바꾼 수, 줄어든 바이트, 실패 수)"""
    img_root = str(rpg_info[1].parent / "img")
    key = load_key(str(rpg_info[1]))
    todo = []
    for p, sz in _walk(img_root):
        if p.lower().endswith(IMG_EXT) and _img_dir_ok(os.path.relpath(p, img_root)):
            todo.append((p, sz))
    m = load_manifest(folder)
    done = [0, 0, 0]
    lock = __import__("threading").Lock()
    total = len(todo)
    count = [0]

    def job(item):
        p, sz = item
        if stop and stop():
            return
        try:
            raw = open(p, "rb").read()
            enc = _is_enc_name(p)
            plain = decrypt(raw, key) if enc else raw
            src = plain
            if is_webp_bytes(plain):
                # 이미 WebP: 무손실이면 할 일 없음, 고화질이면 백업한 원본 PNG에서 다시 만듦
                bk = os.path.join(_bk_root(folder), os.path.relpath(p, folder))
                if mode == "lossless" or not os.path.exists(bk):
                    return
                r2 = open(bk, "rb").read()
                src = decrypt(r2, key) if enc else r2
            out = _encode_webp(src, mode)
            if out and len(out) < len(plain) * 0.95:
                data = encrypt(out, key) if enc else out
                tmp = p + ".gs_tmp"
                with open(tmp, "wb") as f:
                    f.write(data)
                with lock:
                    _move_to_backup(folder, p, m, sz, len(data))
                os.replace(tmp, p)
                with lock:
                    done[0] += 1
                    done[1] += sz - len(data)
        except Exception:
            with lock:
                done[2] += 1
        finally:
            with lock:
                count[0] += 1
                if progress and (count[0] % 5 == 0 or count[0] == total):
                    progress(count[0], total, os.path.basename(p))

    try:
        with ThreadPoolExecutor(max_workers=workers or max(1, (os.cpu_count() or 2) - 1)) as ex:
            list(ex.map(job, todo))
    finally:
        m.setdefault("actions", []).append("이미지 " + ("무손실" if mode == "lossless" else "고화질"))
        save_manifest(folder, m)
    return tuple(done)


# ------------------------------------------------------------------ 2단계: 음악
def remove_m4a_dups(folder):
    """MV 게임의 m4a(아이폰용, PC에서는 안 씀)를 백업으로 이동. 같은 이름 ogg가 있을 때만."""
    files = list(_walk(folder))
    oggs = {os.path.splitext(p)[0].lower() for p, _ in files if p.lower().endswith((".ogg", ".rpgmvo", ".ogg_"))}
    m = load_manifest(folder)
    n = size = 0
    for p, sz in files:
        if p.lower().endswith((".m4a", ".rpgmvm", ".m4a_")) and os.path.splitext(p)[0].lower() in oggs:
            _move_to_backup(folder, p, m, sz, 0)
            n += 1
            size += sz
    m.setdefault("actions", []).append("m4a 정리")
    save_manifest(folder, m)
    return n, size


def find_ffmpeg(extra_dirs=()):
    for d in extra_dirs:
        p = os.path.join(d, "ffmpeg.exe")
        if os.path.isfile(p):
            return p
    return shutil.which("ffmpeg")


def shrink_audio(folder, rpg_info, ffmpeg, progress=None, stop=None, quality=3):
    """ogg 다시 인코딩 (Vorbis q3 ≈ 112kbps, 루프 정보 유지)"""
    data_dir = str(rpg_info[1])
    key = load_key(data_dir)
    aud_root = str(rpg_info[1].parent / "audio")
    todo = [(p, sz) for p, sz in _walk(aud_root) if p.lower().endswith((".ogg", ".rpgmvo", ".ogg_")) and sz > 150_000]
    m = load_manifest(folder)
    done = [0, 0, 0]
    flags = 0x08000000 if os.name == "nt" else 0       # CREATE_NO_WINDOW
    for i, (p, sz) in enumerate(todo):
        if stop and stop():
            break
        try:
            raw = open(p, "rb").read()
            enc = _is_enc_name(p)
            plain = decrypt(raw, key) if enc else raw
            r = subprocess.run([ffmpeg, "-v", "error", "-i", "pipe:0", "-map", "0:a:0", "-map_metadata", "0:s:a:0",
                                "-c:a", "libvorbis", "-q:a", str(quality), "-f", "ogg", "pipe:1"],
                               input=plain, capture_output=True, timeout=300, creationflags=flags)
            out = r.stdout
            if r.returncode == 0 and out and len(out) < len(plain) * 0.85:
                data = encrypt(out, key) if enc else out
                tmp = p + ".gs_tmp"
                with open(tmp, "wb") as f:
                    f.write(data)
                _move_to_backup(folder, p, m, sz, len(data))
                os.replace(tmp, p)
                done[0] += 1
                done[1] += sz - len(data)
        except Exception:
            done[2] += 1
        if progress:
            progress(i + 1, len(todo), os.path.basename(p))
    m.setdefault("actions", []).append("음악 압축")
    save_manifest(folder, m)
    return tuple(done)


# ------------------------------------------------------------------ 3단계: 실행 파일 공유 (하드 링크)
def _runtime_files(folder):
    out = []
    for p, sz in _walk(folder):
        if sz >= 4096 and _is_runtime(p, folder):
            out.append((p, sz))
    return out


def _hash(p):
    h = hashlib.sha1()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dedupe_runtime(folders, progress=None, stop=None, dry_run=False):
    """여러 게임 폴더의 같은 실행 파일을 하드 링크로 합침.
    반환: dict(saved=절약 바이트, linked=링크한 파일 수, already=이미 공유 중, groups=..., errors=...)"""
    cand = {}
    for i, folder in enumerate(folders):
        if stop and stop():
            break
        if progress:
            progress(i + 1, len(folders), "찾는 중: " + os.path.basename(folder))
        for p, sz in _runtime_files(folder):
            cand.setdefault((os.path.basename(p).lower(), sz), []).append(p)
    groups = [v for v in cand.values() if len(v) > 1]
    res = {"saved": 0, "linked": 0, "already": 0, "errors": 0, "groups": len(groups)}
    total = sum(len(g) for g in groups)
    n = 0
    for g in groups:
        if stop and stop():
            break
        by_hash = {}
        for p in g:
            n += 1
            try:
                st = os.stat(p)
                by_hash.setdefault((st.st_dev, _hash(p)), []).append((p, st))
            except OSError:
                res["errors"] += 1
            if progress and n % 3 == 0:
                progress(n, total, "비교 중: " + os.path.basename(p))
        for (dev, _), items in by_hash.items():
            if len(items) < 2:
                continue
            src, sst = items[0]
            for p, st in items[1:]:
                if st.st_ino == sst.st_ino and st.st_ino != 0:
                    res["already"] += 1
                    continue
                if dry_run:
                    res["saved"] += st.st_size
                    res["linked"] += 1
                    continue
                tmp = p + ".gs_link"
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                    os.link(src, tmp)
                    os.replace(tmp, p)
                    res["saved"] += st.st_size
                    res["linked"] += 1
                except OSError:
                    res["errors"] += 1
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
    return res
