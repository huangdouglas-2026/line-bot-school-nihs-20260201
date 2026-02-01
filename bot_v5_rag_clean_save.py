import os
import json
import numpy as np
import pandas as pd
import faiss
import google.generativeai as genai
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
# 修改後 (正確)
from linebot.models import MessageEvent, TextMessage, TextSendMessage

# ==========================================
# 🔑 金鑰設定 (從環境變數讀取)
# ==========================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")

# 初始化 Google Gemini
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
else:
    print("⚠️ 警告：找不到 GEMINI_API_KEY")

# 初始化 LINE Bot
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

app = Flask(__name__)

# 檔案路徑
RAW_DATA_FILE = 'nihs_knowledge_full.json'

# ==========================================
# 🧠 AI 大腦核心 (雲端輕量版)
# ==========================================
class CloudSchoolBrain:
    def __init__(self, json_path):
        self.ready = False
        self.index = None
        self.chunks = []
        self.json_path = json_path
        print("☁️ [雲端大腦] 正在初始化...")
        self.build_brain()

    def get_embedding(self, text):
        """ 呼叫 Google API 取得向量 (省記憶體關鍵) """
        try:
            # 使用 Google 最新的 text-embedding-004 模型
            result = genai.embed_content(
                model="models/text-embedding-004",
                content=text,
                task_type="retrieval_query"
            )
            return result['embedding']
        except Exception as e:
            print(f"❌ Embedding Error: {e}")
            return [0] * 768 # 失敗回傳空向量

    def build_brain(self):
        try:
            if not os.path.exists(self.json_path):
                print(f"❌ 找不到資料檔: {self.json_path}")
                return

            with open(self.json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # 準備資料
            self.chunks = []
            vectors = []
            
            print(f"🐢 [系統] 正在透過 API 建立索引 (共 {len(data)} 筆)，請稍候...")
            
            # 逐筆建立向量 (這裡可能會花一點時間，但不會吃記憶體)
            for item in data:
                # 組合標題與內文
                text_content = f"標題：{item.get('title', '')}\n內文：{item.get('content', '')}"
                self.chunks.append(text_content)
                
                # 呼叫 API
                vec = self.get_embedding(text_content)
                vectors.append(vec)

            # 轉為 numpy 矩陣
            embedding_matrix = np.array(vectors).astype('float32')
            
            # 建立 FAISS 索引 (維度 768)
            dimension = 768 
            self.index = faiss.IndexFlatL2(dimension)
            self.index.add(embedding_matrix)
            
            self.ready = True
            print(f"✅ [雲端大腦] 建置完成！索引大小: {self.index.ntotal}")

        except Exception as e:
            print(f"❌ 建置失敗: {e}")

    def search(self, query, top_k=3):
        if not self.ready:
            return []
        
        # 1. 把使用者的問題轉成向量
        query_vec = self.get_embedding(query)
        query_vec_np = np.array([query_vec]).astype('float32')
        
        # 2. 搜尋
        distances, indices = self.index.search(query_vec_np, top_k)
        
        results = []
        for i in range(top_k):
            idx = indices[0][i]
            if idx != -1:
                results.append(self.chunks[idx])
        return results

    def ask_gemini(self, query, context_list):
        context_text = "\n\n".join(context_list)
        prompt = f"""
        你是內湖高工的親切校園助手。請根據參考資料回答家長問題。
        若資料不足，請禮貌告知「目前查無相關資訊」。
        
        【參考資料】：
        {context_text}
        
        【家長問題】：{query}
        """
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(prompt)
        return response.text

# ==========================================
# 🚀 賴皮啟動機制 (Lazy Loading)
# ==========================================
brain = None

def get_brain():
    global brain
    if brain is None:
        print("🐢 [系統] 第一次收到訊息，開始載入大腦...")
        brain = CloudSchoolBrain(RAW_DATA_FILE)
    return brain

# ==========================================
# 🌐 Flask 路由
# ==========================================
@app.route("/", methods=['GET'])
def health_check():
    return "I am alive!", 200

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_msg = event.message.text.strip()
    
    try:
        current_brain = get_brain()
        
        if not current_brain or not current_brain.ready:
            reply_text = "系統正在暖機中，請再試一次..."
        else:
            # RAG 流程
            relevant_docs = current_brain.search(user_msg, top_k=3)
            reply_text = current_brain.ask_gemini(user_msg, relevant_docs)

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )
    except Exception as e:
        print(f"Error: {e}")
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="機器人發生錯誤，請稍後再試。")
        )

if __name__ == "__main__":
    app.run()

