import os
import json
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

# ==========================================
# 🧠 AI 大腦 (美式積極服務模式)
# ==========================================
class FullContextBrain:
    def __init__(self):
        self.ready = False
        self.knowledge_data = []
        self.load_data()

    def load_data(self):
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, 'r', encoding='utf-8') as f:
                    self.knowledge_data = json.load(f)
                self.ready = True
            except: self.ready = False

    def search(self, query, top_k=3):
        # 簡單的檢索過濾 (未來可根據您的需求改為更複雜的搜尋)
        results = [i for i in self.knowledge_data if query[:3] in str(i.values())]
        return results[:top_k]

    def ask(self, user_msg):
        if not self.ready:
            return "系統維護中：暫時無法存取資料庫，請稍後再試。"

        found_data = self.search(user_msg, top_k=3)
        
        context_text = ""
        source_url = ""
        
        for i, row in enumerate(found_data):
            # 獲取主來源網址 (取第一筆)
            if i == 0: source_url = row.get('url', '')
            
            # 處理附件
            attachments = row.get('attachments', [])
            attach_str = ", ".join([f"[{a.get('name')}]" for a in attachments if isinstance(a, dict)]) if attachments else ""

            context_text += f"""
【資料來源 {i+1}】
標題：{row.get('title')}
網址：{row.get('url')}
附件：{attach_str}
內容摘要：{str(row.get('content'))[:400]}...
--------------------------------
"""

        # 🤖 更新後的 Prompt：加入美式服務風格指令
        prompt = f"""
你是一個親切且積極的內湖高工校園小幫手。
請根據下方的【檢索資料】回答家長的【問題】。

【回答準則】：
1. 語氣：親切、專業、充滿熱情（繁體中文）。
2. **美式服務風格（針對查無資料時）**：
   如果資料中找不到答案，請使用以下風格回覆：
   「您的問題很好！目前公告中暫時找不到相關資訊。建議家長您可以先直接聯繫學校詢問。同時，我們也會將您的問題記錄下來，並儘快更新在資料庫中，讓其他家長未來也可以參考。謝謝您幫助我們變得更好！」
3. **資訊對等**：
   - 如果有答案，請清晰條列，並適度使用 Emoji。
   - 務必提到資料中出現的「網址」或「附件」下載提醒。
4. **來源標註**：
   - 不要在文中反覆貼網址，請在回答結束後統一標註。

【檢索資料】：
{context_text if context_text else "EMPTY_DATABASE"}

【家長問題】：
{user_msg}

【你的回答】：
"""
        try:
            model = genai.GenerativeModel(MODEL_NAME)
            response = model.generate_content(prompt)
            reply = response.text
            
            # 只有在有資料且回答中沒包含 URL 時，才在最後補上來源
            if source_url and source_url not in reply:
                reply += f"\n\n🔗 來源資料參考：\n{source_url}"
                
            return reply
        except:
            return "您的問題很好！不過小幫手現在連線有點忙碌，可以請您稍後再試一次嗎？感謝您的耐心！"

# 初始化
brain = FullContextBrain()

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
    reply = brain.ask(user_msg)
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    app.run(port=5000)
