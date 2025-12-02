# ================================
# 🚀 IMPORTS & FLASK SETUP
# ================================
from flask import Flask, request, render_template, jsonify, session, send_from_directory
import yt_dlp
import re
import threading
import os
from pathlib import Path
import json
import uuid
from datetime import timedelta

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=1)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = True  # Railway uses HTTPS

# ================================
# 📁 RAILWAY-SPECIFIC CONFIGURATION
# ================================

# Get port from Railway environment variable or default to 5000
PORT = int(os.environ.get('PORT', 5000))

# Create downloads directory (Railway provides ephemeral storage)
DOWNLOAD_PATH = Path('/tmp/yt-downloader')  # Use /tmp for Railway ephemeral storage
DOWNLOAD_PATH.mkdir(parents=True, exist_ok=True)

# ================================
# 📦 SESSION-BASED PROGRESS STATE
# ================================
user_sessions = {}
cancel_flags = {}

def get_session_id():
    """Get or create session ID for the current user"""
    if 'user_id' not in session:
        session['user_id'] = str(uuid.uuid4())
        session.permanent = True
        # Initialize session data
        user_sessions[session['user_id']] = {
            "status": "ready",
            "progress": "0%",
            "title": "",
            "current": 0,
            "total": 0,
            "overall_percent": 0.0,
            "playlist_info": [],
            "current_download": "",
            "downloaded_videos": []
        }
        cancel_flags[session['user_id']] = {"cancel": False}
    return session['user_id']

def get_progress_data(user_id=None):
    """Get progress data for current session"""
    if user_id is None:
        user_id = get_session_id()
    return user_sessions.get(user_id, {
        "status": "ready",
        "progress": "0%",
        "title": "",
        "current": 0,
        "total": 0,
        "overall_percent": 0.0,
        "playlist_info": [],
        "current_download": "",
        "downloaded_videos": []
    })

def update_progress_data(updates, user_id=None):
    """Update progress data for current session"""
    if user_id is None:
        user_id = get_session_id()
    if user_id not in user_sessions:
        user_sessions[user_id] = get_progress_data(user_id)
    user_sessions[user_id].update(updates)

def reset_progress_data(user_id=None):
    """Reset progress data for current session"""
    if user_id is None:
        user_id = get_session_id()
    user_sessions[user_id] = {
        "status": "ready",
        "progress": "0%",
        "title": "",
        "current": 0,
        "total": 0,
        "overall_percent": 0.0,
        "playlist_info": [],
        "current_download": "",
        "downloaded_videos": user_sessions.get(user_id, {}).get("downloaded_videos", [])
    }

def get_cancel_flag(user_id=None):
    """Get cancel flag for current session"""
    if user_id is None:
        user_id = get_session_id()
    if user_id not in cancel_flags:
        cancel_flags[user_id] = {"cancel": False}
    return cancel_flags[user_id]

def strip_ansi(text):
    """Remove ANSI escape codes from text"""
    return re.sub(r'\x1b\[[0-9;]*m', '', text)

def progress_hook(d, user_id, cancel_flag):
    if cancel_flag["cancel"]:
        raise Exception("Download canceled by user")
    
    progress_data = get_progress_data(user_id)
    
    if d['status'] == 'downloading':
        raw = strip_ansi(d.get('_percent_str', '0%')).strip()
        try:
            percent = float(raw.replace('%', '').strip())
        except Exception:
            percent = 0.0
        info_dict = d.get('info_dict') or {}
        title = info_dict.get('title', '')
        
        current = progress_data.get("current", 0)
        total = progress_data.get("total", 1)
        
        if total == 1:
            overall = percent
        else:
            completed_portion = current * 100.0
            current_portion = percent
            overall = (completed_portion + current_portion) / total
        
        update_progress_data({
            "status": "downloading",
            "progress": raw,
            "title": title,
            "current_download": title,
            "overall_percent": round(overall, 2)
        }, user_id)
        
    elif d['status'] == 'finished':
        info_dict = d.get('info_dict') or {}
        title = info_dict.get('title', '')
        current_index = progress_data.get("current", 0)
        
        downloaded_videos = progress_data.get("downloaded_videos", [])
        if title and title not in downloaded_videos:
            downloaded_videos.append(title)
        
        playlist_info = progress_data.get("playlist_info", [])
        if current_index < len(playlist_info):
            playlist_info[current_index]["downloaded"] = True
        
        new_current = current_index + 1
        total = progress_data.get("total", 1)
        is_complete = new_current >= total
        
        if is_complete:
            overall = 100.0
        else:
            overall = (new_current / total) * 100
        
        update_progress_data({
            "current": new_current,
            "status": "finished" if is_complete else "downloading",
            "progress": "100%",
            "playlist_info": playlist_info,
            "downloaded_videos": downloaded_videos,
            "current_download": "" if is_complete else title,
            "overall_percent": round(overall, 2)
        }, user_id)

