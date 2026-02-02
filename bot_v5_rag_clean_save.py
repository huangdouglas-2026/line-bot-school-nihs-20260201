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
# 🧠 混合式大腦 (規則直通車 + 向量檢索)
# ==========================================
class HybridBrain:
    def __init__(self):
        self.ready = False
        
        # 1. 結構化資料 (給規則用)
        self.faq_data = {} 
        
        # 2. 向量化資料 (給 AI 用)
        self.vectors = None
        self.source_data = []
        
        self.load_data()

    def load_data(self):
        files = ['nihs_knowledge_full.json', 'nihs_faq.json', 'nihs_calendar.json']
        all_items = []
        
        try:
            for file in files:
                if os.path.exists(file):
                    with open(file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        
                        # A. 處理 FAQ (同時存入規則庫與向量庫)
                        if file == 'nihs_faq.json':
                            self.faq_data = data # 存下來做直通車
                            
                            # 也要放進向量庫，以防萬一
                            traffic = data.get('traffic', {})
                            all_items.append(f"【交通】地址:{traffic.get('address')} 捷運:{traffic.get('mrt')} 公車:{traffic.get('bus')}")
                            for c in data.get('contacts', []):
                                all_items.append(f"【電話】{c.get('title')}: {c.get('phone')}")

                        # B. 處理行事曆
                        elif isinstance(data, list):
                            for item in data:
                                if 'event' in item:
                                    all_items.append(f"【行事曆】日期:{item.get('date')} 活動:{item.get('event')}")
                                else: # 公告
                                    # 擷取較長內容以增加準度
                                    content = str(item.get('content', ''))[:300]
                                    all_items.append(f"【公告】標題:{item.get('title')} 內容:{content}")

            # 向量化處理
            print(f"📡 正在建立向量索引 (共 {len(all_items)} 筆)...")
            batch_size = 50
            combined_embeddings = []
            
            for i in range(0, len(all_items), batch_size):
                batch = all_items[i : i + batch_size]
                try:
                    res = genai.embed_content(model=EMBED_MODEL, content=batch, task_type="retrieval_document")
                    combined_embeddings.extend(res['embedding'])
                except:
                    # 避免單一批次失敗導致全掛
                    combined_embeddings.extend([[0]*768] * len(batch))
                    
            self.vectors = np.array(combined_embeddings).astype('float32')
            self.source_data = all_items
            self.ready = True
            print("✅ 混合大腦啟動完畢！")

        except Exception as e:
            print(f"❌ 初始化失敗: {e}")

    # 🔥 關鍵：規則直通車 (Rule-Based Router)
    def check_rules(self, query):
        q = query.lower()
        
        # 1. 攔截「交通」相關
        if any(k in q for k in ['交通', '地址', '在哪', '捷運', '公車', '怎麼去']):
            t = self.faq_data.get('traffic', {})
            return (
                "🏫 **內湖高工交通資訊**\n\n"
                f"📍 **地址**：{t.get('address', '無資料')}\n"
                f"🚇 **捷運**：{t.get('mrt', '無資料')}\n"
                f"🚌 **公車**：\n{t.get('bus', '無資料')}"
            )
            
        # 2. 攔截「電話」相關
        if any(k in q for k in ['電話', '分機', '聯絡', '總機', '校安']):
            contacts = self.faq_data.get('contacts', [])
            msg = "📞 **內湖高工常用電話**\n"
            for c in contacts:
                msg += f"\n🔸 {c.get('title')}: {c.get('phone')}"
            return msg
            
        return None

    def search_vector(self, query, top_k=5):
        if not self.ready or self.vectors is None: return []
        try:
            res = genai.embed_content(model=EMBED_MODEL, content=query, task_type="retrieval_query")
            q_vec = np.array(res['embedding']).astype('float32')
            
            sims = np.dot(self.vectors, q_vec) / (
                np.linalg.norm(self.vectors, axis=1) * np.linalg.norm(q_vec) + 1e-10
            )
            top_indices = np.argsort(sims)[-top_k:][::-1]
            return [self.source_data[i] for i in top_indices]
        except: return []

    def ask(self, user_query):
        # ⚡ 第一關：先查規則直通車
        direct_answer = self.check_rules(user_query)
        if direct_answer:
            return direct_answer

        # ⚡ 第二關：AI 向量檢索 (處理行事曆、公告)
        if not self.ready: return "系統熱機中..."
        
        relevant_docs = self.search_vector(user_query, top_k=5)
        
        # 若是問行事曆，強制多抓幾筆
        if "行事曆" in user_query:
             relevant_docs = self.search_vector("2026年行事曆", top_k=10)

        context = "\n---\n".join(relevant_docs)
        now = datetime.now()
        
        prompt = f"""
你是「內湖高工校園小幫手」。今天是 {now.year}/{now.month}/{now.day}。
請根據【參考資料】回答問題。

【策略】：
1. **行事曆**：請列出最接近今天的未來活動。
2. **公告**：請摘要重點。
3. **查無資料**：若資料完全無關，請回覆「抱歉，目前公告中找不到相關資訊」。

【參考資料】：
{context}

【問題】：{user_query}
"""
        try:
            model = genai.GenerativeModel(MODEL_NAME)
            response = model.generate_content(prompt, generation_config={"temperature": 0.3})
            return response.text
        except:
            return "小幫手連線忙碌中，請稍後再試。"

brain = HybridBrain()

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
