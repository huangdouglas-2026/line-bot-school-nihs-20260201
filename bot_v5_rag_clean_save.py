# ====================================================
# 🏫 內湖高工全能機器人 V5 (Clean & Save / 清洗存檔版)
# 目標：
# 1. 讀取原始髒資料 (nihs_final_v40.json)
# 2. 清洗重複資料
# 3. 【新功能】將乾淨資料另存為 nihs_cleaned_data.json
# 4. 啟動 RAG AI 機器人
# ====================================================
import os
import json
import logging
import numpy as np
import pandas as pd
import faiss
from sentence_transformers import SentenceTransformer
import google.generativeai as genai
from flask import Flask, request, abort

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

# 設定 Log
logging.basicConfig(level=logging.INFO)

# ==========================================
# 🔑 金鑰設定區 (請確認填入)
# ==========================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyDvnSyAaHEjEumP5CJW1fMmkm7yczfELPg")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "RNxaa/RsOPgMRCrV6g4BHU+yIkJ/1bRrumy7qKjvzj/BUfzCqCcNkK6VM6tLdW6k6XqIuoDDn4VjgEf8F/4ylv 6QxzSyeQO6UYqCWTJ6+U3jzcHvitJ6Ccj8rhq5727FmjWnBwmMzjHoEPC5O/tSvAdB04t89/1O/w1cDnyilFU=")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "7281d74da94dc5dfd693a4f21052a82e")

# ==========================================
# 📂 檔案設定
# ==========================================
# RAW_DATA_FILE = "nihs_final_v40.json"  <-- 改掉這個
RAW_DATA_FILE = "nihs_knowledge_full.json"
CLEANED_DATA_FILE = "nihs_cleaned_data.json" # 清洗後的新檔案
# 改用 small 版本，省記憶體
MODEL_NAME = "intfloat/multilingual-e5-small"

# ==========================================
# 🧠 AI 大腦核心 (SchoolBrain)
# ==========================================
class SchoolBrain:
    def __init__(self, raw_path, clean_path):
        print(f"🤖 [系統] 正在喚醒大腦...")
        
        # 1. 檢查原始檔案
        if not os.path.exists(raw_path):
            print(f"❌ 嚴重錯誤：找不到原始資料 {raw_path}。")
            self.ready = False
            return

        # 2. 載入模型
        print(f"📥 [模型] 載入語意理解模型 ({MODEL_NAME})...")
        self.model = SentenceTransformer(MODEL_NAME)
        
        # 3. 執行清洗、存檔與索引
        print(f"🧠 [記憶] 正在處理資料...")
        self.df, self.index = self._process_and_index(raw_path, clean_path)
        
        self.ready = True
        print(f"✅ [就緒] 大腦啟動完成！目前擁有 {len(self.df)} 筆精華記憶。")

    def _process_and_index(self, raw_path, clean_path):
        """讀取原始檔 -> 清洗 -> 存新檔 -> 建立索引"""
        
        # --- A. 讀取與清洗 ---
        print(f"   📖 讀取原始資料: {raw_path}")
        with open(raw_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        df = pd.DataFrame(data)
        original_count = len(df)
        print(f"   📊 原始筆數: {original_count}")
        
        # 1. 針對 URL 去重
        df.drop_duplicates(subset=['url'], keep='first', inplace=True)
        # 2. 針對 內容(標題+日期) 去重
        df.drop_duplicates(subset=['title', 'date'], keep='first', inplace=True)
        
        # 重置索引
        df.reset_index(drop=True, inplace=True)
        cleaned_count = len(df)
        print(f"   ✨ 清洗後筆數: {cleaned_count} (移除了 {original_count - cleaned_count} 筆重複資料)")

        # --- B. 另存新檔 (核心需求) ---
        print(f"   💾 正在將乾淨資料寫入: {clean_path} ...")
        try:
            # 將 DataFrame 轉回字典列表
            cleaned_records = df.to_dict(orient='records')
            with open(clean_path, 'w', encoding='utf-8') as f:
                json.dump(cleaned_records, f, ensure_ascii=False, indent=4)
            print("   ✅ 存檔成功！")
        except Exception as e:
            print(f"   ⚠️ 存檔失敗 (但不影響機器人運作): {e}")

        # --- C. 建立向量索引 ---
        print("   ⚡ 正在建立向量索引 (Vector Index)...")
        # 組合語意欄位
        df['semantic_text'] = df.apply(
            lambda x: f"日期:{x['date']}，單位:{x['unit']}，標題:{x['title']}，內容:{str(x.get('content',''))[:200]}", 
            axis=1
        )
        
        # 轉成向量
        sentences = df['semantic_text'].tolist()
        embeddings = self.model.encode(sentences, normalize_embeddings=True)
        
        # 建立 FAISS 索引
        dimension = embeddings.shape[1]
        index = faiss.IndexFlatIP(dimension)
        index.add(embeddings)
        
        return df, index

    def search(self, query, top_k=3):
        """搜尋最相關的資料"""
        if not self.ready: return []
        
        query_embedding = self.model.encode([f"query: {query}"], normalize_embeddings=True)
        scores, indices = self.index.search(query_embedding, top_k)
        
        results = []
        for idx in indices[0]:
            if idx < len(self.df):
                results.append(self.df.iloc[idx])
        return results

# ==========================================
# ⚙️ 系統初始化
# ==========================================
app = Flask(__name__)
genai.configure(api_key=GEMINI_API_KEY)
ai_model = genai.GenerativeModel('gemini-2.0-flash') 

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 初始化大腦 (傳入原始檔名 和 目標存檔名)
brain = SchoolBrain(RAW_DATA_FILE, CLEANED_DATA_FILE)

# ==========================================
# 🤖 對話邏輯
# ==========================================
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_msg = event.message.text.strip()
    print(f"\n🗣️ 家長問: {user_msg}")

    try:
        if not brain.ready:
            reply_text = "系統維護中：資料庫載入失敗。"
        else:
            # 1. 檢索
            found_data = brain.search(user_msg, top_k=3)
            
            # 2. 構建 Context
            context_text = ""
            for i, row in enumerate(found_data):
                attach_str = ""
                # 確保 attachments 是列表且有內容
                if isinstance(row.get('attachments'), list) and len(row['attachments']) > 0:
                    attach_names = [f"[{a['name']}]" for a in row['attachments']]
                    attach_str = ", ".join(attach_names)

                context_text += f"""
【資料來源 {i+1}】
日期：{row['date']}
單位：{row['unit']}
標題：{row['title']}
網址：{row['url']}
附件：{attach_str}
內容摘要：{str(row.get('content',''))[:200]}...
--------------------------------
"""
            # 3. 生成
            prompt = f"""
你是一個親切的內湖高工校園小幫手。
請根據下方的【檢索資料】回答家長的【問題】。

【回答準則】：
1. 語氣要親切、有禮貌（繁體中文）。
2. **務必附上「網址」**：如果資料中有連結，請直接提供給家長點擊。
3. **提及附件**：如果資料有附件，請提醒家長可以點擊連結下載。
4. 如果資料中沒有答案，請誠實說「目前公告中找不到相關資訊」，建議家長直接聯繫學校。

【檢索資料】：
{context_text}

【家長問題】：
{user_msg}
"""
            response = ai_model.generate_content(prompt)
            reply_text = response.text

        # 4. 回覆
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)]
                )
            )
        print("✅ 已回覆")

    except Exception as e:
        print(f"❌ 錯誤: {e}")

if __name__ == "__main__":
    print("🚀 LINE Bot 伺服器已啟動 (Port 5000)")
    app.run(port=5000, use_reloader=False)