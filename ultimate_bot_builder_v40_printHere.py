# ====================================================
# 🧠 終極大腦建構者 V40 (Target Locked / printHere 精確鎖定版)
# 目標：
# 1. 根據使用者提供的 HTML，鎖定 id="printHere" 抓取內文
# 2. 鎖定 .htmldisplay class 提取純文字
# 3. 完整保留內文與附件，與真實網址一併存檔
# ====================================================
import asyncio
import json
import re
import os
from datetime import datetime
from playwright.async_api import async_playwright, expect

# 📂 設定
TARGET_URL = "https://www.nihs.tp.edu.tw/nss/s/main/index"
MAX_PAGES = 10      # 每個處室抓 5 頁
OUTPUT_FILENAME = "nihs_final_v40.json"

TARGET_TABS = [
    "重要訊息 頁籤",
    "學務處 頁籤",
    "教務處 頁籤",
    "實習處 頁籤",
    "輔導室 頁籤",
    "圖書館 頁籤",
    "政令宣導 頁籤",
    "合作社 頁籤",
    "學生活動 頁籤",
    "新生專區 頁籤",
    "升學資訊 頁籤",
    "考試資訊 頁籤",
    "教師研習 頁籤",
    "政令宣導 頁籤",
]

all_data = []

async def force_close_modal(page):
    """暴力關閉視窗"""
    await page.keyboard.press("Escape")
    try:
        # 嘗試點擊右上角關閉鈕
        close_btn = page.locator("button.close, button[data-dismiss='modal'], #closeCross").first
        if await close_btn.is_visible():
            await close_btn.click()
    except: pass
    
    # 點擊背景 (座標 0,0)
    await page.mouse.click(0, 0)
    await page.wait_for_timeout(500)

async def extract_details(page):
    """
    提取資料 (基於 HTML id="printHere")
    """
    data = {"body": "", "attachments": [], "real_url": "無法取得"}
    
    try:
        # 等待視窗載入 (以 mailto 按鈕或 printHere 出現為準)
        try:
            await page.wait_for_selector("#printHere, a[href^='mailto:']", state="visible", timeout=5000)
        except:
            return data # 逾時返回空資料

        # 1. 提取 Permalink (這部分之前已驗證成功)
        try:
            mailto = page.locator("a[href^='mailto:']").first
            if await mailto.count() > 0:
                href = await mailto.get_attribute("href")
                match = re.search(r'(https?://[^\s&]+)', href)
                if match: data["real_url"] = match.group(1).replace("&amp;", "&")
        except: pass
        
        # 2. 提取內文 (Body) - 【核心修正點】
        # 根據您的 HTML，內容在 #printHere 下面的 .htmldisplay
        # 如果沒有 .htmldisplay，就直接抓 #printHere 的文字
        print_here = page.locator("#printHere").first
        
        if await print_here.count() > 0:
            # 優先找 .htmldisplay (通常包含排版好的內文)
            html_display = print_here.locator(".htmldisplay")
            if await html_display.count() > 0:
                data["body"] = await html_display.inner_text()
            else:
                # 備案：直接抓 printHere 容器文字
                data["body"] = await print_here.inner_text()
        else:
            # 備案：如果這篇剛好沒有 printHere (舊版公告)，回退到通用選擇器
            fallback = page.locator(".modal-body, .module-detail").first
            if await fallback.count() > 0:
                data["body"] = await fallback.inner_text()

        # 3. 提取附件 (Attachments)
        # 掃描 #printHere 內部以及整個 modal
        modal_content = page.locator(".modal-content, div[role='dialog']").first
        
        # 收集候選區域
        candidates = []
        if await print_here.count() > 0: candidates.append(print_here)
        if await modal_content.count() > 0: candidates.append(modal_content)
        
        seen_urls = set() # 防止重複
        
        for container in candidates:
            links = await container.locator("a").all()
            for link in links:
                href = await link.get_attribute("href")
                text = await link.inner_text()
                text = text.strip()
                
                if not href: continue
                href_lower = href.lower()
                
                # 判斷是否為檔案
                is_file = False
                # 特徵 A: 包含 feeder
                if "feeder" in href_lower: is_file = True
                # 特徵 B: 副檔名
                elif any(ext in href_lower for ext in ['.pdf', '.doc', '.xls', '.ppt', '.zip', '.jpg', '.png']): is_file = True
                
                # 排除
                if "mailto:" in href_lower: is_file = False
                if not text: is_file = False 
                
                if is_file:
                    if href.startswith("/"): href = "https://www.nihs.tp.edu.tw" + href
                    
                    if href not in seen_urls:
                        data["attachments"].append({"name": text, "url": href})
                        seen_urls.add(href)

    except Exception as e:
        print(f"      ⚠️ 解析細節微誤: {e}")
        
    return data

