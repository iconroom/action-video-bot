import os
import sys
import json
import feedparser
import requests
import asyncio
import edge_tts
import subprocess
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Environment Credentials
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") # Drop this in your GitHub Secrets to use OpenAI instead
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
FB_PAGE_ID = os.getenv("FB_PAGE_ID")
FB_PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN")
TIKTOK_ACCESS_TOKEN = os.getenv("TIKTOK_ACCESS_TOKEN")

# YouTube OAuth
YOUTUBE_CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID")
YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET")
YOUTUBE_REFRESH_TOKEN = os.getenv("YOUTUBE_REFRESH_TOKEN")

STOCK_ASSETS_DIR = "stock_assets"
OUTPUT_DIR = "output"
FINAL_VIDEO_PATH = os.path.join(OUTPUT_DIR, "final_news_story.mp4")
AUDIO_PATH = os.path.join(OUTPUT_DIR, "voiceover.mp3")
STATE_FILE = "news_state.json"

def get_processed_ids():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return set(json.load(f).get("processed", []))
        except Exception:
            pass
    return set()

def save_processed_ids(processed_set):
    with open(STATE_FILE, "w") as f:
        json.dump({"processed": list(processed_set)[-100:]}, f)

def fetch_latest_trending_news(processed_ids):
    # Updated to pull top global/world trending headlines regardless of region
    feed = feedparser.parse("https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx1YlY4U0FtVnVHZ0pWVXlnQVAB?hl=en-US&gl=US&ceid=US:en")
    for entry in feed.entries:
        news_id = entry.get("id", entry.link)
        if news_id not in processed_ids:
            return entry, news_id
    return None, None

def generate_multimedia_script(title, summary):
    prompt = f"""
    Turn this news story into a short, punchy vertical video script. Return valid JSON with keys: 
    'title', 'narrative', 'hashtags', and 'scenes' (a list of 5 separate sequential objects, each having 'search_keyword' for Pexels background footage and 'narration_text' matching that specific segment).
    
    Title: {title}
    Summary: {summary}
    """

    # Use OpenAI if the OpenAI API Key is present
    if OPENAI_API_KEY and not GEMINI_API_KEY:
        url = "https://api.openai.com/v1/chat/completions"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {OPENAI_API_KEY}"}
        payload = {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"}
        }
        res = requests.post(url, headers=headers, json=payload)
        res.raise_for_status()
        return json.loads(res.json()["choices"][0]["message"]["content"])

    # Default to Gemini 3.5 Flash (Current 2026 Standard)
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"}
    }
    res = requests.post(url, headers=headers, json=payload)
    res.raise_for_status()
    return json.loads(res.json()["candidates"][0]["content"]["parts"][0]["text"])

async def generate_voiceover(full_text):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    communicate = edge_tts.Communicate(full_text, "en-US-ChristopherNeural")
    await communicate.save(AUDIO_PATH)

def fetch_pexels_clips(scenes):
    os.makedirs(STOCK_ASSETS_DIR, exist_ok=True)
    headers = {"Authorization": PEXELS_API_KEY}
    for idx, scene in enumerate(scenes):
        keyword = scene.get("search_keyword", "news background")
        url = f"https://api.pexels.com/videos/search?query={keyword}&per_page=1&orientation=portrait"
        res = requests.get(url, headers=headers)
        if res.status_code == 200:
            videos = res.json().get("videos", [])
            if videos:
                video_url = videos[0]["video_files"][0]["link"]
                filepath = os.path.join(STOCK_ASSETS_DIR, f"clip_{idx}.mp4")
                with open(filepath, "wb") as f:
                    f.write(requests.get(video_url).content)

