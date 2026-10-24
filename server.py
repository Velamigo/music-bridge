#!/usr/bin/env python3
import concurrent.futures
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import uuid
from urllib.parse import urlparse, unquote, urlencode
from urllib.request import urlopen, Request
from functools import lru_cache
import unicodedata
from difflib import SequenceMatcher
import music_bridge
import download_progress
import ncm_client
import cloud_art
import cloud_manage
import local_bridge
import download_settings
from opencc import OpenCC

ROOT = Path(__file__).resolve().parent
YTDLP = str(ROOT / '.venv/bin/yt-dlp')
POOL = concurrent.futures.ThreadPoolExecutor(max_workers=2)
JOBS = {}
LOCK = threading.Lock()
DOWNLOAD_LOCK = threading.Lock()
CHINESE = OpenCC('t2s')

@lru_cache(maxsize=512)
def audio_details(filename, size, modified):
    try:
        result = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a:0',
                                 '-show_entries', 'stream=codec_name,bit_rate', '-of', 'json', filename],
                                capture_output=True, text=True, timeout=15, check=True)
        stream = json.loads(result.stdout).get('streams', [{}])[0]
        bitrate = int(stream.get('bit_rate', 0))
        return {'format': Path(filename).suffix.lstrip('.').upper(),
                'codec': str(stream.get('codec_name', '')).upper(),
                'bitrate_kbps': round(bitrate / 1000) if bitrate > 0 else None}
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return {'format': Path(filename).suffix.lstrip('.').upper(), 'codec': '', 'bitrate_kbps': None}

def execute(cmd, timeout=180):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(music_bridge.user_error(p.stderr) or '命令执行失败')
    return p.stdout

def update(key, **values):
    with LOCK:
        JOBS[key].update(values)

def field(body, name):
    value = str(body.get(name, '')).strip()
    if len(value) > 300:
        raise RuntimeError('输入内容不能超过 300 字。')
    return value

def apple(endpoint, **params):
    url = 'https://itunes.apple.com/' + endpoint + '?' + urlencode(dict(country='TW', **params))
    with urlopen(Request(url, headers={'User-Agent': 'MusicShelf/1.0'}), timeout=30) as response:
        return json.load(response).get('results', [])

def album_summary(item):
    return {'id': item['collectionId'], 'title': item['collectionName'], 'artist': item.get('artistName', ''),
            'count': item.get('trackCount'), 'year': (item.get('releaseDate') or '')[:4],
            'url': item.get('collectionViewUrl', '')}

@lru_cache(maxsize=50)
def album_tracks(collection_id):
    if not re.fullmatch(r'[0-9]{1,18}', str(collection_id)):
        raise RuntimeError('无效的专辑编号。')
    data = apple('lookup', id=collection_id, entity='song', limit=200)
    albums = [x for x in data if x.get('wrapperType') == 'collection']
    if not albums:
        raise RuntimeError('目录中未找到这张专辑。')
    album = album_summary(albums[0])
    tracks = [{'title': x['trackName'], 'artist': x.get('artistName', album['artist']),
               'album': album['title'], 'track': x.get('trackNumber', 0), 'disc': x.get('discNumber', 1),
               'duration': round(x.get('trackTimeMillis', 0) / 1000), 'catalog_id': x['trackId']}
              for x in data if x.get('kind') == 'song' and x.get('collectionId') == int(collection_id)]
    tracks.sort(key=lambda x: (x['disc'], x['track']))
    if not tracks:
        raise RuntimeError('这张专辑没有可用的歌曲目录。')
    return album, tracks

def youtube(query, count=10):
    data = json.loads(execute([YTDLP, '--ignore-config', '--flat-playlist', '--dump-single-json', '--', f'ytsearch{count}:' + query]))
    return [{k: item.get(k) for k in ('id', 'title', 'channel', 'duration', 'url', 'channel_is_verified', 'channel_id', 'channel_url', 'description')}
            for item in data.get('entries', []) if item and re.fullmatch(r'[A-Za-z0-9_-]{11}', item.get('id', ''))]

