import os
import json
import re
import google.generativeai as genai
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

# ==========================================
# 🔑 設定區
# ==========================================
MODEL_NAME = 'gemini-2.0-flash'
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

app = Flask(__name__)

# 檔案路徑
DATA_FILE = 'nihs_knowledge_full.json'
FAQ_FILE = 'nihs_faq.json'
CALENDAR_FILE = 'nihs_calendar.json'

# ==========================================
# 🧠 AI 大腦 (Keynote Style + 來源精準標註)
# ==========================================
class SmartBrain:
    def __init__(self):
        self.load_all_data()

    def load_all_data(self):
        self.knowledge_data = []
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                self.knowledge_data = json.load(f)
        
        self.faq_data = {}
        if os.path.exists(FAQ_FILE):
            with open(FAQ_FILE, 'r', encoding='utf-8') as f:
                self.faq_data = json.load(f)

        self.calendar_data = []
        if os.path.exists(CALENDAR_FILE):
            with open(CALENDAR_FILE, 'r', encoding='utf-8') as f:
                self.calendar_data = json.load(f)

    def check_static_faq(self, query):
        """ 處理基礎通訊與交通 (此類不強烈要求外部來源網址) """
        q = query.lower()
        if any(k in q for k in ["電話", "分機", "地址", "交通", "怎麼去"]):
            res = "◤  校園通訊與交通  ◢\n━━━━━━━━━━━━━━\n\n"
            if any(k in q for k in ["地址", "交通"]):
                t = self.faq_data.get("traffic", {})
                res += f"◈  學校地址\n   {t.get('address')}\n\n◈  交通引導\n   {t.get('mrt')}\n\n"
            else:
                contacts = self.faq_data.get("contacts", [])
                found = [c for c in contacts if any(k in c['title'] for k in [q.replace("電話","")])]
                target = found[:4] if found else contacts[:4]
                for c in target:
                    res += f"◈  {c['title']} {c['name']}\n   {c['phone']}\n\n"
            res += "━━━━━━━━━━━━━━\n  Keynote 簡約模式"
            return res
        return None

    def ask_ai(self, user_query):
        # 1. 優先處理基礎 FAQ (不需複雜 RAG)
        static_faq = self.check_static_faq(user_query)
        if static_faq: return static_faq

        # 2. 檢索相關公告與行事曆 (RAG)
        # 尋找最相關的一筆資料作為主來源
        relevant_items = [i for i in self.knowledge_data if user_query[:3] in i.get('title', '') or user_query[:3] in i.get('content', '')]
        
        source_url = ""
        context_text = ""
        
        if relevant_items:
            # 取第一筆作為主要來源 URL
            source_url = relevant_items[0].get('url', '')
            for i, row in enumerate(relevant_items[:3]):
                context_text += f"來源{i+1}: {row['title']}\n內容: {row['content'][:300]}\n\n"

        # 3. 呼叫 Gemini 生成回覆
        prompt = f"""
        你是一位內湖高工校園助手。請以 Apple Keynote 風格回答。

        【視覺與邏輯規範】：
        1. 使用 ◤ ◢ 包裹標題，使用 ◈ 作為項目符號。
        2. 段落與項目之間必須空一行，保持視覺寬鬆感。
        3. 內容聚焦於「家長與學生利益」，剔除冗長的行政術語。
        4. 適度加入 Emoji (📅, 🏫, 💡)。
        5. **嚴格禁止在文中反覆呈現 URL**。

        【校園資料庫】：
        {context_text if context_text else "無相關公告資料"}

        【家長問題】：
        {user_query}
        """

        try:
            model = genai.GenerativeModel(MODEL_NAME)
            response = model.generate_content(prompt)
            final_text = response.text

            # 4. 根據您的要求：在最後提供來源資料網址
            if source_url:
                final_text += f"\n\n🔗 來源參考資料：\n{source_url}"
            
            return final_text
        except:
            return "◤  系統提醒  ◢\n━━━━━━━━━━━━━━\n\n◈  資料檢索忙碌中\n   請稍後再試\n\n  NIHS AI"

# ==========================================
# 🌐 路由區
# ==========================================
brain = SmartBrain()

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
    reply = brain.ask_ai(user_msg)
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    app.run(port=5000)
