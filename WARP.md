# WARP.md

This file provides guidance to WARP (warp.dev) when working with code in this repository.

## Project Overview

This is a Flask-based YouTube Downloader web application that allows users to download YouTube videos and audio in various qualities. The application uses yt-dlp for downloading content and provides real-time progress updates through AJAX polling.

## Core Architecture

### Flask Application (`app.py`)
- **Main Flask server**: Handles routing, download processing, and progress tracking
- **Global progress state**: Thread-safe progress tracking using a global dictionary
- **Background downloads**: Uses Python threading for non-blocking downloads
- **Progress hooks**: Custom progress callback system for yt-dlp integration

### Frontend (`templates/index.html`)
- **Single-page application**: Complete UI in one HTML file with embedded CSS/JavaScript
- **Real-time updates**: AJAX polling every 500ms to `/progress` endpoint for live progress
- **Dynamic quality options**: JavaScript filters quality options based on download type (video/audio)

### Key Components
- **Progress tracking**: Multi-level progress system (individual file + overall progress for playlists)
- **Download options**: Supports both video (360p, 720p, 1080p) and audio (160k, 256k, 320k) downloads
- **Output paths**: Audio downloads go to `~/Music/YT-Downloader/`, videos to `~/Music/`

## Common Development Commands

### Running the Application
```bash
# Start development server
python app.py
# Or with explicit Python version
python3 app.py
```

### Dependencies Installation
```bash
# Install required packages (create requirements.txt if needed)
pip install flask yt-dlp
# Or for development
pip install flask yt-dlp --user
```

### Testing the Application
```bash
# Test basic Flask routes
curl http://localhost:5000/
curl http://localhost:5000/progress

# Test download endpoint (replace with actual YouTube URL)
curl -X POST -d "url=https://youtube.com/watch?v=example&download_type=video&quality=720" http://localhost:5000/download
```

## Key Files and Directories

- `app.py` - Main Flask application with all backend logic
- `templates/index.html` - Complete frontend interface
- `static/` - Static assets (images, loading GIFs)
- `assets/` - Project assets (preview images)

## Development Notes

### Progress System Architecture
The application implements a sophisticated progress tracking system:
- Individual video progress via yt-dlp hooks
- Overall progress calculation for playlists
- Thread-safe global state management
- Real-time frontend updates via polling

### Download Flow
1. User submits form → POST to `/download`
2. Server pre-fetches metadata to determine total items
3. Background thread starts download with progress hooks
4. Frontend polls `/progress` endpoint for updates
5. Progress hooks update global state
6. Completion triggers user notification

### Threading Model
- Main Flask thread handles HTTP requests
- Background daemon threads handle downloads
- Global `progress_data` dictionary serves as IPC mechanism
- No synchronization primitives needed due to GIL and simple data structure

### yt-dlp Integration
- Custom progress hooks for real-time updates
- ANSI escape sequence stripping for clean progress display
- Configurable output templates and post-processing
- Support for both single videos and playlists