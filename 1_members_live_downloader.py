#!/usr/bin/env python3
"""Member-only live recorder. Use only an account entitled to watch it."""
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yt_core as core


def main() -> None:
    print("=== Members-only live recorder ===\n")
    if not core.check_ytdlp() or not core.check_ffmpeg():
        return
    raw_input = input("Channel handle, Channel URL, or Video URL: ").strip()
    if not raw_input:
        print("No channel or URL entered.")
        return
    script_dir = os.path.dirname(os.path.abspath(__file__))
    cookies_path = os.path.join(script_dir, "cookies.txt")
    if not os.path.isfile(cookies_path):
        print(f"cookies.txt is missing beside this script: {cookies_path}")
        print("Export fresh Netscape-format cookies from the browser account with the membership, then try again.")
        return

    import re
    video_id_match = re.search(r'(?:v=|\/live\/|\/shorts\/|youtu\.be\/|^)([A-Za-z0-9_-]{11})(?:[?&]|$)', raw_input)
    is_direct_video = bool(video_id_match and ("watch" in raw_input or "youtu.be" in raw_input or len(raw_input) == 11))
    
    slug = core.slugify(raw_input)
    output_dir = os.path.join(script_dir, "downloads", slug)
    session_number = 1
    known_live_ids = set()

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
            core.merge_chunks(chunks, os.path.join(output_dir, f"{slug}_members_live_{session_stamp}.mkv"))
        return

    channel = raw_input
    channel_url = core.build_channel_url(channel)
    while True:
        session_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_dir = os.path.join(output_dir, "recovery_pieces", f"session_{session_stamp}")
        print(f"\n=== Watching session {session_number}; Ctrl+C stops the watcher ===")
        try:
            chunks, stopped_by_user = core.run_recording_session(
                get_live_id=lambda: core.probe_live(f"{channel_url}/live", cookies_path),
                url_builder=lambda video_id: f"https://www.youtube.com/watch?v={video_id}",
                out_dir=session_dir,
                cookies=cookies_path,
                known_live_ids=known_live_ids,
            )
        except Exception as error:
            core.log(f"Watcher error: {error}. It will retry in 60 seconds.")
            time.sleep(60)
            continue
        if chunks:
            core.merge_chunks(chunks, os.path.join(output_dir, f"{slug}_members_live_{session_stamp}.mkv"))
        if stopped_by_user:
            break
        if chunks:
            core.log("Broadcast finished. Its file is saved; continuing to watch for the next live.")
        else:
            core.log("No broadcast was recorded; continuing to watch for the next live.")
        session_number += 1


if __name__ == "__main__":
    main()
