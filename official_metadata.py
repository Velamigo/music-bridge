"""Cross-label release catalog lookup. Store records are evidence, not artist authentication."""
import json
import hashlib
from pathlib import Path
import re
import threading
import time
from functools import lru_cache
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from opencc import OpenCC
CC = OpenCC('t2s')
RATE_LOCK = threading.Lock()
LAST_REQUEST = 0.0
CACHE = Path(__file__).resolve().parent / '.state' / 'catalog-cache'
CACHE_TTL = 7*86400
MEMORY = {}

def cached(endpoint, params):
    key = json.dumps([endpoint, params], sort_keys=True)
    name = hashlib.sha256(key.encode()).hexdigest()
    value = MEMORY.get(name)
    if value is None:
        try: value = json.loads((CACHE / (name+'.json')).read_text())
        except (OSError, ValueError): return None
    if time.time()-value.get('at',0) > value.get('ttl',CACHE_TTL): return None
    MEMORY[name]=value
    return value.get('rows')

def store(endpoint, params, rows):
    key=hashlib.sha256(json.dumps([endpoint,params],sort_keys=True).encode()).hexdigest()
    value={'at':time.time(),'rows':rows,'endpoint':endpoint,'params':params,'ttl':CACHE_TTL if rows else 300}
    MEMORY[key]=value
    try:
        CACHE.mkdir(parents=True,exist_ok=True,mode=0o700)
        path=CACHE/(key+'.json');temp=path.with_suffix('.tmp')
        temp.write_text(json.dumps(value,ensure_ascii=False));temp.replace(path)
    except OSError: pass

def known_album(title,artist,album):
    if not album: return []
    try:
        for path in CACHE.glob('*.json'):
            if path.stem not in MEMORY:
                try: MEMORY[path.stem]=json.loads(path.read_text())
                except (OSError,ValueError): pass
    except OSError: pass
    for value in list(MEMORY.values()):
        if value.get('endpoint')!='lookup' or time.time()-value.get('at',0)>CACHE_TTL: continue
        rows=value.get('rows',[])
        release=next((x for x in rows if x.get('wrapperType')=='collection' and norm(x.get('collectionName',''))==norm(album)),None)
        if not release or not release.get('copyright'): continue
        for track in rows:
            if track.get('kind')=='song' and track.get('collectionId')==release.get('collectionId') and norm(track.get('trackName',''))==norm(title) and norm(track.get('artistName',''))==norm(artist):
                ref=reference(track,release,value.get('params',{}).get('country','TW'))
                if ref:return [ref]
    return []

def reference(track,release,country):
    link=safe_url(track.get('trackViewUrl') or release.get('collectionViewUrl'))
    if not link:return None
    return {'title':track['trackName'],'artist':track['artistName'],'album':release['collectionName'],
      'date':str(release.get('releaseDate',''))[:10],'duration':round(track.get('trackTimeMillis',0)/1000),
      'track':track.get('trackNumber'),'disc':track.get('discNumber'),'publisher':release['copyright'],
      'url':link,'artist_id':str(track['artistId']),'album_id':str(release['collectionId']),
      'artwork':track.get('artworkUrl100') or release.get('artworkUrl100') or '',
      'source':'Apple iTunes 正式发行目录','country':country,
      'source_note':'已核对歌手、歌名及专辑曲目 ID；平台目录记录不等于原唱身份认证。'}

def norm(value):
    return ''.join(c for c in CC.convert(str(value)).lower() if c.isalnum())

def apple(endpoint, **params):
    global LAST_REQUEST
    hit=cached(endpoint,params)
    if hit is not None:return hit
    # Apple documents approximately 20 requests/minute; shared pacing for batch checks.
    with RATE_LOCK:
        hit=cached(endpoint,params)
        if hit is not None:return hit
        delay = 3.2 - (time.monotonic() - LAST_REQUEST)
        if delay > 0:
            time.sleep(delay)
        LAST_REQUEST = time.monotonic()
        url = 'https://itunes.apple.com/' + endpoint + '?' + urlencode(params)
        with urlopen(Request(url, headers={'User-Agent': 'MusicShelf/1.0'}), timeout=20) as response:
            rows=json.load(response).get('results', [])
            store(endpoint,params,rows)
            return rows

def safe_url(value):
    parsed = urlparse(value or '')
    return value if parsed.scheme == 'https' and parsed.hostname in ('music.apple.com', 'itunes.apple.com') else ''

@lru_cache(maxsize=256)
def lookup(title, artist, album='', hour=0):
    if not title or not artist:
        return []
    known=known_album(title,artist,album)
    if known:return known
    # Country is explicit; fallback to US only when TW has no exact song/artist record.
    matches = []
    for country in ('TW', 'US'):
        rows = apple('search', term=CC.convert(title)+' '+CC.convert(artist), country=country,
                     media='music', entity='song', limit=50)
        matches = [x for x in rows if x.get('kind') == 'song'
                   and norm(x.get('trackName','')) == norm(title)
                   and norm(x.get('artistName','')) == norm(artist)
                   and (not album or norm(x.get('collectionName','')) == norm(album))]
        if matches:
            break
    found = []
    seen = set()
    for item in matches:
        collection_id = item.get('collectionId')
        if not collection_id or collection_id in seen:
            continue
        if len(seen) >= 3:
            break
        seen.add(collection_id)
        # Resolve the album and full tracklist by stable ID, not another free-text hit.
        rows = apple('lookup', id=collection_id, entity='song', country=country, limit=200)
        release = next((x for x in rows if x.get('wrapperType') == 'collection'
                        and x.get('collectionId') == collection_id), None)
        track = next((x for x in rows if x.get('trackId') == item.get('trackId')
                      and x.get('artistId') == item.get('artistId')
                      and norm(x.get('trackName','')) == norm(title)
                      and norm(x.get('artistName','')) == norm(artist)), None)
        if not release or not track or not release.get('copyright'):
            continue
        ref=reference(track,release,country)
        if ref:found.append(ref)
        if len(seen) >= 3:
            break
    return found
