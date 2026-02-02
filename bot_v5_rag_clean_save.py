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
                                    # 補上單位資訊，增加辨識度
                                    unit = item.get('unit', '')
                                    all_items.append(f"【公告】單位:{unit} 標題:{item.get('title')} 內容:{str(item.get('content'))[:200]}")
            
            if not all_items: return

            print(f"📡 準備向量化 {len(all_items)} 筆資料...")
            batch_size = 50  # 每批 50 筆，符合免費版限制
            combined_embeddings = []

            for i in range(0, len(all_items), batch_size):
                batch = all_items[i : i + batch_size]
                # 呼叫 Google API
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

    def search(self, query, top_k=5):
        """ 計算相似度找出最相關的資料 (擴大至 5 筆) """
        if not self.ready: return []
        
        try:
            # 1. 取得問題向量
            res = genai.embed_content(model=EMBED_MODEL, content=query, task_type="retrieval_query")
            query_vec = np.array(res['embedding']).astype('float32')
            
            # 2. 餘弦相似度計算
            similarities = np.dot(self.vectors, query_vec) / (
                np.linalg.norm(self.vectors, axis=1) * np.linalg.norm(query_vec) + 1e-10
            )
            
            # 3. 取得前 k 名索引
            top_indices = np.argsort(similarities)[-top_k:][::-1]
            
            # 除錯 Log
            results = [self.source_data[i] for i in top_indices]
            print(f"🔍 用戶問: {query}")
            print(f"📖 AI 抓到的前 {top_k} 筆資料開頭: {[r[:30] for r in results]}")
            
            return results
        except Exception as e:
            print(f"❌ 搜尋錯誤: {e}")
            return []

    def ask(self, user_query):
        if not self.ready: return "校園助手正在整理資料中，請稍候..."

        # ⚡ 檢索擴大為 5 筆
        relevant_docs = self.search(user_query, top_k=5)
        
        # 如果真的完全沒資料
        if not relevant_docs:
             return "您的問題很好！目前公告中暫時找不到相關資訊。建議您聯繫學校，我們會記錄並更新。"

        context = "\n---\n".join(relevant_docs)
        now = datetime.now()

        # ⚡ 放寬後的 Prompt
        prompt = f"""
你是「內湖高工校園小幫手」。今天是西元 {now.year}年{now.month}月{now.day}日。
請根據下方【參考資料】回答家長問題。

【回答策略】：
1. **有幾分證據說幾分話**：只要參考資料中有提到相關關鍵字，請整理出來。
2. **找不到時**：若資料內容完全不相關，才回覆查無資料的客套話。
3. **格式**：請用親切口吻，重點條列，適度使用 Emoji。
4. **日期**：將民國年份轉為西元。

【參考資料】：
{context}

【家長問題】：{user_query}
"""
        try:
            model = genai.GenerativeModel(MODEL_NAME)
            # 溫度調高至 0.3，增加靈活性
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

if __name__ == "__main__":
    app.run(port=10000)
