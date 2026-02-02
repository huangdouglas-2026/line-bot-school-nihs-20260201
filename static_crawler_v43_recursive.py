# ====================================================
# 🏛️ 靜態頁面捕手 V43 (Recursive Navigator / 遞迴導航版)
# 目標：
# 1. 抓取主選單頁面
# 2. 自動偵測左側子選單 (nav-Vertical) 並遞迴抓取
# 3. 排除公告列表，只抓取靜態內容 (htmldisplay)
# ====================================================
import asyncio
import json
import os
from datetime import datetime
from playwright.async_api import async_playwright

# 📂 設定
OUTPUT_FILENAME = "nihs_static_data_v43.json"
BASE_URL = "https://www.nihs.tp.edu.tw/nss/p/"
MAX_DEPTH = 3  # 設定遞迴深度限制

# 初始目標 (主選單)
START_PAGES = {
    # --- 關於湖工 ---
    "關於湖工-本校緣起": "21",
    "關於湖工-優質環境": "23",
    "關於湖工-組織架構": "22",
    "關於湖工-業務職掌": "org1",
    "關於湖工-大事紀": "210",

    # --- 行政單位 ---
    "行政單位-校長室": "headmaster1",
    "行政單位-教務處": "32",
    "行政單位-學務處": "student9",
    "行政單位-實習處": "practice1",
    "行政單位-圖書館": "library03",
    "行政單位-總務處": "36",
    "行政單位-輔導室": "37",
    "行政單位-人事室": "39",
    "行政單位-會計室": "310",

    # --- 教學單位 (科系介紹) ---
    "教學單位-共同科目": "48",
    "教學單位-電子科": "42",
    "教學單位-電機科": "41",
    "教學單位-資訊科": "44",
    "教學單位-控制科": "con",
    "教學單位-冷凍空調科": "43",
    "教學單位-應用英語科": "46",
    "教學單位-門市服務科": "47",
    "教學單位-家電技術科": "49",

    # --- 學生園地 (僅抓取校內靜態頁，排除外部系統) ---
    "學生園地-學生手冊": "stuhb",
    
    # --- 相關組織 ---
    "相關組織-教師會": "teacher",
    "相關組織-家長會": "92",
    "相關組織-合作社": "93",
    "相關組織-夥伴學校": "76",
    
    # --- English ---
    "English-History & Features": "english2",
    "English-Department Profile": "english3"
}

# 用來記錄已抓過的網址，避免無窮迴圈
visited_urls = set()
all_data = []

