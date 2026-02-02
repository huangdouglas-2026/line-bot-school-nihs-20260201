import os
import json
import numpy as np
import faiss
import google.generativeai as genai
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from datetime import datetime
from sentence_transformers import SentenceTransformer

# ==========================================
# 🔑 設定區
# ==========================================
MODEL_NAME = 'gemini-2.0-flash'
EMBED_MODEL_NAME = 'paraphrase-multilingual-MiniLM-L12-v2' # 輕量且支援中文

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")

genai.configure(api_key=GEMINI_API_KEY)
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

app = Flask(__name__)

# ==========================================
# 🧠 向量大腦 (FAISS 版)
# ==========================================
class VectorBrain:
    def __init__(self):
        self.ready = False
        self.encoder = SentenceTransformer(EMBED_MODEL_NAME) # 載入輕量化模型
        self.source_data = [] # 存放原始文字
        self.index = None
        self.load_and_build_index()

    def load_and_build_index(self):
        """ 讀取 JSON 並建立 FAISS 向量索引 """
        files = ['nihs_knowledge_full.json', 'nihs_faq.json', 'nihs_calendar.json']
        all_items = []
        
        for file in files:
            if os.path.exists(file):
                with open(file, 'r', encoding='utf-8') as f:
                    content = json.load(f)
                    if isinstance(content, list):
                        all_items.extend([json.dumps(i, ensure_ascii=False) for i in content])
                    else:
                        all_items.append(json.dumps(content, ensure_ascii=False))

        if not all_items: return

        # 1. 將文字轉為向量 (Embedding)
        self.source_data = all_items
        embeddings = self.encoder.encode(all_items)
        
        # 2. 建立 FAISS 索引
        dimension = embeddings.shape[1]
        self.index = faiss.IndexFlatL2(dimension)
        self.index.add(np.array(embeddings).astype('float32'))
        
        self.ready = True
        print(f"✅ FAISS 索引建立完成，共 {len(all_items)} 筆資料")

    def search(self, query, top_k=4):
        """ 向量搜尋：找出最相關的資料 """
        if not self.ready: return []
        query_vector = self.encoder.encode([query]).astype('float32')
        distances, indices = self.index.search(query_vector, top_k)
        
        # 回傳最相關的原始資料
        return [self.source_data[i] for i in indices[0] if i != -1]

    def ask(self, user_query):
        now = datetime.now()
        
        # ⚡ 關鍵優化：只抓取跟問題最相關的 4 筆資料
        relevant_docs = self.search(user_query, top_k=4)
        context = "\n---\n".join(relevant_docs)

        prompt = f"""
你是「內湖高工校園小幫手」。今天 {now.year}/{now.month}/{now.day}。
請「嚴格根據」下方知識庫回答。若資料中完全沒有與 "{user_query}" 相關的關鍵內容，請回覆查無資料的美式風格範本。

【規則】：
1. 西元年呈現。
2. 僅顯示當月行事曆（除非指定月份）。
3. 嚴禁幻覺，沒看到就說找不到。

【知識庫】：
{context}

【問題】：{user_query}
"""
        model = genai.GenerativeModel(MODEL_NAME)
        response = model.generate_content(prompt, generation_config={"temperature": 0})
        return response.text

brain = VectorBrain()

# ==========================================
# 🌐 路由區
# ==========================================
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except: abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    reply = brain.ask(event.message.text.strip())
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    app.run(port=5000)
