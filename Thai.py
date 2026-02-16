import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import edge_tts
import asyncio
import random
from streamlit_mic_recorder import speech_to_text
from rapidfuzz import fuzz
from streamlit_gsheets import GSheetsConnection  

# === [新增] 引入畫布與 AI 視覺辨識需要的套件 ===
from streamlit_drawable_canvas import st_canvas
import google.generativeai as genai
from PIL import Image
import json
import numpy as np
# ===============================================

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

# === [新增] 設定左側 API Key 輸入 ===
with st.sidebar:
    st.subheader("⚙️ AI 設定")
    gemini_api_key = st.text_input("Gemini API Key (用於手寫辨識)", type="password")
    if gemini_api_key:
        genai.configure(api_key=gemini_api_key)
    st.markdown("---")
    if st.button("🔄 Reload Data"):
        st.session_state.df = load_data()
        st.session_state.current_idx = None
        st.session_state.stage = 'quiz'
        st.rerun()
# ===============================================

# ==========================================
# 2. 資料處理 (Google Sheets 版本)
# ==========================================

conn = st.connection("gsheets", type=GSheetsConnection)

def load_data():
    try:
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
    df['Next'] = pd.to_datetime(df['Next'], errors='coerce').fillna(pd.Timestamp.now()).dt.date
    return df.dropna(subset=['Thai'])

def save_data(df):
    try:
        save_df = df.copy()
        save_df['Next'] = pd.to_datetime(save_df['Next']).dt.strftime('%Y-%m-%d')
        conn.update(worksheet="Sheet1", data=save_df)
        st.cache_data.clear() 
    except Exception as e:
        st.error(f"⚠️ 無法存檔至 Google Sheet：{e}")

async def generate_audio(text):
    try:
        communicate = edge_tts.Communicate(text, "th-TH-PremwadeeNeural")
        audio_data = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data += chunk["data"]
        return audio_data
    except:
        return b""

def get_distractors(df, current_row, n=3):
    category = current_row['Category']
    pool = df[(df['Category'] == category) & (df['Thai'] != current_row['Thai'])]
    if len(pool) < n:
        return pool.sample(len(pool)).to_dict('records')
    return pool.sample(n).to_dict('records')

# === [新增] AI 手寫圖片辨識邏輯 ===
def evaluate_handwriting(image_array, target_text, meaning):
    if not gemini_api_key:
        return {"is_correct": False, "score": 0, "feedback": "⚠️ 尚未輸入 Gemini API Key，無法啟用 AI 老師批改！"}
    
    try:
        # 將 Canvas 的 RGBA 矩陣轉為 RGB 圖片
        img = Image.fromarray(image_array.astype('uint8'), 'RGBA').convert('RGB')
        
        # 呼叫強大的 Flash 視覺模型
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = f"""
        You are a strict but encouraging Thai language teacher.
        The user was asked to write the Thai text: "{target_text}" (Meaning: {meaning}).
        Look at the provided image of their handwriting on the blackboard.
        
        Evaluate based on these rules:
        1. Is it readable and structurally correct? (Minor proportional mistakes are okay, but wrong characters, missing vowels, or reversed writing are not).
        2. Score from 0 to 100.
        3. Provide brief, actionable feedback in Traditional Chinese (e.g., '寫得很棒！', '圈圈畫反了', '尾巴太長了').
        
        Output ONLY valid JSON in this exact format:
        {{
            "is_correct": true,
            "score": 90,
            "feedback": "string"
        }}
        """
        response = model.generate_content([prompt, img], generation_config={"temperature": 0.2})
        # 清理字串以防 JSON 解析失敗
        text_res = response.text.strip().replace('```json', '').replace('```', '')
        return json.loads(text_res)
    except Exception as e:
        return {"is_correct": False, "score": 0, "feedback": f"系統辨識發生錯誤: {e}"}
# ===============================================


