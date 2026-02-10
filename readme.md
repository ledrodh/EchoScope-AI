# 🔴 EchoScope AI - YouTube Niche Intelligence

![Project Status](https://img.shields.io/badge/status-active-brightgreen) ![Python](https://img.shields.io/badge/python-3.10%2B-blue) ![License](https://img.shields.io/badge/license-MIT-lightgrey)

**EchoScope AI** is a powerful market intelligence dashboard designed for creators, agencies, and strategists. It tracks competitor metrics, compares **Shorts vs. Long-form** efficiency, and leverages **Google Gemini AI** to generate strategic reports. 

Visualize trends, identify gaps, and dominate your niche with data-driven insights.

---

## 🚀 Key Features

### 🧠 AI-Powered Strategy
- **Macro Analysis:** One-click AI report on the entire niche. Who dominates? Who is rising? Is the market saturated?
- **Micro Analysis:** Deep-dive audits for specific channels. What formats work best? What topics are trending?

### 📊 Advanced Visualization
- **Market Share:** Visualize who owns the audience in your niche.
- **Format Battle:** Automatically distinguish between **Shorts** and **Long-form** videos to see which strategy yields better efficiency.
- **Efficiency Index:** Identify "Rising Stars"—channels with fewer subscribers but massive engagement.

### 🔎 Deep Dive Tools
- **Interactive Rankings:** Sortable tables with high-performance metrics.
- **Visual Gallery:** View video thumbnails and watch top-performing content directly from the dashboard.
- **Daily Progression:** Track views and upload consistency over time.

---

## 🛠️ Tech Stack

- **Core:** Python 3.12+
- **Frontend:** Streamlit (Custom Dark/Red Theme)
- **Data Engine:** Pandas, SQLite
- **Visualization:** Plotly, AgGrid
- **Intelligence:** Google Gemini 1.5 Flash (Generative AI)
- **Data Source:** YouTube Data API v3

---

## Installation & Setup

### Prerequisites
- Python 3.10 or higher installed.
- A **YouTube Data API v3** Key.
- A **Google Gemini API** Key.

## 1. Clone the Repository
```bash
git clone [https://github.com/your-username/echoscope-ai.git](https://github.com/your-username/echoscope-ai.git)
cd echoscope-ai

2. Configure API Keys
Note: For security, never commit your API keys to GitHub.

Open bot_v1.py and paste your YouTube Data API Key:
```
## 2. Configure API Keys
Note: For security, never commit your API keys to GitHub.

###  1. Open bot_v1.py and paste your YouTube Data API Key: 

```bash
API_KEY = 'YOUR_YOUTUBE_KEY_HERE'
```
#### 2. Open app.py and paste your Gemini API Key:
```bash
 GOOGLE_API_KEY = "YOUR_GEMINI_KEY_HERE"
```
##### 3. Run the App
 **Option A:** The Easy Way (Windows)
Simply double-click the INICIAR_TRACKER.bat file.

- It automatically checks for Python.

- It installs all dependencies (requirements.txt).

- It launches the dashboard in your browser.
 
 **Option B:** Manual Way (Terminal)
 ```bash
pip install -r requirements.txt
streamlit run app.py
```
## 📖 How to Use
🚀 **New Collection:**

- Go to the sidebar and select "New Collection".

- Enter a list of channel handles (e.g., @ChannelName).

- Select the date range.

- Click Start Scan. The bot will fetch data and save it to the local database (youtube_tracker.db).

📊 **Dashboard:**

- Switch to the "Dashboard" tab.

- Select a Snapshot (Date range) from the dropdown.

- Macro View: Analyze the Niche Intelligence, Charts, and Rankings.

- Micro View: Click on any channel in the table to see detailed stats, graphs, and top videos.

- AI Insights: Click the "✨ Analyze" buttons to get instant strategic advice from Gemini.
 
📂 **Project Structure**
 ```bash
EchoScope_AI/
│
├── app.py                # Main Application (Frontend & Logic)
├── bot_v1.py             # Data Engine (YouTube API & Database)
├── requirements.txt      # Project Dependencies
├── INICIAR_TRACKER.bat   # One-click Launcher for Windows
├── youtube_tracker.db    # Local SQLite Database (Auto-generated)
└── README.md             # Project Documentation
```