def normalized(value):
    return ''.join(c for c in CHINESE.convert(unicodedata.normalize('NFKC', value)).lower() if c.isalnum())

def artist_in_channel(artist, channel):
    """Full artist-name match with Latin word boundaries; never fuzzy identity."""
    if not artist or not channel:
        return False
    artist = CHINESE.convert(unicodedata.normalize('NFKC', artist)).lower().strip()
    channel = CHINESE.convert(unicodedata.normalize('NFKC', channel)).lower()
    if re.search(r'[\u3400-\u9fff]', artist):
        # Reject suffixes that indicate a fan or imitation account.
        if re.search(r'粉[丝絲]|歌迷|後援|后援|模仿|致敬|fan(?:s|page|club)?\b|tribute', channel, re.I):
            return False
        return normalized(artist) in normalized(channel)
    words = re.findall(r'[^\W_]+', artist)
    return bool(words) and bool(re.search(r'(?<!\w)' + r'[\W_]*'.join(re.escape(x) for x in words) + r'(?!\w)', channel))

def annotate(item, artist='', song='', expected=None):
    item = dict(item)
    title = item.get('title') or ''
    channel = item.get('channel') or ''
    verified = bool(item.get('channel_is_verified'))
    same_artist = artist_in_channel(artist, channel)
    flags = []
    checks = [('翻唱线索', r'翻唱|翻彈|翻弹|\bcover(?:ed)?\b|女[版聲声]|男[版聲声]|ai翻唱|AI\s*cover'),
              ('现场版', r'演唱[會会]|現場|现场|\b(?:live|concert)\b'),
              ('伴奏版', r'伴奏|\b(?:karaoke|ktv|instrumental)\b'),
              ('改编／混音', r'跨[時时]空|合唱版|\b(?:remix|mashup|sped up|slowed|8d)\b|加速版|慢速版'),
              ('合集／联播', r'整[張张]|聯播|联播|串[燒烧]|合[輯辑]|\bfull album\b')]
    for label, pattern in checks:
        if re.search(pattern, title, re.I):
            flags.append(label)
    source = '来源待确认'
    reasons = []
    score = 0
    trusted = False
    if same_artist and verified:
        source = '歌手同名认证频道'
        reasons.append(f'频道名与「{artist}」匹配，且具有 YouTube 认证标记')
        score += 100
        trusted = True
    elif same_artist and re.search(r'\s-\sTopic$', channel, re.I):
        source = '同名 Topic 发行线索'
        reasons.append('同名频道带有 Topic 后缀；发行身份仍需核对')
        score += 65
        trusted = True
    elif same_artist:
        source = '歌手同名频道 · 待确认'
        reasons.append('频道名与歌手匹配，但未取得认证依据')
        score += 20
    elif verified:
        reasons.append('频道已认证，但未匹配到所填歌手；认证不等于原唱')
        score += 3
    else:
        reasons.append('未取得歌手频道或发行方的身份依据')
    versions = []
    if re.search(r'official audio|官方音[源频頻]|audio only', title, re.I):
        versions.append('音频版')
        reasons.append('标题标注为官方音频；标题本身不作为身份认证')
        score += 15 if trusted else 0
    if re.search(r'official (?:mv|music video)|官方\s*mv', title, re.I):
        versions.append('MV 音轨')
        reasons.append('MV 音轨可能包含对白或片头')
        score += 8 if trusted else 0
    if re.search(r'歌词|歌詞|\blyrics?\b', title, re.I):
        versions.append('歌词版')
    if song and normalized(song) in normalized(title):
        score += 15
    if expected and item.get('duration'):
        delta = abs(item['duration'] - expected)
        reasons.append(f'与专辑目录时长相差约 {round(delta)} 秒；时长相近不能证明同一录音')
        score -= min(20, delta / 3)
    if flags:
        reasons.append('标题含有：' + '、'.join(flags))
        score -= 150
    if not artist:
        reasons.append('补填歌手名后可比较频道身份')
    item.update(source_label=source, version_labels=versions, version_flags=flags,
                source_reasons=reasons, recommended=trusted and not flags,
                source_tone='good' if trusted and not flags else 'warn' if flags else 'neutral',
                source_score=score)
    return item

