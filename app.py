# ================================
# 🚀 IMPORTS & FLASK SETUP
# ================================
from flask import Flask, request, render_template, jsonify, session, send_from_directory, send_file
import yt_dlp
import re
import threading
import os
from pathlib import Path
import json
import uuid
from datetime import timedelta
import mimetypes
from urllib.parse import quote

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=1)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = True  # Railway uses HTTPS

# ================================
# 📁 MULTI-DEVICE STORAGE CONFIGURATION
# ================================

# Get port from Railway environment variable or default to 5000
PORT = int(os.environ.get('PORT', 5000))

# Determine storage path based on environment
if os.environ.get('RAILWAY_ENVIRONMENT'):
    # Railway production - use ephemeral storage
    BASE_STORAGE_PATH = Path('/tmp/yt-downloader')
    print("🚆 Running on Railway - using ephemeral storage")
else:
    # Local development - use persistent storage
    BASE_STORAGE_PATH = Path.home() / 'yt-downloader' / 'downloads'
    print("💻 Running locally - using persistent storage")

# Create storage structure
DOWNLOAD_PATH = BASE_STORAGE_PATH / 'downloads'
CACHE_PATH = BASE_STORAGE_PATH / 'cache'
LOG_PATH = BASE_STORAGE_PATH / 'logs'

for path in [DOWNLOAD_PATH, CACHE_PATH, LOG_PATH]:
    path.mkdir(parents=True, exist_ok=True)

print(f"📁 Storage path: {BASE_STORAGE_PATH}")
print(f"📂 Downloads: {DOWNLOAD_PATH}")
print(f"📁 Cache: {CACHE_PATH}")
print(f"📝 Logs: {LOG_PATH}")

# ================================
# 🔧 DEV TOOLS CONFIGURATION
# ================================
DEV_MODE = os.environ.get('DEV_MODE', 'False').lower() == 'true'
dev_logs = []

def dev_log(message, level="INFO"):
    """Log message for dev tools"""
    if DEV_MODE:
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        log_entry = f"[{timestamp}] [{level}] {message}"
        dev_logs.append(log_entry)
        # Keep only last 1000 log entries
        if len(dev_logs) > 1000:
            dev_logs.pop(0)
        print(log_entry)

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
            "downloaded_videos": [],
            "local_files": [],  # Track files in local storage
            "downloads_history": []  # Track download history
        }
        cancel_flags[session['user_id']] = {"cancel": False}
        dev_log(f"New session created: {session['user_id'][:8]}", "SESSION")
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
        "downloaded_videos": [],
        "local_files": [],
        "downloads_history": []
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
        "downloaded_videos": user_sessions.get(user_id, {}).get("downloaded_videos", []),
        "local_files": scan_local_files(user_id),
        "downloads_history": user_sessions.get(user_id, {}).get("downloads_history", [])
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

def scan_local_files(user_id):
    """Scan DOWNLOAD_PATH for files belonging to this session/user"""
    try:
        user_files = []
        # Look for files with session ID in name or all files if we can't distinguish
        for file_path in DOWNLOAD_PATH.glob("*"):
            if file_path.is_file():
                # Get file info
                stat = file_path.stat()
                file_info = {
                    "name": file_path.name,
                    "size": stat.st_size,
                    "size_formatted": format_file_size(stat.st_size),
                    "modified": stat.st_mtime,
                    "modified_formatted": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "path": str(file_path),
                    "url": f"/downloads/{quote(file_path.name)}"
                }
                user_files.append(file_info)
        
        dev_log(f"Scanned {len(user_files)} files in local storage for session {user_id[:8]}", "STORAGE")
        return sorted(user_files, key=lambda x: x["modified"], reverse=True)
    except Exception as e:
        dev_log(f"Error scanning local files: {e}", "ERROR")
        return []