# ==========================================
# 3. 初始化 Session State
# ==========================================
if 'df' not in st.session_state: st.session_state.df = load_data()
if 'current_idx' not in st.session_state: st.session_state.current_idx = None
if 'last_idx' not in st.session_state: st.session_state.last_idx = None 
if 'quiz_data' not in st.session_state: st.session_state.quiz_data = {}
if 'mode_status' not in st.session_state: st.session_state.mode_status = "" 
if 'stage' not in st.session_state: st.session_state.stage = 'quiz' 
if 'result_info' not in st.session_state: st.session_state.result_info = {}

st.title("🇹🇭 Thai Master SRS")

df = st.session_state.df
today = datetime.now().date()

# ==========================================
# 4. 邏輯流程
# ==========================================

# --- A. 選題階段 ---
if st.session_state.current_idx is None and st.session_state.stage == 'quiz':
    due_indices = df[df['Next'] <= today].index.tolist()
    target_pool = []
    
    if due_indices:
        target_pool = due_indices
        st.session_state.mode_status = f"📝 複習模式 (剩 {len(due_indices)} 題)"
    else:
        target_pool = df.index.tolist()
        st.session_state.mode_status = "🔀 隨機練習模式"

    if len(target_pool) > 1 and st.session_state.last_idx in target_pool:
        target_pool.remove(st.session_state.last_idx)
    
    if not target_pool:
        st.warning("資料庫空的，請檢查 Google Sheet。")
        st.stop()
        
    idx = random.choice(target_pool)
    st.session_state.current_idx = idx
    row = df.loc[idx]
    
    tts_text = row['TTS_Text'] if pd.notna(row['TTS_Text']) and str(row['TTS_Text']).strip() != "" else row['Thai']
    category = row['Category']
    current_times = int(row['Times'])
    
    mode = ""
    options = []
    
    # === [修改] Category Logic: 加入手寫模式 ===
    if category == 'Char':
        possible = ['char_pron_to_thai', 'char_thai_to_meaning']
        if current_times > 1: possible.append('char_writing_blind') # 盲寫挑戰
        if current_times > 3: possible.append('char_listening_typing')
        mode = random.choice(possible)
        
    elif category == 'Word':
        possible = ['word_thai_to_meaning', 'word_listen_to_thai']
        if current_times > 0: possible.append('word_writing_copy')  # 看字照抄挑戰
        if current_times > 3: possible.append('word_listening_typing')
        mode = random.choice(possible)
        
    elif category == 'Sentence':
        possible = ['sentence_listen_to_meaning', 'speaking_sentence_text', 'speaking_sentence_shadowing']
        if current_times > 0: possible.append('sentence_writing_copy') # 句子照抄挑戰
        mode = random.choice(possible)
    # ===============================================

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

