# -*- coding: utf-8 -*-
"""YouTube ETL: API -> clean -> CSV.   python youtube_etl.py
Needs env var YOUTUBE_API_KEY (or .env file). Writes data/youtube_channels.csv, youtube_videos.csv, youtube_history.csv
"""
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent if HERE.name == "python" else HERE  # works in repo layout or flat folder

try:  # local run: read .env if python-dotenv installed. Actions uses real env var
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

CHANNELS = {
    "UCnz-ZXXER4jOvuED5trXfEA": "techTFQ",
    "UC7cs8q-gJRlGwj4A8OmCmXg": "Alex The Analyst",
    "UCChmJrVa8kDg05JfCmxpLRw": "Darshil Parmar",
    "UCh9nVJoWXmFb7sLApWGcLPQ": "codebasics",
    "UC8LUT6Qn7MSvPQPM8ZJsW8g": "LearnWidGiggs",
    "UCNU_lfiiWBdtULKOw6X0Dig": "Krish Naik",
    "UCk7NcgnqCmui1AV7MTXZwOw": "Ankit Bansal",
    "UCcfngi7_ASuo5jdWX0bNauQ": "How to PowerBI",
    "UCOMG0iHHi27owoyxGvURxiQ": "Analytics with Nag",
}

DATA_DIR = ROOT / "data"
CH_CSV = DATA_DIR / "youtube_channels.csv"
VID_CSV = DATA_DIR / "youtube_videos.csv"
HIST_CSV = DATA_DIR / "youtube_history.csv"

CH_COLS = ["Channel_ID", "Channel_Name", "Subscribers", "TotalVideos", "Views", "playlist_id"]
VID_COLS = ["VideoID", "Channel_ID", "Channel_Name", "VideoTitle", "Published_date", "Views", "Likes", "Comments"]
HIST_COLS = ["Snapshot_Date", "Channel_ID", "Channel_Name", "Subscribers", "TotalVideos", "Views"]


def call(req, tries=4):
    """execute with retry on 5xx / rate limit"""
    for n in range(tries):
        try:
            return req.execute()
        except HttpError as e:
            if e.resp.status in (429, 500, 502, 503, 504) and n < tries - 1:
                time.sleep(2 ** n)
                continue
            raise


def get_channel_stats(yt, ids):
    rows = []
    ids = list(ids)
    for i in range(0, len(ids), 50):
        res = call(yt.channels().list(part="snippet,contentDetails,statistics", id=",".join(ids[i:i + 50])))
        for it in res.get("items", []):
            st = it.get("statistics", {})
            rows.append({
                "Channel_ID": it["id"],
                "Channel_Name": it["snippet"]["title"],
                "Subscribers": st.get("subscriberCount"),  # missing if hidden
                "TotalVideos": st.get("videoCount"),
                "Views": st.get("viewCount"),
                "playlist_id": it["contentDetails"]["relatedPlaylists"]["uploads"],
            })
    return rows


def get_video_ids(yt, playlist_id):
    ids, token = [], None
    while True:
        res = call(yt.playlistItems().list(
            part="contentDetails", playlistId=playlist_id, maxResults=50, pageToken=token))
        ids += [x["contentDetails"]["videoId"] for x in res.get("items", [])]
        token = res.get("nextPageToken")
        if not token:
            return ids


def get_video_details(yt, video_ids, channel_id, channel_name):
    rows = []
    for i in range(0, len(video_ids), 50):
        res = call(yt.videos().list(part="snippet,statistics", id=",".join(video_ids[i:i + 50])))
        for v in res.get("items", []):
            sn, st = v.get("snippet", {}), v.get("statistics", {})
            rows.append({
                "VideoID": v["id"],
                "Channel_ID": channel_id,
                "Channel_Name": channel_name,
                "VideoTitle": sn.get("title", ""),
                "Published_date": sn.get("publishedAt", ""),
                "Views": st.get("viewCount", 0),
                "Likes": st.get("likeCount", 0),  # missing if likes hidden
                "Comments": st.get("commentCount", 0),  # missing if comments off
            })
    return rows


def clean_channels(df):
    for c in ("Subscribers", "TotalVideos", "Views"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype("int64")
    return df[CH_COLS]


def clean_videos(df):
    df["Published_date"] = pd.to_datetime(df["Published_date"], errors="coerce", utc=True).dt.strftime("%Y-%m-%d")
    for c in ("Views", "Likes", "Comments"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype("int64")
    df["VideoTitle"] = df["VideoTitle"].astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
    return df.drop_duplicates("VideoID")[VID_COLS]


def write_csv(df, path):
    tmp = path.with_suffix(".tmp")
    df.to_csv(tmp, index=False, encoding="utf-8")
    tmp.replace(path)


def main():
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        sys.exit("YOUTUBE_API_KEY not set")
    DATA_DIR.mkdir(exist_ok=True)
    yt = build("youtube", "v3", developerKey=key, cache_discovery=False)

    ch = clean_channels(pd.DataFrame(get_channel_stats(yt, CHANNELS)))
    if ch.empty:
        sys.exit("no channel data returned. Abort, CSV untouched")

    old_vid = pd.read_csv(VID_CSV) if VID_CSV.exists() else pd.DataFrame(columns=VID_COLS)
    new_parts, failed = [], []
    for r in ch.itertuples():
        try:
            ids = get_video_ids(yt, r.playlist_id)
            new_parts.append(pd.DataFrame(get_video_details(yt, ids, r.Channel_ID, r.Channel_Name)))
            print(f"{r.Channel_Name}: {len(ids)} videos")
        except HttpError as e:
            failed.append(r.Channel_ID)
            print(f"FAIL {r.Channel_Name}: {e}")

    # failed channel -> keep its old rows
    keep = old_vid[old_vid["Channel_ID"].isin(failed)] if failed else old_vid.iloc[0:0]
    frames = [p for p in new_parts if not p.empty] + ([keep] if not keep.empty else [])
    if not frames:
        sys.exit("no video data. Abort, CSV untouched")
    vid = clean_videos(pd.concat(frames, ignore_index=True))

    # history: one row per channel per day, rerun same day overwrites
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    snap = ch[["Channel_ID", "Channel_Name", "Subscribers", "TotalVideos", "Views"]].copy()
    snap.insert(0, "Snapshot_Date", today)
    hist = pd.read_csv(HIST_CSV) if HIST_CSV.exists() else pd.DataFrame(columns=HIST_COLS)
    hist = pd.concat([hist[hist["Snapshot_Date"] != today], snap], ignore_index=True)

    write_csv(ch, CH_CSV)
    write_csv(vid, VID_CSV)
    write_csv(hist[HIST_COLS], HIST_CSV)
    print(f"done: {len(ch)} channels, {len(vid)} videos, {len(hist)} history rows")
    if failed:
        sys.exit(f"partial: {len(failed)} channel(s) failed, old rows kept")


if __name__ == "__main__":
    main()   #by_dishant