def format_file_size(size_bytes):
    """Format file size in human-readable format"""
    if size_bytes == 0:
        return "0B"
    size_names = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes /= 1024.0
        i += 1
    return f"{size_bytes:.2f} {size_names[i]}"

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
        
        # Scan for new files in local storage
        local_files = scan_local_files(user_id)
        
        update_progress_data({
            "current": new_current,
            "status": "finished" if is_complete else "downloading",
            "progress": "100%",
            "playlist_info": playlist_info,
            "downloaded_videos": downloaded_videos,
            "current_download": "" if is_complete else title,
            "overall_percent": round(overall, 2),
            "local_files": local_files
        }, user_id)

def start_download(url, download_type, quality, user_id):
    """Start download in background thread"""
    cancel_flag = get_cancel_flag(user_id)
    cancel_flag["cancel"] = False
    
    def wrapped_hook(d):
        progress_hook(d, user_id, cancel_flag)
    
    # Check if we have pre-loaded playlist data
    progress_data = get_progress_data(user_id)
    use_preview = (progress_data.get("preview_loaded") and 
                  progress_data.get("preview_url") == url)
    
    # 🚀 yt-dlp options
    ydl_base_opts = {
        'progress_hooks': [wrapped_hook],
        'noplaylist': False,
        'outtmpl': str(DOWNLOAD_PATH / "%(title).100s.%(ext)s"),
        'quiet': False,
        'no_warnings': False,
        'ignoreerrors': True,
        'extract_flat': use_preview,
        'lazy_playlist': True,
        'concurrent_fragment_downloads': 4,
        'http_chunk_size': 1048576,
        'continuedl': True,
        'noprogress': False,
        'sleep_interval': 1,
        'max_sleep_interval': 5,
        'retry_sleep': 1,
        'socket_timeout': 30,
        'source_address': '0.0.0.0',
        'writethumbnail': False,
        'writeinfojson': False,
        'writesubtitles': False,
        'writeautomaticsub': False,
        'getcomments': False,
        'cachedir': str(CACHE_PATH),  # Use cache directory
        'no_cache_dir': False,
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
        'retries': 3,
        'fragment_retries': 3,
        'skip_unavailable_fragments': True,
        'continue_dl': True,
        'nocheckcertificate': True,
        'proxy': '',
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
        dev_log(f"Starting download: {url}", "DOWNLOAD")
        dev_log(f"Type: {download_type}, Quality: {quality}", "DOWNLOAD")
        dev_log(f"Storage path: {DOWNLOAD_PATH}", "STORAGE")
        
        # If we already have playlist info from preview, use it
        if use_preview:
            update_progress_data({
                "status": "starting",
                "current": 0
            }, user_id)
            dev_log(f"Using pre-loaded playlist: {progress_data.get('total', 0)} videos", "DOWNLOAD")
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
                'cachedir': str(CACHE_PATH),
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
                    dev_log(f"Slow playlist loaded: {total_count} videos", "DOWNLOAD")
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
                    dev_log(f"Single video loaded (slow)", "DOWNLOAD")
        
        # 🚀 START ACTUAL DOWNLOAD
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            
        if not cancel_flag["cancel"]:
            # Add to download history
            progress_data = get_progress_data(user_id)
            history_entry = {
                "timestamp": datetime.now().isoformat(),
                "url": url,
                "type": download_type,
                "quality": quality,
                "title": progress_data.get("title", ""),
                "total_videos": progress_data.get("total", 0)
            }
            
            downloads_history = progress_data.get("downloads_history", [])
            downloads_history.append(history_entry)
            
            update_progress_data({
                "status": "finished",
                "overall_percent": 100.0,
                "current_download": "",
                "downloads_history": downloads_history[-20:]  # Keep last 20 entries
            }, user_id)
            dev_log(f"Download completed successfully", "DOWNLOAD")
        
    except Exception as e:
        if cancel_flag["cancel"]:
            update_progress_data({
                "status": "canceled",
                "progress": "❌ Download canceled"
            }, user_id)
            dev_log(f"Download canceled by user", "DOWNLOAD")
        else:
            update_progress_data({
                "status": "error",
                "progress": f"❌ Error: {str(e)}"
            }, user_id)
            dev_log(f"Download error: {e}", "ERROR")

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
    
    # Reset state but preserve downloaded videos and history
    current_data = get_progress_data(user_id)
    downloaded_videos = current_data.get("downloaded_videos", [])
    downloads_history = current_data.get("downloads_history", [])
    local_files = scan_local_files(user_id)
    
    reset_progress_data(user_id)
    update_progress_data({
        "downloaded_videos": downloaded_videos,
        "downloads_history": downloads_history,
        "local_files": local_files
    }, user_id)
    
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
    # Always update local files when checking progress
    progress_data = get_progress_data(user_id)
    progress_data["local_files"] = scan_local_files(user_id)
    return jsonify(progress_data)

@app.route("/cancel", methods=["POST"])
def cancel():
    user_id = get_session_id()
    cancel_flag = get_cancel_flag(user_id)
    cancel_flag["cancel"] = True
    update_progress_data({
        "status": "canceled",
        "progress": "❌ Download canceled"
    }, user_id)
    dev_log(f"Cancel request received", "DOWNLOAD")
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
# 📂 LOCAL STORAGE ROUTES
# ================================
@app.route("/downloads/<filename>")
def download_file(filename):
    """Serve downloaded files"""
    try:
        file_path = DOWNLOAD_PATH / filename
        if not file_path.exists():
            return "File not found", 404
        
        # Get file info for logging
        stat = file_path.stat()
        dev_log(f"Serving file: {filename} ({format_file_size(stat.st_size)})", "STORAGE")
        
        return send_file(
            file_path,
            as_attachment=True,
            download_name=filename,
            mimetype=mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        )
    except Exception as e:
        dev_log(f"Error serving file {filename}: {e}", "ERROR")
        return str(e), 500

@app.route("/storage/files")
def list_files():
    """List all files in local storage"""
    user_id = get_session_id()
    files = scan_local_files(user_id)
    return jsonify({"files": files})

@app.route("/storage/delete/<filename>", methods=["DELETE"])
def delete_file(filename):
    """Delete a file from local storage"""
    try:
        file_path = DOWNLOAD_PATH / filename
        if file_path.exists():
            file_path.unlink()
            dev_log(f"Deleted file: {filename}", "STORAGE")
            
            # Update session data
            user_id = get_session_id()
            update_progress_data({
                "local_files": scan_local_files(user_id)
            }, user_id)
            
            return jsonify({"status": "success", "message": f"Deleted {filename}"})
        else:
            return jsonify({"status": "error", "message": "File not found"}), 404
    except Exception as e:
        dev_log(f"Error deleting file {filename}: {e}", "ERROR")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/storage/clear", methods=["POST"])
def clear_storage():
    """Clear all files from local storage"""
    try:
        deleted_count = 0
        for file_path in DOWNLOAD_PATH.glob("*"):
            if file_path.is_file():
                file_path.unlink()
                deleted_count += 1
        
        dev_log(f"Cleared {deleted_count} files from storage", "STORAGE")
        
        # Update session data
        user_id = get_session_id()
        update_progress_data({
            "local_files": []
        }, user_id)
        
        return jsonify({"status": "success", "message": f"Cleared {deleted_count} files"})
    except Exception as e:
        dev_log(f"Error clearing storage: {e}", "ERROR")
        return jsonify({"status": "error", "message": str(e)}), 500

# ================================
# 🔧 DEV TOOLS ROUTES
# ================================
@app.route("/dev/logs")
def get_dev_logs():
    """Get development logs"""
    if not DEV_MODE:
        return jsonify({"error": "Dev mode disabled"}), 403
    return jsonify({"logs": dev_logs[-100:]})  # Return last 100 logs

@app.route("/dev/storage/info")
def get_storage_info():
    """Get storage information"""
    try:
        total_size = 0
        file_count = 0
        
        for file_path in DOWNLOAD_PATH.glob("*"):
            if file_path.is_file():
                total_size += file_path.stat().st_size
                file_count += 1
        
        return jsonify({
            "path": str(DOWNLOAD_PATH),
            "file_count": file_count,
            "total_size": total_size,
            "total_size_formatted": format_file_size(total_size),
            "free_space": get_free_space(),
            "is_railway": bool(os.environ.get('RAILWAY_ENVIRONMENT'))
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/dev/session/info")
def get_session_info():
    """Get current session information"""
    user_id = get_session_id()
    progress_data = get_progress_data(user_id)
    
    return jsonify({
        "session_id": user_id,
        "session_data_keys": list(progress_data.keys()),
        "cancel_flag": get_cancel_flag(user_id),
        "active_sessions": len(user_sessions)
    })

def get_free_space():
    """Get free space in storage directory"""
    try:
        if hasattr(os, 'statvfs'):
            stat = os.statvfs(str(DOWNLOAD_PATH))
            free = stat.f_bavail * stat.f_frsize
            return format_file_size(free)
    except:
        pass
    return "Unknown"

# ================================
# 🗑️ CLEANUP ROUTE (For Railway ephemeral storage)
# ================================
@app.route("/cleanup", methods=["POST"])
def cleanup():
    """Clean up downloaded files"""
    try:
        deleted_count = 0
        for file_path in DOWNLOAD_PATH.glob("*"):
            try:
                if file_path.is_file():
                    file_path.unlink()
                    deleted_count += 1
                    dev_log(f"Cleaned up: {file_path.name}", "STORAGE")
            except Exception as e:
                dev_log(f"Failed to clean {file_path}: {e}", "ERROR")
        
        # Update all sessions
        for user_id in user_sessions:
            user_sessions[user_id]["local_files"] = []
        
        return jsonify({
            "status": "success", 
            "message": f"Cleaned {deleted_count} files",
            "deleted_count": deleted_count
        })
    except Exception as e:
        dev_log(f"Cleanup error: {e}", "ERROR")
        return jsonify({"status": "error", "message": str(e)}), 500

# ================================
# 🔍 HEALTH CHECK (Required by Railway)
# ================================
@app.route("/health")
def health_check():
    return jsonify({
        "status": "healthy", 
        "service": "yt-downloader",
        "storage_path": str(DOWNLOAD_PATH),
        "file_count": len(list(DOWNLOAD_PATH.glob("*")))
    })

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
            'extract_flat': True,
            'lazy_playlist': True,
            'ignoreerrors': True,
            'extract_flat': 'in_playlist',
            'nocheckcertificate': True,
            'proxy': '',
            'cachedir': str(CACHE_PATH),
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
                
                dev_log(f"Fast playlist loaded: {total_count} videos", "PREVIEW")
                
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
                dev_log(f"Single video loaded (fast)", "PREVIEW")
            
            return {
                "total": total_count,
                "playlist_info": playlist_info,
                "title": info.get('title', 'Playlist'),
                "original_url": url
            }
            
    except Exception as e:
        dev_log(f"Fast playlist error: {e}", "ERROR")
        return None

# ================================
# 🏁 RUN FLASK APP
# ================================
if __name__ == "__main__":
    print("🚀 Starting YouTube Downloader...")
    print(f"📁 Storage: {BASE_STORAGE_PATH}")
    print(f"🔧 Dev Mode: {DEV_MODE}")
    print(f"🌐 Port: {PORT}")
    print("📱 Multi-device features enabled:")
    print("   - Local storage scanning")
    print("   - File management API")
    print("   - Download history")
    print("   - Dev tools endpoints")
    print("🚀 Ready!")
    
    app.run(
        debug=os.environ.get('DEBUG', 'False').lower() == 'true',
        host='0.0.0.0',
        port=PORT
    )
