import discord
import sys
sys.stdout.reconfigure(encoding='utf-8')
import asyncio
import random
import re
import os
from dotenv import load_dotenv

# ================= 設定區 =================
# 載入 .env 檔案裡的機密資料
load_dotenv()

# 透過 os.getenv 抓取 .env 裡名為 TRANSLATOR_TOKEN 的值
# 吹雪的token與染岡的使用者ID
BOT_TOKEN = os.getenv('TRANSLATOR_TOKEN')
TARGET_BOT_ID = 1492439449714561044
# ------------------------------------------
# 可選：當偵測到 Tweetcord 的 Twitter auth_token 失效時，要 @ 提醒誰
#
# 你需要在 .env 裡放：ALERT_MENTION_USER_ID=你的Discord使用者ID(純數字)
# 如果你不填、或填錯（例如不是數字），程式會自動當成 0（不 @ 任何人），避免整支 bot 起不來。
# ------------------------------------------
_alert_mention_user_id_raw = (os.getenv("ALERT_MENTION_USER_ID", "") or "").strip()
if _alert_mention_user_id_raw.isdigit():
    ALERT_MENTION_USER_ID = int(_alert_mention_user_id_raw)
else:
    ALERT_MENTION_USER_ID = 0

# --- 新增的監控設定 ---
# 警告要送到的 Discord 頻道需要在 .env 放：ALERT_CHANNEL_ID=某個頻道ID(純數字)
# 如果不填，會是 0，代表找不到頻道 -> 不會發警告（但 bot 仍會正常跑翻譯流程）
_alert_channel_id_raw = (os.getenv("ALERT_CHANNEL_ID", "") or "").strip()
ALERT_CHANNEL_ID = int(_alert_channel_id_raw) if _alert_channel_id_raw.isdigit() else 0
# 替換成你的染岡 Docker 容器名稱 (可以在 VM 輸入 docker ps 查看 NAMES 那欄)
DOCKER_CONTAINER_NAME = "tweetcord"

# 建立 client 物件 (必須放在 event 之前)
intents = discord.Intents.default()
intents.message_content = True  # 必須開啟才能讀取網址內容
client = discord.Client(intents=intents)
ALLOWED_MENTIONS_NONE = discord.AllowedMentions.none()

# ==========================================

def strip_discord_mentions(text: str) -> str:
    if not text:
        return text

    # ------------------------------------------
    # 這個函式的目標：
    # - 讓「吹雪送出去的訊息內容」不要帶有會被 Discord 顯示成提及/跳轉的語法
    # - 即使你不小心把染岡（Tweetcord）原文整段複製過來，也不會看到一堆 @mention
    #
    # 注意：這是「文字層面」的清理（讓畫面上不要出現提及語法）。
    # 另外我們也會在 send() 用 allowed_mentions 做「功能層面」的保護（避免真的 ping 到人）。
    # 兩個一起做最安全。
    # ------------------------------------------

    # 移除 Discord 會解析的提及語法，避免「複製到吹雪訊息」時還出現 @mention
    # 使用者: <@123>、<@!123>
    text = re.sub(r'<@!?\d+>', '', text)
    # 身分組: <@&123>
    text = re.sub(r'<@&\d+>', '', text)
    # 頻道: <#123>
    text = re.sub(r'<#\d+>', '', text)
    # @everyone / @here
    text = re.sub(r'@everyone\b', 'everyone', text, flags=re.IGNORECASE)
    text = re.sub(r'@here\b', 'here', text, flags=re.IGNORECASE)

    return text

async def get_alert_channel():
    """
    取得「警報要送到哪個頻道」。

    為什麼要這樣寫？
    - client.get_channel(id) 只會從快取拿，有時候機器人剛啟動、或沒快取到該頻道，會拿到 None
    - 在 VM/容器環境重啟很常發生「快取還沒暖起來」

    所以我們做 fallback：拿不到就用 API fetch_channel 再抓一次。
    """
    if not ALERT_CHANNEL_ID:
        print("ALERT_CHANNEL_ID 未設定或不是數字，因此不會發警告訊息。")
        return None

    channel = client.get_channel(ALERT_CHANNEL_ID)
    if channel:
        return channel

    try:
        return await client.fetch_channel(ALERT_CHANNEL_ID)
    except Exception as e:
        print(f"找不到警告回報頻道，請確認 ALERT_CHANNEL_ID 是否正確，且 bot 有權限看到該頻道！err={e}")
        return None

