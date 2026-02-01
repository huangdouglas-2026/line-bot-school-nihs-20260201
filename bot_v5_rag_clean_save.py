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
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")

# 初始化
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

if LINE_CHANNEL_ACCESS_TOKEN and LINE_CHANNEL_SECRET:
    line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
    handler = WebhookHandler(LINE_CHANNEL_SECRET)

app = Flask(__name__)
DATA_FILE = 'nihs_knowledge_full.json'

# ==========================================
# 🧠 AI 大腦 (Long Context 全知模式)
# ==========================================
class FullContextBrain:
    def __init__(self, json_path):
        self.knowledge_text = ""
        self.load_data(json_path)

    def load_data(self, path):
        """ 直接讀取 JSON，組合成超長文本 """
        if not os.path.exists(path):
            print(f"❌ 找不到 {path}")
            self.knowledge_text = "目前系統資料庫遺失，無法回答問題。"
            return
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 統計一下載入了什麼
            print(f"📂 [系統] 正在載入 {len(data)} 筆資料...")
            
            # 將資料組合成適合閱讀的文本
            text_parts = []
            for item in data:
                # 容錯處理：有些欄位可能是 None
                title = item.get('title', '無標題')
                content = item.get('content', '無內容')
                date = item.get('date', '')
                
                part = f"【日期】：{date}\n【標題】：{title}\n【內容】：{content}\n----------------"
                text_parts.append(part)
            
            self.knowledge_text = "\n".join(text_parts)
            print(f"✅ [系統] 資料載入完成！總字數: {len(self.knowledge_text)}")
            
        except Exception as e:
            print(f"❌ 讀取資料失敗: {e}")
            self.knowledge_text = "資料讀取發生錯誤。"

    def ask(self, user_query):
        """ 把整份資料丟給 Gemini 1.5 Flash """
        if not self.knowledge_text:
            return "系統資料庫讀取失敗。"

        # Prompt 設計
        prompt = f"""
        你是內湖高工的校園親切助手。
        請閱讀下方的【校園知識庫】，並根據內容回答使用者的問題。
        
        【回答規則】：
        1. **一定要從資料庫裡找答案**。
        2. 如果資料庫裡有「地址」、「校長」等資訊，請直接回答。
        3. 如果資料庫裡真的完全沒有提到，才說「查無資料」。
        4. 語氣要親切、有禮貌。

        【校園知識庫開始】
        {self.knowledge_text}
        【校園知識庫結束】

        使用者問題：{user_query}
        """

        try:
            # ✅ 使用 1.5 Flash (支援長文本)
            model = genai.GenerativeModel('models/gemini-1.5-flash')
            response = model.generate_content(prompt)
            return response.text
        except Exception as e:
            print(f"❌ API Error: {e}")
            return "AI 連線忙碌中，請稍後再試。"

# 賴皮啟動 (Lazy Loading)
brain = None
def get_brain():
    global brain
    if brain is None:
        brain = FullContextBrain(DATA_FILE)
    return brain

# ==========================================
# 🌐 路由區
# ==========================================
@app.route("/", methods=['GET'])
def home():
    return "Hello, NIHS Bot (Full Context Version) is alive!", 200

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
    msg = event.message.text.strip()
    print(f"👉 收到: {msg}")

    try:
        current_brain = get_brain()
        reply_text = current_brain.ask(msg)
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    app.run(port=5000)