def ranked_sources(items, artist='', song='', expected=None):
    return sorted((annotate(x, artist, song, expected) for x in items), key=lambda x: x['source_score'], reverse=True)

def narrow_albums(results, artist, title):
    # Apple also returns loosely related albums. Prefer names close to the input,
    # allowing simplified/traditional character differences and edition suffixes.
    def similar(a, b):
        a, b = normalized(a), normalized(b)
        return bool(a and b) and (a in b or b in a or SequenceMatcher(None, a, b).ratio() >= .55)
    for query, field_name in ((title, 'title'), (artist, 'artist')):
        if query:
            matches = [x for x in results if similar(query, x[field_name])]
            if matches:
                results = matches
    return results

def track_candidates(track):
    candidates = youtube(track['artist'] + ' ' + track['title'], 8)
    expected = track['duration']
    # Exclude compilations, multi-song videos, and obvious alternative performances.
    candidates = [x for x in candidates if normalized(track['title']) in normalized(x.get('title','')) and x.get('duration') and
                  abs(x['duration'] - expected) <= max(60, expected * .25) and
                  not re.search(r'full album|全集|整張|整张|串燒|串烧|聯播|联播|翻唱|伴奏|跨時空|跨时空|\b(?:karaoke|ktv|cover|live|concert|remix)\b|現場|现场|演唱會|演唱会', x['title'], re.I)]
    return ranked_sources(candidates, track['artist'], track['title'], expected)[:4]

def catalog_songs(artist, song):
    entries=apple('search',term=' '.join(x for x in (artist,song) if x),entity='song',media='music',limit=100)
    entries=[x for x in entries if x.get('kind')=='song']
    if artist:
        entries=[x for x in entries if normalized(x.get('artistName',''))==normalized(artist)]
    if song:
        entries=[x for x in entries if normalized(song) in normalized(x.get('trackName',''))]
    entries.sort(key=lambda x:x.get('releaseDate','9999'))
    seen=set();result=[]
    for x in entries:
        key=(normalized(x['trackName']),normalized(x.get('artistName','')))
        if key in seen:continue
        seen.add(key)
        result.append({'catalog_id':x['trackId'],'title':x['trackName'],'artist':x.get('artistName',''),
            'album':x.get('collectionName',''),'duration':round(x.get('trackTimeMillis',0)/1000),
            'track':x.get('trackNumber',0),'disc':x.get('discNumber',1),'url':x.get('trackViewUrl','')})
    return result

@lru_cache(maxsize=256)
def source_for_catalog(catalog_id):
    if not re.fullmatch(r'[0-9]{1,18}',str(catalog_id)):raise RuntimeError('无效的歌曲编号')
    rows=apple('lookup',id=catalog_id,entity='song')
    x=next((x for x in rows if x.get('kind')=='song' and str(x.get('trackId'))==str(catalog_id)),None)
    if not x:raise RuntimeError('这首歌暂时无法读取，请重新搜索。')
    track={'catalog_id':x['trackId'],'title':x['trackName'],'artist':x.get('artistName',''),
        'album':x.get('collectionName',''),'duration':round(x.get('trackTimeMillis',0)/1000),
        'track':x.get('trackNumber',0),'disc':x.get('discNumber',1)}
    return dict(track,candidates=track_candidates(track))