def check_needs_translation(text):
    """檢查文字是否真的需要翻譯"""
    if not text:
        return False
        
    # 1. 移除推文裡的網址 (包含圖片連結)
    text = re.sub(r'http\S+', '', text)
    
    # 2. 移除 Discord 標記 (像是 @染岡)
    text = strip_discord_mentions(text)
    
    # 3. 核心過濾：移除所有標點符號與 Emoji
    # \w 代表保留各國語言文字與數字，\s 代表保留空格。其餘(包含Emoji)全部殺掉
    clean_text = re.sub(r'[^\w\s]', '', text)
    
    # 4. 去除頭尾多餘的空白
    clean_text = clean_text.strip()

    # 5. 圖片/影片但「沒有內文」的推文，在 fxtwitter embed 的 description
    #    有時會只剩互動統計（例如 23 173 1.5K）或其他非語意內容。
    #    這種情況不應該觸發翻譯/重整，所以在這裡多做一次保護。
    #
    #    規則：清理後若只包含數字、空白、逗號、小數點，以及 K/M（千/百萬縮寫），就當作「沒有可翻譯內文」。
    #    例： "23 173 1.5K"、"1,234"、"2.1M"
    if re.fullmatch(r"[\d\s,\.kKmM]+", clean_text or ""):
        return False

    # 6. 另一個常見情況：fxtwitter 會在沒有內文的推文上，把互動統計加上英文單字
    #    例如： "23 likes 173 reposts 1.5K views"
    #    這些不是推文正文，不應該觸發翻譯/重整。
    #
    #    做法：把數字與 K/M 去掉後，只要剩下的英文字都在「互動統計詞彙表」裡，就視為無內文。
    engagement_words = {
        "like", "likes",
        "reply", "replies",
        "repost", "reposts",
        "retweet", "retweets",
        "quote", "quotes",
        "view", "views",
        "bookmark", "bookmarks",
        "share", "shares",
    }
    lowered = clean_text.lower()
    lowered_wo_numbers = re.sub(r"[\d\s,\.]+", " ", lowered)
    lowered_wo_numbers = re.sub(r"\b[km]\b", " ", lowered_wo_numbers)  # 1.5k / 2m 這類縮寫
    tokens = [t for t in lowered_wo_numbers.split() if t]
    if tokens and all(t in engagement_words for t in tokens):
        return False
    
    # 判斷：如果清完之後變成空的，或是「只剩下純數字」，就回傳 False (不需要翻譯)
    if not clean_text or clean_text.isnumeric():
        return False
        
    return True

def is_japanese(text):
    # 偵測是否含有平假名 (\u3040-\u309f) 或 片假名 (\u30a0-\u30ff)
    # 這是區分日文與中文最準確的方法
    return re.search(r'[\u3040-\u30ff]', text) is not None

def has_chinese(text):
    # 偵測是否包含任何中文字符 (CJK 統一表意文字)
    return re.search(r'[\u4e00-\u9fa5]', text) is not None

