import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import edge_tts
import asyncio
import random
from streamlit_mic_recorder import speech_to_text
from rapidfuzz import fuzz
from streamlit_gsheets import GSheetsConnection  # 新增引用

# ==========================================
# 1. UI 設定
# ==========================================
st.set_page_config(page_title="Thai Master SRS 🇹🇭", page_icon="🐘", layout="centered")

st.markdown("""
<style>
    .stApp { background-color: #fdfbf7; }
    
    .thai-huge { font-size: 60px !important; font-weight: bold; color: #2c3e50; font-family: 'Thonburi', 'Sarabun', sans-serif; text-align: center; margin: 20px 0; }
    .thai-big { font-size: 40px !important; font-weight: bold; color: #2c3e50; font-family: 'Thonburi', 'Sarabun', sans-serif; text-align: center; }
    .pron-text { font-size: 24px; color: #e67e22; font-weight: bold; text-align: center; margin-bottom: 10px; }
    .meaning-text { font-size: 20px; color: #7f8c8d; text-align: center; margin-bottom: 20px; }

    .card { 
        background-color: white; 
        padding: 30px; 
        border-radius: 20px; 
        box-shadow: 0 4px 15px rgba(0,0,0,0.05); 
        text-align: center; 
        border: 2px solid #eee; 
        margin-bottom: 20px;
    }
    
    .tag-badge {
        background-color: #2980b9;
        color: white;
        padding: 5px 15px;
        border-radius: 15px;
        font-size: 0.9rem;
        display: inline-block;
        margin-bottom: 15px;
    }
    
    .status-badge {
        padding: 5px 10px;
        border-radius: 5px;
        font-size: 0.8rem;
        font-weight: bold;
        margin-bottom: 10px;
        display: inline-block;
    }
    .status-due { background-color: #e74c3c; color: white; }
    .status-free { background-color: #27ae60; color: white; }
    
    .result-correct { background-color: #d4edda; color: #155724; padding: 20px; border-radius: 10px; margin-top: 20px; }
    .result-wrong { background-color: #f8d7da; color: #721c24; padding: 20px; border-radius: 10px; margin-top: 20px; }

    footer {visibility: hidden;}
    
    /* 按鈕樣式 */
    .stButton button {
        height: 60px;
        font-size: 18px;
        border-radius: 12px;
        font-weight: 500;
        width: 100%;
    }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. 資料處理 (Google Sheets 版本)
# ==========================================

# 建立連線物件
conn = st.connection("gsheets", type=GSheetsConnection)

def load_data():
    try:
        # 使用 ttl=0 確保每次都讀取最新資料，不使用快取
        df = conn.read(worksheet="Sheet1", ttl=0)
    except Exception as e:
        st.error(f"無法讀取 Google Sheet: {e}")
        return pd.DataFrame()

    df.columns = df.columns.str.strip()
    required_cols = ['Thai', 'TTS_Text', 'Pronunciation', 'Meaning', 'Category', 'Times', 'Next']
    for col in required_cols:
        if col not in df.columns:
            if col == 'Times': df[col] = 0
            elif col == 'Next': df[col] = datetime.now().date()
            else: df[col] = ""
    
    df['Times'] = pd.to_numeric(df['Times'], errors='coerce').fillna(0).astype(int)
    # 確保日期格式正確
    df['Next'] = pd.to_datetime(df['Next'], errors='coerce').fillna(pd.Timestamp.now()).dt.date
    return df.dropna(subset=['Thai'])

def save_data(df):
    try:
        # 將日期轉為字串格式存入 Google Sheet，避免格式錯亂
        save_df = df.copy()
        save_df['Next'] = pd.to_datetime(save_df['Next']).dt.strftime('%Y-%m-%d')
        
        conn.update(worksheet="Sheet1", data=save_df)
        st.cache_data.clear() # 清除快取以防萬一
    except Exception as e:
        st.error(f"⚠️ 無法存檔至 Google Sheet：{e}")

async def generate_audio(text):
    communicate = edge_tts.Communicate(text, "th-TH-PremwadeeNeural")
    audio_data = b""
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_data += chunk["data"]
    return audio_data

def get_distractors(df, current_row, n=3):
    category = current_row['Category']
    pool = df[(df['Category'] == category) & (df['Thai'] != current_row['Thai'])]
    if len(pool) < n:
        return pool.sample(len(pool)).to_dict('records')
    return pool.sample(n).to_dict('records')

# ==========================================
# 3. 初始化 Session State
# ==========================================
if 'df' not in st.session_state:
    st.session_state.df = load_data()
if 'current_idx' not in st.session_state:
    st.session_state.current_idx = None
if 'last_idx' not in st.session_state:
    st.session_state.last_idx = None 
if 'quiz_data' not in st.session_state:
    st.session_state.quiz_data = {}
if 'mode_status' not in st.session_state:
    st.session_state.mode_status = "" 
if 'stage' not in st.session_state:
    st.session_state.stage = 'quiz' # 'quiz' or 'result'
if 'result_info' not in st.session_state:
    st.session_state.result_info = {}

st.title("🇹🇭 Thai Master SRS")

if st.sidebar.button("🔄 Reload Data"):
    st.session_state.df = load_data()
    st.session_state.current_idx = None
    st.session_state.stage = 'quiz'
    st.rerun()

df = st.session_state.df
today = datetime.now().date()

# ==========================================
# 4. 邏輯流程
# ==========================================

# --- A. 選題階段 (Selection Phase) ---
if st.session_state.current_idx is None and st.session_state.stage == 'quiz':
    
    # 1. 找出所有「到期」的題目
    due_indices = df[df['Next'] <= today].index.tolist()
    
    target_pool = []
    
    if due_indices:
        target_pool = due_indices
        st.session_state.mode_status = f"📝 複習模式 (剩 {len(due_indices)} 題)"
    else:
        target_pool = df.index.tolist()
        st.session_state.mode_status = "🔀 隨機練習模式"

    # 2. 防重複
    if len(target_pool) > 1 and st.session_state.last_idx in target_pool:
        target_pool.remove(st.session_state.last_idx)
    
    if not target_pool:
        st.warning("資料庫空的，請檢查 Google Sheet。")
        st.stop()
        
    idx = random.choice(target_pool)
    st.session_state.current_idx = idx
    row = df.loc[idx]
    
    # 3. 決定題型
    tts_text = row['TTS_Text'] if pd.notna(row['TTS_Text']) and str(row['TTS_Text']).strip() != "" else row['Thai']
    category = row['Category']
    current_times = int(row['Times'])
    
    mode = ""
    options = []
    
    # --- Category Logic ---
    if category == 'Char':
        possible = ['char_pron_to_thai', 'char_thai_to_meaning']
        if current_times > 3: possible.append('char_listening_typing')
        mode = random.choice(possible)
        
    elif category == 'Word':
        possible = ['word_thai_to_meaning', 'speaking_thai_show', 'word_listen_to_thai']
        if current_times > 3: possible.append('word_listening_typing')
        mode = random.choice(possible)
        
    elif category == 'Sentence':
        mode = random.choice(['sentence_listen_to_meaning', 'speaking_sentence_text', 'speaking_sentence_shadowing'])

    # 準備選項
    if mode in ['char_pron_to_thai', 'char_thai_to_meaning', 'word_thai_to_meaning', 'word_listen_to_thai', 'sentence_listen_to_meaning']:
        distractors = get_distractors(df, row)
        opts = distractors + [row.to_dict()]
        random.shuffle(opts)
        options = opts

    st.session_state.quiz_data = {
        'mode': mode,
        'tts_text': tts_text,
        'thai': row['Thai'],
        'meaning': row['Meaning'],
        'pronunciation': row['Pronunciation'],
        'options': options
    }
    st.rerun()

# --- B. 顯示階段 (Display Phase) ---
if st.session_state.current_idx is not None:
    idx = st.session_state.current_idx
    row = df.loc[idx]
    q = st.session_state.quiz_data
    mode = q['mode']
    
    # 生成音檔
    audio_bytes = asyncio.run(generate_audio(q['tts_text']))

    # 顯示狀態
    status_class = "status-due" if "複習" in st.session_state.mode_status else "status-free"
    st.markdown(f'<div style="text-align:center;"><span class="status-badge {status_class}">{st.session_state.mode_status}</span></div>', unsafe_allow_html=True)

    # st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown(f'<span class="tag-badge">{row["Category"]} | Lv.{row["Times"]}</span>', unsafe_allow_html=True)

    # ========================================================
    #  PART 1: 作答區 (Quiz Stage)
    # ========================================================
    if st.session_state.stage == 'quiz':
        
        # --- ⌨️ Typing Mode ---
        if 'typing' in mode:
            st.subheader("⌨️ 聽寫挑戰")
            st.audio(audio_bytes, format='audio/mpeg', autoplay=True)
            
            with st.form(key='typing_form'):
                user_input = st.text_input("請輸入泰文...", key="thai_input")
                submit_btn = st.form_submit_button("送出答案", use_container_width=True)
            
            if submit_btn:
                is_correct = (user_input.strip() == q['thai'].strip())
                st.session_state.result_info = {'is_correct': is_correct, 'user_input': user_input}
                
                # Update Data
                if is_correct:
                    current_times = int(df.at[idx, 'Times'])
                    df.at[idx, 'Times'] = current_times + 1
                    df.at[idx, 'Next'] = today + timedelta(days=current_times * 2 + 1)
                else:
                    df.at[idx, 'Times'] = current_times
                    df.at[idx, 'Next'] = today
                
                save_data(df)
                st.session_state.stage = 'result'
                st.rerun()

        # --- 🎙️ Speaking Mode ---
        elif 'speaking' in mode:
            st.subheader("🎙️ Speaking Challenge")
            
            if mode == 'speaking_thai_show': 
                st.markdown(f'<div class="thai-huge">{q["thai"]}</div>', unsafe_allow_html=True)
                with st.expander("💡 提示"): st.write(f"{q['pronunciation']} ({q['meaning']})")
                    
            elif mode == 'speaking_sentence_text': 
                st.markdown(f'<div class="thai-big">{q["thai"]}</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="meaning-text">{q["meaning"]}</div>', unsafe_allow_html=True)
                
            elif mode == 'speaking_sentence_shadowing': 
                st.markdown("### 🎧 Listen & Repeat")
                st.audio(audio_bytes, format='audio/mpeg', autoplay=True)
                st.caption("請聽音檔，然後唸出來")

            st.markdown("---")
            text = speech_to_text(language='th', start_prompt="🔴 錄音", stop_prompt="⏹️ 停止", just_once=True, key=f'STT_{idx}')
            
            if text:
                target = str(q['tts_text']).strip()
                score = fuzz.ratio(text, target)
                pass_score = 70 if row['Category'] == 'Sentence' else 80
                is_correct = (score >= pass_score)
                
                st.session_state.result_info = {'is_correct': is_correct, 'user_input': text, 'score': score}
                
                if is_correct:
                    current_times = int(df.at[idx, 'Times'])
                    df.at[idx, 'Times'] = current_times + 1
                    df.at[idx, 'Next'] = today + timedelta(days=current_times * 2 + 1)
                else:
                    df.at[idx, 'Times'] = 0
                    df.at[idx, 'Next'] = today
                
                save_data(df)
                st.session_state.stage = 'result'
                st.rerun()

        # --- 🖱️ Choice Mode ---
        else:
            if mode == 'char_pron_to_thai':
                st.markdown("### 請選出對應的泰文")
                st.markdown(f'<div class="pron-text">{q["pronunciation"]}</div>', unsafe_allow_html=True)
            elif mode == 'char_thai_to_meaning':
                st.markdown("### 這個字是什麼意思？")
                st.markdown(f'<div class="thai-huge">{q["thai"]}</div>', unsafe_allow_html=True)
            elif mode == 'word_thai_to_meaning':
                st.markdown("### 這個單字的意思是？")
                st.markdown(f'<div class="thai-big">{q["thai"]}</div>', unsafe_allow_html=True)
            elif mode == 'word_listen_to_thai':
                st.markdown("### 🎧 聽到的是哪個字？")
                st.audio(audio_bytes, format='audio/mpeg', autoplay=True)
            elif mode == 'sentence_listen_to_meaning':
                st.markdown("### 🎧 這句話是什麼意思？")
                st.audio(audio_bytes, format='audio/mpeg', autoplay=True)

            st.write("")
            cols = st.columns(2)
            for i, opt in enumerate(q['options']):
                label = opt['Thai'] if mode in ['char_pron_to_thai', 'word_listen_to_thai'] else opt['Meaning']
                
                if cols[i%2].button(label, key=f"btn_{i}", use_container_width=True):
                    is_correct = (opt['Thai'] == q['thai'])
                    st.session_state.result_info = {'is_correct': is_correct}
                    
                    if is_correct:
                        current_times = int(df.at[idx, 'Times'])
                        df.at[idx, 'Times'] = current_times + 1
                        df.at[idx, 'Next'] = today + timedelta(days=current_times * 2 + 1)
                    else:
                        df.at[idx, 'Times'] = 0
                        df.at[idx, 'Next'] = today
                    
                    save_data(df)
                    st.session_state.stage = 'result'
                    st.rerun()

    # ========================================================
    #  PART 2: 結果與檢討區 (Result Stage)
    # ========================================================
    elif st.session_state.stage == 'result':
        res = st.session_state.result_info
        
        if 'shadowing' in mode or 'listening_typing' in mode:
            st.markdown(f'<div class="thai-big">{q["thai"]}</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="meaning-text">{q["meaning"]}</div>', unsafe_allow_html=True)

        if res['is_correct']:
            st.markdown(f"""
            <div class="result-correct">
                <h2>✅ 答對了！</h2>
                <p>標準答案: <b>{q['thai']}</b></p>
                <p>{q['meaning']} | {q['pronunciation']}</p>
            </div>
            """, unsafe_allow_html=True)
            if 'score' in res: st.caption(f"發音分數: {res['score']}")
        else:
            st.markdown(f"""
            <div class="result-wrong">
                <h2>❌ 答錯了...</h2>
                <p>標準答案: <b>{q['thai']}</b></p>
                <p>{q['meaning']} | {q['pronunciation']}</p>
            </div>
            """, unsafe_allow_html=True)
            if 'user_input' in res: st.write(f"你的輸入: {res['user_input']}")
            if 'score' in res: st.caption(f"發音分數: {res['score']}")
            
            st.write("🔊 聽聽看標準發音：")
            st.audio(audio_bytes, format='audio/mpeg')

        st.write("")
        if st.button("➡️ 下一題", type="primary", use_container_width=True):
            st.session_state.last_idx = idx
            st.session_state.current_idx = None
            st.session_state.stage = 'quiz'
            st.session_state.result_info = {}
            st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)