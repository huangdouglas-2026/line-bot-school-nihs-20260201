# ====================================================
# 🏛️ 靜態頁面捕手 V44 (寬容擷取 + 智慧等待版)
# ====================================================
import asyncio
import json
import os
import random
from datetime import datetime
from playwright.async_api import async_playwright

# 📂 設定
OUTPUT_FILENAME = "nihs_static_data_v43.json" # 維持 v43 檔名以便 merge_data 讀取
BASE_URL = "https://www.nihs.tp.edu.tw/nss/p/"
MAX_DEPTH = 3 

# 初始目標 (維持不變)
START_PAGES = {
    "關於湖工-本校緣起": "21",
    "關於湖工-優質環境": "23",
    "關於湖工-組織架構": "22",
    "關於湖工-業務職掌": "org1",
    "關於湖工-大事紀": "210",
    "行政單位-校長室": "headmaster1",
    "行政單位-教務處": "32",
    "行政單位-學務處": "student9",
    "行政單位-實習處": "practice1",
    "行政單位-圖書館": "library03",
    "行政單位-總務處": "36",
    "行政單位-輔導室": "37",
    "行政單位-人事室": "39",
    "行政單位-會計室": "310",
    "教學單位-共同科目": "48",
    "教學單位-電子科": "42",
    "教學單位-電機科": "41",
    "教學單位-資訊科": "44",
    "教學單位-控制科": "con",
    "教學單位-冷凍空調科": "43",
    "教學單位-應用英語科": "46",
    "教學單位-門市服務科": "47",
    "教學單位-家電技術科": "49",
    "學生園地-學生手冊": "stuhb",
    "相關組織-教師會": "teacher",
    "相關組織-家長會": "92",
    "相關組織-合作社": "93",
    "相關組織-夥伴學校": "76"
}

visited_urls = set()
all_data = []

async def extract_content(page, category, title, url, depth=0):
    if url in visited_urls: return
    if depth > MAX_DEPTH: return 

    visited_urls.add(url)
    
    prefix = "  " * depth
    print(f"{prefix}🔍 分析頁面: [{category}] {title}")
    
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
        # ✅ 修正 1: 延長 Timeout 並使用 networkidle (等待網路靜止)
        # GitHub Actions 比較慢，給它多一點時間
        await page.goto(url, timeout=60000, wait_until='domcontentloaded')
        
        try:
            # 嘗試等待網路閒置 (最準確，但有時會等太久，設個 timeout)
            await page.wait_for_load_state("networkidle", timeout=5000)
        except:
            pass # 如果超時就不等了，繼續往下

        # ✅ 修正 2: 模擬人類捲動 (觸發 Lazy Loading 內容)
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(2000) # 再給 2 秒讓內容長出來

        # --- A. 抓取內容 (多重策略) ---
        full_text = ""
        
        # 策略 1: 優先抓取標準靜態區塊 (.htmldisplay)
        target_selectors = [".htmldisplay", ".module-content", ".content", "#main-content"]
        found_selector = False
        
        for selector in target_selectors:
            if await page.locator(selector).count() > 0:
                # 排除隱藏元素
                elements = await page.locator(f"{selector}:visible").all()
                for el in elements:
                    text = await el.inner_text()
                    if len(text.strip()) > 10: # 稍微過濾太短的雜訊
                        full_text += text + "\n"
                        
                        # 抓附件
                        links = await el.locator("a").all()
                        for link in links:
                            href = await link.get_attribute("href")
                            name = await link.inner_text()
                            if href and any(ext in href.lower() for ext in ['.pdf', '.doc', '.xls', '.ppt']):
                                if href.startswith("/"): href = "https://www.nihs.tp.edu.tw" + href
                                data["attachments"].append({"name": name.strip(), "url": href})
                
                if len(full_text) > 20: # 確保有抓到東西
                    found_selector = True
                    break # 找到一種就夠了

        # 策略 2: 如果上面都沒抓到，使用「大絕招」抓 Body 並清洗
        if not found_selector or len(full_text) < 20:
            #print(f"{prefix}   ⚠️ 標準區塊無內容，啟動全頁掃描...")
            full_text = await page.evaluate("""() => {
                // 複製 body 避免破壞頁面
                let clone = document.body.cloneNode(true);
                // 移除導覽列、頁尾、腳本
                let garbages = clone.querySelectorAll('nav, footer, script, style, .nav-Vertical, .header');
                garbages.forEach(el => el.remove());
                return clone.innerText;
            }""")

        # 簡單清洗
        lines = [line.strip() for line in full_text.split('\n') if line.strip()]
        data["content"] = "\n".join(lines)
        
        if len(data["content"]) > 30: # 門檻設低一點，避免漏抓
            print(f"{prefix}   📝 抓到內容: {len(data['content'])} 字")
            all_data.append(data)
        else:
            print(f"{prefix}   ⚠️ 內容過短或確實為目錄頁")

        # --- B. 偵測子選單 ---
        # 尋找左側導航列
        sub_links = await page.locator(".nav-Vertical a").all()
        
        next_targets = []
        for link in sub_links:
            href = await link.get_attribute("href")
            name = await link.inner_text()
            name = name.strip()
            
            if href and name:
                if href.startswith("http") and "nihs.tp.edu.tw" not in href: continue
                
                if not href.startswith("http"):
                    if href.startswith("/nss/p/"):
                         full_href = f"https://www.nihs.tp.edu.tw{href}"
                    elif href.startswith("/"):
                         full_href = f"https://www.nihs.tp.edu.tw{href}"
                    else:
                         full_href = f"{BASE_URL}{href}"
                else:
                    full_href = href
                
                if full_href not in visited_urls:
                    next_targets.append((name, full_href))

        if next_targets:
            print(f"{prefix}   🔗 發現 {len(next_targets)} 個子分頁...")
            for sub_name, sub_url in next_targets:
                await extract_content(page, category, f"{title}-{sub_name}", sub_url, depth + 1)
                # ✅ 修正 3: 遞迴間隔稍微加長，減輕伺服器負擔
                await asyncio.sleep(random.uniform(1.0, 2.0))

    except Exception as e:
        print(f"{prefix}   ❌ 錯誤: {e}")

async def main():
    print("🚀 V44 (寬容擷取版) 啟動...")
    async with async_playwright() as p:
        # ✅ 雲端必須是 True
        browser = await p.chromium.launch(headless=True) 
        
        # 使用真實 User-Agent
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800} # 設定視窗大小確保不會變成手機版
        )
        page = await context.new_page()

        for name, pid in START_PAGES.items():
            start_url = f"{BASE_URL}{pid}"
            await extract_content(page, name, name, start_url)

        await browser.close()

    print("\n" + "="*30)
    if len(all_data) > 0:
        with open(OUTPUT_FILENAME, 'w', encoding='utf-8') as f:
            json.dump(all_data, f, ensure_ascii=False, indent=4)
        print(f"✅ 完成！共抓取 {len(all_data)} 頁。")
    else:
        print("⚠️ 未抓取到資料。")

if __name__ == "__main__":
    asyncio.run(main())
