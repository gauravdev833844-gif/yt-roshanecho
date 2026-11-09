"""Shared, Windows-friendly helpers for the three YouTube tools.

The live recorder deliberately asks yt-dlp to keep retrying inside one
recording process.  This is what prevents a brief Wi-Fi or broadcaster drop
from becoming a pile of small video files.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
import http.cookiejar
import urllib.request
from datetime import datetime
from typing import Optional, Set, Tuple

# Best separate video + audio, falling back to the best already-combined feed.
BEST_FORMAT = "bv*+ba/b"
# Keep even a short, valid live recording.  The previous 1 MB threshold
# silently discarded brief streams and recordings stopped soon after start.
MIN_CHUNK_BYTES = 64 * 1024

# Detection is intentionally polite: quick immediately after launching the
# watcher, then a normal 20-second interval.  Recording itself retries
# indefinitely, so this only matters before a broadcast begins or after a
# real end.
STARTUP_WINDOW_SECONDS = 60
STARTUP_POLL_SECONDS = 5
NORMAL_POLL_SECONDS = 20
OFFLINE_GRACE_SECONDS = 180


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


def check_ytdlp() -> bool:
    try:
        subprocess.run(
            get_ytdlp_command() + ["--version"],
            check=True, capture_output=True, text=True,
        )
        return True
    except Exception:
        log("yt-dlp is not installed or is not working.")
        log("Run setup.bat first, then run this tool again.")
        return False


def get_ytdlp_command() -> list:
    """Prefer the yt-dlp.exe kept beside these scripts when it exists.

    That avoids a stale Python-installed copy behaving differently from the
    copy the user updates with ``yt-dlp.exe -U``.
    """
    bundled = os.path.join(os.path.dirname(os.path.abspath(__file__)), "yt-dlp.exe")
    if os.path.isfile(bundled):
        return [bundled]
    return [sys.executable, "-m", "yt_dlp"]


def check_ffmpeg() -> bool:
    if get_ffmpeg_path():
        return True
    log("WARNING: ffmpeg was not found on PATH or beside this toolkit.")
    log("Install it with: winget install Gyan.FFmpeg")
    log("Then close and reopen Command Prompt before recording.")
    return False


def get_ffmpeg_path() -> Optional[str]:
    """Find a normal PATH install or the ffmpeg.exe bundled in C:\\YT-Downloads."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    here = os.path.dirname(os.path.abspath(__file__))
    for folder in (here, os.path.dirname(here), os.path.dirname(os.path.dirname(here))):
        candidate = os.path.join(folder, "ffmpeg.exe")
        if os.path.isfile(candidate):
            return candidate
    return None


def slugify(value: str) -> str:
    value = value.strip().rstrip("/").split("/")[-1].replace("@", "")
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value) or "channel"


def build_channel_url(value: str) -> str:
    value = value.strip().rstrip("/")
    # Users often paste a channel's /live or /streams URL.  The callers add
    # /live themselves, so remove that suffix instead of making /live/live.
    for suffix in ("/live", "/streams", "/videos"):
        if value.lower().endswith(suffix):
            value = value[:-len(suffix)]
            break
    if value.startswith(("https://", "http://")):
        return value
    if value.startswith("@"):
        return f"https://www.youtube.com/{value}"
    if value.startswith("UC") and len(value) == 24:
        return f"https://www.youtube.com/channel/{value}"
    return f"https://www.youtube.com/@{value}"


def probe_live(channel_live_url: str, cookies: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """Return a live video id, or a short useful error when probing fails.
    
    Checks the /streams tab first with member cookies (highest reliability for
    members-only streams and recent broadcasts), then falls back to /live and HTML scrape.
    """
    channel_url = channel_live_url.rsplit("/live", 1)[0]
    streams_url = f"{channel_url}/streams"

    probe_targets = [streams_url, channel_live_url]
    for target in probe_targets:
        command = get_ytdlp_command() + [
            "--simulate", "--no-warnings",
            "--playlist-items", "1:3" if target == streams_url else "1",
            "--print", "%(id)s|||%(is_live)s",
        ]
        if cookies:
            command += ["--cookies", cookies]
        command.append(target)
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=25)
            for line in (result.stdout or "").splitlines():
                if "|||" not in line:
                    continue
                video_id, is_live = line.split("|||", 1)
                if is_live.strip().lower() == "true" and video_id.strip():
                    return video_id.strip(), None
        except subprocess.TimeoutExpired:
            continue
        except Exception:
            pass

    # Fallback: scrape the channel HTML page for isLiveNow markers
    html_live_id = _find_html_live_id(channel_url, cookies)
    if html_live_id:
        return html_live_id, "live found from the channel page"
    return None, None



