import os
import json
import google.generativeai as genai
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from datetime import datetime

# ==========================================
# 🔑 設定區
# ==========================================
# 統一使用 gemini-2.0-flash 確保最高智商與邏輯能力
MODEL_NAME = 'gemini-2.0-flash'
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

app = Flask(__name__)

# 📂 核心資料來源
DATA_FILE = 'nihs_knowledge_full.json'
FAQ_FILE = 'nihs_faq.json'
CALENDAR_FILE = 'nihs_calendar.json'

# ==========================================
# 🧠 AI 大腦 (邏輯分流與全量檢索版)
# ==========================================
class FullContextBrain:
    def __init__(self):
        self.ready = False
        self.combined_context = ""
        self.load_all_sources()

    def load_all_sources(self):
        """ 載入並標籤化所有資料源，強化 AI 的檢索定位能力 """
        all_text_parts = []
        try:
            # 1. 載入全知資料庫 (包含公告內容)
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for item in data:
                        all_text_parts.append(f"【公告/知識】標題:{item.get('title')} 內容:{item.get('content')} 網址:{item.get('url')}")
            
            # 2. 載入 FAQ (包含地址、交通、電話)
            if os.path.exists(FAQ_FILE):
                with open(FAQ_FILE, 'r', encoding='utf-8') as f:
                    faq = json.load(f)
                    t = faq.get("traffic", {})
                    all_text_parts.append(f"【交通資訊】地址:{t.get('address')} 捷運:{t.get('mrt')} 公車:{t.get('bus')}")
                    for c in faq.get("contacts", []):
                        all_text_parts.append(f"【聯絡電話】職稱:{c.get('title')} 姓名:{c.get('name')} 電話/分機:{c.get('phone')}")
            
            # 3. 載入行事曆 (包含活動日期)
            if os.path.exists(CALENDAR_FILE):
                with open(CALENDAR_FILE, 'r', encoding='utf-8') as f:
                    cal = json.load(f)
                    for ev in cal:
                        all_text_parts.append(f"【行事曆活動】日期:{ev.get('date')} 活動:{ev.get('event')} 類別:{ev.get('category')}")

            self.combined_context = "\n".join(all_text_parts)
            self.ready = True
            print(f"✅ 成功載入 {len(all_text_parts)} 條校園知識。")
        except Exception as e:
            print(f"❌ 資料載入失敗: {e}")
            self.ready = False

    def ask(self, user_query):
        if not self.ready:
            return "小幫手正在整理校園資料，請稍等一下再問我喔！🙏"

        # 🕒 獲取目前時間
        now = datetime.now()
        cur_year, cur_month = now.year, now.month

        # 🧠 強化版 Prompt：加入處理邏輯優先級，防止答非所問
        prompt = f"""
你是一個親切且積極的內湖高工校園小幫手。
今天是西元 {cur_year} 年 {cur_month} 月 {now.day} 日。

【處理邏輯優先級 (請嚴格遵守)】：
1. **通訊與交通查詢**：若問題包含「電話」、「分機」、「聯絡」、「地址」、「怎麼去」、「交通」，請「優先」從【聯絡電話】與【交通資訊】標籤中尋找答案，不要列出行事曆活動。
2. **行事曆查詢**：
   - 僅在問題涉及「行事曆」、「日程」、「活動」或詢問日期時才使用。
   - 若家長未指定月份，請僅列出西元 {cur_year} 年 {cur_month} 月份的活動。
   - 若家長指定月份（如：3月），請列出該月份的活動。
3. **一般公告查詢**：若是詢問具體政策或消息（如：獎學金、轉學、放假規定），請從【公告/知識】尋找。

【回答準則】：
- **年份換算**：資料庫中出現民國 114、115 年，請一律在回答中換算為西元 2025、2026 年呈現。
- **美式服務風格 (僅在查無資料時使用)**：
  「您的問題很好！目前公告中暫時找不到相關資訊。建議家長您可以先直接聯繫學校詢問。同時，我們也會將您的問題記錄下來，並儘快更新在資料庫中，讓其他家長未來可以參考。謝謝您幫助我們變得更好！」
- **格式要求**：語氣親切有禮，適度使用 Emoji，條列式呈現重點。
- **來源標註**：回答最後請標註「👉 來源網址：[僅提供一條最相關的 URL]」。

【全量校園知識庫內容】：
{self.combined_context}

【家長問題】：
{user_query}

【你的回答】：
"""
        try:
            model = genai.GenerativeModel(MODEL_NAME)
            # 使用低溫度 (0.1) 確保 AI 嚴謹對照資料庫回答
            response = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(temperature=0.1)
            )
            return response.text
        except Exception as e:
            print(f"❌ AI 生成錯誤: {e}")
            return "您的問題很好！不過小幫手現在連線有點忙碌，能請您再試一次嗎？感謝包容！"

# ==========================================
# 🌐 Web 服務與路由
# ==========================================
brain = FullContextBrain()

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
    # 執行回覆
    reply = brain.ask(user_msg)
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    app.run(port=5000)
