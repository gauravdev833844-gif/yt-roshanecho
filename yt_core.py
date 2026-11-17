"""Shared, cloud-hardened helpers for Autonomous Members & Public Live Recorder."""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import time
import http.cookiejar
import urllib.request
from datetime import datetime
from typing import Optional, Set, Tuple

BEST_FORMAT = (
    "b[height=720][protocol*=m3u8]"
    "/b[height<=720][protocol*=m3u8]"
    "/b[height<=1080][protocol*=m3u8]"
    "/b[protocol*=m3u8]"
    "/b[height<=720]"
    "/b[height<=1080]"
    "/bv*+ba/b"
)
MIN_CHUNK_BYTES = 256 * 1024

STARTUP_WINDOW_SECONDS = 60
STARTUP_POLL_SECONDS = 5
NORMAL_POLL_SECONDS = 20
OFFLINE_GRACE_SECONDS = 180
BOOT_MONO = time.monotonic()


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


def get_ytdlp_command() -> list:
    bundled = os.path.join(os.path.dirname(os.path.abspath(__file__)), "yt-dlp.exe")
    if os.path.isfile(bundled):
        return [bundled]
    return [sys.executable, "-m", "yt_dlp"]


def js_args() -> list:
    if shutil.which("node"):
        return ["--js-runtimes", "node"]
    if shutil.which("nodejs"):
        return ["--js-runtimes", "nodejs"]
    return []


def check_ytdlp() -> bool:
    try:
        subprocess.run(
            get_ytdlp_command() + ["--version"],
            check=True, capture_output=True, text=True,
        )
        return True
    except Exception:
        log("yt-dlp is not installed or is not working.")
        return False


def get_ffmpeg_path() -> Optional[str]:
    found = shutil.which("ffmpeg")
    if found:
        return found
    here = os.path.dirname(os.path.abspath(__file__))
    for folder in (here, os.path.dirname(here), os.path.dirname(os.path.dirname(here))):
        candidate = os.path.join(folder, "ffmpeg.exe")
        if os.path.isfile(candidate):
            return candidate
    return None


def check_ffmpeg() -> bool:
    if get_ffmpeg_path():
        return True
    log("WARNING: ffmpeg was not found on PATH.")
    return False


def slugify(value: str) -> str:
    value = value.strip().rstrip("/").split("/")[-1].replace("@", "")
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value) or "channel"


def build_channel_url(value: str) -> str:
    value = value.strip().rstrip("/")
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


