import sqlite3
import pandas as pd
from datetime import datetime
from googleapiclient.discovery import build
import isodate

# --- CONFIGURATION ---
# Replace with your actual YouTube Data API Key
API_KEY = 'YOUR_YOUTUBE_DATA_API_KEY' 

class DatabaseManager:
    def __init__(self, db_name):
        self.conn = sqlite3.connect(db_name)
        self.cursor = self.conn.cursor()
        self.create_tables()

    def create_tables(self):
        # Table 1: Collection Metadata
        self.cursor.execute('''
        CREATE TABLE IF NOT EXISTS historico_coletas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_handle TEXT,
            inscritos_no_momento INTEGER,
            periodo_analisado_inicio DATE,
            periodo_analisado_fim DATE,
            data_da_coleta DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        # Table 2: Video Details
        self.cursor.execute('''
        CREATE TABLE IF NOT EXISTS videos_detalhes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            coleta_id INTEGER,
            video_id TEXT,
            thumbnail_url TEXT,
            titulo TEXT,
            data_publicacao DATE,
            categoria TEXT,
            duracao_segundos INTEGER,
            views INTEGER,
            likes INTEGER,
            eficiencia_percent REAL,
            FOREIGN KEY (coleta_id) REFERENCES historico_coletas (id)
        )
        ''')
        self.conn.commit()

    def save_collection(self, channel_handle, subscribers, start_date, end_date, df_videos):
        self.cursor.execute('''
            INSERT INTO historico_coletas (channel_handle, inscritos_no_momento, periodo_analisado_inicio, periodo_analisado_fim)
            VALUES (?, ?, ?, ?)
        ''', (channel_handle, subscribers, start_date, end_date))
        
        collection_id = self.cursor.lastrowid
        
        records = []
        for _, row in df_videos.iterrows():
            records.append((
                collection_id,
                row['video_id'],
                row['thumbnail_url'],
                row['title'],
                row['publish_date'],
                row['category'],
                row['duration_sec'],
                row['views'],
                row['likes'],
                row['efficiency_pct']
            ))
            
        self.cursor.executemany('''
            INSERT INTO videos_detalhes 
            (coleta_id, video_id, thumbnail_url, titulo, data_publicacao, categoria, duracao_segundos, views, likes, eficiencia_percent)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', records)
        
        self.conn.commit()
        return collection_id

def fetch_youtube_data(api_key, channel_handle, start_date_str, end_date_str):
    youtube = build('youtube', 'v3', developerKey=api_key)
    start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()

    try:
        # 1. Get Channel Details
        req = youtube.channels().list(part='contentDetails,statistics', forHandle=channel_handle)
        res = req.execute()
        
        if not res['items']: return None, None
        
        channel_info = res['items'][0]
        uploads_id = channel_info['contentDetails']['relatedPlaylists']['uploads']
        subscribers = int(channel_info['statistics']['subscriberCount'])
        if subscribers == 0: subscribers = 1

        # 2. Fetch Videos
        videos_data = []
        next_page_token = None
        keep_searching = True
        
        while keep_searching:
            pl_req = youtube.playlistItems().list(
                part='contentDetails,snippet',
                playlistId=uploads_id,
                maxResults=50,
                pageToken=next_page_token
            )
            pl_res = pl_req.execute()
            
            video_ids_fetch = []
            
            for item in pl_res['items']:
                vid_date_str = item['snippet']['publishedAt'][:10]
                vid_date = datetime.strptime(vid_date_str, "%Y-%m-%d").date()
                
                if vid_date > end_date: continue
                if vid_date < start_date: 
                    keep_searching = False
                    break
                video_ids_fetch.append(item['contentDetails']['videoId'])
                
            if video_ids_fetch:
                stats_req = youtube.videos().list(
                    part='statistics,contentDetails,snippet', id=','.join(video_ids_fetch)
                )
                stats_res = stats_req.execute()
                
                for item in stats_res['items']:
                    dur = isodate.parse_duration(item['contentDetails']['duration']).total_seconds()
                    # Logic: Shorts are typically <= 60s
                    cat = 'Shorts' if dur <= 60 else 'Long-form'
                    views = int(item['statistics'].get('viewCount', 0))
                    
                    # Thumbnail extraction (High -> Medium -> Default)
                    thumbs = item['snippet']['thumbnails']
                    thumb_url = thumbs.get('high', thumbs.get('medium', thumbs.get('default')))['url']
                    
                    videos_data.append({
                        'video_id': item['id'],
                        'thumbnail_url': thumb_url,
                        'publish_date': item['snippet']['publishedAt'][:10],
                        'title': item['snippet']['title'],
                        'category': cat,
                        'views': views,
                        'likes': int(item['statistics'].get('likeCount', 0)),
                        'efficiency_pct': round((views/subscribers*100), 2),
                        'duration_sec': int(dur)
                    })
            
            next_page_token = pl_res.get('nextPageToken')
            if not next_page_token: keep_searching = False

        return pd.DataFrame(videos_data), subscribers

    except Exception as e:
        print(f"Error fetching {channel_handle}: {e}")
        return None, None