def start_download(url, download_type, quality, user_id):
    """Start download in background thread - RAILWAY EDITION"""
    cancel_flag = get_cancel_flag(user_id)
    cancel_flag["cancel"] = False
    
    def wrapped_hook(d):
        progress_hook(d, user_id, cancel_flag)
    
    # Check if we have pre-loaded playlist data
    progress_data = get_progress_data(user_id)
    use_preview = (progress_data.get("preview_loaded") and 
                  progress_data.get("preview_url") == url)
    
    # 🚀 yt-dlp options (Railway optimized)
    ydl_base_opts = {
        'progress_hooks': [wrapped_hook],
        'noplaylist': False,
        'outtmpl': str(DOWNLOAD_PATH / "%(title).100s.%(ext)s"),  # Use Railway's /tmp
        'quiet': False,
        'no_warnings': False,
        'ignoreerrors': True,
        'extract_flat': use_preview,
        'lazy_playlist': True,
        'concurrent_fragment_downloads': 4,  # Reduced for Railway limits
        'http_chunk_size': 1048576,  # 1MB chunks for stability
        'continuedl': True,
        'noprogress': False,
        'sleep_interval': 1,  # Added delay to prevent rate limiting
        'max_sleep_interval': 5,
        'retry_sleep': 1,
        'socket_timeout': 30,
        'source_address': '0.0.0.0',
        'writethumbnail': False,
        'writeinfojson': False,
        'writesubtitles': False,
        'writeautomaticsub': False,
        'getcomments': False,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'web'],
                'player_skip': ['configs', 'webpage'],
                'throttled_rate': None,
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate',
        },
        'retries': 3,  # Reduced retries
        'fragment_retries': 3,
        'skip_unavailable_fragments': True,
        'continue_dl': True,
        'cachedir': False,
        'no_cache_dir': True,
        'nocheckcertificate': True,  # Important for Railway
        'proxy': '',  # Explicitly disable proxy
    }

    if download_type == "audio":
        ydl_opts = {
            **ydl_base_opts,
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': quality
            }],
            'extractaudio': True,
            'audioformat': 'mp3',
        }
    else:
        quality_map = {
            '360': 'best[height<=360]',
            '720': 'best[height<=720]', 
            '1080': 'best[height<=1080]'
        }
        format_selection = quality_map.get(quality, 'best[height<=720]')
        ydl_opts = {
            **ydl_base_opts,
            'format': format_selection,
            'merge_output_format': 'mp4',
        }

    try:
        print(f"🎯 [{user_id[:8]}] Starting download on Railway: {url}")
        print(f"📦 [{user_id[:8]}] Type: {download_type}, Quality: {quality}")
        print(f"📁 [{user_id[:8]}] Download path: {DOWNLOAD_PATH}")
        
        # If we already have playlist info from preview, use it
        if use_preview:
            update_progress_data({
                "status": "starting",
                "current": 0
            }, user_id)
            print(f"📋 [{user_id[:8]}] Using pre-loaded playlist: {progress_data.get('total', 0)} videos")
        else:
            # Fall back to original slow method
            update_progress_data({"status": "processing"}, user_id)
            with yt_dlp.YoutubeDL({
                'quiet': True,
                'no_warnings': True,
                'extract_flat': False,
                'lazy_playlist': False,
                'ignoreerrors': True,
                'nocheckcertificate': True,
                'proxy': '',
            }) as ydl:
                basic_info = ydl.extract_info(url, download=False)
                
                if basic_info and 'entries' in basic_info:
                    entries = [e for e in basic_info.get('entries', []) if e is not None]
                    total_count = len(entries)
                    
                    playlist_info = []
                    for i, entry in enumerate(entries):
                        if entry is None:
                            continue
                        playlist_info.append({
                            "title": entry.get('title', f'Video {i+1}') if isinstance(entry, dict) else f'Video {i+1}',
                            "duration": entry.get('duration_string', 'Unknown') if isinstance(entry, dict) else 'Unknown',
                            "downloaded": False
                        })
                    
                    update_progress_data({
                        "total": total_count,
                        "playlist_info": playlist_info,
                        "status": "starting"
                    }, user_id)
                    print(f"📋 [{user_id[:8]}] Slow playlist loaded: {total_count} videos")
                else:
                    if basic_info is None:
                        raise Exception("Could not retrieve video info")
                    playlist_info = [{
                        "title": basic_info.get('title', 'Video'),
                        "duration": basic_info.get('duration_string', 'Unknown'),
                        "downloaded": False
                    }]
                    update_progress_data({
                        "total": 1,
                        "playlist_info": playlist_info,
                        "status": "starting"
                    }, user_id)
                    print(f"🎬 [{user_id[:8]}] Single video loaded (slow)")
        
        # 🚀 START ACTUAL DOWNLOAD
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            
        if not cancel_flag["cancel"]:
            update_progress_data({
                "status": "finished",
                "overall_percent": 100.0,
                "current_download": ""
            }, user_id)
            print(f"✅ [{user_id[:8]}] Download completed successfully")
        
    except Exception as e:
        if cancel_flag["cancel"]:
            update_progress_data({
                "status": "canceled",
                "progress": "❌ Download canceled"
            }, user_id)
            print(f"⏹️ [{user_id[:8]}] Download canceled by user")
        else:
            update_progress_data({
                "status": "error",
                "progress": f"❌ Error: {str(e)}"
            }, user_id)
            print(f"❌ [{user_id[:8]}] Download error: {e}")