def download_metadata(body):
    song = field(body, 'song')
    video_title = field(body, 'video_title')
    artist = field(body, 'artist')
    album = field(body, 'album')
    if not song:
        bracket = re.search(r'[【\[]([^】\]]+)', video_title)
        song = bracket.group(1).strip() if bracket else video_title
    rows = apple('search', term=song + ' ' + artist, entity='song', media='music', limit=50)
    candidates = []
    for row in rows:
        name, singer = row.get('trackName', ''), row.get('artistName', '')
        if row.get('kind') != 'song' or not name or not singer:
            continue
        title_matches = normalized(name) == normalized(song)
        if not title_matches and not body.get('song'):
            title_matches = normalized(name) in normalized(song)
        if not title_matches:
            continue
        if artist:
            if normalized(singer) != normalized(artist): continue
        elif not artist_in_channel(singer, video_title):
            continue
        if album and normalized(row.get('collectionName','')) != normalized(album):
            continue
        candidates.append(row)
    artists = {normalized(x['artistName']) for x in candidates}
    if len(artists) != 1:
        return {'message': '暂未找到明确匹配的发行资料，可手动填写歌手和专辑。'}
    candidates.sort(key=lambda x: (x.get('releaseDate','9999'), x.get('collectionId',0)))
    row = candidates[0]
    return {'title': row['trackName'], 'artist': row['artistName'], 'album': row.get('collectionName',''),
            'url': row.get('trackViewUrl',''),
            'message': '已按 Apple 发行目录预填，可手动修改。' + ('此歌收录于多张专辑，已预填较早发行版本，请核对。' if len({x.get('collectionId') for x in candidates}) > 1 else '')}

