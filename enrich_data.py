import json
import os
import time
import google.generativeai as genai

# ==========================================
# 🔑 設定區
# ==========================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# 設定要處理的檔案 (這裡是動態公告的主檔)
TARGET_FILE = 'nihs_knowledge_full.json'

def generate_tags_and_summary(title, content):
    """
    呼叫 Gemini 為這篇公告生成「標籤」與「一句話摘要」
    """
    try:
        model = genai.GenerativeModel('gemini-2.0-flash')
        
        # 精簡 Prompt，節省 Token 並提高速度
        prompt = f"""
        你是內湖高工的資料整理員。請閱讀以下公告，並回傳 JSON 格式的標籤與摘要。
        
        【公告標題】：{title}
        【公告內容】：{content[:800]} (內容過長已截斷)

        【需求】：
        1. tags: 3-5 個關鍵字標籤 (例如: ["#高三", "#升學", "#統測", "#教務處"])。
        2. summary: 用一句話講完重點 (包含對象、截止日期)。
        
        請直接回傳 JSON 字串，不要有 markdown 格式。
        範例：{{ "tags": ["#標籤1", "#標籤2"], "summary": "摘要內容..." }}
        """
        
        response = model.generate_content(prompt, generation_config={"temperature": 0.1})
        text = response.text.strip().replace("```json", "").replace("```", "")
        result = json.loads(text)
        return result.get("tags", []), result.get("summary", "")
    except Exception as e:
        print(f"⚠️ AI 生成失敗: {e}")
        return [], ""

def enrich_json_data():
    if not os.path.exists(TARGET_FILE):
        print(f"❌ 找不到檔案 {TARGET_FILE}，跳過處理。")
        return

    print(f"📖 讀取 {TARGET_FILE}...")
    with open(TARGET_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 為了節省時間與 API 配額，我們只處理「最新的 20 筆」或「尚未標記」的資料
    # 在實際 production 中，您可以設計邏輯只處理 new data
    process_count = 0
    max_process = 50  # 每次更新最多處理 50 筆，避免超時
    
    total = len(data)
    print(f"🔍 共有 {total} 筆資料，準備進行語意增強...")

    for i, item in enumerate(data):
        # 如果已經有 tags 欄位，就跳過 (增量更新)
        if 'tags' in item and item['tags']:
            continue
            
        # 如果處理數量達到上限，先停止，留給下次 (避免 GitHub Action 超時)
        if process_count >= max_process:
            print("⏳ 達到單次處理上限，暫停處理。")
            break

        print(f"✨ [{process_count + 1}] 正在 AI 加料：{item.get('title', '無標題')}")
        
        tags, summary = generate_tags_and_summary(item.get('title', ''), str(item.get('content', '')))
        
        # 將 AI 產出的結果寫入資料
        item['tags'] = tags
        item['summary'] = summary
        
        # 組合出一個「增強版內容」供搜尋使用
        # 這是給 bot_v5_sqlite_fts.py 的 search_db 用的
        tags_str = " ".join(tags)
        item['content_enriched'] = f"【標籤】{tags_str}\n【摘要】{summary}\n{item.get('content', '')}"
        
        process_count += 1
        time.sleep(1) # 避免觸發 API Rate Limit

    # 存檔
    if process_count > 0:
        with open(TARGET_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        print(f"✅ 更新完成！共增強了 {process_count} 筆資料。")
    else:
        print("🎉 所有資料皆已標記，無需更新。")

if __name__ == "__main__":
    enrich_json_data()