# ================================
# 🌐 ROUTES
# ================================
@app.route("/")
def index():
    get_session_id()
    reset_progress_data()
    return render_template("index.html")

@app.route("/download", methods=["POST"])
def download():
    user_id = get_session_id()
    
    # Reset state but preserve downloaded videos
    current_data = get_progress_data(user_id)
    downloaded_videos = current_data.get("downloaded_videos", [])
    
    reset_progress_data(user_id)
    update_progress_data({"downloaded_videos": downloaded_videos}, user_id)
    
    # Get form data
    url = request.form.get("url")
    download_type = request.form.get("download_type")
    quality = request.form.get("quality")

    if not url:
        return "Missing URL", 400

    # Quick initial response
    update_progress_data({"status": "processing"}, user_id)
    
    # Start download immediately in background thread
    try:
        t = threading.Thread(
            target=start_download, 
            args=(url, download_type, quality, user_id), 
            daemon=True
        )
        t.start()
        
        return "Download started", 200
        
    except Exception as e:
        update_progress_data({
            "status": "error", 
            "progress": f"❌ Failed to start: {e}"
        }, user_id)
        return jsonify(get_progress_data(user_id)), 500

@app.route("/progress")
def progress():
    user_id = get_session_id()
    return jsonify(get_progress_data(user_id))

@app.route("/cancel", methods=["POST"])
def cancel():
    user_id = get_session_id()
    cancel_flag = get_cancel_flag(user_id)
    cancel_flag["cancel"] = True
    update_progress_data({
        "status": "canceled",
        "progress": "❌ Download canceled"
    }, user_id)
    print(f"⏹️ [{user_id[:8]}] Cancel request received")
    return "Canceled"

