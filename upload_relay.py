#!/usr/bin/env python3
"""Gofile Relay and Summary Reporter for YouTube Live Recordings."""
from __future__ import annotations

import glob
import json
import os
import sys
import time
import urllib.request
import urllib.error


def upload_to_gofile(file_path: str) -> str:
    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
    print(f"\n[UPLOAD] Preparing to upload: {os.path.basename(file_path)} ({file_size_mb:.2f} MB)")

    # 1. First attempt: primary Gofile upload endpoint
    endpoints = ["https://upload.gofile.io/uploadfile"]

    # Also resolve active best server
    try:
        req = urllib.request.Request("https://api.gofile.io/servers", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            servers = data.get("data", {}).get("servers", [])
            for s in servers:
                name = s.get("name")
                if name:
                    endpoints.append(f"https://{name}.gofile.io/contents/uploadfile")
    except Exception as err:
        print(f"[UPLOAD] Note: Could not fetch dynamic server list: {err}")

    boundary = f"----WebKitFormBoundary{int(time.time()*1000)}"

    for endpoint in endpoints:
        print(f"[UPLOAD] Trying endpoint: {endpoint}...")
        for attempt in range(1, 4):
            try:
                with open(file_path, "rb") as f:
                    file_data = f.read()

                filename = os.path.basename(file_path)
                body = bytearray()
                body.extend(f"--{boundary}\r\n".encode("utf-8"))
                body.extend(f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode("utf-8"))
                body.extend(b"Content-Type: video/x-matroska\r\n\r\n")
                body.extend(file_data)
                body.extend(f"\r\n--{boundary}--\r\n".encode("utf-8"))

                req = urllib.request.Request(
                    endpoint,
                    data=bytes(body),
                    headers={
                        "Content-Type": f"multipart/form-data; boundary={boundary}",
                        "User-Agent": "Mozilla/5.0",
                    },
                )

                with urllib.request.urlopen(req, timeout=300) as resp:
                    res_json = json.loads(resp.read().decode("utf-8"))
                    if res_json.get("status") == "ok":
                        download_url = res_json.get("data", {}).get("downloadPage", "")
                        if download_url:
                            print(f"[UPLOAD] SUCCESS! Download Link: {download_url}")
                            return download_url
            except Exception as e:
                print(f"[UPLOAD] Attempt {attempt} failed on {endpoint}: {e}")
                time.sleep(3)

    return ""


def main():
    print("=== Processing & Uploading Completed Recordings ===")
    search_dirs = [
        "final_recordings",
        os.path.join("downloads", "Roshanecho"),
        "downloads",
    ]

    found_videos = []
    for d in search_dirs:
        if os.path.isdir(d):
            for ext in ("*.mkv", "*.mp4"):
                found_videos.extend(glob.glob(os.path.join(d, ext)))

    # Filter out empty files or small fragments < 1MB
    valid_videos = [f for f in set(found_videos) if os.path.isfile(f) and os.path.getsize(f) > 500 * 1024]

    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    summary_lines = ["## 🎥 YouTube Live Stream Recording Summary (@Roshanecho)\n"]

    if not valid_videos:
        print("[INFO] No complete recordings found to upload in this shift.")
        summary_lines.append("> [!NOTE]\n> No active live stream broadcast was detected or recorded during this shift interval.\n")
    else:
        print(f"[INFO] Found {len(valid_videos)} video(s) for upload.")
        summary_lines.append(f"Recorded **{len(valid_videos)}** video file(s):\n")
        summary_lines.append("| Filename | Size | Gofile Download Link |")
        summary_lines.append("| :--- | :--- | :--- |")

        for video in valid_videos:
            size_mb = os.path.getsize(video) / (1024 * 1024)
            size_str = f"{size_mb:.1f} MB" if size_mb < 1024 else f"{size_mb/1024:.2f} GB"
            fname = os.path.basename(video)
            link = upload_to_gofile(video)
            if link:
                summary_lines.append(f"| `{fname}` | {size_str} | [⬇️ Direct Download Link]({link}) |")
            else:
                summary_lines.append(f"| `{fname}` | {size_str} | *(Uploaded to GitHub Actions Artifacts)* |")

    if summary_file:
        try:
            with open(summary_file, "a", encoding="utf-8") as sf:
                sf.write("\n" + "\n".join(summary_lines) + "\n")
        except Exception as e:
            print(f"[WARNING] Could not write to GITHUB_STEP_SUMMARY: {e}")


if __name__ == "__main__":
    main()