def worker(key, action, body):
    try:
        update(key, status='running', message='正在搜索 YouTube…' if action == 'search' else '正在检查音源并下载 M4A…')
        if action == 'search':
            artist, album, song = (field(body, name) for name in ('artist', 'album', 'song'))
            if not any((artist, album, song)):
                raise RuntimeError('请填写歌手、专辑或歌曲。')
            if body.get('mode')=='songs':
                results=catalog_songs(artist,song)
                update(key,status='done',kind='catalogSongs',results=results,message=f'找到 {len(results)} 首歌曲，点击下载即可。')
            elif song:
                results = ranked_sources(youtube(' '.join(x for x in (artist, song, album) if x)), artist, song)
                update(key, status='done', kind='songs', results=results, message=f'单曲搜索：{song} · 找到 {len(results)} 个音源候选')
            else:
                update(key, message='正在查询专辑目录…')
                entries = apple('search', term=' '.join(x for x in (artist, album) if x), entity='album', limit=20)
                results = [album_summary(x) for x in entries if x.get('collectionType') == 'Album']
                results = narrow_albums(results, artist, album)
                if body.get('mode')=='albums' and artist:
                    results=[x for x in results if normalized(x['artist'])==normalized(artist)]
                update(key, status='done', kind='albums', results=results, message=f'找到 {len(results)} 张专辑，请选择正确版本')
        elif action == 'album':
            album, tracks = album_tracks(str(body.get('id', '')))
            update(key, status='done', album=album, tracks=tracks, message=f'{album["artist"]} · {album["title"]} · {len(tracks)} 首')
        elif action == 'match':
            album, tracks = album_tracks(str(body.get('id', '')))
            result = []
            for i, track in enumerate(tracks):
                update(key, message=f'正在逐首查找音源 {i + 1}/{len(tracks)}：{track["title"]}', tracks=list(result))
                try:
                    options = track_candidates(track)
                    result.append(dict(track, candidates=options))
                except Exception:
                    result.append(dict(track, candidates=[], error='搜索失败，可单独重试'))
                update(key, tracks=list(result))
            found = sum(bool(x['candidates']) for x in result)
            update(key, status='done', album=album, tracks=result, message=f'已匹配 {found}/{len(tracks)} 首。请核对版本并勾选要下载的歌曲。')
        elif action == 'track':
            album, tracks = album_tracks(str(body.get('id', '')))
            track = next((x for x in tracks if str(x['catalog_id']) == str(body.get('catalog_id'))), None)
            if not track:
                raise RuntimeError('曲目不在这张专辑内。')
            result = dict(track, candidates=track_candidates(track))
            update(key, status='done', track=result, message=f'找到 {len(result["candidates"])} 个单曲候选，请核对版本。')
        elif action == 'song_sources':
            update(key,message='正在寻找可下载版本…')
            update(key,status='done',track=source_for_catalog(body.get('catalog_id')),message='已找到可下载版本')
        elif action == 'metadata':
            update(key, message='正在自动填写发行资料…')
            update(key, status='done', result=download_metadata(body), message='发行资料查询完成')
        elif action == 'directory_choose':
            update(key, message='请在 Mac 窗口中选择文件夹…')
            folder = download_settings.choose()
            update(key, status='done', folder=folder, cancelled=folder is None, message='已选择文件夹' if folder else '已取消选择')
        elif action == 'directory':
            if not DOWNLOAD_LOCK.acquire(blocking=False):
                raise RuntimeError('正在下载，请等下载结束后再更改目录。')
            try:
                folder = download_settings.save(body.get('folder'))
            finally:
                DOWNLOAD_LOCK.release()
            update(key, status='done', folder=str(folder), message='下载目录已保存')
        elif action in ('ncm_snapshot', 'ncm_check', 'ncm_prepare', 'ncm_apply', 'ncm_folders'):
            update(key, message='正在联网检查，尚未修改云盘…' if action == 'ncm_check' else '正在处理云盘管理请求…')
            functions = {'ncm_snapshot': cloud_manage.snapshot, 'ncm_check': cloud_manage.check, 'ncm_prepare': cloud_manage.prepare, 'ncm_apply': cloud_manage.apply,
                         'ncm_folders': lambda data: cloud_manage.folders(data, write=bool(data.get('operation')))}
            result = functions[action](body)
            update(key, status='done', result=result, message=result.get('message', '分类已更新'))
        elif action in ('ncm_qr', 'ncm_poll', 'ncm_disconnect', 'ncm_list'):
            commands = {'ncm_qr': 'qr_start', 'ncm_poll': 'qr_poll', 'ncm_disconnect': 'disconnect', 'ncm_list': 'list'}
            result = ncm_client.CLIENT.call(commands[action], {'offset': body.get('offset', 0)}, timeout=180)
            if action == 'ncm_list':
                cloud_art.enrich(result['items'])
            update(key, status='done', result=result, message=result.get('message', '网易云状态已更新'))
        elif action in ('ncm_upload', 'ncm_verify'):
            filename = str(body.get('file', ''))
            if not re.fullmatch(r'[A-Za-z0-9_-]{11}\.m4a', filename):
                raise RuntimeError('无效的本地音频文件。')
            path = download_settings.folder() / filename
            music_bridge.inspect(path)
            update(key, message='正在核对网易云云盘…' if action == 'ncm_verify' else '正在上传并核对网易云云盘…')
            result = ncm_client.upload(path, body.get('user_id'), verify_only=action == 'ncm_verify')
            update(key, status='done', result=result, message=result['message'])
        else:
            video = str(body.get('id', ''))
            if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video):
                raise RuntimeError('无效的 YouTube 视频编号')
            cmd = [str(ROOT / '.venv/bin/python'), str(ROOT / 'music_bridge.py'), 'download', 'https://www.youtube.com/watch?v=' + video]
            for name in ('title', 'artist', 'album', 'track', 'disc'):
                value = str(body.get(name, '')).strip()
                if len(value) > 300:
                    raise RuntimeError('标签过长')
                if value:
                    cmd.extend(['--' + name, value])
            # Serialize downloads to avoid simultaneous jobs overwriting the same video.
            with DOWNLOAD_LOCK:
                cmd.extend(['--directory', str(download_settings.folder())])
                dest = download_settings.folder() / (video + '.m4a')
                if dest.exists():
                    music_bridge.inspect(dest)
                else:
                    download_progress.run(cmd,lambda values:update(key,**values),timeout=900)
                    music_bridge.inspect(dest)
                    meta = {k: str(body.get(k, '')) for k in ('title', 'artist', 'album', 'track', 'disc')}
                    meta.update(id=video, downloaded_at=time.time(), file=dest.name)
                    dest.with_suffix('.json').write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
            cloud = None
            message = '下载完成 · M4A / AAC · 未重新编码'
            if body.get('upload') is True:
                update(key,stage='uploading',progress=None,message='下载完成，正在上传网易云盘…',file=dest.name)
                try:
                    cloud = ncm_client.upload(dest, body.get('user_id'), onprogress=lambda stage,text:update(key,stage=stage,progress=None,message=text))
                    message += '；' + cloud['message']
                except ncm_client.NcmError as exc:
                    cloud = {'state': 'error', 'message': str(exc)}
                    message += '；上传未完成：' + str(exc)
            update(key,status='done',stage='done',progress=None,message=message,file=dest.name,cloud=cloud)
    except subprocess.TimeoutExpired:
        update(key, status='error', message='请求超时，可稍后重试。')
    except Exception as exc:
        update(key, status='error', message=str(exc))

