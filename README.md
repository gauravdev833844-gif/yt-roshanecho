# 🔴 24/7 Autonomous YouTube Live Stream Recorder (@Roshanecho)

Automated cloud recorder for **@Roshanecho** live streams on YouTube.
Handles both **public** and **members-only** streams, multi-broadcast link transitions, automatic network recovery, fragment stitching, and Gofile cloud relay delivery.

---

## ⚡ How It Works

1. **Autonomous Live Detection**: Periodically polls YouTube `/streams` with member authentication cookies. Catches members-only broadcasts, unlisted links, and public streams the moment they go live.
2. **Dynamic Multi-Broadcast Handling**: If the streamer switches from public to members-only, or creates a new broadcast link mid-session, the recorder automatically finalizes and merges the first broadcast, and instantly begins recording the new link without losing frames or corrupting files.
3. **Resilient Chunk Recording**: Uses `--hls-use-mpegts` and infinite retries with `yt-dlp` to absorb stream buffering, temporary disconnects, and packet drops.
4. **Lossless MKV Concatenation**: Automatically concatenates recovery pieces with FFmpeg without re-encoding quality loss.
5. **Direct Delivery**: Automatically uploads final merged recordings to Gofile.io with high-speed download links posted directly in the GitHub Actions run summary.

---

## 🚀 Usage

### 1. Cloud Execution (GitHub Actions)
- Runs automatically on a scheduled 4-hour cycle (`cron: '0 */4 * * *'`).
- Can be manually triggered at any time from the **Actions** tab with custom target channel or shift duration.

### 2. Local Execution (Windows PC)
Run `run_1_members_live.bat` or:
```bash
python 1_members_live_downloader.py
```
Or for non-interactive autonomous recording:
```bash
python recorder_roshanecho.py
```