@app.route("/reset", methods=["POST"])
def reset():
    user_id = get_session_id()
    reset_progress_data(user_id)
    cancel_flag = get_cancel_flag(user_id)
    cancel_flag["cancel"] = False
    return "Progress reset"

@app.route("/new")
def new_session():
    if 'user_id' in session:
        user_id = session['user_id']
        if user_id in cancel_flags:
            del cancel_flags[user_id]
        if user_id in user_sessions:
            del user_sessions[user_id]
    
    session.clear()
    get_session_id()
    return render_template("index.html")

# ================================
# 🗑️ CLEANUP ROUTE (For Railway ephemeral storage)
# ================================
@app.route("/cleanup", methods=["POST"])
def cleanup():
    """Clean up downloaded files (useful for Railway's ephemeral storage)"""
    try:
        # Remove all files in download directory
        for file_path in DOWNLOAD_PATH.glob("*"):
            try:
                if file_path.is_file():
                    file_path.unlink()
                    print(f"Cleaned up: {file_path}")
            except Exception as e:
                print(f"Failed to clean {file_path}: {e}")
        
        return jsonify({"status": "success", "message": "Download directory cleaned"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ================================
# 🔍 HEALTH CHECK (Required by Railway)
# ================================
@app.route("/health")
def health_check():
    return jsonify({"status": "healthy", "service": "yt-downloader"})

# ================================
# 🚀 FAST PLAYLIST PREVIEW SUPPORT
# ================================

def get_fast_playlist_info(url, user_id):
    """Get playlist info instantly using flat playlist mode"""
    try:
        update_progress_data({"status": "processing"}, user_id)
        
        # 🚀 FAST PLAYLIST EXTRACTION
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,  # ✅ This is the magic option
            'lazy_playlist': True,
            'ignoreerrors': True,
            'extract_flat': 'in_playlist',
            'nocheckcertificate': True,
            'proxy': '',
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            if not info:
                return None
                
            playlist_info = []
            
            if 'entries' in info:
                # It's a playlist
                entries = [e for e in info.get('entries', []) if e is not None]
                total_count = len(entries)
                
                for i, entry in enumerate(entries):
                    if entry is None:
                        continue
                    playlist_info.append({
                        "title": entry.get('title', f'Video {i+1}'),
                        "duration": entry.get('duration_string', 'Unknown'),
                        "downloaded": False,
                        "url": entry.get('url', ''),
                        "id": entry.get('id', f'vid_{i}')
                    })
                
                print(f"🚀 [{user_id[:8]}] Fast playlist loaded: {total_count} videos")
                
            else:
                # Single video
                playlist_info = [{
                    "title": info.get('title', 'Video'),
                    "duration": info.get('duration_string', 'Unknown'),
                    "downloaded": False,
                    "url": url,
                    "id": info.get('id', 'single_video')
                }]
                total_count = 1
                print(f"🎬 [{user_id[:8]}] Single video loaded (fast)")
            
            return {
                "total": total_count,
                "playlist_info": playlist_info,
                "title": info.get('title', 'Playlist'),
                "original_url": url
            }
            
    except Exception as e:
        print(f"❌ [{user_id[:8]}] Fast playlist error: {e}")
        return None

# ================================
# 🏁 RUN FLASK APP FOR RAILWAY
# ================================
if __name__ == "__main__":
    print("🚀 Starting YouTube Downloader on Railway...")
    print(f"📁 Downloads will be saved to: {DOWNLOAD_PATH}")
    print(f"🌐 Server will run on port: {PORT}")
    print("🔧 Railway Optimizations:")
    print("   - Ephemeral storage at /tmp")
    print("   - Reduced concurrent downloads")
    print("   - Certificate checking disabled")
    print("   - Health check endpoint")
    print("   - Cleanup route for file management")
    print("🚀 Ready for Railway deployment!")
    
    app.run(
        debug=os.environ.get('DEBUG', 'False').lower() == 'true',
        host='0.0.0.0',
        port=PORT
    )
