# ================================
# 🚀 IMPORTS & FLASK SETUP
# ================================
from flask import Flask, request, render_template, jsonify
import yt_dlp
import re
import threading
import os
from pathlib import Path

app = Flask(__name__)
# ================================
# 📦 GLOBAL PROGRESS STATE
# ================================
progress_data = {
    "status": "",
    "progress": "0%",
    "title": "",
    "current": 0,
    "total": 0,
    "overall_percent": 0.0
}

def strip_ansi(text):
    return re.sub(r'\x1b\[[0-9;]*m', '', text)

def progress_hook(d):
    percent = 0.0
    if d['status'] == 'downloading':
        raw = strip_ansi(d.get('_percent_str', '0%')).strip()
        try:
            percent = float(raw.replace('%', '').strip())
        except Exception:
            percent = 0.0
        title = d.get('info_dict', {}).get('title', '')
        # Calculate overall percent
        current = progress_data.get("current", 0)
        total = progress_data.get("total", 1)
        overall = ((current + percent / 100.0) / total) * 100 if total else percent
        progress_data.update({
            "status": "downloading",
            "progress": raw,
            "title": title,
            "overall_percent": round(overall, 2)
        })
    elif d['status'] == 'finished':
        progress_data["current"] += 1
        progress_data["status"] = "finished"
        progress_data["progress"] = "100%"
        # When a video finishes, set overall_percent to next step
        current = progress_data.get("current", 0)
        total = progress_data.get("total", 1)
        overall = (current / total) * 100 if total else 100
        progress_data["overall_percent"] = round(overall, 2)

cancel_flag = {"cancel": False}
download_thread = {"thread": None}

def start_download(url, download_type, quality):
    cancel_flag["cancel"] = False  # Reset cancel flag

    def wrapped_hook(d):
        if cancel_flag["cancel"]:
            raise Exception("Download canceled by user.")
        progress_hook(d)

    # Enhanced yt-dlp options to bypass YouTube restrictions
    ydl_base_opts = {
        'progress_hooks': [wrapped_hook],
        'noplaylist': False,
        'outtmpl': str(Path.home() / "Music" / "YT-Downloader" / "%(title).100s.%(ext)s"),
        'quiet': False,
        'no_warnings': False,
        'ignoreerrors': True,
        'extract_flat': False,
        
        # Critical options for YouTube
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'web'],
                'player_skip': ['configs', 'webpage']
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Accept-Charset': 'ISO-8859-1,utf-8;q=0.7,*;q=0.7',
        },
        
        # Enhanced retry and fragment handling
        'retries': 20,
        'fragment_retries': 20,
        'skip_unavailable_fragments': True,
        'continue_dl': True,
        'keep_fragments': True,
        
        # Throttle to avoid detection
        'throttled_rate': '1M',
        
        # Force IPv4 to avoid network issues
        'source_address': '0.0.0.0',
        
        # Better format selection
        'format_sort': ['res:720', 'res:480', 'res:360', 'res:1080'],
        'format_selection': 'best[height<=?1080]',
    }

    if download_type == "audio":
        ydl_opts = {
            **ydl_base_opts,
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': quality
            }]
        }
    else:
        # Simple and reliable format selection
        ydl_opts = {
            **ydl_base_opts,
            'format': f'best[height<={quality}]/best',
        }

    try:
        output_dir = Path.home() / "Music" / "YT-Downloader"
        output_dir.mkdir(parents=True, exist_ok=True)
        
        print(f"🎯 Starting download: {url}")
        print(f"📦 Type: {download_type}, Quality: {quality}")
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            
        progress_data["status"] = "finished"
        print("✅ Download completed successfully")
        
    except Exception as e:
        progress_data["status"] = "error"
        progress_data["progress"] = f"❌ Error: {str(e)}"
        print(f"❌ Download error: {e}")

# ================================
# 🌐 ROUTE: Home Page
# ================================
@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

# ================================
# 🔽 ROUTE: Download Handler
# ================================
@app.route("/download", methods=["POST"])
def download():
    # 🔁 Reset state
    progress_data.update({
        "status": "downloading",
        "progress": "0%",
        "title": "",
        "current": 0,
        "total": 0,
        "overall_percent": 0.0
    })
    
    # 📝 Get form data
    url = request.form.get("url")
    download_type = request.form.get("download_type")
    quality = request.form.get("quality")

    if not url:
        return "Missing URL", 400

    # 🧠 Pre-fetch metadata with error handling
    try:
        with yt_dlp.YoutubeDL({
            'quiet': False,
            'ignoreerrors': True,
            'extract_flat': False,
            'no_warnings': False,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
        }) as ydl:
            info = ydl.extract_info(url, download=False)
            
            # Fix for NoneType iteration error
            if info is None:
                progress_data["status"] = "error"
                progress_data["progress"] = "❌ Failed to extract video info"
                return jsonify(progress_data), 400
                
            if 'entries' in info:
                # It's a playlist - safely handle entries
                entries = info['entries']
                if entries is None:
                    progress_data["total"] = 1
                else:
                    # Filter out None entries safely
                    valid_entries = [e for e in entries if e is not None]
                    progress_data["total"] = len(valid_entries)
                    print(f"📋 Playlist detected: {len(valid_entries)} videos")
            else:
                # Single video
                progress_data["total"] = 1
                print(f"🎬 Single video: {info.get('title', 'Unknown')}")
                
    except Exception as e:
        progress_data["status"] = "error"
        progress_data["progress"] = f"❌ Info fetch failed: {e}"
        print(f"❌ Metadata extraction failed: {e}")
        return jsonify(progress_data), 400

    # Start download in background
    try:
        t = threading.Thread(target=start_download, args=(url, download_type, quality), daemon=True)
        download_thread["thread"] = t
        t.start()
        return "Download started", 200
    except Exception as e:
        progress_data["status"] = "error"
        progress_data["progress"] = f"❌ Failed to start download: {e}"
        return jsonify(progress_data), 500

# ================================
# 📊 ROUTE: Progress Polling API
# ================================
@app.route("/progress", methods=["GET"])
def progress():
    return jsonify(progress_data)

@app.route("/cancel", methods=["POST"])
def cancel():
    cancel_flag["cancel"] = True
    progress_data["status"] = "error"
    progress_data["progress"] = "❌ Canceled by user"
    return "Canceled"

# ===============================
# 🏁 RUN FLASK APP
# ================================
if __name__ == "__main__":
    print("🚀 Starting YouTube Downloader...")
    print("📁 Downloads will be saved to:", Path.home() / "Music" / "YT-Downloader")
    print("🔧 Enhanced configuration for YouTube compatibility")
    app.run(debug=True, host='0.0.0.0', port=5000)