# ================== 吹雪的秘密監視任務 ==================
async def monitor_someoka_logs():
    """
    監控 Tweetcord 容器日誌，偵測 token 失效並發出警告。

    重要提醒（你說你在 VM 上跑、也不確定權限）：
    - 這段會在同一台 VM 上執行 `docker logs ...`
    - 需要「吹雪所在的環境」能執行 docker 指令，且有權限讀取 `DOCKER_CONTAINER_NAME` 那個容器的 logs
    - 如果權限不足，這段不會讓 bot 當掉，但會印出錯誤，並持續每 10 分鐘重試
    """
    await client.wait_until_ready()
    channel = await get_alert_channel()
    
    if not channel:
        return

    while not client.is_closed():
        try:
            # 讓吹雪執行指令，抓取染岡 Docker 的最後 30 行日誌
            cmd = f"docker logs --tail 30 {DOCKER_CONTAINER_NAME}"
            
            # 使用異步執行，避免這動作卡住吹雪原本的翻譯工作
            process = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            # Docker 的日誌有時候會跑到 stderr，所以兩個都抓出來看。
            #
            # 注意：容器輸出的編碼不一定是 UTF-8（可能混到 Big5/CP950 或其他位元組）。
            # 如果直接用 utf-8 decode 會遇到：
            #   'utf-8' codec can't decode byte ... invalid start byte
            #
            # 這裡的策略是：
            # - 優先用 utf-8 解碼
            # - 失敗就用「取代不可解碼字元」的方式保留文字內容，避免監控任務整個中斷
            def _safe_decode(b: bytes) -> str:
                if not b:
                    return ""
                try:
                    return b.decode("utf-8")
                except UnicodeDecodeError:
                    # errors="replace" 會把無法解碼的位元組變成 �，保證不會拋例外
                    return b.decode("utf-8", errors="replace")

            logs = _safe_decode(stdout) + _safe_decode(stderr)

            # 檢查日誌裡有沒有出現 Token 失效的關鍵字
            if "401" in logs or "Unauthorized" in logs:
                mention_prefix = (
                    f"<@{ALERT_MENTION_USER_ID}> " if ALERT_MENTION_USER_ID else ""
                )
                allowed_mentions = (
                    discord.AllowedMentions(users=[discord.Object(id=ALERT_MENTION_USER_ID)])
                    if ALERT_MENTION_USER_ID
                    else ALLOWED_MENTIONS_NONE
                )
                await channel.send(
                    mention_prefix
                    + strip_discord_mentions(
                        "🚨 **警告！** 染岡同學使用的 Twitter auth_token 好像失效了～"
                    ),
                    allowed_mentions=allowed_mentions,
                )
                print("已發送 auth_token 過期警告！")
                # 為了避免吹雪每 10 分鐘就一直狂發訊息洗版，發送一次後讓他暫停監視 12 小時 (43200秒)
                await asyncio.sleep(43200)
                continue
                
        except Exception as e:
            print(f"Monitor 監控 Docker 出錯: {e}")

        # 如果沒事，吹雪就去休息，10 分鐘 (600秒) 後再來偷看一次
        await asyncio.sleep(600)

# ========================================================

@client.event
async def on_ready():
    print(f'已登入為 {client.user}，開始檢查染岡同學的翻譯狀況...')

    # ------------------------------------------
    # 你原本有寫 monitor_someoka_logs()，但沒有啟動它，所以警報永遠不會發生。
    # 這裡我們在 bot ready 後，把監控任務丟到背景執行。
    #
    # 這樣做的好處：
    # - 監控與翻譯可以同時跑，不會互相卡住
    # - 就算 docker logs 失敗，也只會在背景印錯誤，不會影響 on_message 的翻譯流程
    # ------------------------------------------
    asyncio.create_task(monitor_someoka_logs())

@client.event
async def on_message(message):
    # ------------------------------------------
    # 重要：on_message 是 Discord 事件回呼。
    # 如果你在裡面做「等待很久」的工作（例如輪詢 embed 10 秒），
    # 在推文很多時會同時堆很多個 handler，造成延遲、甚至看起來像卡住。
    #
    # 所以我們把每一則要處理的訊息丟到背景 task，讓事件回呼快速返回。
    # ------------------------------------------
    asyncio.create_task(process_message(message))

