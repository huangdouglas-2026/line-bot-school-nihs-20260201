import os
import json
import requests
import pdfplumber
import google.generativeai as genai
import re
from datetime import datetime

# ==========================================
# 🔑 設定區
# ==========================================
# 1. 更新模型名稱為 flash-lite
MODEL_NAME = 'gemini-2.5-flash-lite' 

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

INPUT_FILE = 'nihs_knowledge_full.json'
OUTPUT_FILE = 'nihs_calendar.json'
TEMP_PDF = 'temp_calendar.pdf'

def find_official_calendar():
    """ 
    邏輯優化：精準鎖定標題符合「XX學年度第X學期行事曆」的 PDF
    """
    if not os.path.exists(INPUT_FILE):
        print("❌ 找不到資料庫檔案")
        return None, None, None

    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 用正則表達式匹配：XX學年度(第X學期)行事曆
    # 範例：114學年度第2學期行事曆
    pattern = re.compile(r"(\d{3})學年度(第[一二12]學期)?行事曆")

    candidates = []
    for item in data:
        title = item.get('title', '')
        match = pattern.search(title)
        
        if match and item.get('attachments'):
            # 找到標題符合的，再找裡面的 PDF 連結
            for att in item['attachments']:
                url = att.get('url', '')
                if url.lower().endswith('.pdf'):
                    candidates.append({
                        "weight": int(match.group(1)), # 用學年度當權重，越大的越新
                        "date": item.get('date', '1900/01/01'),
                        "title": title,
                        "url": url
                    })
    
    if not candidates:
        print("⚠️ 找不到符合「學年度學期」格式的行事曆 PDF")
        return None, None, None

    # 先依學年度(weight)排，再依公告日期(date)排
    candidates.sort(key=lambda x: (x['weight'], x['date']), reverse=True)
    latest = candidates[0]
    
    print(f"✅ 成功鎖定正式行事曆：{latest['title']}")
    return latest['url'], latest['title'], latest['date']

def download_pdf(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, stream=True, timeout=15, verify=False)
        if response.status_code == 200:
            with open(TEMP_PDF, 'wb') as f:
                f.write(response.content)
            return True
    except Exception as e:
        print(f"❌ 下載錯誤: {e}")
    return False

def extract_text_from_pdf():
    full_text = ""
    try:
        with pdfplumber.open(TEMP_PDF) as pdf:
            for page in pdf.pages:
                # 提取表格文字對於行事曆極其重要
                tables = page.extract_tables()
                if tables:
                    for table in tables:
                        for row in table:
                            clean_row = [str(cell).strip().replace('\n', '') for cell in row if cell]
                            full_text += " | ".join(clean_row) + "\n"
                # 補充純文字以防萬一
                full_text += page.extract_text() or ""
        return full_text
    except Exception as e:
        print(f"❌ PDF 解析失敗: {e}")
        return ""

def generate_calendar_json(pdf_text, doc_title, doc_date):
    print(f"🧠 使用 {MODEL_NAME} 解析行事曆...")
    
    current_year = datetime.now().year
    
    prompt = f"""
    你是校務資料處理專家。請根據下方行事曆 PDF 內容，整理出完整的活動清單。

    【背景資訊】:
    - 文件標題: "{doc_title}"
    - 公告日期: "{doc_date}"
    - 當前參考年份: {current_year}

    【任務】:
    1. 將所有活動轉為標準 JSON 格式。
    2. 必須判斷正確年份。若標題為 114學年度第2學期，則 2月之後的活動應為 2026 年。
    3. 日期格式: "YYYY/MM/DD"。
    4. 類別請分類為: "教務", "學務", "總務", "實習", "輔導", "放假", "考試"。

    【輸出格式】:
    [
      {{ "date": "YYYY/MM/DD", "event": "活動名稱", "category": "分類" }}
    ]

    【PDF 內容】:
    {pdf_text[:40000]}
    """

    generation_config = genai.types.GenerationConfig(
        response_mime_type="application/json",
        max_output_tokens=8192,
        temperature=0
    )

    try:
        model = genai.GenerativeModel(MODEL_NAME)
        response = model.generate_content(prompt, generation_config=generation_config)
        return json.loads(response.text)
    except Exception as e:
        print(f"❌ AI 解析出錯: {e}")
        return []

if __name__ == "__main__":
    pdf_url, title, date_str = find_official_calendar()
    
    if pdf_url:
        if download_pdf(pdf_url):
            raw_text = extract_text_from_pdf()
            if raw_text:
                events = generate_calendar_json(raw_text, title, date_str)
                if events:
                    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
                        json.dump(events, f, ensure_ascii=False, indent=4)
                    print(f"✅ 成功生成行事曆資料庫 ({len(events)} 筆活動)")
            
            if os.path.exists(TEMP_PDF):
                os.remove(TEMP_PDF)