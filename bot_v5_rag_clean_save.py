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
# 🧠 AI 大腦 (家長利益優先 + Keynote Style)
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

    def check_calendar_logic(self, query):
        """ 專門處理行事曆：剔除行政代號，聚焦學生與家長利益 """
        q = query.lower()
        if not any(k in q for k in ["行事曆", "幾號", "日期", "活動", "什麼時候"]):
            return None

        matched_events = []
        # 判斷月份
        month_match = re.search(r'(\d+)月', q)
        if month_match:
            m = month_match.group(1).zfill(2)
            matched_events = [e for e in self.calendar_data if f"/{m}/" in e['date']]
        else:
            # 預設抓取最近 5 筆
            matched_events = self.calendar_data[:5]

        if matched_events:
            header = f"◤  內湖高工 學習里程碑  ◢\n━━━━━━━━━━━━━━\n\n"
            body = ""
            for ev in matched_events:
                # 去除行政術語 (如: 召開XX會議、彙報等)，聚焦學生權益
                event_name = ev['event']
                if any(x in event_name for x in ["會議", "彙報", "處室", "撰寫"]): 
                    continue
                
                # 簡化日期
                d = ev['date'].split('/')
                short_date = f"{d[1]}.{d[2]}"
                body += f"◈  {short_date}\n   {event_name}\n\n"
            
            if not body: return None
            
            footer = "━━━━━━━━━━━━━━\n  家長重要日程提醒"
            return header + body + footer
        return None

    def ask_ai(self, user_query):
        # 1. 優先攔截行事曆 (數據驅動邏輯)
        calendar_res = self.check_calendar_logic(user_query)
        if calendar_res: return calendar_res

        # 2. 檢索相關公告 (Top 3)
        relevant_context = ""
        # 簡易關鍵字匹配 (RAG)
        found = [i for i in self.knowledge_data if user_query[:4] in i.get('title', '') or user_query[:4] in i.get('content', '')]
        for i, row in enumerate(found[:3]):
            relevant_context += f"標題:{row['title']}\n網址:{row['url']}\n內容:{row['content'][:300]}\n---\n"

        # 3. 呼叫 Gemini 並設定排版準則
        prompt = f"""
        你是一位極簡專業的內湖高工校園小幫手。

        【回答準則】：
        1. 格式：Apple Keynote 風格 (標題用 ◤ ◢，內容用 ◈，段落空一行)。
        2. 利益導向：剔除複雜行政代號，請告訴家長這件事對「學生」的影響。
        3. URL：資料中的 URL 僅呈現一次，請放在最後並標註「👉 查看原文」。
        4. 符號：適當使用優雅的 Emoji (如 📅, 🏫, 💡)。
        5. 數據驅動：若有日期、地點、電話，請精確列出。

        【資料庫內容】：
        {relevant_context}

        【家長問題】：
        {user_query}
        """

        try:
            model = genai.GenerativeModel(MODEL_NAME)
            response = model.generate_content(prompt)
            return response.text
        except:
            return "◤  系統忙碌中  ◢\n\n◈  請稍後再試\n\n  NIHS Bot"

# 初始化
brain = SmartBrain()

# ==========================================
# 🌐 路由區
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

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_msg = event.message.text.strip()
    reply = brain.ask_ai(user_msg)
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    app.run(port=5000)
