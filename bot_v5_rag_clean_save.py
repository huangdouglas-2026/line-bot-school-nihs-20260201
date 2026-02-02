import os
# ⚡ 鎖定單執行緒，這在 Render 的受限環境中能提供最高穩定性
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

import json
import numpy as np
import google.generativeai as genai
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from datetime import datetime

# ==========================================
# 🔑 設定區
# ==========================================
MODEL_NAME = 'gemini-2.0-flash'
EMBED_MODEL = 'models/text-embedding-004' # Google 雲端向量模型

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

app = Flask(__name__)

# ==========================================
# 🧠 輕量化大腦 (API 向量檢索版)
# ==========================================
class LightVectorBrain:
    def __init__(self):
        self.ready = False
        self.source_data = []
        self.vectors = None
        self.load_and_vectorize()

    def load_and_vectorize(self):
        """ 讀取資料並『分批』透過 API 取得向量 """
        files = ['nihs_knowledge_full.json', 'nihs_faq.json', 'nihs_calendar.json']
        all_items = []
        
        try:
            for file in files:
                if os.path.exists(file):
                    with open(file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        if file == 'nihs_faq.json':
                            # FAQ 處理
                            all_items.append(f"【地址交通】{data['traffic']['address']} {data['traffic']['mrt']}")
                            for c in data.get('contacts', []):
                                all_items.append(f"【聯絡電話】{c.get('title')}: {c.get('phone')}")
                        elif isinstance(data, list):
                            for item in data:
                                if 'event' in item: # 行事曆
                                    all_items.append(f"【行事曆】日期:{item.get('date')} 活動:{item.get('event')}")
                                else: # 公告
                                    all_items.append(f"【公告】標題:{item.get('title')} 內容:{str(item.get('content'))[:200]}")
            
            if not all_items: return

            print(f"📡 準備向量化 {len(all_items)} 筆資料...")
            batch_size = 50  # 每批 50 筆，符合免費版限制
            combined_embeddings = []

            for i in range(0, len(all_items), batch_size):
                batch = all_items[i : i + batch_size]
                result = genai.embed_content(
                    model=EMBED_MODEL,
                    content=batch,
                    task_type="retrieval_document"
                )
                combined_embeddings.extend(result['embedding'])
                print(f"⏳ 進度: {min(i + batch_size, len(all_items))}/{len(all_items)}")

            self.vectors = np.array(combined_embeddings).astype('float32')
            self.source_data = all_items
            self.ready = True
            print("✅ 雲端向量大腦啟動成功！")
            
        except Exception as e:
            print(f"❌ 向量化失敗: {e}")

def search(self, query, top_k=5): # ⚡ 修改點：從 3 改為 5，擴大搜尋範圍
        if not self.ready: return []
        try:
            res = genai.embed_content(model=EMBED_MODEL, content=query, task_type="retrieval_query")
            query_vec = np.array(res['embedding']).astype('float32')
            
            similarities = np.dot(self.vectors, query_vec) / (
                np.linalg.norm(self.vectors, axis=1) * np.linalg.norm(query_vec) + 1e-10
            )
            
            top_indices = np.argsort(similarities)[-top_k:][::-1]
            
            # ⚡ 修改點：印出它找到的資料標題，方便我們除錯
            results = [self.source_data[i] for i in top_indices]
            print(f"🔍 用戶問: {query}")
            print(f"📖 AI 抓到的前 {top_k} 筆資料開頭: {[r[:50] for r in results]}")
            return results
        except Exception as e:
            print(f"❌ 搜尋出錯: {e}")
            return []

    def ask(self, user_query):
        if not self.ready: return "校園助手正在整理資料中，請稍候..."

        relevant_docs = self.search(user_query, top_k=5) # 擴大範圍
        
        # 如果真的完全沒抓到資料
        if not relevant_docs:
            return "您的問題很好！目前公告中暫時找不到相關資訊。建議您聯繫學校，我們會記錄並更新。"

        context = "\n---\n".join(relevant_docs)
        now = datetime.now()

        # ⚡ 修改點：Prompt 微調，鼓勵它嘗試回答，並移除過度嚴格的限制
        prompt = f"""
你是內湖高工校園小幫手。今天是 {now.year}/{now.month}/{now.day}。
請根據下方【參考資料】回答問題。

【回答策略】：
1. **有幾分證據說幾分話**：只要參考資料中有提到相關關鍵字或標題，請將該資訊整理出來給家長。
2. **找不到時**：若資料完全不相關，才回覆查無資料的客套話。
3. **格式**：請用親切的口吻，重點條列。
4. **日期**：將民國年份轉為西元 (例如 114年 -> 2025年)。

【參考資料】：
{context}

【家長問題】：{user_query}
"""
        try:
            model = genai.GenerativeModel(MODEL_NAME)
            # ⚡ 修改點：稍微提高溫度 (0.1 -> 0.3)，讓它比較靈活一點
            response = model.generate_content(prompt, generation_config={"temperature": 0.3})
            return response.text
        except:
            return "小幫手連線忙碌中，請稍後再試。"
            

# 實例化
brain = LightVectorBrain()

# ==========================================
# 🌐 路由區
# ==========================================
@app.route("/", methods=['GET'])
def index(): return "Bot Live", 200

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
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

# 👇 新增這個診斷路徑
@app.route("/status", methods=['GET'])
def status():
    # 檢查向量資料庫狀態
    if not brain.ready:
        return "⚠️ 腦袋尚未就緒 (Loading...)", 503
    
    count = len(brain.source_data)
    # 顯示前 5 筆資料標題，確認它讀到了什麼
    preview = "\n".join([s[:50] + "..." for s in brain.source_data[:5]])
    
    return f"""
    <h1>🤖 機器人健康報告</h1>
    <p>✅ 狀態: Online</p>
    <p>📚 知識庫筆數: <strong>{count}</strong> 筆 (正常應約 1600 筆)</p>
    <hr>
    <h3>🔍 資料預覽 (前 5 筆):</h3>
    <pre>{preview}</pre>
    """, 200

if __name__ == "__main__":
    app.run(port=10000)