class Handler(BaseHTTPRequestHandler):
    def allowed(self):
        return self.headers.get('Host') in ('127.0.0.1:8766', 'localhost:8766')

    def end_headers(self):
        origin = self.headers.get('Origin')
        if local_bridge.trusted(origin):
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Vary', 'Origin')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('X-Content-Type-Options', 'nosniff')
        super().end_headers()

    def access(self):
        return local_bridge.authorized(self.headers.get('Origin'), self.headers.get('X-Music-Bridge-Token'))

    def do_OPTIONS(self):
        if not self.allowed() or not local_bridge.trusted(self.headers.get('Origin')):
            return self.reply({'error': 'Invalid origin'}, 403)
        self.send_response(204)
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Music-Bridge-Token')
        self.send_header('Access-Control-Max-Age', '600')
        if self.headers.get('Access-Control-Request-Private-Network') == 'true':
            self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.end_headers()

    def reply(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if not self.allowed():
            return self.reply({'error': 'Invalid host'}, 403)
        route = urlparse(self.path).path
        if route == '/api/bridge-info':
            return self.reply({'app':'music-bridge','version':'1.2','paired':bool(self.headers.get('Origin') and self.access())})
        if route == '/connect':
            if self.headers.get('Origin') and self.headers.get('Origin') not in local_bridge.LOCAL_ORIGINS:
                return self.reply({'error':'Open this page directly'},403)
            raw=local_bridge.connection_page()
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(raw)))
            self.end_headers();self.wfile.write(raw);return
        if (route.startswith('/api/') or route.startswith('/files/')) and not self.access():
            return self.reply({'error':'请先连接这台 Mac'},401)
        if route.startswith('/covers/'):
            name = route.removeprefix('/covers/')
            if not re.fullmatch(r'[a-f0-9]{64}\.jpg', name):
                return self.reply({'error':'Invalid cover'},400)
            path = ROOT / '.state' / 'covers' / name
            if not path.is_file():
                return self.reply({'error':'Not found'},404)
            raw = path.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type','image/png' if raw.startswith(b'\x89PNG') else 'image/jpeg')
            self.send_header('Content-Length',str(len(raw)))
            self.send_header('Cache-Control','public, max-age=604800')
            self.end_headers();self.wfile.write(raw);return
        if route == '/api/status':
            return self.reply({'version': execute([YTDLP, '--version']).strip(), 'ffmpeg': bool(shutil.which('ffmpeg')), 'folder': str(download_settings.folder())})
        if route == '/api/ncm/status':
            try:
                if ncm_client.CLIENT.installed():
                    ncm_client.CLIENT.call('status')
                return self.reply(dict(ncm_client.CLIENT.public, ready=ncm_client.CLIENT.installed()))
            except ncm_client.NcmError as exc:
                return self.reply({'error': str(exc)}, 503)
        if route == '/api/library':
            items = []
            for path in sorted((download_settings.folder()).glob('*.m4a'), key=lambda p: p.stat().st_mtime, reverse=True):
                meta = path.with_suffix('.json')
                data = json.loads(meta.read_text()) if meta.exists() else {'title': path.stem, 'file': path.name}
                data['size'] = path.stat().st_size
                data.update(audio_details(str(path), data['size'], path.stat().st_mtime_ns))
                data['cloud'] = ncm_client.upload_state(path)
                items.append(data)
            return self.reply(items)
        if route.startswith('/api/jobs/'):
            with LOCK:
                data = dict(JOBS.get(route.rsplit('/', 1)[-1], {'status': 'error', 'message': '任务不存在'}))
            return self.reply(data)
        if route.startswith('/files/'):
            name = unquote(route[len('/files/'):])
            if not re.fullmatch(r'[A-Za-z0-9_-]{11}\.m4a', name):
                return self.reply({'error': 'Invalid file'}, 400)
            path = download_settings.folder() / name
            if not path.exists():
                return self.reply({'error': 'Not found'}, 404)
            self.send_response(200)
            self.send_header('Content-Type', 'audio/mp4')
            self.send_header('Content-Length', str(path.stat().st_size))
            self.end_headers()
            with path.open('rb') as f:
                shutil.copyfileobj(f, self.wfile)
            return
        if route == '/':
            raw = (ROOT / 'index.html').read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        self.reply({'error': 'Not found'}, 404)

    def do_POST(self):
        if not self.allowed() or not local_bridge.trusted(self.headers.get('Origin')):
            return self.reply({'error': 'Invalid origin'}, 403)
        if self.path == '/api/pair':
            try:
                length=int(self.headers.get('Content-Length',0))
                if not 0 < length < 1024: raise ValueError('请求无效')
                body=json.loads(self.rfile.read(length))
                if not isinstance(body,dict): raise ValueError('请求无效')
                token=local_bridge.pair(self.headers.get('Origin'),body.get('code',''))
                return self.reply({'token':token,'version':'1.2'})
            except (ValueError,TypeError) as exc:
                return self.reply({'error':str(exc)},403)
        if not self.access():
            return self.reply({'error':'请先连接这台 Mac'},401)
        if self.path not in ('/api/search', '/api/download', '/api/album', '/api/match', '/api/track',
                             '/api/ncm_qr', '/api/ncm_poll', '/api/ncm_disconnect', '/api/ncm_list', '/api/ncm_upload', '/api/ncm_verify',
                             '/api/song_sources', '/api/metadata', '/api/directory_choose', '/api/directory', '/api/ncm_snapshot', '/api/ncm_check', '/api/ncm_prepare', '/api/ncm_apply', '/api/ncm_folders'):
            return self.reply({'error': 'Not found'}, 404)
        try:
            length = int(self.headers.get('Content-Length', 0))
            if length < 1 or length > 8192:
                raise ValueError('Invalid body size')
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError('Invalid body')
            with LOCK:
                if sum(v['status'] in ('queued', 'running') for v in JOBS.values()) >= 8:
                    return self.reply({'error': '任务较多，请稍后再试'}, 429)
                # Keep recent jobs only.
                if len(JOBS) > 100:
                    for old in list(JOBS):
                        if JOBS[old]['status'] in ('done', 'error'):
                            del JOBS[old]
                            break
                key = uuid.uuid4().hex
                JOBS[key] = {'status': 'queued', 'message': '等待处理…'}
            POOL.submit(worker, key, self.path.rsplit('/', 1)[-1], body)
            self.reply({'job': key}, 202)
        except (ValueError, TypeError):
            self.reply({'error': '请求格式错误'}, 400)

if __name__ == '__main__':
    print('音乐收集盒已启动：http://127.0.0.1:8766', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 8766), Handler).serve_forever()
