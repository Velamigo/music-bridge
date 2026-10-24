"""Release-verified artwork fallback for private cloud tracks; local image cache."""
import hashlib
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request,urlopen
import official_metadata as catalog
ROOT=Path(__file__).resolve().parent / '.state' / 'covers'
ALBUMS={}
def image(url):
    parsed=urlparse(url)
    if parsed.scheme!='https' or not parsed.hostname or not parsed.hostname.endswith('.mzstatic.com'):return ''
    key=hashlib.sha256(url.encode()).hexdigest()+'.jpg'
    path=ROOT/key
    if not path.exists():
        with urlopen(Request(url,headers={'User-Agent':'MusicShelf/1.0'}),timeout=10) as response:
            target=urlparse(response.url)
            if target.scheme!='https' or not target.hostname.endswith('.mzstatic.com'):return ''
            raw=response.read(3000001)
        if len(raw)>3000000 or not (raw.startswith(b'\xff\xd8') or raw.startswith(b'\x89PNG\r\n\x1a\n')):return ''
        ROOT.mkdir(parents=True,exist_ok=True,mode=0o700)
        path.write_bytes(raw);path.chmod(0o600)
    return '/covers/'+key

def enrich(items):
    for row in items:
        if row.get('cover_valid') and catalog.norm(row.get('cover_album',''))==catalog.norm(row.get('album','')):continue
        row['cover']=''
        artist,album=row.get('artist',''),row.get('album','')
        if not artist or not album or album.startswith('未知'):continue
        key=(catalog.norm(artist),catalog.norm(album))
        saved=ALBUMS.get(key)
        if saved and time.time()-saved[0]<7*86400:
            row['cover']=saved[1];continue
        try:
            refs=catalog.lookup(row['title'],artist,album=album,hour=int(time.time()//3600))
            ref=next((r for r in refs if catalog.norm(r['album'])==key[1] and catalog.norm(r['artist'])==key[0] and r.get('artwork')),None)
            if ref:
                cover=image(ref['artwork'])
                if cover:ALBUMS[key]=(time.time(),cover);row['cover']=cover
        except Exception:pass
    return items
