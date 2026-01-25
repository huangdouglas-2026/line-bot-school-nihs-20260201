# ==========================================
# 內湖高工家長小幫手 (自動蜘蛛版)
# ==========================================
import os
import logging
import asyncio
import nest_asyncio
import requests                   # 新增: 用來抓網頁連結
from bs4 import BeautifulSoup     # 新增: 用來分析 HTML
from urllib.parse import urljoin  # 新增: 用來處理網址拼接
from flask import Flask, request, abort

# --- LINE SDK v3 ---
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

# --- AI 與 資料庫 ---
from crawl4ai import AsyncWebCrawler
import google.generativeai as genai
import chromadb

nest_asyncio.apply()
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# 🔑 設定區 (建議在 Render 後台設定環境變數)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "您的_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "您的_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "您的_SECRET")

# 初始化
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.0-flash')
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 資料庫 (Render 重開機後會重置)
chroma_client = chromadb.Client()
collection = chroma_client.get_or_create_collection(name="school_data_spider")

# ==========================================
# 🕷️ 蜘蛛功能: 自動尋找重要連結
# ==========================================
def get_school_links(start_url, max_limit=15):
    """
    從首頁出發，抓取前 max_limit 個不重複的學校內網連結
    """
    print(f"🕷️ [蜘蛛] 正在掃描首頁: {start_url}")
    found_urls = set()  # 使用 set (集合) 自動去除重複網址
    
    # 1. 為了確保重要資訊不被遺漏，我們先手動加入必備網址
    important_urls = [
        "https://www.nihs.tp.edu.tw/nss/s/principal/p/01", # 校長簡介
        "https://www.nihs.tp.edu.tw/nss/p/contact",        # 聯絡資訊
        "https://www.nihs.tp.edu.tw/nss/p/access",         # 交通資訊
        start_url                                          # 首頁自己
    ]
    for url in important_urls:
        found_urls.add(url)

    # 2. 自動分析首頁連結
    try:
        response = requests.get(start_url, timeout=5)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        links = soup.find_all('a', href=True)
        print(f"🕸️ [蜘蛛] 首頁共發現 {len(links)} 個連結，正在過濾...")

        for link in links:
            if len(found_urls) >= max_limit:
                break
                
            href = link['href']
            full_url = urljoin(start_url, href)
            
            # 過濾規則:
            # 1. 必須是學校網域 (防止爬到外部廣告)
            # 2. 排除檔案下載 (jpg, pdf, zip) 避免爬蟲卡住
            # 3. 排除登入頁面
            if "nihs.tp.edu.tw" in full_url:
                if not any(x in full_url.lower() for x in ['.jpg', '.png', '.pdf', '.zip', '.doc', 'login', 'passport']):
                    found_urls.add(full_url)
                    
    except Exception as e:
        print(f"⚠️ [蜘蛛] 掃描失敗: {e}")

    final_list = list(found_urls)
    print(f"✅ [蜘蛛] 最終確認爬取目標: {len(final_list)} 頁")
    return final_list

# 黃金小抄 (必備知識)
school_fact_sheet = """
【學校基本資料】
* 現任校長：林俊岳
* 學校地址：臺北市內湖區內湖路一段520號
* 總機電話：(02) 2657-4874
* 學校網址：https://www.nihs.tp.edu.tw
"""

# ==========================================
# 🧠 知識庫更新 (含過濾機制)
# ==========================================
async def update_knowledge_base():
    # 1. 啟動蜘蛛抓連結
    target_urls = get_school_links("https://www.nihs.tp.edu.tw/nss/p/index", max_limit=15)
    
    print("🚀 [爬蟲] 開始讀取網頁內容...")
    async with AsyncWebCrawler() as crawler:
        results = await crawler.arun_many(urls=target_urls)

    success_count = 0
    for result in results:
        if not result.success: continue
        
        content = result.markdown
        # 🧹 雜訊過濾: 內容太短通常是無效頁面
        if not content or len(content) < 50: 
            print(f"🗑️ [過濾] 內容過短，跳過: {result.url}")
            continue
            
        # 切割並存入資料庫
        chunk_size = 1000
        chunks = [content[i:i+chunk_size] for i in range(0, len(content), chunk_size)]
        
        # 這裡使用 url + 序號作為 ID，確保同一網址重跑時會覆蓋舊資料，不會重複堆疊
        if chunks:
            ids = [f"{result.url}_{i}" for i in range(len(chunks))]
            collection.upsert(
                documents=chunks,
                ids=ids,
                metadatas=[{"source": result.url} for _ in range(len(chunks))]
            )
            success_count += 1
            
    print(f"✅ [完成] 成功建立 {success_count} 個頁面的知識庫！")

# ==========================================
# 🤖 LINE Webhook 與 啟動
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

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_msg = event.message.text
    try:
        # 搜尋資料庫
        results = collection.query(query_texts=[user_msg], n_results=3)
        context_text = ""
        if results['documents'] and results['documents'][0]:
            for i, doc in enumerate(results['documents'][0]):
                context_text += f"【來源】: {results['metadatas'][0][i]['source']}\n{doc[:200]}...\n\n"

        if not context_text:
            prompt = f"請參考基本資料回答：{school_fact_sheet}\n使用者問題：{user_msg}"
        else:
            prompt = f"""
            你是一個專業的家長小幫手。請根據以下資料回答問題。
            
            【學校基本資料 (最優先)】：
            {school_fact_sheet}
            
            【搜尋到的網頁資料】：
            {context_text}
            
            【使用者問題】：{user_msg}
            
            回答時請附上資料來源網址。
            """

        response = model.generate_content(prompt)
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=response.text)]
                )
            )
    except Exception as e:
        print(f"Error: {e}")

# 啟動時執行爬蟲
with app.app_context():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(update_knowledge_base())

if __name__ == "__main__":
    app.run()
