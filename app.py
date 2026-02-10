import streamlit as st
import pandas as pd
import sqlite3
import plotly.express as px
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode, GridUpdateMode, DataReturnMode
import streamlit.components.v1 as components
import os
import time
from datetime import date
import google.generativeai as genai 

# --- GEMINI API CONFIGURATION ---
# ⚠️ Replace with your actual Gemini API Key
GOOGLE_API_KEY = "YOUR_GEMINI_API_KEY" 

# --- IMPORT ENGINE ---
try:
    # We alias the imported function to match the old naming if necessary, 
    # or update usage below. using 'fetch_youtube_data' from the new bot_v1.
    from bot_v1 import API_KEY, DatabaseManager, fetch_youtube_data
except ImportError:
    st.error("CRITICAL ERROR: Could not import 'bot_v1.py'. Ensure the file exists in the same directory.")
    st.stop()

# --- 1. PAGE CONFIG ---
st.set_page_config(layout="wide", page_title="EchoScope AI", page_icon="🔴", initial_sidebar_state="expanded")

# --- 2. GLOBAL CSS ---
st.markdown("""
    <style>
    /* Dark Theme Base */
    .stApp { background-color: #0E1117; color: white; }
    .block-container { padding-top: 1rem; }
    
    /* AgGrid Cursor Logic */
    div[class^="ag-row"] { cursor: pointer !important; }
    div[class^="ag-row"]:hover { background-color: #E5091444 !important; }
    
    /* Input Fields Styling */
    .stTextInput > div > div > input { color: white; background-color: #161B22; }
    .stTextArea > div > div > textarea { color: white; background-color: #161B22; }
    .stDateInput > div > div > input { color: white; background-color: #161B22; }
    
    /* Primary Button */
    .stButton > button {
        background-color: #E50914;
        color: white;
        border: none;
        width: 100%;
        font-weight: bold;
    }
    .stButton > button:hover { background-color: #ff0f1f; }
    
    /* AI Response Box */
    .ai-box {
        background-color: #161B22;
        border: 1px solid #30363D;
        padding: 20px;
        border-radius: 10px;
        border-left: 5px solid #4285F4; /* Google Blue */
        margin-bottom: 20px;
    }
    .ai-title {
        color: #4285F4;
        font-weight: bold;
        font-size: 1.2rem;
        margin-bottom: 10px;
    }
    </style>
""", unsafe_allow_html=True)

# --- 3. DATABASE CONNECTION ---
PROJECT_PATH = os.path.dirname(os.path.abspath(__file__))
DB_NAME = "youtube_tracker.db"
DB_PATH = os.path.join(PROJECT_PATH, DB_NAME)

def get_db_connection():
    if not os.path.exists(DB_PATH): 
        # Initialize DB if it doesn't exist
        db = DatabaseManager(db_name=DB_PATH)
        return sqlite3.connect(DB_PATH, check_same_thread=False)
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def list_available_snapshots():
    if not os.path.exists(DB_PATH): return [], pd.DataFrame()
    conn = get_db_connection()
    try:
        # SQL queries must use original Portuguese column names for compatibility
        query = "SELECT DISTINCT periodo_analisado_inicio, periodo_analisado_fim FROM historico_coletas ORDER BY periodo_analisado_inicio DESC"
        df = pd.read_sql(query, conn); conn.close()
        if not df.empty:
            return df.apply(lambda x: f"{x['periodo_analisado_inicio']} to {x['periodo_analisado_fim']}", axis=1).tolist(), df
        return [], pd.DataFrame()
    except: return [], pd.DataFrame()