def _fetch_html(url: str, cookies: Optional[str]) -> str:
    try:
        handlers = []
        if cookies and os.path.isfile(cookies):
            jar = http.cookiejar.MozillaCookieJar(cookies)
            jar.load(ignore_discard=True, ignore_expires=True)
            handlers.append(urllib.request.HTTPCookieProcessor(jar))
        opener = urllib.request.build_opener(*handlers)
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        with opener.open(request, timeout=15) as response:
            return response.read().decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _extract_live_ids_from_html(html: str) -> list[str]:
    if not html or len(html) < 500:
        return []
    found: list[str] = []
    m = re.search(
        r'"videoDetails"\s*:\s*\{(.{1,6000}?)\}\s*,\s*"(?:playerConfig|microformat|storyboards|annotations|cards|trackingParams)"',
        html, flags=re.DOTALL,
    )
    if not m:
        m = re.search(r'"videoDetails"\s*:\s*\{(.{1,4500}?)\}', html, flags=re.DOTALL)
    if m:
        block = m.group(1)
        vid_m = re.search(r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"', block)
        is_live = bool(re.search(r'"isLive"\s*:\s*true', block))
        is_upcoming = bool(re.search(r'"isUpcoming"\s*:\s*true', block))
        micro_m = re.search(r'"liveBroadcastDetails"\s*:\s*\{([^}]{1,600})\}', html)
        ended = False
        if micro_m:
            mblock = micro_m.group(1)
            if '"isLiveNow":false' in mblock or '"isLiveNow": false' in mblock:
                ended = True
            elif '"endTimestamp"' in mblock and '"isLiveNow":true' not in mblock and '"isLiveNow": true' not in mblock:
                ended = True
        if vid_m and is_live and not is_upcoming and not ended:
            found.append(vid_m.group(1))

    for match in re.finditer(r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"', html):
        vid = match.group(1)
        if vid in found:
            continue
        ctx = html[max(0, match.start() - 2500):min(len(html), match.end() + 2500)]
        if re.search(r'"isLiveNow"\s*:\s*true|BADGE_STYLE_TYPE_LIVE_NOW|"style"\s*:\s*"LIVE"|"label"\s*:\s*"LIVE"', ctx, re.I):
            if not re.search(r'"isUpcoming"\s*:\s*true|UPCOMING', ctx, re.I):
                found.append(vid)
    return found


def _verify_video_live(video_id: str, cookies: Optional[str]) -> bool:
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    html = _fetch_html(watch_url, cookies)
    if video_id in _extract_live_ids_from_html(html):
        return True
    for client in ("mweb", "tv", "android"):
        cmd = get_ytdlp_command() + [
            "--simulate", "--no-warnings", "--ignore-no-formats-error",
            "--extractor-args", f"youtube:player_client={client}",
            "--print", "%(is_live)s|||%(live_status)s",
        ] + js_args()
        if cookies and client != "android":
            cmd += ["--cookies", cookies]
        cmd.append(watch_url)
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            out = (res.stdout or "").strip().lower()
            if "true|||" in out or "|||is_live" in out:
                return True
            if "|||was_live" in out or "|||not_live" in out or "|||is_upcoming" in out:
                return False
        except Exception:
            pass
    return False


def probe_live(channel_live_url: str, cookies: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    channel_url = channel_live_url.rsplit("/live", 1)[0]
    streams_url = f"{channel_url}/streams"

    for page_url in (channel_live_url, streams_url, channel_url):
        html = _fetch_html(page_url, cookies)
        for cand_id in _extract_live_ids_from_html(html)[:3]:
            if page_url == channel_live_url or _verify_video_live(cand_id, cookies):
                return cand_id, None

    for target in (channel_live_url, streams_url):
        command = get_ytdlp_command() + [
            "--simulate", "--no-warnings", "--ignore-no-formats-error",
            "--extractor-args", "youtube:player_client=mweb,tv,web_safari",
            "--playlist-items", "1:4" if target == streams_url else "1",
            "--print", "%(id)s|||%(is_live)s|||%(live_status)s",
        ] + js_args()
        if cookies:
            command += ["--cookies", cookies]
        command.append(target)
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            for line in (result.stdout or "").splitlines():
                if "|||" not in line:
                    continue
                parts = [p.strip() for p in line.split("|||")]
                video_id = parts[0] if parts else ""
                is_live = parts[1].lower() if len(parts) > 1 else ""
                lstatus = parts[2].lower() if len(parts) > 2 else ""
                if video_id and (is_live == "true" or lstatus == "is_live"):
                    return video_id, None
        except Exception as exc:
            return None, f"yt-dlp probe warning: {exc}"

    return None, None


def _remaining_shift_seconds() -> Optional[int]:
    max_rt = int(os.environ.get("MAX_RUNTIME_SECONDS", "0") or "0")
    if max_rt <= 0:
        return None
    elapsed = time.monotonic() - BOOT_MONO
    return max(10, int(max_rt - elapsed - 90))


def run_live_download(url: str, out_dir: str, chunk_index: int, cookies: Optional[str] = None,
                      live_from_start: bool = False) -> Tuple[Optional[str], bool]:
    os.makedirs(out_dir, exist_ok=True)
    interrupted = False

    for client in ("mweb", "tv", "web_safari", "android", None):
        rem = _remaining_shift_seconds()
        if rem is not None and rem <= 15:
            break

        output_template = os.path.join(out_dir, f"chunk_{chunk_index:03d}_%(id)s.ts")
        ffmpeg_i_args = (
            "ffmpeg_i:-reconnect 1 -reconnect_streamed 1 "
            "-reconnect_on_network_error 1 -reconnect_on_http_error 4xx,5xx "
            "-reconnect_delay_max 10 -rw_timeout 15000000"
        )
        command = get_ytdlp_command() + [
            "--newline", "--no-warnings", "--no-part", "--continue",
            "--retries", "20", "--fragment-retries", "infinite",
            "--retry-sleep", "http:2", "--retry-sleep", "fragment:1",
            "--socket-timeout", "20",
            "--hls-use-mpegts", "--downloader", "m3u8:ffmpeg",
            "--downloader-args", ffmpeg_i_args,
            "-f", BEST_FORMAT,
            "-o", output_template,
        ] + js_args()

        if client:
            command += ["--extractor-args", f"youtube:player_client={client}"]
        ffmpeg_path = get_ffmpeg_path()
        if ffmpeg_path:
            command += ["--ffmpeg-location", os.path.dirname(ffmpeg_path)]
        if cookies and client != "android":
            command += ["--cookies", cookies]
        command.append(url)

        log(f"recording chunk {chunk_index:03d} (client={client or 'default'}, timeout={rem or 'none'}s)...")
        proc = subprocess.Popen(
            command,
            start_new_session=(os.name != "nt"),
        )
        started = time.monotonic()
        try:
            while proc.poll() is None:
                time.sleep(2)
                rem_now = _remaining_shift_seconds()
                if rem_now is not None and rem_now <= 15:
                    log("Shift runtime limit approaching; stopping ffmpeg gracefully to save chunk...")
                    if hasattr(os, "killpg"):
                        try:
                            os.killpg(proc.pid, signal.SIGTERM)
                        except Exception:
                            proc.terminate()
                    else:
                        proc.terminate()
                    proc.wait(timeout=15)
                    break
        except KeyboardInterrupt:
            interrupted = True
            if hasattr(os, "killpg"):
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except Exception:
                    proc.terminate()
            else:
                proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                pass

        prefix = f"chunk_{chunk_index:03d}_"
        candidates = [
            os.path.join(out_dir, name)
            for name in os.listdir(out_dir)
            if name.startswith(prefix) and os.path.isfile(os.path.join(out_dir, name))
        ]
        produced = max(candidates, key=os.path.getsize) if candidates else None
        if produced and os.path.getsize(produced) >= MIN_CHUNK_BYTES:
            return produced, interrupted
        if interrupted:
            break
        if time.monotonic() - started < 15:
            log(f"client {client or 'default'} failed quickly; trying next client...")
            continue
        break

    return None, interrupted


def _poll_delay(watch_started: float) -> int:
    if time.monotonic() - watch_started < STARTUP_WINDOW_SECONDS:
        return STARTUP_POLL_SECONDS
    return NORMAL_POLL_SECONDS


def run_recording_session(get_live_id, url_builder, out_dir: str, cookies: Optional[str] = None,
                          live_from_start: bool = False,
                          known_live_ids: Optional[Set[str]] = None) -> Tuple[list[str], bool]:
    chunks: list[str] = []
    chunk_index = 0
    offline_since: Optional[float] = None
    current_live_id: Optional[str] = None
    known_live_ids = known_live_ids if known_live_ids is not None else set()
    watch_started = time.monotonic()
    last_heartbeat = watch_started
    probe_count = 0
    log("Watching for live: checking every 5 seconds for the first minute, then every 20 seconds.")

    stopped_by_user = False
    try:
        while True:
            rem = _remaining_shift_seconds()
            if rem is not None and rem <= 15:
                log("Shift runtime limit reached during watch loop.")
                break

            live_id, reason = get_live_id()
            probe_count += 1
            if live_id:
                offline_since = None
                if live_id != current_live_id:
                    if current_live_id and chunks:
                        log("A different broadcast was detected; finishing the first recording.")
                        break
                    current_live_id = live_id
                    known_live_ids.add(live_id)
                    log(f"LIVE detected: id={live_id}")
                produced, interrupted = run_live_download(
                    url_builder(live_id), out_dir, chunk_index, cookies, False,
                )
                chunk_index += 1
                if produced and os.path.getsize(produced) >= MIN_CHUNK_BYTES:
                    chunks.append(produced)
                    log(f"saved: {os.path.basename(produced)} ({os.path.getsize(produced) / 1_048_576:.1f} MB)")
                else:
                    log("no usable file was completed in this recording attempt.")
                if interrupted:
                    stopped_by_user = True
                    break
                time.sleep(5)
                continue

            now = time.monotonic()
            if offline_since is None:
                offline_since = now
                log(f"Not live. Next check in {_poll_delay(watch_started)} seconds.")
            elif current_live_id and now - offline_since >= OFFLINE_GRACE_SECONDS:
                log("The channel has been offline for 3 minutes; session finished.")
                break
            elif now - last_heartbeat >= 120:
                last_heartbeat = now
                log(f"Still watching... (probes={probe_count}, status=offline)")
            if reason:
                log(f"Probe note: {reason}")
            time.sleep(_poll_delay(watch_started))
    except KeyboardInterrupt:
        stopped_by_user = True
        log("Stopped by signal. Combining completed recordings now.")
    return chunks, stopped_by_user


def merge_chunks(chunks: list[str], final_path: str) -> Optional[str]:
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
        return chunks[0]
    result = subprocess.run(
        [ffmpeg, "-y", "-fflags", "+genpts", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", final_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
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
    return chunks[0]