def _find_html_live_id(channel_url: str, cookies: Optional[str]) -> Optional[str]:
    try:
        opener = urllib.request.build_opener()
        if cookies and os.path.isfile(cookies):
            jar = http.cookiejar.MozillaCookieJar(cookies)
            jar.load(ignore_discard=True, ignore_expires=True)
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        request = urllib.request.Request(
            channel_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        with opener.open(request, timeout=20) as response:
            html = response.read().decode("utf-8", errors="ignore")
    except Exception:
        return None

    candidates = []
    for match in re.finditer(r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"', html):
        context = html[max(0, match.start() - 1800):match.end() + 1800]
        if re.search(r'"isLiveNow"\s*:\s*true|"isLive"\s*:\s*true', context, re.I):
            video_id = match.group(1)
            if video_id not in candidates:
                candidates.append(video_id)
    return candidates[0] if candidates else None


def run_live_download(url: str, out_dir: str, chunk_index: int, cookies: Optional[str] = None,
                      live_from_start: bool = True) -> Tuple[Optional[str], bool]:
    """Record one continuous attempt.

    ``--hls-use-mpegts`` keeps the in-progress recording recoverable as an
    MPEG-TS-style stream.  Infinite retries absorb normal short outages in
    this same attempt; a second chunk is only made if yt-dlp actually exits.
    """
    os.makedirs(out_dir, exist_ok=True)
    output_template = os.path.join(out_dir, f"chunk_{chunk_index:03d}_%(id)s.%(ext)s")
    command = get_ytdlp_command() + [
        "--newline", "--no-warnings", "--continue",
        "--retries", "infinite", "--fragment-retries", "infinite",
        "--retry-sleep", "linear=1:20:2",
        "--socket-timeout", "20", "--hls-use-mpegts",
        "-f", BEST_FORMAT, "--merge-output-format", "mkv",
        "-o", output_template,
    ]
    ffmpeg_path = get_ffmpeg_path()
    if ffmpeg_path:
        command += ["--ffmpeg-location", os.path.dirname(ffmpeg_path)]
    if cookies:
        command += ["--cookies", cookies]
    if live_from_start:
        command.append("--live-from-start")
    command.append(url)

    log(f"recording chunk {chunk_index:03d}; short outages will retry automatically (Ctrl+C to finish).")
    interrupted = False
    try:
        subprocess.run(command)
    except KeyboardInterrupt:
        interrupted = True

    prefix = f"chunk_{chunk_index:03d}_"
    ignored_suffixes = (".part", ".ytdl", ".tmp", ".frag")
    candidates = [
        os.path.join(out_dir, name)
        for name in os.listdir(out_dir)
        # yt-dlp's separate temporary video/audio streams have names such as
        # .f140. and .f303.  They are not a playable final recording on their
        # own, so only accept the merged output as a completed chunk.
        if (
            name.startswith(prefix)
            and not name.lower().endswith(ignored_suffixes)
            and not re.search(r"\.f\d+\.", name, re.IGNORECASE)
        )
    ]
    produced = max(candidates, key=os.path.getsize) if candidates else None
    return produced, interrupted


def _poll_delay(watch_started: float) -> int:
    if time.monotonic() - watch_started < STARTUP_WINDOW_SECONDS:
        return STARTUP_POLL_SECONDS
    return NORMAL_POLL_SECONDS


def run_recording_session(get_live_id, url_builder, out_dir: str, cookies: Optional[str] = None,
                          live_from_start: bool = True,
                          known_live_ids: Optional[Set[str]] = None) -> Tuple[list[str], bool]:
    """Wait, record, survive short drops, and return ``(pieces, stopped)``.

    A normal finished broadcast returns ``stopped=False``.  The caller can
    then save that broadcast and immediately keep watching for the next one.
    ``stopped=True`` only means the user pressed Ctrl+C.
    """
    chunks: list[str] = []
    chunk_index = 0
    offline_since: Optional[float] = None
    current_live_id: Optional[str] = None
    start_from_beginning = live_from_start
    known_live_ids = known_live_ids if known_live_ids is not None else set()
    watch_started = time.monotonic()
    probe_count = 0
    log("Watching for live: checking every 5 seconds for the first minute, then every 20 seconds.")

    stopped_by_user = False
    try:
        while True:
            live_id, reason = get_live_id()
            probe_count += 1
            if live_id:
                offline_since = None
                if live_id != current_live_id:
                    if current_live_id and chunks:
                        log("A different broadcast was detected; finishing the first recording instead of mixing two streams.")
                        break
                    current_live_id = live_id
                    start_from_beginning = live_from_start and live_id not in known_live_ids
                    known_live_ids.add(live_id)
                    log(f"LIVE detected: id={live_id}")
                produced, interrupted = run_live_download(
                    url_builder(live_id), out_dir, chunk_index, cookies, start_from_beginning,
                )
                # If a recorder must be restarted while this same broadcast is
                # still live, continue from "now".  Re-applying
                # --live-from-start would duplicate its earlier minutes.
                start_from_beginning = False
                chunk_index += 1
                if produced and os.path.getsize(produced) >= MIN_CHUNK_BYTES:
                    chunks.append(produced)
                    log(f"saved: {os.path.basename(produced)} ({os.path.getsize(produced) / 1_048_576:.1f} MB)")
                else:
                    log("no usable file was completed in this recording attempt.")
                if interrupted:
                    stopped_by_user = True
                    log("Stopped by you. Combining the completed recordings now.")
                    break
                # The recorder exited; do not hammer YouTube.  A live stream
                # whose process genuinely ended gets a normal re-check first.
                time.sleep(NORMAL_POLL_SECONDS)
                continue

            now = time.monotonic()
            if offline_since is None:
                offline_since = now
                log(f"Not live. Next check in {_poll_delay(watch_started)} seconds.")
            elif current_live_id and now - offline_since >= OFFLINE_GRACE_SECONDS:
                log("The channel has been offline for 3 minutes; session finished.")
                break
            if reason and probe_count % 3 == 0:
                log(f"Probe note: {reason}")
            time.sleep(_poll_delay(watch_started))
    except KeyboardInterrupt:
        stopped_by_user = True
        log("Stopped by you. Combining the completed recordings now.")
    return chunks, stopped_by_user


def merge_chunks(chunks: list[str], final_path: str) -> Optional[str]:
    """Join attempts into one MKV without deleting the recoverable originals."""
    if not chunks:
        log("Nothing was recorded, so there is no final file to create.")
        return None
    os.makedirs(os.path.dirname(final_path), exist_ok=True)
    if len(chunks) == 1:
        shutil.copy2(chunks[0], final_path)
        log(f"Saved final recording: {final_path}")
        return final_path

    list_path = final_path + ".concat.txt"
    with open(list_path, "w", encoding="utf-8") as handle:
        for chunk in chunks:
            safe_path = os.path.abspath(chunk).replace("\\", "/").replace("'", r"\'")
            handle.write(f"file '{safe_path}'\n")
    log(f"Combining {len(chunks)} recovered attempts into one file...")
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        log("ffmpeg is unavailable, so the recovery pieces cannot be combined automatically.")
        return None
    result = subprocess.run(
        [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", final_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        log("Fast join failed because stream formats changed; re-encoding one final file.")
        result = subprocess.run(
            [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", list_path, final_path],
            capture_output=True, text=True,
        )
    try:
        os.remove(list_path)
    except OSError:
        pass
    if result.returncode == 0 and os.path.exists(final_path):
        log(f"Saved final recording: {final_path}")
        return final_path
    log(f"Could not combine automatically. Your completed recording pieces are safe in: {os.path.dirname(chunks[0])}")
    return None