# --- 4. DATA LOADING FUNCTIONS ---
def load_macro_data(start_date, end_date):
    conn = get_db_connection()
    # Note: 'Views Reais' is hardcoded in DB from previous logic. Mapping to 'Long-form' in UI.
    query = f"""
        SELECT h.channel_handle as Channel, MAX(h.inscritos_no_momento) as Subs,
        COUNT(v.id) as Videos_Published, SUM(v.views) as Total_Views,
        SUM(CASE WHEN v.categoria = 'Views Reais' OR v.categoria = 'Long-form' THEN v.views ELSE 0 END) as Long_Views,
        SUM(CASE WHEN v.categoria = 'Shorts' THEN v.views ELSE 0 END) as Shorts_Views,
        AVG(v.eficiencia_percent) as Avg_Efficiency
        FROM videos_detalhes v JOIN historico_coletas h ON v.coleta_id = h.id
        WHERE h.periodo_analisado_inicio = '{start_date}' AND h.periodo_analisado_fim = '{end_date}'
        GROUP BY h.channel_handle ORDER BY Long_Views DESC
    """
    df = pd.read_sql(query, conn); conn.close()
    return df

def load_channel_details(channel_handle, start_date, end_date):
    conn = get_db_connection()
    query = f"""
        SELECT v.data_publicacao as date, v.titulo as title, v.categoria as category, v.views, v.thumbnail_url, v.video_id
        FROM videos_detalhes v JOIN historico_coletas h ON v.coleta_id = h.id
        WHERE h.channel_handle = '{channel_handle}'
          AND h.periodo_analisado_inicio = '{start_date}' AND h.periodo_analisado_fim = '{end_date}'
        ORDER BY v.data_publicacao ASC
    """
    df = pd.read_sql(query, conn); conn.close()
    return df

def format_thousands(value):
    # Using US standard (comma for thousands)
    return f"{int(value):,}" if not pd.isna(value) else "-"

# --- 5. AI LOGIC (GEMINI) ---

def query_gemini(prompt):
    if "YOUR_KEY" in GOOGLE_API_KEY:
        return "⚠️ Error: Please configure your GOOGLE_API_KEY in app.py first!"
    try:
        genai.configure(api_key=GOOGLE_API_KEY)
        model = genai.GenerativeModel('gemini-1.5-flash') 
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Gemini Connection Error: {e}"

def generate_niche_prompt(df_macro):
    """Creates a market-wide statistical summary for the AI."""
    
    top_3_volume = df_macro.sort_values(by='Total_Views', ascending=False).head(3)
    resumo_top_vol = "\n".join([f"- {row['Channel']}: {row['Total_Views']:,.0f} views" for _, row in top_3_volume.iterrows()])

    # Filtering for Efficiency to find rising stars (min 100k views to avoid noise)
    df_filtered = df_macro[df_macro['Total_Views'] > 100000]
    if df_filtered.empty: df_filtered = df_macro
    
    top_3_eff = df_filtered.sort_values(by='Avg_Efficiency', ascending=False).head(3)
    resumo_top_eff = "\n".join([f"- {row['Channel']}: {row['Avg_Efficiency']:.2f}% efficiency ({row['Subs']:,.0f} subs)" for _, row in top_3_eff.iterrows()])
    
    total_market_views = df_macro['Total_Views'].sum()
    total_market_shorts = df_macro['Shorts_Views'].sum()
    share_shorts = (total_market_shorts / total_market_views) * 100 if total_market_views > 0 else 0

    prompt = f"""
    Act as a Senior Digital Market Strategist. Analyze this YOUTUBE NICHE based on the consolidated data below:
    
    MARKET STATE (MACRO):
    - Total Channels Monitored: {len(df_macro)}
    - Total Niche Volume: {total_market_views:,.0f} Views
    - Format Battle: {share_shorts:.1f}% of views come from Shorts vs {(100-share_shorts):.1f}% from Long-form.
    
    NICHE KINGS (Highest Audience):
    {resumo_top_vol}
    
    RISING STARS (Highest Efficiency/Virality):
    {resumo_top_eff}
    *(Note: High efficiency indicates the channel gets massive views relative to subscriber count)*
    
    YOUR STRATEGIC ANALYSIS:
    1. **Who Dominates?** Identify the leader and theorize why (based on volume).
    2. **Who is Rising?** Analyze the 'Rising Stars'. Is there a smaller channel disrupting the giants?
    3. **Format Trend:** Does this niche prefer Shorts or Long-form? What does this imply about the audience?
    4. **Verdict:** Is the niche saturated (dominated by one) or fragmented (competitive)?
    
    Be direct, professional, and use bullet points. Respond in English.
    """
    return prompt

