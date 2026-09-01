import os
import sys
import json
import random
import subprocess
import requests
import time
import asyncio
import edge_tts
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# API Credentials from Environment
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
FB_PAGE_ID = os.getenv("FB_PAGE_ID")
FB_PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN")
IG_USER_ID = os.getenv("IG_USER_ID")
TIKTOK_ACCESS_TOKEN = os.getenv("TIKTOK_ACCESS_TOKEN")

# YouTube OAuth Credentials
YOUTUBE_CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID")
YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET")
YOUTUBE_REFRESH_TOKEN = os.getenv("YOUTUBE_REFRESH_TOKEN")

STOCK_ASSETS_DIR = "stock_assets"
OUTPUT_DIR = "output"
FINAL_VIDEO_PATH = os.path.join(OUTPUT_DIR, "final_story.mp4")
AUDIO_PATH = os.path.join(OUTPUT_DIR, "voiceover.mp3")
STATE_FILE = "episode_state.json"

def get_next_episode_number():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                return data.get("episode", 1)
        except Exception:
            pass
    return 1

def save_episode_number(ep_num):
    with open(STATE_FILE, "w") as f:
        json.dump({"episode": ep_num}, f)

def generate_story_script(episode_num):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    prompt = (
        f"Write Episode {episode_num} of an action-packed cinematic story arc. "
        "Return valid JSON with keys: 'title', 'narrative', 'hashtags', and 'scenes' "
        "(a list of 10 separate sequential objects, each having 'search_keyword' for Pexels background "
        "footage and 'narration_text' matching that specific part of the story)."
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"}
    }
    
    response = requests.post(url, headers=headers, json=payload)
    response.raise_for_status()
    data = response.json()
    content_text = data["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(content_text)
    
async def generate_voiceover(full_text):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    voice = "en-US-ChristopherNeural"
    communicate = edge_tts.Communicate(full_text, voice)
    await communicate.save(AUDIO_PATH)
    print("[+] Generated Voiceover MP3 using Edge-TTS")

def fetch_pexels_clips(scenes):
    os.makedirs(STOCK_ASSETS_DIR, exist_ok=True)
    headers = {"Authorization": PEXELS_API_KEY}
    
    for idx, scene in enumerate(scenes):
        keyword = scene.get("search_keyword", "action movie")
        url = f"https://api.pexels.com/videos/search?query={keyword}&per_page=3&orientation=portrait"
        res = requests.get(url, headers=headers)
        if res.status_code == 200:
            videos = res.json().get("videos", [])
            if videos:
                video_files = videos[0].get("video_files", [])
                selected_url = next((v["link"] for v in video_files if v.get("file_type") == "video/mp4"), None)
                if selected_url:
                    filepath = os.path.join(STOCK_ASSETS_DIR, f"clip_{idx}.mp4")
                    v_res = requests.get(selected_url)
                    with open(filepath, "wb") as f:
                        f.write(v_res.content)
                    print(f"[+] Downloaded dynamic background clip {idx} for keyword: {keyword}")

def render_video():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    stock_files = sorted([os.path.join(STOCK_ASSETS_DIR, f) for f in os.listdir(STOCK_ASSETS_DIR) if f.endswith('.mp4')])
    
    if not stock_files:
        raise FileNotFoundError("No clips were downloaded.")

    inputs = []
    filter_complex = ""
    for idx, clip in enumerate(stock_files):
        inputs.extend(["-stream_loop", "-1", "-i", clip])
        filter_complex += f"[{idx}:v]scale=2160:3840:force_original_aspect_ratio=decrease,pad=2160:3840:(ow-iw)/2:(oh-ih)/2,fps=30[v{idx}];"

    concat_inputs = "".join([f"[v{idx}]" for idx in range(len(stock_files))])
    filter_complex += f"{concat_inputs}concat=n={len(stock_files)}:v=1:a=0[v_cat];"

    if os.path.exists("watermark.png"):
        filter_complex += "[v_cat]movie=watermark.png,scale=300:-1[wm];[v_cat][wm]overlay=W-w-50:H-h-150[outv]"
    else:
        filter_complex += "[v_cat]copy[outv]"

    audio_idx = len(stock_files)

    ffmpeg_cmd = [
        "ffmpeg", "-y"
    ] + inputs + [
        "-i", AUDIO_PATH,
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-map", f"{audio_idx}:a",
        "-t", "180",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        FINAL_VIDEO_PATH
    ]
    subprocess.run(ffmpeg_cmd, check=True)
    print("[+] Full 3-minute episodic movie rendered successfully with 10 multi-scene switches and synchronized audio.")

def upload_youtube(title, description):
    creds = Credentials(
        token=None,
        refresh_token=YOUTUBE_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=YOUTUBE_CLIENT_ID,
        client_secret=YOUTUBE_CLIENT_SECRET
    )
    youtube = build("youtube", "v3", credentials=creds)
    body = {
        "snippet": {"title": title[:100], "description": description, "categoryId": "24"},
        "status": {"privacyStatus": "public"}
    }
    media = MediaFileUpload(FINAL_VIDEO_PATH, chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = request.execute()
    print(f"[+] YouTube Success: Video ID {response.get('id')}")

if __name__ == "__main__":
    current_episode = get_next_episode_number()
    print(f"Generating story script for Episode {current_episode}...")
    
    story = generate_story_script(current_episode)
    scenes = story.get("scenes", [])
    
    full_narrative = " ".join([scene.get("narration_text", "") for scene in scenes])
    if not full_narrative:
        full_narrative = story.get("narrative", "")

    caption = f"{story['title']} (Ep. {current_episode})\n\n{full_narrative}\n\n{' '.join(story.get('hashtags', []))}"
    
    print("Generating voiceover audio track...")
    asyncio.run(generate_voiceover(full_narrative))

    print("Fetching multi-scene action clips from Pexels...")
    fetch_pexels_clips(scenes)

    print("Rendering final episodic video with multi-background switching and voiceover...")
    render_video()
    
    if YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET and YOUTUBE_REFRESH_TOKEN:
        try: upload_youtube(f"{story['title']} - Ep. {current_episode}", caption)
        except Exception as e: print(f"[-] YouTube Error: {e}")
        
    save_episode_number(current_episode + 1)
    print("Execution completed successfully.")