# --- B. 顯示階段 ---
if st.session_state.current_idx is not None:
    idx = st.session_state.current_idx
    row = df.loc[idx]
    q = st.session_state.quiz_data
    mode = q['mode']
    
    audio_bytes = asyncio.run(generate_audio(q['tts_text']))

    status_class = "status-due" if "複習" in st.session_state.mode_status else "status-free"
    st.markdown(f'<div style="text-align:center;"><span class="status-badge {status_class}">{st.session_state.mode_status}</span></div>', unsafe_allow_html=True)
    st.markdown(f'<span class="tag-badge">{row["Category"]} | Lv.{row["Times"]}</span>', unsafe_allow_html=True)

    if st.session_state.stage == 'quiz':
        
        # === [新增] ✍️ 手寫模式 UI ===
        if 'writing' in mode:
            st.subheader("✍️ 手寫黑板挑戰")
            
            # 判斷是盲寫還是看寫
            if mode == 'char_writing_blind':
                st.markdown("### 請在黑板上默寫出以下字母：")
                st.markdown(f'<div class="pron-text">{q["pronunciation"]} ({q["meaning"]})</div>', unsafe_allow_html=True)
                st.audio(audio_bytes, format='audio/mpeg', autoplay=True) # 播個聲音幫助記憶
            else:
                st.markdown("### 請照著寫出以下泰文（注意細節）：")
                st.markdown(f'<div class="thai-big">{q["thai"]}</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="meaning-text">{q["meaning"]}</div>', unsafe_allow_html=True)

            # 建立畫布
            canvas_result = st_canvas(
                fill_color="rgba(255, 165, 0, 0.3)", 
                stroke_width=6,                       # 畫筆粗細
                stroke_color="#FFFFFF",               # 畫筆白色
                background_color="#2c3e50",           # 黑板色
                height=300,                           # 畫布高度
                width=350,                            # 畫布寬度 (適合手機板)
                drawing_mode="freedraw",
                key=f"canvas_{idx}",
            )
            
            st.caption("🖌️ 寫錯了可以使用左下角的橡皮擦或垃圾桶清空重來喔！")
            
            if st.button("📤 送出給 AI 老師批改", use_container_width=True):
                if canvas_result.image_data is not None:
                    with st.spinner("👀 AI 老師批閱中，請稍候..."):
                        # 呼叫判斷邏輯
                        eval_res = evaluate_handwriting(canvas_result.image_data, q['thai'], q['meaning'])
                        
                        is_correct = eval_res.get('is_correct', False)
                        st.session_state.result_info = {
                            'is_correct': is_correct,
                            'score': eval_res.get('score', 0),
                            'feedback': eval_res.get('feedback', '無法取得回饋'),
                            'user_input': '(已提交手寫圖片)'
                        }
                        
                        # 儲存與計分
                        if is_correct:
                            current_times = int(df.at[idx, 'Times'])
                            df.at[idx, 'Times'] = current_times + 1
                            df.at[idx, 'Next'] = today + timedelta(days=current_times+1)
                        else:
                            df.at[idx, 'Times'] -= 1
                            df.at[idx, 'Next'] = today
                        
                        save_data(df)
                        st.session_state.stage = 'result'
                        st.rerun()
                else:
                    st.warning("⚠️ 請先在黑板上寫字喔！")
        # ===============================================

        elif 'typing' in mode:
            st.subheader("⌨️ 聽寫挑戰")
            st.audio(audio_bytes, format='audio/mpeg', autoplay=True)
            
            with st.form(key='typing_form'):
                user_input = st.text_input("請輸入泰文...", key="thai_input")
                submit_btn = st.form_submit_button("送出答案", use_container_width=True)
            
            if submit_btn:
                is_correct = (user_input.strip() == q['thai'].strip())
                st.session_state.result_info = {'is_correct': is_correct, 'user_input': user_input}
                
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
                    df.at[idx, 'Times'] -= 1
                    df.at[idx, 'Next'] = today
                
                save_data(df)
                st.session_state.stage = 'result'
                st.rerun()

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
                        df.at[idx, 'Times'] -= 1
                        df.at[idx, 'Next'] = today
                    
                    save_data(df)
                    st.session_state.stage = 'result'
                    st.rerun()

    # ========================================================
    #  PART 2: 結果與檢討區 (Result Stage)
    # ========================================================
    elif st.session_state.stage == 'result':
        res = st.session_state.result_info
        
        if 'shadowing' in mode or 'listening_typing' in mode or 'writing' in mode:
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
        else:
            st.markdown(f"""
            <div class="result-wrong">
                <h2>❌ 答錯了...</h2>
                <p>標準答案: <b>{q['thai']}</b></p>
                <p>{q['meaning']} | {q['pronunciation']}</p>
            </div>
            """, unsafe_allow_html=True)
            
        # === [新增] 顯示手寫/口說分數與回饋 ===
        if 'score' in res and 'writing' not in mode: 
            st.caption(f"發音/拼字相似度分數: {res['score']}")
        elif 'writing' in mode:
            st.caption(f"📝 筆跡 AI 評分: {res.get('score', 0)} 分")
            if 'feedback' in res:
                st.info(f"💡 AI 老師回饋：{res['feedback']}")
        # =====================================

        if 'user_input' in res and 'writing' not in mode: 
            st.write(f"你的輸入/辨識結果: {res['user_input']}")
            
        st.write("🔊 聽聽看標準發音：")
        st.audio(audio_bytes, format='audio/mpeg')

        st.write("")
        if st.button("➡️ 下一題", type="primary", use_container_width=True):
            st.session_state.last_idx = idx
            st.session_state.current_idx = None
            st.session_state.stage = 'quiz'
            st.session_state.result_info = {}
            st.rerun()