async def process_message(message):
    # 1. 只處理染岡發出的訊息
    if message.author.id == TARGET_BOT_ID:
        
        # 2. 判斷是否為 fxtwitter 連結且需要翻譯
        if "fxtwitter.com" in message.content and "/zh-TW" in message.content and "?" not in message.content:
            
            # 把原本的推文內容(通常就是網址)存起來，方便印在 Log 裡
            log_url = message.content 
            print(f"\n🔍 [開始檢查] 收到新推文: {log_url}")
            
            check_text = ""
            embed_full_text = "" 
            
            # 6. 【等待邏輯：輪詢檢查】等待原本的預覽跑完
            # 最多等待 10 秒，每 2 秒檢查一次
            for i in range(5): 
                await asyncio.sleep(2) 
                try:
                    updated_msg = await message.channel.fetch_message(message.id)
                    if updated_msg.embeds:
                        check_text = updated_msg.embeds[0].description or ""
                        embed_full_text = str(updated_msg.embeds[0].to_dict())
                        # 不會一有字就急著 break，等到「翻譯自」標籤出來，代表 Fxtwitter 真的跑完了才中斷等待
                        
                        if "翻譯自" in embed_full_text:
                            break 
                except Exception as e:
                    print(f"⚠️ 檢查卡片時出錯: {e} | 網址: {log_url}")
            
            # 如果等了 10 秒連內文都沒有，直接放生
            if not check_text:
                print(f"❌ [放棄] 等待超時，抓不到卡片內容 | 網址: {log_url}")
                return 

            # 裝上過濾器！如果判定不需要翻譯(空字串、全符號)，直接結束
            if not check_needs_translation(check_text):
                print(f"⏭️ [省略] 內容為空或無意義符號 | 網址: {log_url}")
                return
            
            # ================== 【乾淨俐落的兩段式過濾】 ==================
            
            # 優先級 1：檢查卡片裡是否有「翻譯自」(代表 Fxtwitter 有嘗試翻譯)
            if "翻譯自" in embed_full_text:
                if "原文" in check_text:
                    parts = check_text.split("原文")
                    translated_part = parts[0].strip()
                    original_part = parts[1].strip() if len(parts) > 1 else ""
                    
                    if not translated_part:
                        print(f"🔄 [重整] 翻譯結果為空白 | 網址: {log_url}")
                    elif translated_part == original_part:
                        print(f"🔄 [重整] 翻譯結果與原文相同 (無效翻譯) | 網址: {log_url}")
                    elif is_japanese(translated_part):
                        print(f"🔄 [重整] 翻譯結果仍包含日文假名 | 網址: {log_url}")
                    elif not has_chinese(translated_part):
                        print(f"🔄 [重整] 翻譯結果完全不含中文 (翻譯失敗) | 網址: {log_url}")
                    else:
                        print(f"\n✅ [通過] 偵測到有效翻譯，不需處理 | 網址: {log_url}")
                        return
                else:
                    print(f"\n✅ [通過] 有「翻譯自」且無原文對照，確認為已翻譯 | 網址: {log_url}")
                    return
            
            # 優先級 2：沒有「翻譯自」標記(代表 Fxtwitter 全無反應)
            else:
                # 只要整段文字「沒有中文」(代表是外文)，或是「含有日文假名」，一律觸發重整
                if not has_chinese(check_text) or is_japanese(check_text):
                    print(f"🔄 [重整] 發現未翻譯的外文推文 (無中文或含日文) | 網址: {log_url}")
                else:
                    print(f"\n✅ [通過] 推文為純中文，不需翻譯 | 網址: {log_url}")
                    return
            
            # ==============================================================
            # 走到這裡代表需要重整

            # 4. 產生一個隨機數作為亂碼
            random_num = random.randint(100, 9999)
            
            # 5. 在網址最後面加上 ?隨機數
            refreshed_url = message.content.replace("/zh-TW", f"/zh-TW?{random_num}")
            
            # 7. 送出翻譯訊息
            print(f"📤 [發送] 已送出重整網址: {refreshed_url}")
            await message.channel.send(
                strip_discord_mentions(
                    f"**真是的～染岡同學想說的是這個吧** ❄️\n{refreshed_url}"
                ),
                allowed_mentions=ALLOWED_MENTIONS_NONE,
            )

# 啟動機器人
client.run(BOT_TOKEN)