def render_video():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    stock_files = sorted([os.path.join(STOCK_ASSETS_DIR, f) for f in os.listdir(STOCK_ASSETS_DIR) if f.endswith('.mp4')])
    if not stock_files:
        raise FileNotFoundError("No background clips available for rendering.")

    inputs = []
    filter_complex = ""
    for idx, clip in enumerate(stock_files):
        inputs.extend(["-stream_loop", "-1", "-i", clip])
        filter_complex += f"[{idx}:v]scale=2160:3840:force_original_aspect_ratio=decrease,pad=2160:3840:(ow-iw)/2:(oh-ih)/2,fps=30[v{idx}];"

    concat_inputs = "".join([f"[v{idx}]" for idx in range(len(stock_files))])
    filter_complex += f"{concat_inputs}concat=n={len(stock_files)}:v=1:a=0[outv]"
    audio_idx = len(stock_files)

    ffmpeg_cmd = [
        "ffmpeg", "-y"
    ] + inputs + [
        "-i", AUDIO_PATH,
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-map", f"{audio_idx}:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        FINAL_VIDEO_PATH
    ]
    subprocess.run(ffmpeg_cmd, check=True)

def upload_youtube(title, description):
    creds = Credentials(
        token=None, refresh_token=YOUTUBE_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=YOUTUBE_CLIENT_ID, client_secret=YOUTUBE_CLIENT_SECRET
    )
    youtube = build("youtube", "v3", credentials=creds)
    body = {
        "snippet": {"title": title[:100], "description": description, "categoryId": "25"},
        "status": {"privacyStatus": "public"}
    }
    media = MediaFileUpload(FINAL_VIDEO_PATH, chunksize=-1, resumable=True, mimetype="video/mp4")
    response = youtube.videos().insert(part="snippet,status", body=body, media_body=media).execute()
    print(f"[+] YouTube Success: {response.get('id')}")

def upload_facebook(title, description):
    url = f"https://graph-video.facebook.com/v19.0/{FB_PAGE_ID}/videos"
    payload = {"access_token": FB_PAGE_ACCESS_TOKEN, "title": title, "description": description}
    with open(FINAL_VIDEO_PATH, "rb") as vf:
        requests.post(url, data=payload, files={"source": vf}).raise_for_status()
    print("[+] Facebook Success")

def upload_tiktok(title):
    url = "https://open.tiktokapis.com/v2/post/publish/video/init/"
    headers = {"Authorization": f"Bearer {TIKTOK_ACCESS_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "post_info": {"title": title[:150], "privacy_level": "PUBLIC_TO_EVERYONE"},
        "source_info": {"source": "FILE_UPLOAD", "video_size": os.path.getsize(FINAL_VIDEO_PATH)}
    }
    response = requests.post(url, headers=headers, json=payload).json()
    print(f"[+] TikTok Success: {response}")

if __name__ == "__main__":
    processed = get_processed_ids()
    article, news_id = fetch_latest_trending_news(processed)
    
    if not article:
        print("[-] No new trending articles found.")
        sys.exit(0)
        
    print(f"[+] Processing News: {article.title}")
    story = generate_multimedia_script(article.title, article.summary)
    scenes = story.get("scenes", [])
    
    full_narrative = " ".join([s.get("narration_text", "") for s in scenes])
    caption = f"{story['title']}\n\n{full_narrative}\n\n{' '.join(story.get('hashtags', []))}"
    
    asyncio.run(generate_voiceover(full_narrative))
    fetch_pexels_clips(scenes)
    render_video()
    
    if YOUTUBE_CLIENT_ID and YOUTUBE_REFRESH_TOKEN:
        try: upload_youtube(story['title'], caption)
        except Exception as e: print(f"[-] YouTube Error: {e}")

    if FB_PAGE_ID and FB_PAGE_ACCESS_TOKEN:
        try: upload_facebook(story['title'], caption)
        except Exception as e: print(f"[-] Facebook Error: {e}")

    if TIKTOK_ACCESS_TOKEN:
        try: upload_tiktok(story['title'])
        except Exception as e: print(f"[-] TikTok Error: {e}")
        
    processed.add(news_id)
    save_processed_ids(processed)
    print("[+] Pipeline execution completed successfully.")