async def extract_content(page, category, title, url, depth=0):
    """抓取單一頁面的靜態內容"""
    if url in visited_urls: return
    if depth > MAX_DEPTH: return # 超過深度限制則停止

    visited_urls.add(url)
    
    prefix = "  " * depth # 縮排顯示層級
    print(f"{prefix}🔍 分析頁面: [{category}] {title} ...")
    
    data = {
        "category": "校園靜態資訊",
        "unit": category,
        "date": datetime.now().strftime("%Y/%m/%d"),
        "title": title,
        "url": url,
        "content": "",
        "attachments": [],
        "crawled_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    try:
        # 設定較長的 timeout 避免網路慢時錯誤
        await page.goto(url, timeout=60000, wait_until='domcontentloaded')
        
        # 等待主要內容或選單出現，容錯處理
        try:
            await page.wait_for_selector(".module-content", timeout=5000)
        except: 
            pass # 沒找到也沒關係，繼續嘗試抓內容
        
        await page.wait_for_timeout(1000) # 等待渲染

        # --- A. 抓取靜態內容 (排除公告列表) ---
        # 我們只對 .htmldisplay 感興趣 (這是 Ischool 系統放靜態圖文的地方)
        # 尋找所有靜態區塊，但排除包含公告列表的區塊
        content_blocks = await page.locator(".htmldisplay").all()
        
        full_text = ""
        for block in content_blocks:
            # 確保這個區塊可見
            if await block.is_visible():
                text = await block.inner_text()
                full_text += text + "\n"
                
                # 抓取該區塊內的附件
                links = await block.locator("a").all()
                for link in links:
                    href = await link.get_attribute("href")
                    name = await link.inner_text()
                    if href and any(ext in href.lower() for ext in ['.pdf', '.doc', '.xls', '.ppt', '.jpg', '.png']):
                        if href.startswith("/"): href = "https://www.nihs.tp.edu.tw" + href
                        data["attachments"].append({"name": name.strip(), "url": href})
        
        # 簡單清洗
        data["content"] = "\n".join([line.strip() for line in full_text.split('\n') if line.strip()])
        
        if data["content"]:
            print(f"{prefix}   📝 抓到內容: {len(data['content'])} 字")
            all_data.append(data)
        else:
            print(f"{prefix}   ⚠️ 無靜態內文 (可能僅是目錄頁)")

        # --- B. 偵測子選單 (遞迴核心) ---
        # 尋找左側導航列 (.nav-Vertical a)
        sub_links = await page.locator(".nav-Vertical a").all()
        
        # 收集需要前往的子連結
        next_targets = []
        for link in sub_links:
            href = await link.get_attribute("href")
            name = await link.inner_text()
            name = name.strip()
            
            if href and name:
                # 排除外部連結 (http開頭但不是本校)
                if href.startswith("http") and "nihs.tp.edu.tw" not in href: continue
                
                # 處理相對路徑 (Ischool 系統通常直接給 PageID，例如 "Academic2")
                if not href.startswith("http"):
                    # 判斷是否已經包含 /nss/p/，避免重複疊加
                    if href.startswith("/nss/p/"):
                         full_href = f"https://www.nihs.tp.edu.tw{href}"
                    elif href.startswith("/"): # 其他根目錄連結
                         full_href = f"https://www.nihs.tp.edu.tw{href}"
                    else: # 純 PageID
                         full_href = f"{BASE_URL}{href}"
                else:
                    full_href = href
                
                # 如果還沒抓過，就加入佇列
                if full_href not in visited_urls:
                    next_targets.append((name, full_href))

        # 遞迴抓取子頁面
        if next_targets:
            print(f"{prefix}   🔗 發現 {len(next_targets)} 個子分頁，準備深入...")
            for sub_name, sub_url in next_targets:
                # 遞迴呼叫 (傳遞當前的 category 作為母單位)
                await extract_content(page, category, f"{title}-{sub_name}", sub_url, depth + 1)
                await page.wait_for_timeout(500)

    except Exception as e:
        print(f"{prefix}   ❌ 處理失敗: {e}")

async def main():
    print("🚀 V43 (遞迴導航版) 啟動...")
    async with async_playwright() as p:
        # ⚠️ 重要修正：雲端環境通常需要 headless=True，本地測試可改為 False
        # 為了保險起見，我們預設 True (背景執行)，這樣在 GitHub Actions 就不會報錯
        browser = await p.chromium.launch(headless=True) 
        
        # 設定 User-Agent 模擬真實瀏覽器，避免被擋
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        # 從設定的種子頁面開始
        for name, pid in START_PAGES.items():
            start_url = f"{BASE_URL}{pid}"
            # 從頂層開始抓
            await extract_content(page, name, name, start_url)

        await browser.close()

    # 存檔
    print("\n" + "="*30)
    if len(all_data) > 0:
        with open(OUTPUT_FILENAME, 'w', encoding='utf-8') as f:
            json.dump(all_data, f, ensure_ascii=False, indent=4)
        print(f"✅ 完成！共抓取 {len(all_data)} 個靜態頁面。")
        print(f"👉 檔案位置: {os.path.abspath(OUTPUT_FILENAME)}")
    else:
        print("⚠️ 未抓取到資料。")

if __name__ == "__main__":
    asyncio.run(main())