def generate_channel_prompt(df_channel, channel_name):
    total_videos = len(df_channel)
    total_views = df_channel['views'].sum()
    avg_views = df_channel['views'].mean()
    shorts = df_channel[df_channel['category'] == 'Shorts']
    longs = df_channel[(df_channel['category'] == 'Views Reais') | (df_channel['category'] == 'Long-form')]
    
    top_5 = df_channel.sort_values(by='views', ascending=False).head(5)
    list_top = "\n".join([f"- '{row['title']}' ({row['category']}): {int(row['views']):,} views" for _, row in top_5.iterrows()])
    
    prompt = f"""
    Analyze the YouTube channel '{channel_name}':
    
    DATA: 
    - Videos: {total_videos} | Total Views: {total_views:,.0f} | Avg Views: {avg_views:,.0f}
    - Format Split: {len(shorts)} Shorts | {len(longs)} Long-form
    
    TOP 5 PERFORMING VIDEOS:
    {list_top}
    
    TASK: 
    1. Identify the winning format. 
    2. Find patterns in successful titles. 
    3. Provide 3 tactical suggestions for growth.
    
    Respond in English.
    """
    return prompt

# ==============================================================================
# NAVIGATION
# ==============================================================================
st.sidebar.title("🔴 EchoScope AI")
page = st.sidebar.radio("Navigate", ["📊 Dashboard", "🚀 New Collection"])

# ==============================================================================
# PAGE 1: NEW COLLECTION
# ==============================================================================
if page == "🚀 New Collection":
    st.title("🚀 New Data Collection")
    st.markdown("Configure the channels and date range for the bot to analyze.")
    
    with st.container(border=True):
        col_date1, col_date2 = st.columns(2)
        with col_date1: start = st.date_input("Start Date", value=date(2024, 1, 1))
        with col_date2: end = st.date_input("End Date", value=date.today())
        
        channels_text = st.text_area("Channel List (One per line)", height=200, placeholder="@ChannelA\n@ChannelB")
        btn_process = st.button("START SCAN", type="primary")

    if btn_process:
        if not channels_text.strip(): st.warning("Please insert at least one channel handle.")
        else:
            channel_list = [c.strip() for c in channels_text.split('\n') if c.strip()]
            status_bar = st.progress(0, text="Initializing...")
            log = st.empty()
            
            db_manager = DatabaseManager(DB_PATH)
            success_count = 0
            
            for i, channel in enumerate(channel_list):
                status_bar.progress(int((i/len(channel_list))*100), text=f"Scanning {channel}...")
                try:
                    # Using the updated fetch function
                    df, subs = fetch_youtube_data(API_KEY, channel, str(start), str(end))
                    
                    if df is not None and not df.empty:
                        # Saving to DB using the manager from bot_v1
                        db_manager.save_collection(channel, subs, str(start), str(end), df)
                        log.write(f"✅ {channel}: {len(df)} videos found.")
                        success_count += 1
                    else: log.write(f"⚠️ {channel}: No data found.")
                except Exception as e: st.error(f"Error {channel}: {e}")
            
            status_bar.progress(100, text="Done!")
            st.success(f"Process complete! {success_count}/{len(channel_list)} channels processed.")
            st.info("Go to '📊 Dashboard' to view the results.")

