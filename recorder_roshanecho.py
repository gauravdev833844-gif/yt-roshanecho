#!/usr/bin/env python3
"""Autonomous Members-Only and Public Live Stream Recorder for YouTube.
Specifically configured for @Roshanecho with seamless handling of public/members stream switches,
new broadcast links, automatic recovery, and 24/7 cloud execution on GitHub Actions.
"""
from __future__ import annotations

import os
import re
import signal
import sys
import time
from datetime import datetime
from typing import Optional, Set

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yt_core as core

stop_requested = False


def handle_shutdown(signum, frame):
    global stop_requested
    core.log(f"[SIGNAL] Received signal {signum}, initiating graceful shutdown...")
    stop_requested = True


signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGINT, handle_shutdown)


def get_target_input() -> str:
    # 1. Environment variable (Cloud Runner)
    env_target = os.environ.get("TARGET_CHANNEL", "").strip() or os.environ.get("YOUTUBE_TARGET", "").strip()
    if env_target:
        return env_target
    # 2. Command-line argument
    if len(sys.argv) > 1 and sys.argv[1].strip():
        return sys.argv[1].strip()
    # 3. Interactive prompt if run in local terminal
    if sys.stdin and sys.stdin.isatty():
        try:
            user_input = input("Channel handle, Channel URL, or Video URL: ").strip()
            if user_input:
                return user_input
        except (EOFError, KeyboardInterrupt):
            pass
    # 4. Default target channel
    return "@Roshanecho"


def get_cookies_path() -> Optional[str]:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.environ.get("COOKIES_FILE", "").strip(),
        os.path.join(script_dir, "cookies.txt"),
        os.path.join(os.getcwd(), "cookies.txt"),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return os.path.abspath(c)
    return None


def main() -> None:
    print("=== @Roshanecho Autonomous Members & Public Live Recorder ===\n", flush=True)
    if not core.check_ytdlp() or not core.check_ffmpeg():
        sys.exit(1)

    raw_input = get_target_input()
    if not raw_input:
        core.log("No channel or URL specified.")
        sys.exit(1)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    cookies_path = get_cookies_path()
    if not cookies_path:
        core.log("WARNING: cookies.txt was not found beside this script or in current directory.")
        core.log("Members-only probing and private/unlisted live streams will not be detected without cookies.")
    else:
        core.log(f"Authentication cookies loaded: {cookies_path}")

    video_id_match = re.search(r'(?:v=|\/live\/|\/shorts\/|youtu\.be\/|^)([A-Za-z0-9_-]{11})(?:[?&]|$)', raw_input)
    is_direct_video = bool(video_id_match and ("watch" in raw_input or "youtu.be" in raw_input or len(raw_input) == 11))
    
    slug = core.slugify(raw_input)
    output_dir = os.path.join(script_dir, "downloads", slug)
    os.makedirs(output_dir, exist_ok=True)
    session_number = 1
    known_live_ids: Set[str] = set()

    max_runtime = int(os.environ.get("MAX_RUNTIME_SECONDS", "0"))  # 0 = indefinite (local), >0 = cloud shift limit
    overall_start = time.monotonic()

    if max_runtime > 0:
        core.log(f"Active shift runtime limit: {max_runtime}s ({max_runtime/3600:.2f} hours)")

    if is_direct_video:
        direct_vid = video_id_match.group(1)
        core.log(f"Direct live video specified: {direct_vid}")
        session_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_dir = os.path.join(output_dir, "recovery_pieces", f"session_{session_stamp}")
        chunks, _ = core.run_recording_session(
            get_live_id=lambda: (direct_vid, None),
            url_builder=lambda video_id: f"https://www.youtube.com/watch?v={video_id}",
            out_dir=session_dir,
            cookies=cookies_path,
            known_live_ids=known_live_ids,
        )
        if chunks:
            final_path = os.path.join(output_dir, f"{slug}_members_live_{session_stamp}.mkv")
            core.merge_chunks(chunks, final_path)
        return

    channel = raw_input
    channel_url = core.build_channel_url(channel)
    core.log(f"Target channel: {channel} -> {channel_url}")

    while not stop_requested:
        if max_runtime > 0 and (time.monotonic() - overall_start) >= max_runtime:
            core.log("Shift runtime limit reached. Concluding session gracefully.")
            break

        session_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_dir = os.path.join(output_dir, "recovery_pieces", f"session_{session_stamp}")
        core.log(f"\n=== Watching session {session_number} (@Roshanecho) ===")

        def probe_wrapper():
            if stop_requested:
                raise KeyboardInterrupt("Stop requested via signal")
            if max_runtime > 0 and (time.monotonic() - overall_start) >= max_runtime:
                raise KeyboardInterrupt("Shift runtime limit reached")
            return core.probe_live(f"{channel_url}/live", cookies_path)

        try:
            chunks, stopped_by_user = core.run_recording_session(
                get_live_id=probe_wrapper,
                url_builder=lambda video_id: f"https://www.youtube.com/watch?v={video_id}",
                out_dir=session_dir,
                cookies=cookies_path,
                known_live_ids=known_live_ids,
            )
        except KeyboardInterrupt:
            stopped_by_user = True
            chunks = []
        except Exception as error:
            core.log(f"Watcher error: {error}. Retrying in 60 seconds...")
            time.sleep(60)
            continue

        if chunks:
            final_mkv = os.path.join(output_dir, f"{slug}_members_live_{session_stamp}.mkv")
            core.merge_chunks(chunks, final_mkv)

        if stopped_by_user or stop_requested:
            core.log("Recording stopped. Exiting watcher loop.")
            break

        if chunks:
            core.log("Broadcast finished. Video saved; continuing watcher for next live stream / new link...")
        else:
            core.log("No active broadcast detected in this check cycle; continuing to watch...")
        session_number += 1
        time.sleep(5)


if __name__ == "__main__":
    main()
