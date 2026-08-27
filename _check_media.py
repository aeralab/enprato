from pathlib import Path
from backend.app.ingest import find_session_media
from backend.app.media import is_browser_video, media_has_audio, stream_codec

p = Path("backend/data/sessions/7af6ed1e05f9")
print("files:", sorted([f.name for f in p.iterdir() if f.is_file()]))
m = find_session_media(p)
print("media:", m)
if m:
    print("codec:", stream_codec(m))
    print("browser:", is_browser_video(m))
    print("audio:", media_has_audio(m))
    print("size:", m.stat().st_size)