# ==============================================================================
# PAGE 2: DASHBOARD
# ==============================================================================
elif page == "📊 Dashboard":
    st.sidebar.header("Filters")
    options, df_dates = list_available_snapshots()
    
    if options:
        choice = st.sidebar.selectbox("📅 Select Snapshot:", options)
        idx = options.index(choice)
        start_snap, end_snap = df_dates.iloc[idx]['periodo_analisado_inicio'], df_dates.iloc[idx]['periodo_analisado_fim']
        
        df_macro = load_macro_data(start_snap, end_snap)
        
        if df_macro.empty: st.warning("Snapshot is empty."); st.stop()

        st.title("📊 Intelligence Dashboard")
        
        # --- MACRO AI ANALYSIS ---
        with st.expander("🧠 Niche Intelligence (Market Analysis)", expanded=False):
            st.markdown("Use AI to understand overall market behavior before looking at specific channels.")
            if st.button("✨ Analyze Niche Trends", type="primary"):
                with st.spinner("Gemini is analyzing all competitors..."):
                    prompt_macro = generate_niche_prompt(df_macro)
                    resp_macro = query_gemini(prompt_macro)
                    st.markdown(f"""
                        <div class="ai-box">
                            <div class="ai-title">📊 Market Report (Gemini)</div>
                            {resp_macro}
                        </div>
                    """, unsafe_allow_html=True)
        
        st.markdown("---")

        # KPIs
        c1, c2, c3, c4 = st.columns(4)
        with c1: st.metric("TOTAL VIEWS", format_thousands(df_macro['Total_Views'].sum()))
        with c2: st.metric("LONG-FORM VIEWS", format_thousands(df_macro['Long_Views'].sum()))
        with c3: st.metric("SHORTS VIEWS", format_thousands(df_macro['Shorts_Views'].sum()))
        with c4: st.metric("ACTIVE CHANNELS", len(df_macro))

        st.markdown("---")

        # Charts
        g1, g2 = st.columns([2, 1])
        with g1:
            st.subheader("Comparison: Long-form vs Shorts")
            df_melt = df_macro.melt(id_vars=['Channel'], value_vars=['Long_Views', 'Shorts_Views'], var_name='Type', value_name='Views')
            # Mapping internal DB names to clean labels
            clean_map = {'Long_Views': 'Long-Form', 'Shorts_Views': 'Shorts'}
            df_melt['Type'] = df_melt['Type'].map(clean_map)
            
            fig_bar = px.bar(df_melt, x='Channel', y='Views', color='Type', barmode='group', 
                             template="plotly_dark", color_discrete_map={'Long-Form': '#E50914', 'Shorts': '#555555'})
            fig_bar.update_layout(height=350)
            st.plotly_chart(fig_bar, use_container_width=True)
        with g2:
            st.subheader("Market Share (Long-Form)")
            fig_pie = px.pie(df_macro, values='Long_Views', names='Channel', hole=0.4, template="plotly_dark", 
                             color_discrete_sequence=px.colors.sequential.RdBu)
            fig_pie.update_layout(height=350)
            st.plotly_chart(fig_pie, use_container_width=True)

        st.markdown("---")

        # Interactive Table
        st.subheader("Channel Ranking")
        
        # JS Formatters for English/US Locale
        js_thousands = JsCode("""function(params) { return params.value !== undefined ? params.value.toLocaleString('en-US') : ''; }""")
        js_percent = JsCode("""function(params) { return params.value !== undefined ? params.value.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2}) + '%' : ''; }""")
        custom_css = { ".ag-row": {"cursor": "pointer !important"}, ".ag-row-hover": {"background-color": "#E5091444 !important"} }

        gb = GridOptionsBuilder.from_dataframe(df_macro)
        gb.configure_selection(selection_mode="single", use_checkbox=False)
        gb.configure_pagination(paginationAutoPageSize=False, paginationPageSize=15)
        gb.configure_default_column(groupable=True, value=True, enableRowGroup=True, aggFunc='sum', editable=False, resizable=True)
        
        # Configure Columns (Mapping DB names to UI names)
        gb.configure_column("Channel", pinned="left", width=180)
        gb.configure_column("Subs", type=["numericColumn"], cellRenderer=js_thousands, width=120)
        gb.configure_column("Videos_Published", header_name="Videos", type=["numericColumn"], width=100)
        gb.configure_column("Total_Views", type=["numericColumn"], cellRenderer=js_thousands, width=150)
        gb.configure_column("Long_Views", header_name="Long Views", type=["numericColumn"], cellRenderer=js_thousands, sort="desc", width=150)
        gb.configure_column("Shorts_Views", header_name="Shorts Views", type=["numericColumn"], cellRenderer=js_thousands, width=150)
        gb.configure_column("Avg_Efficiency", header_name="Efficiency", cellRenderer=js_percent, width=130)

        gridOptions = gb.build()

        grid_response = AgGrid(
            df_macro, gridOptions=gridOptions, update_mode=GridUpdateMode.SELECTION_CHANGED, 
            data_return_mode=DataReturnMode.FILTERED_AND_SORTED, fit_columns_on_grid_load=True, 
            theme='balham-dark', height=400, width='100%', allow_unsafe_jscode=True, custom_css=custom_css, 
            key='grid_ranking'
        )

        selected = grid_response['selected_rows']
        current_channel = None

        if selected is not None:
            if isinstance(selected, pd.DataFrame) and not selected.empty:
                current_channel = selected.iloc[0]['Channel']
            elif isinstance(selected, list) and len(selected) > 0:
                item = selected[0]
                if isinstance(item, dict): current_channel = item.get('Channel')
                elif isinstance(item, str): current_channel = item

        # --- MICRO ANALYSIS (CHANNEL DRILL-DOWN) ---
        if current_channel:
            st.markdown('<div id="analysis_focus"></div>', unsafe_allow_html=True)
            st.markdown("---")
            st.markdown(f"### 🔎 Deep Dive: <span style='color:#E50914'>{current_channel}</span>", unsafe_allow_html=True)
            
            df_details = load_channel_details(current_channel, start_snap, end_snap)
            
            if not df_details.empty:
                c1, c2 = st.columns([2, 1])
                
                with c1:
                    st.caption("Daily Progression")
                    # Clean categories for chart
                    df_details['category'] = df_details['category'].replace({'Views Reais': 'Long-form'})
                    
                    df_daily = df_details.groupby(['date', 'category'])['views'].sum().reset_index()
                    fig = px.line(df_daily, x='date', y='views', color='category', 
                                  template="plotly_dark", color_discrete_map={'Long-form': '#E50914', 'Shorts': '#555555'})
                    fig.update_layout(height=400)
                    st.plotly_chart(fig, use_container_width=True)
                    
                with c2:
                    st.caption("Top 10 Videos")
                    top10 = df_details.sort_values('views', ascending=False).head(10).copy()
                    
                    with st.container(height=400):
                        for index, row in top10.iterrows():
                            col_img, col_info = st.columns([1, 2])
                            with col_img:
                                if row['thumbnail_url']: st.image(row['thumbnail_url'], use_container_width=True)
                                else: st.write("🖼️")
                            with col_info:
                                link = f"https://www.youtube.com/watch?v={row['video_id']}"
                                title = row['title']
                                if len(title) > 50: title = title[:50] + "..."
                                st.markdown(f"**[{title}]({link})**")
                                cat_label = "Long-form" if row['category'] == "Views Reais" else row['category']
                                st.caption(f"👀 {format_thousands(row['views'])} views | {cat_label}")
                            st.divider()

                # --- CHANNEL AI BUTTON ---
                st.markdown("---")
                st.subheader("🤖 AI Consultant (Channel Specific)")
                
                if st.button(f"✨ Analyze Strategy for {current_channel}", type="secondary", use_container_width=True):
                    with st.spinner(f"Gemini is auditing {current_channel}..."):
                        prompt_ai = generate_channel_prompt(df_details, current_channel)
                        ai_response = query_gemini(prompt_ai)
                        st.markdown(f"""
                        <div class="ai-box">
                            <div class="ai-title">📊 Diagnosis: {current_channel}</div>
                            {ai_response}
                        </div>
                        """, unsafe_allow_html=True)

            # Auto-Scroll Script
            timestamp = int(time.time())
            js_scroll = f"""
                <script>
                    function scrollToFocus() {{
                        var element = window.parent.document.getElementById('analysis_focus');
                        if (!element) {{
                            window.parent.scrollTo({{ top: window.parent.document.body.scrollHeight, behavior: 'smooth' }});
                        }} else {{
                            element.scrollIntoView({{behavior: 'smooth', block: 'start'}});
                        }}
                    }}
                    setTimeout(scrollToFocus, 300);
                </script>
            """
            components.html(js_scroll, height=0)
    else:
        st.info("👈 Use the sidebar menu to go to **New Collection** and create your first report.")