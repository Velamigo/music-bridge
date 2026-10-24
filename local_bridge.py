"""Origin-limited, explicitly paired access from the GitHub Pages frontend."""
import secrets
import threading
import time

PAGES_ORIGIN = 'https://velamigo.github.io'
LOCAL_ORIGINS = ('http://127.0.0.1:8766', 'http://localhost:8766')
LOCK = threading.Lock()
CODE = secrets.token_hex(4).upper()
CODE_EXPIRES = time.time() + 600
TOKENS = {}
ATTEMPTS = []

def trusted(origin):
    return origin in (*LOCAL_ORIGINS, PAGES_ORIGIN)

def pairing_code():
    global CODE, CODE_EXPIRES
    with LOCK:
        if time.time() > CODE_EXPIRES:
            CODE = secrets.token_hex(4).upper()
            CODE_EXPIRES = time.time() + 600
        return CODE

def pair(origin, code):
    if origin != PAGES_ORIGIN:
        raise ValueError('此网站未获准连接本机。')
    with LOCK:
        now = time.time()
        ATTEMPTS[:] = [t for t in ATTEMPTS if t > now - 60]
        if len(ATTEMPTS) >= 10:
            raise ValueError('尝试过于频繁，请一分钟后再试。')
        ATTEMPTS.append(now)
        if now > CODE_EXPIRES or not secrets.compare_digest(str(code).strip().upper(), CODE):
            raise ValueError('连接码错误或过期，请打开本机连接页面查看。')
        token = secrets.token_urlsafe(32)
        TOKENS[token] = {'origin': origin, 'expires': now + 86400}
        # Keep the pairing table bounded.
        for key in list(TOKENS):
            if TOKENS[key]['expires'] < now: del TOKENS[key]
        if len(TOKENS) > 32: del TOKENS[next(iter(TOKENS))]
        return token

def authorized(origin, token):
    if origin in LOCAL_ORIGINS or not origin:
        return True
    if origin != PAGES_ORIGIN or not token:
        return False
    with LOCK:
        record = TOKENS.get(token)
        return bool(record and record['origin'] == origin and record['expires'] > time.time())

def connection_page():
    code = pairing_code()
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>连接这台 Mac · 音乐收集盒</title><style>body{{font:16px -apple-system,sans-serif;background:#f5f4ef;color:#234b3b;max-width:600px;margin:70px auto;padding:24px;line-height:1.8}}code{{display:block;font-size:38px;letter-spacing:8px;padding:22px;background:white;border-radius:12px}}a{{color:inherit}}</style><h1>连接这台 Mac</h1><p>仅在你信任的音乐收集盒网页中输入下方连接码：</p><code>{code}</code><p>允许的网站：{PAGES_ORIGIN}<br>连接码有效期 10 分钟，连接仅限本机。</p><p>音乐和网易云凭据仍保存在这台 Mac。请勿把连接码发给其他人。</p><p><a href="/">直接使用本机网页 →</a></p></html>'''.encode()
