import os
# ⚡ 鎖定單執行緒，減少記憶體震盪與啟動延遲
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

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
# ⚡ 更換為超輕量級模型，減少啟動時下載與載入的時間 (約 45MB)
EMBED_MODEL_NAME = 'all-MiniLM-L6-v2' 

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

app = Flask(__name__)

# ==========================================
# 🧠 向量大腦 (啟動優化版)
# ==========================================
class VectorBrain:
    def __init__(self):
        self.ready = False
        self.encoder = None
        self.index = None
        self.source_data = []
        # 初始化時先不建立索引，等第一次請求或後台載入，避免 Render Start Timeout
        try:
            self.load_and_build_index()
        except Exception as e:
            print(f"⚠️ 初始載入警告 (將於背景重試): {e}")

    def load_and_build_index(self):
        files = ['nihs_knowledge_full.json', 'nihs_faq.json', 'nihs_calendar.json']
        all_items = []
        
        for file in files:
            if os.path.exists(file):
                with open(file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        for item in data:
                            # 簡化內容，節省記憶體與處理速度
                            t = f"標題:{item.get('title','')} 內容:{str(item.get('content',''))[:200]}"
                            all_items.append(t)
                    else:
                        all_items.append(str(data))

        if not all_items: return

        # ⚡ 載入超輕量模型
        if self.encoder is None:
            self.encoder = SentenceTransformer(EMBED_MODEL_NAME)
        
        embeddings = self.encoder.encode(all_items, batch_size=32, show_progress_bar=False)
        
        dimension = embeddings.shape[1]
        self.index = faiss.IndexFlatL2(dimension)
        self.index.add(np.array(embeddings).astype('float32'))
        self.source_data = all_items
        self.ready = True
        print(f"✅ FAISS 索引就緒 ({len(all_items)} 筆)")

    def ask(self, user_query):
        # 如果還沒準備好，嘗試在此時建立 (Lazy Loading)
        if not self.ready:
            self.load_all_sources() 
        
        now = datetime.now()
        # 向量檢索 (精準抓取 3 筆以縮短回覆時間)
        query_vector = self.encoder.encode([user_query]).astype('float32')
        _, indices = self.index.search(query_vector, 3)
        context = "\n---\n".join([self.source_data[i] for i in indices[0] if i != -1])

        prompt = f"你是內工小幫手。今日 {now.year}/{now.month}/{now.day}。根據以下知識庫回覆家長，找不到請用美式查無資料風格回覆。資料中民國年份請顯示為西元年。\n\n【知識庫】：\n{context}\n\n【問題】：{user_query}"
        
        model = genai.GenerativeModel(MODEL_NAME)
        response = model.generate_content(prompt, generation_config={"temperature": 0.1})
        return response.text

# 實例化大腦
brain = VectorBrain()

# ==========================================
# 🌐 路由
# ==========================================
@app.route("/", methods=['GET'])
def index():
    return "NIHS Bot is Live!", 200

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_msg = event.message.text.strip()
    reply = brain.ask(user_msg)
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    app.run(port=10000) # Render 預設 port
