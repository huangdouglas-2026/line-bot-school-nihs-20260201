import os
# ⚠️ 必須置於所有 import 之首：限制科學運算執行緒以節省 Render 記憶體
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

import json
import numpy as np
import faiss
import google.generativeai as genai
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from datetime import datetime
from sentence_transformers import SentenceTransformer

# ==========================================
# 🔑 設定區
# ==========================================
MODEL_NAME = 'gemini-2.0-flash'
# 使用極輕量模型 (約 60MB)，確保在 512MB RAM 穩定運作
EMBED_MODEL_NAME = 'paraphrase-MiniLM-L3-v2' 

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

app = Flask(__name__)

# ==========================================
# 🧠 向量大腦 (FAISS + 語義檢索)
# ==========================================
class VectorBrain:
    def __init__(self):
        self.ready = False
        self.encoder = None
        self.index = None
        self.source_data = []
        self.load_and_build_index()

    def load_and_build_index(self):
        """ 讀取資料並建立 FAISS 索引 """
        files = ['nihs_knowledge_full.json', 'nihs_faq.json', 'nihs_calendar.json']
        all_items = []
        
        try:
            for file in files:
                if os.path.exists(file):
                    with open(file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            # 將物件轉為易讀的文字區塊
                            for item in data:
                                text = f"標題:{item.get('title','')} 內容:{item.get('content','')} 網址:{item.get('url','')}"
                                if 'event' in item: # 行事曆
                                    text = f"日期:{item.get('date','')} 活動:{item.get('event','')} 類別:{item.get('category','')}"
                                all_items.append(text)
                        elif isinstance(data, dict):
                            all_items.append(json.dumps(data, ensure_ascii=False))

            if not all_items: return

            # 載入模型 (這一步最耗記憶體)
            if self.encoder is None:
                self.encoder = SentenceTransformer(EMBED_MODEL_NAME)
            
            # 轉換向量 (Batch 大小設為 16 以平衡速度與記憶體)
            embeddings = self.encoder.encode(all_items, batch_size=16, show_progress_bar=False)
            
            dimension = embeddings.shape[1]
            self.index = faiss.IndexFlatL2(dimension)
            self.index.add(np.array(embeddings).astype('float32'))
            self.source_data = all_items
            
            self.ready = True
            print(f"✅ 向量索引建立完成：{len(all_items)} 筆")
            
        except Exception as e:
            print(f"❌ 向量化失敗: {e}")

    def search(self, query, top_k=4):
        """ 語義搜尋最相關的 4 筆資料 """
        if not self.ready: return []
        query_vector = self.encoder.encode([query]).astype('float32')
        distances, indices = self.index.search(query_vector, top_k)
        return [self.source_data[i] for i in indices[0] if i != -1]

    def ask(self, user_query):
        if not self.ready:
            return "小幫手正在更新資料庫，請稍後再試。"

        now = datetime.now()
        cur_year, cur_month = now.year, now.month

        # ⚡ 向量檢索：精準抓取最相關的 4 筆，解決幻覺與洗版問題
        relevant_docs = self.search(user_query, top_k=4)
        context = "\n---\n".join(relevant_docs)

        prompt = f"""
你是一個親切的內湖高工校園小幫手。今天是 {cur_year}/{cur_month}/{now.day}。
請「嚴格根據」下方知識庫內容回答家長。

【處理規則】：
1. 僅顯示當月行事曆（除非指定月份）。
2. 民國轉西元（114/115 -> 2025/2026）。
3. **嚴禁幻覺**：如果知識庫中找不到與 "{user_query}" 相關的具體資訊，必須回覆查無資料的美式風格範本，絕對不要列出不相關的公告。
4. **格式**：條列式、適度 Emoji、結尾標註來源。

【知識庫】：
{context}

【家長問題】：
{user_query}
"""
        try:
            model = genai.GenerativeModel(MODEL_NAME)
            response = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(temperature=0)
            )
            return response.text
        except Exception as e:
            return "您的問題很好！不過小幫手現在連線有點忙碌，能請您再試一次嗎？"

# ==========================================
# 🌐 服務啟動
# ==========================================
brain = VectorBrain()

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_msg = event.message.text.strip()
    reply = brain.ask(user_msg)
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    app.run(port=5000)