async def harvest_tab(page, tab_label):
    print(f"\n🔵 準備切換至: {tab_label} ...")
    
    # 1. 頁籤切換
    tab_link = page.locator(f"a[aria-label='{tab_label}']")
    tab_li = page.locator(f"//li[contains(@class, 'nav-item') and .//a[@aria-label='{tab_label}']]")
    
    if await tab_link.count() == 0:
        print(f"❌ 找不到頁籤: {tab_label}")
        return

    is_active = await tab_li.get_attribute("class")
    if "active" not in str(is_active):
        await tab_link.click()
        try:
            await expect(tab_li).to_have_class(re.compile(r"active"), timeout=5000)
            print("   ✅ 頁籤切換成功")
        except:
            print("   ⚠️ 頁籤切換超時，嘗試繼續...")
    else:
        print("   ✅ 已經在目標頁籤")

    container = tab_li
    try:
        await container.locator("table").wait_for(state="visible", timeout=5000)
    except: return

    # 2. 分頁迴圈
    for current_page in range(1, MAX_PAGES + 1):
        print(f"   📄 [第 {current_page} 頁] ...")

        # 翻頁
        if current_page > 1:
            page_btn = container.locator(f"button[title='第{current_page}頁']")
            if await page_btn.count() == 0: page_btn = container.locator("button[title='下一頁']")
            if await page_btn.count() > 0:
                try:
                    await page_btn.click()
                    await page.wait_for_timeout(3000)
                except: break
            else:
                print("      🏁 無下一頁")
                break

        # 逐行處理
        rows = await container.locator("table tbody tr").all()
        total_rows = len(rows)
        print(f"      📊 發現 {total_rows} 行...")

        for i in range(total_rows):
            row = container.locator("table tbody tr").nth(i)
            if await row.locator("td").count() < 3: continue

            title_el = row.locator("td:nth-child(1) a").first
            if await title_el.count() == 0: continue

            title = await title_el.inner_text()
            unit = await row.locator("td:nth-child(2)").inner_text()
            date = await row.locator("td:nth-child(3)").inner_text()
            
            await title_el.scroll_into_view_if_needed()
            print(f"      [{i+1:02d}] {title[:10]}...", end="", flush=True)

            try:
                # 點擊開啟
                await title_el.click()
                
                # 抓取詳細資料
                details = await extract_details(page)
                
                # 狀態顯示
                status = []
                if details["real_url"] != "無法取得": status.append("🔗")
                if len(details["body"]) > 10: status.append(f"📝{len(details['body'])}字")
                if len(details["attachments"]) > 0: status.append(f"📎{len(details['attachments'])}")
                
                if status:
                    print(f" -> ✅ {' '.join(status)}", end="", flush=True)
                else:
                    print(f" -> ⚠️ 空資料", end="", flush=True)

                # 存檔
                all_data.append({
                    "category": tab_label.replace(" 頁籤", ""),
                    "date": date.strip(),
                    "unit": unit.strip(),
                    "title": title.strip(),
                    "url": details["real_url"],
                    "content": details["body"].strip(),
                    "attachments": details["attachments"],
                    "crawled_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })

            except Exception as e:
                print(f" -> ❌ {e}", end="", flush=True)

            # 關閉視窗
            await force_close_modal(page)
            print(" -> ⏹️", flush=True)
            await page.wait_for_timeout(500)

async def main():
    print("🚀 V40 (printHere 精確鎖定版) 啟動...")
    async with async_playwright() as p:
        # 改成 True，代表在背景執行 (無頭模式)
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        await page.goto(TARGET_URL)
        await page.wait_for_load_state("networkidle")
        
        for tab in TARGET_TABS:
            await harvest_tab(page, tab)
            await page.wait_for_timeout(1000)
        
        await browser.close()

    print("\n" + "="*30)
    if all_data:
        with open(OUTPUT_FILENAME, 'w', encoding='utf-8') as f:
            json.dump(all_data, f, ensure_ascii=False, indent=4)
        print(f"✅ 全部完成！共 {len(all_data)} 筆。")
        print(f"👉 檔案: {os.path.abspath(OUTPUT_FILENAME)}")
    else:
        print("⚠️ 無資料")

if __name__ == "__main__":

    asyncio.run(main())
