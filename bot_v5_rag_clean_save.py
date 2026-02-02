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
# 依要求統一使用 gemini-2.0-flash
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
# 🧠 AI 大腦 (Apple Keynote Style Edition)
# ==========================================
class SmartBrain:
    def __init__(self):
        self.knowledge_text = ""
        self.faq_data = {}
        self.calendar_data = []
        self.load_all_data()

    def load_all_data(self):
        """ 載入所有整合後的資料源 """
        # 1. 載入全知資料庫
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # 為了 RAG 效能，抓取前 50 筆重要條目作為上下文
                self.knowledge_text = "\n".join([f"【{i.get('title')}】\n{i.get('content')[:400]}" for i in data[:50]])
        
        # 2. 載入 FAQ (交通/電話)
        if os.path.exists(FAQ_FILE):
            with open(FAQ_FILE, 'r', encoding='utf-8') as f:
                self.faq_data = json.load(f)

        # 3. 載入行事曆
        if os.path.exists(CALENDAR_FILE):
            with open(CALENDAR_FILE, 'r', encoding='utf-8') as f:
                self.calendar_data = json.load(f)

    def format_keynote_layout(self, title_text, items):
        """ 
        核心格式：Apple Keynote 風格 
        1. 標題用 ◤ ◢ 包裹
        2. 項目之間空一行
        3. 使用細緻符號 ◈
        """
        header = f"◤  {title_text}  ◢\n━━━━━━━━━━━━━━\n\n"
        body = ""
        for item in items:
            body += f"{item}\n\n"
        
        footer = "━━━━━━━━━━━━━━\n  由 內工小幫手 簡約呈現"
        return header + body + footer

    def check_static_logic(self, user_query):
        """ 靜態資料攔截器：優先處理行事曆與通訊錄 """
        q = user_query.lower()

        # A. 行事曆查詢
        if any(k in q for k in ["行事曆", "日程", "日期", "什麼時候"]):
            month_match = re.search(r'(\d+)月', q)
            if month_match:
                m = month_match.group(1).zfill(2)
                matched = [f"◈  {e['date'].split('/')[-2]}.{e['date'].split('/')[-1]}\n   {e['event']}" 
                           for e in self.calendar_data if f"/{m}/" in e['date']]
                if matched:
                    return self.format_keynote_layout(f"{m}月 重點日程", matched[:6])
            elif self.calendar_data:
                # 顯示最近 5 筆
                recent = [f"◈  {e['date'].replace('2026/','')}\n   {e['event']}" for e in self.calendar_data[:5]]
                return self.format_keynote_layout("近期校園日程", recent)

        # B. 交通與通訊查詢
        if any(k in q for k in ["電話", "分機", "地址", "交通", "怎麼去"]):
            if self.faq_data:
                items = []
                if any(k in q for k in ["地址", "交通"]):
                    t = self.faq_data.get("traffic", {})
                    items.append(f"◈  學校地址\n   {t.get('address')}")
                    items.append(f"◈  交通引導\n   {t.get('mrt')}")
                else:
                    contacts = self.faq_data.get("contacts", [])
                    # 關鍵字篩選職稱
                    found = [f"◈  {c['title']} {c['name']}\n   {c['phone']}" for c in contacts if any(k in c['title'] for k in [q.replace("電話","")])]
                    items = found[:4] if found else [f"◈  {c['title']}\n   {c['phone']}" for c in contacts[:4]]
                
                if items:
                    return self.format_keynote_layout("校園通訊錄", items)
        
        return None

    def ask_ai(self, user_query):
        """ 
        RAG 查詢：針對複雜問題呼叫 Gemini-2.0-Flash 
        """
        # 先嘗試靜態匹配
        static_res = self.check_static_logic(user_query)
        if static_res: return static_res

        # 若無匹配，則詢問 AI 並要求 Keynote 格式
        prompt = f"""
        你是一位極簡主義的校務助理。請根據【資料庫】回答問題。
        
        【視覺格式限定：Apple Keynote 風格】
        1. 標題請用「◤ 」與「 ◢」包裹。
        2. 每一項活動或重點之間必須空一行。
        3. 使用 ◈ 符號。
        4. 結尾加上「  由 內工小幫手 簡約呈現」。
        5. 嚴禁廢話，保持專業留白。

        【資料庫內容】：
        {self.knowledge_text}

        【問題】：{user_query}
        """
        try:
            model = genai.GenerativeModel(MODEL_NAME)
            response = model.generate_content(prompt)
            return response.text
        except:
            return "◤  系統提醒  ◢\n━━━━━━━━━━━━━━\n\n▷  服務暫時忙碌\n   請稍後再試\n\n  NIHS AI"

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
    # 每次對話時重新載入資料確保最新 (可選)
    # brain.load_all_data() 
    reply = brain.ask_ai(user_msg)
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    app.run(port=5000)
