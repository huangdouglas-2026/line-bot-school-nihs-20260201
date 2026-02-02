import os
# ⚡ 鎖定單執行緒
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
EMBED_MODEL = 'models/text-embedding-004'

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

app = Flask(__name__)

# ==========================================
# 🧠 雙層向量大腦 (Split-Index Brain)
# ==========================================
class DualVectorBrain:
    def __init__(self):
        self.ready = False
        # 建立兩個獨立的資料庫
        self.core_data = []      # 存放 FAQ、行事曆 (高權重)
        self.core_vectors = None
        
        self.news_data = []      # 存放公告 (低權重)
        self.news_vectors = None
        
        self.load_and_vectorize()

    def embed_batch(self, text_list):
        """ 批次向量化工具 """
        if not text_list: return None
        batch_size = 50
        all_vecs = []
        print(f"📡 正在處理 {len(text_list)} 筆資料...")
        
        for i in range(0, len(text_list), batch_size):
            batch = text_list[i : i + batch_size]
            try:
                res = genai.embed_content(model=EMBED_MODEL, content=batch, task_type="retrieval_document")
                all_vecs.extend(res['embedding'])
            except Exception as e:
                print(f"⚠️ Batch error: {e}")
                # 補空向量防崩潰
                all_vecs.extend([[0]*768] * len(batch))
                
        return np.array(all_vecs).astype('float32')

    def load_and_vectorize(self):
        files = ['nihs_knowledge_full.json', 'nihs_faq.json', 'nihs_calendar.json']
        
        core_items = [] # 核心區
        news_items = [] # 公告區

        try:
            for file in files:
                if os.path.exists(file):
                    with open(file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        
                        # 1. 處理 FAQ (放入核心區)
                        if file == 'nihs_faq.json':
                            traffic = data.get('traffic', {})
                            # 強力關鍵字植入
                            core_items.append(
                                f"【學校交通資訊】(關鍵字: 怎麼去, 地址, 捷運, 公車)\n"
                                f"地址: {traffic.get('address')}\n"
                                f"捷運: {traffic.get('mrt')}\n"
                                f"公車: {traffic.get('bus')}"
                            )
                            for c in data.get('contacts', []):
                                core_items.append(f"【聯絡電話】{c.get('title')} 電話:{c.get('phone')} (關鍵字: 分機, 找老師)")
                        
                        # 2. 處理行事曆 (放入核心區)
                        elif file == 'nihs_calendar.json':
                            for item in data:
                                core_items.append(f"【行事曆】日期:{item.get('date')} 活動:{item.get('event')}")
                        
                        # 3. 處理公告 (放入公告區)
                        elif file == 'nihs_knowledge_full.json':
                            for item in data:
                                unit = item.get('unit', '')
                                content = str(item.get('content', ''))[:200]
                                news_items.append(f"【公告】單位:{unit} 標題:{item.get('title')} 內容:{content}")

            # 開始向量化 (分開處理)
            print("🚀 正在建立核心資料庫 (Core Index)...")
            self.core_vectors = self.embed_batch(core_items)
            self.core_data = core_items

            print("🚀 正在建立公告資料庫 (News Index)...")
            self.news_vectors = self.embed_batch(news_items)
            self.news_data = news_items
            
            self.ready = True
            print(f"✅ 雙層大腦啟動完畢！核心:{len(core_items)}筆, 公告:{len(news_items)}筆")

        except Exception as e:
            print(f"❌ 初始化失敗: {e}")

    def search_layer(self, query_vec, vectors, data, top_k=3):
        """ 通用搜尋函式 """
        if vectors is None or len(data) == 0: return [], []
        
        # 計算相似度
        sims = np.dot(vectors, query_vec) / (
            np.linalg.norm(vectors, axis=1) * np.linalg.norm(query_vec) + 1e-10
        )
        top_indices = np.argsort(sims)[-top_k:][::-1]
        
        results = [data[i] for i in top_indices]
        scores = [sims[i] for i in top_indices]
        return results, scores

    def ask(self, user_query):
        if not self.ready: return "系統熱機中，請稍候..."

        try:
            # 1. 取得問題向量
            res = genai.embed_content(model=EMBED_MODEL, content=user_query, task_type="retrieval_query")
            q_vec = np.array(res['embedding']).astype('float32')

            final_docs = []
            
            # 🔍 第一層：搜核心區 (FAQ/行事曆)
            core_docs, core_scores = self.search_layer(q_vec, self.core_vectors, self.core_data, top_k=3)
            
            # 判斷核心區是否有強關聯 (門檻值 0.55)
            if core_docs and core_scores[0] > 0.55:
                print(f"🎯 命中核心資料! 分數: {core_scores[0]}")
                final_docs = core_docs
            else:
                # 🔍 第二層：搜公告區 (如果核心區沒找到好的)
                print("🔄 核心區無明顯關聯，轉搜公告區...")
                news_docs, news_scores = self.search_layer(q_vec, self.news_vectors, self.news_data, top_k=5)
                final_docs = news_docs

            if not final_docs:
                return "您的問題很好！目前公告中暫時找不到相關資訊。建議您聯繫學校，我們會記錄並更新。"

            context = "\n---\n".join(final_docs)
            now = datetime.now()

            prompt = f"""
你是「內湖高工校園小幫手」。今天是西元 {now.year}/{now.month}/{now.day}。
請根據【參考資料】回答問題。

【回答策略】：
1. **精準優先**：若資料來自【學校交通資訊】或【聯絡電話】，請直接給出答案，不需要廢話。
2. **公告整理**：若資料來自【公告】，請摘要重點。
3. **查無資料**：若資料與問題無關，請直接說找不到。

【參考資料】：
{context}

【家長問題】：{user_query}
"""
            model = genai.GenerativeModel(MODEL_NAME)
            response = model.generate_content(prompt, generation_config={"temperature": 0.3})
            return response.text

        except Exception as e:
            print(f"❌ 問答錯誤: {e}")
            return "小幫手連線忙碌中，請稍後再試。"

brain = DualVectorBrain()

@app.route("/", methods=['GET'])
def index(): return "Bot Live", 200

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
    app.run(port=10000)
