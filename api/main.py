from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse 
import requests
import random
import os

app = FastAPI()

# Vercel環境では、カレントディレクトリからの相対パスが不安定になるため、
# 絶対パスを取得して静的ファイルを指定します。
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "statics")

# ===============================
# Static Files Routing
# ===============================
# UIから /static/filename でアクセスできるようにマウント
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
def root():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"status": "index.html not found", "path": index_path}

@app.get("/playlist")
def playlist_page():
    playlist_path = os.path.join(STATIC_DIR, "playlist.html")
    if os.path.exists(playlist_path):
        return FileResponse(playlist_path)
    return {"status": "playlist.html not found"}

# ===============================
# API Base Lists
# ===============================
VIDEO_APIS = [
    "https://iv.melmac.space", "https://pol1.iv.ggtyler.dev",
    "https://cal1.iv.ggtyler.dev", "https://invidious.0011.lt",
    "https://yt.omada.cafe", "https://invidious.exma.de/",
    "https://invidious.f5.si/", "https://siawaseok-wakame-server2.glitch.me/",
    "https://lekker.gay/", "https://id.420129.xyz/"
]

SEARCH_APIS = VIDEO_APIS.copy()

# 重複排除
VIDEO_APIS = list(dict.fromkeys(VIDEO_APIS))
SEARCH_APIS = list(dict.fromkeys(SEARCH_APIS))

STREAM_YTDL_API_BASE_URL = "https://yudlp.vercel.app/stream/"
HLS_API_BASE_URL = "https://yudlp.vercel.app/m3u8/"

TIMEOUT = 6
HEADERS = {"User-Agent": "Mozilla/5.0"}

# ===============================
# Utils
# ===============================
def try_json(url, params=None):
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"Request error: {e}")
    return None

# ===============================
# Music Search API
# ===============================
@app.get("/api/search")
def api_search(q: str):
    results = []
    random.shuffle(SEARCH_APIS)
    
    # 音楽のみをヒットさせるための調整（topicやaudioを付加）
    music_query = f"{q} topic audio"

    for base in SEARCH_APIS:
        data = try_json(
            f"{base}/api/v1/search",
            {"q": music_query, "type": "video"}
        )
        if not isinstance(data, list):
            continue

        for v in data:
            if not v.get("videoId"):
                continue

            results.append({
                "videoId": v.get("videoId"),
                "title": v.get("title"),
                "author": v.get("author"),
                "lengthSeconds": v.get("lengthSeconds") or 0,
                "thumbnail": f"https://img.youtube.com/vi/{v.get('videoId')}/mqdefault.jpg"
            })

        if results:
            return {"results": results, "source": base}

    raise HTTPException(status_code=503, detail="Search unavailable")

# ===============================
# Audio Stream URL API
# ===============================
@app.get("/api/streamurl")
def api_streamurl(video_id: str):
    # 1. HLS (m3u8) を優先試行（ストリーミングが安定するため）
    try:
        data = try_json(f"{HLS_API_BASE_URL}{video_id}")
        if data:
            m3u8s = [f for f in data.get("m3u8_formats", []) if f.get("url")]
            if m3u8s:
                # 解像度が高い（＝音質が良い可能性が高い）ものをソート
                best = sorted(m3u8s, key=lambda f: int((f.get("resolution") or "0x0").split("x")[-1]), reverse=True)[0]
                return RedirectResponse(best["url"])
    except:
        pass

    # 2. Invidious Adaptive Formats (音声のみ) を試行
    random.shuffle(VIDEO_APIS)
    for base in VIDEO_APIS:
        data = try_json(f"{base}/api/v1/videos/{video_id}")
        if not data:
            continue

        # 音声のみ(audio/)のストリームを抽出
        audio_streams = [
            f for f in data.get("adaptiveFormats", [])
            if "audio/" in f.get("type", "")
        ]
        
        if audio_streams:
            # 最もビットレートが高いものを選択
            best_audio = sorted(audio_streams, key=lambda x: x.get("bitrate", 0), reverse=True)[0]
            return RedirectResponse(best_audio["url"])

        # 3. 通常のビデオストリーム（音を含む）を試行
        for f in data.get("formatStreams", []):
            if f.get("url"):
                return RedirectResponse(f["url"])

    # 4. 外部の変換プロキシを最終手段として使用
    data = try_json(f"{STREAM_YTDL_API_BASE_URL}{video_id}")
    if data:
        # itag 140 (m4a音声) または 18 (360p mp4) を探す
        for f in data.get("formats", []):
            if f.get("itag") in ["140", "18"] and f.get("url"):
                return RedirectResponse(f["url"])

    raise HTTPException(status_code=503, detail="Stream unavailable")

from fastapi.responses import StreamingResponse

# ===============================
# Download API (音声ファイルとして取得)
# ===============================
@app.get("/api/download")
def api_download(video_id: str, title: str = "track"):
    """
    ストリームURLを取得し、サーバー側で中継(Proxy)して
    ブラウザに「ファイル」としてダウンロードさせます。
    """
    # 1. まず再生用と同じロジックでストリームURLを取得
    # (既存の api_streamurl のロジックを流用するか、直接リダイレクト先を取得)
    # ここでは簡易的に、この関数内でストリームURLを特定します。
    
    stream_url = None
    
    # 既存のロジックでURLを探す (簡略化版)
    # 本来は api_streamurl の中身を共通関数化するのが綺麗です
    try:
        # HLSやInvidiousからURLを取得する処理（中身は api_streamurl と同様）
        # ... (中略) ... 
        # ここでは例として外部プロキシから取得を試みる例
        data = try_json(f"{STREAM_YTDL_API_BASE_URL}{video_id}")
        if data:
            for f in data.get("formats", []):
                if f.get("itag") in ["140", "18"] and f.get("url"):
                    stream_url = f["url"]
                    break
    except:
        pass

    if not stream_url:
        raise HTTPException(status_code=503, detail="Download source not found")

    # 2. ストリームをサーバー側でストリーミング中継する
    def iterfile():
        with requests.get(stream_url, stream=True) as r:
            for chunk in r.iter_content(chunk_size=1024*1024):
                if chunk:
                    yield chunk

    # 3. レスポンスヘッダーにファイル名を指定して返す
    # 拡張子はとりあえず .mp3 または .m4a
    safe_title = "".join([c for c in title if c.isalnum() or c in "._- "]).strip()
    headers = {
        'Content-Disposition': f'attachment; filename="{safe_title}.mp3"'
    }
    
    return StreamingResponse(iterfile(), media_type="audio/mpeg", headers=headers)
