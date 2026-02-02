import os
import json
import time
import random
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
# 引入重試機制
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ==========================================
# 🎯 設定區
# ==========================================
START_URL = "https://www.nihs.tp.edu.tw/nss/p/index"  # 學校首頁
OUTPUT_FILE = "nihs_final_v40.json" # 靜態爬蟲暫存檔
MAX_DEPTH = 3  # 遞迴深度 (避免爬太深回不來)

# ✅ 修正 1: 完整的瀏覽器偽裝標頭
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0"
}

visited_urls = set()
all_data = []

# ==========================================
# 🛠️ 工具函式：建立強健的 Session
# ==========================================
def get_session():
    """ 建立一個帶有重試機制的 Session """
    session = requests.Session()
    # 設定重試策略：遇到 500, 502, 503, 504 錯誤時，最多重試 3 次，每次間隔時間加倍
    retry = Retry(total=3, read=3, connect=3, backoff_factor=2, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(HEADERS)
    return session

# 初始化 Session
http_session = get_session()

def clean_text(text):
    """ 清理多餘的空白與換行 """
    if not text:
        return ""
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    return '\n'.join(lines)

def crawl_recursive(url, depth, category="校園靜態資訊"):
    """ 遞迴爬取函式 """
    if depth > MAX_DEPTH:
        return
    
    # 去除參數，避免重複爬取 (例如 ?id=1 與 ?id=1&t=2 視為同一頁)
    parsed = urlparse(url)
    clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    
    if clean_url in visited_urls:
        return
    visited_urls.add(clean_url)

    print(f"{'   ' * (3-depth)}🔍 分析頁面: {url}")

    try:
        # ✅ 修正 2: 加入隨機延遲 (1~3秒)，模擬人類行為，避免被雲端防火牆封鎖
        time.sleep(random.uniform(1.5, 3.5))

        response = http_session.get(url, timeout=20) # 延長 timeout
        
        # 如果狀態碼不是 200，跳過
        if response.status_code != 200:
            print(f"⚠️ 無法讀取 ({response.status_code})")
            return

        soup = BeautifulSoup(response.text, 'lxml') # 建議安裝 lxml: pip install lxml

        # 1. 抓取標題
        title = soup.title.string.strip() if soup.title else "無標題"
        
        # 2. 抓取主要內容 (針對 NSS 系統結構優化)
        # 嘗試抓取常見的內容區塊 ID 或 Class
        content_div = soup.find('div', class_='content') or \
                      soup.find('div', id='main_content') or \
                      soup.find('div', class_='module-content') or \
                      soup.body

        content_text = ""
        attachments = []
        
        if content_div:
            # 移除 script, style, nav 等干擾元素
            for bad in content_div(['script', 'style', 'nav', 'header', 'footer', 'iframe']):
                bad.decompose()
            
            content_text = clean_text(content_div.get_text())
            
            # 嘗試抓取附件連結 (PDF/Word)
            for a in content_div.find_all('a', href=True):
                href = a['href']
                if href.lower().endswith(('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx')):
                    full_link = urljoin(url, href)
                    attachments.append({
                        "name": a.get_text(strip=True) or "附件",
                        "url": full_link
                    })

        # 只有當內容長度足夠時才儲存 (過濾掉空頁面或載入頁)
        if len(content_text) > 50:
            print(f"{'   ' * (3-depth)}   📝 抓到內容: {len(content_text)} 字")
            
            all_data.append({
                "category": category,
                "unit": "校園官網", # 靜態頁面較難分單位，統一標示
                "date": time.strftime("%Y/%m/%d"), # 抓取當天日期
                "title": title,
                "url": url,
                "content": content_text,
                "attachments": attachments,
                "crawled_at": time.strftime("%Y-%m-%d %H:%M:%S")
            })
        else:
            print(f"{'   ' * (3-depth)}   ⚠️ 內容過短或無內文 (可能需 JavaScript 渲染)")

        # 3. 繼續遞迴抓取子連結 (只抓同網域下的連結)
        # 針對「關於湖工」這種目錄結構
        sub_links = []
        # 抓取左側選單或內容區的連結
        target_area = soup.find('div', class_='panel-group') or content_div
        
        if target_area:
            for a in target_area.find_all('a', href=True):
                href = a['href']
                full_link = urljoin(url, href)
                
                # 簡單過濾：只抓內湖高工網域，且不抓圖片/檔案
                if "nihs.tp.edu.tw" in full_link and not href.lower().endswith(('.jpg', '.png', '.pdf', '.zip')):
                    sub_links.append(full_link)

        # 去重
        sub_links = list(set(sub_links))
        
        if len(sub_links) > 0:
            print(f"{'   ' * (3-depth)}   🔗 發現 {len(sub_links)} 個子分頁，準備深入...")
            
            for link in sub_links:
                crawl_recursive(link, depth + 1, category)

    except Exception as e:
        print(f"❌ 爬取錯誤 {url}: {e}")

# ==========================================
# 🚀 主程式
# ==========================================
if __name__ == "__main__":
    print("🚀 V43 (雲端抗偵測版) 啟動...")
    print(f"🕷️ 目標首頁: {START_URL}")
    
    # 開始爬蟲
    crawl_recursive(START_URL, 1)
    
    # 存檔
    print(f"💾 爬取完成，共 {len(all_data)} 筆資料，正在存檔...")
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(all_data, f, ensure_ascii=False, indent=4)
    
    print(f"✅ 檔案已儲存: {OUTPUT_FILE}")
