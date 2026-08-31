import os
import sys
import json
import random
import subprocess
import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# API Credentials from Environment
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
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

def generate_story_script():
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": "You are a professional action movie storyteller."},
            {
                "role": "user", 
                "content": "Write an engaging action story. Return JSON with keys: 'title', 'narrative', 'hashtags', and 'keywords' (a list of 3 search terms like ['car chase', 'explosion', 'martial arts'])."
            }
        ],
        "response_format": {"type": "json_object"}
    }
    
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code != 200:
        print(f"Groq API Error Response: {response.text}")
    response.raise_for_status()
    data = response.json()["choices"][0]["message"]["content"]
    return json.loads(data)

def fetch_pexels_clips(keywords):
    os.makedirs(STOCK_ASSETS_DIR, exist_ok=True)
    headers = {"Authorization": PEXELS_API_KEY}
    
    for idx, keyword in enumerate(keywords):
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
                    print(f"[+] Downloaded clip for: {keyword}")

def render_video():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    stock_files = [os.path.join(STOCK_ASSETS_DIR, f) for f in os.listdir(STOCK_ASSETS_DIR) if f.endswith('.mp4')]
    
    if not stock_files:
        raise FileNotFoundError("No clips were downloaded.")

    concat_file = os.path.join(OUTPUT_DIR, "concat_list.txt")
    with open(concat_file, "w") as f:
        for clip in stock_files:
            f.write(f"file '{os.path.abspath(clip)}'\n")

    ffmpeg_cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file,
        "-t", "180", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", FINAL_VIDEO_PATH
    ]
    subprocess.run(ffmpeg_cmd, check=True)

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
        "snippet": {
            "title": title[:100],
            "description": description,
            "categoryId": "24"
        },
        "status": {"privacyStatus": "public"}
    }
    
    media = MediaFileUpload(FINAL_VIDEO_PATH, chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = request.execute()
    print(f"[+] YouTube Success: Video ID {response.get('id')}")

def upload_facebook(title, description):
    url = f"https://graph.facebook.com/v19.0/{FB_PAGE_ID}/videos"
    payload = {
        "title": title,
        "description": description,
        "access_token": FB_PAGE_ACCESS_TOKEN
    }
    with open(FINAL_VIDEO_PATH, "rb") as video_file:
        files = {"source": video_file}
        response = requests.post(url, data=payload, files=files)
        response.raise_for_status()
    print(f"[+] Facebook Success: {response.json()}")

def upload_tiktok(title):
    url = "https://open.tiktokapis.com/v2/post/publish/video/init/"
    headers = {
        "Authorization": f"Bearer {TIKTOK_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "post_info": {
            "title": title[:150],
            "privacy_level": "PUBLIC_TO_EVERYONE"
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": os.path.getsize(FINAL_VIDEO_PATH)
        }
    }
    r = requests.post(url, headers=headers, json=payload).json()
    print(f"[+] TikTok Success: {r}")

if __name__ == "__main__":
    print("Generating story script...")
    story = generate_story_script()
    caption = f"{story['title']}\n\n{story['narrative']}\n\n{' '.join(story.get('hashtags', []))}"
    
    print("Fetching matching action clips...")
    keywords = story.get("keywords", ["action movie", "explosion", "chase"])
    fetch_pexels_clips(keywords)

    print("Rendering 3-minute video...")
    render_video()
    
    print("Posting video...")
    if YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET and YOUTUBE_REFRESH_TOKEN:
        try: upload_youtube(story['title'], caption)
        except Exception as e: print(f"[-] YouTube Error: {e}")

    if FB_PAGE_ID and FB_PAGE_ACCESS_TOKEN:
        try: upload_facebook(story['title'], caption)
        except Exception as e: print(f"[-] Facebook Error: {e}")
        
    if TIKTOK_ACCESS_TOKEN:
        try: upload_tiktok(story['title'])
        except Exception as e: print(f"[-] TikTok Error: {e}")
        
    print("Execution completed successfully.")
