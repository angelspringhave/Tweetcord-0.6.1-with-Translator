import discord
import sys
sys.stdout.reconfigure(encoding='utf-8')
import asyncio
import random
import re
import os
import logging
from logging.handlers import RotatingFileHandler
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

# ------------------------------------------
# 進階設定：全部都可以在 .env 覆寫，不填就用預設值，一般不需要更動。
# 這些數字如果在 .env 裡填錯格式（不是數字），會自動退回預設值，不會讓 bot 整支起不來。
# ------------------------------------------
def _get_int_env(name, default):
    raw = (os.getenv(name, "") or "").strip()
    return int(raw) if raw.isdigit() else default

def _get_float_env(name, default):
    raw = (os.getenv(name, "") or "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default

# 等待卡片：最多檢查幾次、每次間隔幾秒（預設 5 次 * 2 秒 = 最多等 10 秒，跟原本行為一樣）
POLL_MAX_ATTEMPTS = _get_int_env("POLL_MAX_ATTEMPTS", 5)
POLL_INTERVAL_SECONDS = _get_float_env("POLL_INTERVAL_SECONDS", 2.0)

# 重整網址最多重試幾次（試完還是失敗就放棄，並發警告到 ALERT_CHANNEL_ID）。
# 預設只重試 1 次：因為 Fxtwitter 翻譯品質本身就不受我們控制，重試太多次
# 也只是一直拿到同樣爛的翻譯，只會在頻道洗版，所以試一次就好，不行就放棄。
MAX_RETRIES = _get_int_env("MAX_RETRIES", 1)

# 每次重試前，依序要多等幾秒才送出重整網址（次數超過清單長度就一律用最後一個數字）
# 例如翻譯剛好卡住/被暫時限制流量時，越等越久比「馬上重試」更容易成功。
_retry_backoff_raw = (os.getenv("RETRY_BACKOFF_SECONDS", "") or "").strip()
try:
    RETRY_BACKOFF_SECONDS = [float(x.strip()) for x in _retry_backoff_raw.split(",") if x.strip()]
    if not RETRY_BACKOFF_SECONDS:
        raise ValueError
except ValueError:
    RETRY_BACKOFF_SECONDS = [5.0, 15.0, 45.0]

# 紀錄檔設定：檔名、單一檔案上限（bytes）、最多保留幾份舊檔。
# 有上限+自動輪替，硬碟不會被無限塞爆（預設頂多約 5MB * 3 = 15MB 左右）。
LOG_FILE_PATH = os.getenv("LOG_FILE_PATH", "translator.log")
LOG_MAX_BYTES = _get_int_env("LOG_MAX_BYTES", 5 * 1024 * 1024)
LOG_BACKUP_COUNT = _get_int_env("LOG_BACKUP_COUNT", 3)

# 建立 client 物件 (必須放在 event 之前)
intents = discord.Intents.default()
intents.message_content = True  # 必須開啟才能讀取網址內容
client = discord.Client(intents=intents)
ALLOWED_MENTIONS_NONE = discord.AllowedMentions.none()

# ==========================================
# 紀錄（Log）設定：畫面照樣看得到（跟原本 print 一樣），同時額外存一份到檔案，
# 方便之後回頭查「翻譯到底失敗幾次」，檔案還會自動輪替、不會佔滿硬碟。
# ==========================================
logger = logging.getLogger("translator")
logger.setLevel(logging.INFO)
logger.propagate = False

_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setFormatter(logging.Formatter("%(message)s"))
logger.addHandler(_console_handler)

try:
    _file_handler = RotatingFileHandler(
        LOG_FILE_PATH, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    _file_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(_file_handler)
except Exception as e:
    logger.warning(f"⚠️ 無法建立紀錄檔 {LOG_FILE_PATH}，將只輸出到畫面: {e}")

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
        logger.info("ALERT_CHANNEL_ID 未設定或不是數字，因此不會發警告訊息。")
        return None

    channel = client.get_channel(ALERT_CHANNEL_ID)
    if channel:
        return channel

    try:
        return await client.fetch_channel(ALERT_CHANNEL_ID)
    except Exception as e:
        logger.error(f"找不到警告回報頻道，請確認 ALERT_CHANNEL_ID 是否正確，且 bot 有權限看到該頻道！err={e}")
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
    # 偵測是否含有平假名 (぀-ゟ) 或 片假名 (゠-ヿ)
    # 這是區分日文與中文最準確的方法
    return re.search(r'[぀-ヿ]', text) is not None

def has_chinese(text):
    # 偵測是否包含任何中文字符 (CJK 統一表意文字)
    return re.search(r'[一-龥]', text) is not None

def collect_embed_text(embed_dict):
    """把 Discord embed 裡所有文字欄位攤平成一段可搜尋文字。"""
    texts = []

    def collect(value):
        if isinstance(value, str):
            texts.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(embed_dict)
    return "\n".join(texts)

def clean_translation_text(text):
    if not text:
        return ""

    # 移除 Discord 引言符號與 Fxtwitter 的「翻譯自...」標籤，只留下真正內容。
    #
    # 「翻譯自」前面可能會帶不同的圖示（📄、📝...等，Fxtwitter 自己會換），
    # 用 [^\w\r\n]* 取代寫死某一個 emoji，才不會因為圖示換了又漏判。
    text = re.sub(r'(?m)^\s*>\s?', '', text)
    text = re.sub(r'(?m)^[^\w\r\n]*翻譯自[^\r\n]*', '', text)
    return strip_discord_mentions(text).strip()

def extract_translation_parts(description_text, embed_text):
    """
    從 embed 文字中拆出「翻譯結果」和「原文」。

    空白翻譯最容易漏判的情況是：description 裡有「翻譯自」，
    但「原文」被 Discord/Fxtwitter 放在其他 embed 欄位。這裡會同時檢查
    description 與攤平後的整個 embed。
    """
    description_text = (description_text or "").replace("\\n", "\n")

    # 新版 Fxtwitter 會把「翻譯自…」與翻譯結果放在 description，
    # 再把「原文」放到另一個 Embed 欄位。不能把整張 Embed 攤平後再當成
    # 翻譯結果，否則標題、作者或原文會讓真正的空白翻譯看起來像有內容。
    if "翻譯自" in description_text:
        # 「原文」這行前面也可能被 Fxtwitter 加上圖示（例如 📝），一樣用
        # [^\w\r\n]* 取代寫死只認 ">"，避免抓不到分界點、把原文誤判成翻譯結果。
        original_marker = re.search(r'(?m)^[^\w\r\n]*原文[^\w\r\n]*$', description_text)
        if original_marker:
            translated_raw = description_text[:original_marker.start()]
            original_raw = description_text[original_marker.end():]
        else:
            translated_raw = description_text
            original_raw = ""

        translated_part = clean_translation_text(translated_raw)
        original_part = clean_translation_text(original_raw)

        # 原文可能被放在其他欄位；description 仍是唯一的翻譯結果來源。
        # 因此即使 original_part 為空，也要直接回傳，避免整張 Embed 的
        # 標題或原文文字掩蓋真正的空白翻譯。
        return translated_part, original_part

    candidates = []
    if embed_text and "翻譯自" in embed_text:
        candidates.append(embed_text.replace("\\n", "\n"))

    for text in candidates:
        if "原文" not in text:
            continue

        original_marker = re.search(r'(?m)^[^\w\r\n]*原文[^\w\r\n]*$', text)
        if original_marker:
            translated_raw = text[:original_marker.start()]
            original_raw = text[original_marker.end():]
        else:
            translated_raw, original_raw = text.split("原文", 1)

        translated_part = clean_translation_text(translated_raw)
        original_part = clean_translation_text(original_raw)
        return translated_part, original_part

    return None, None

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
                logger.info("已發送 auth_token 過期警告！")
                # 為了避免吹雪每 10 分鐘就一直狂發訊息洗版，發送一次後讓他暫停監視 12 小時 (43200秒)
                await asyncio.sleep(43200)
                continue

        except Exception as e:
            logger.error(f"Monitor 監控 Docker 出錯: {e}")

        # 如果沒事，吹雪就去休息，10 分鐘 (600秒) 後再來偷看一次
        await asyncio.sleep(600)

# ========================================================

@client.event
async def on_ready():
    logger.info(f'已登入為 {client.user}，開始檢查染岡同學的翻譯狀況...')

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

async def wait_for_embed(channel, message_id, log_url):
    """
    輪詢等待某則訊息的 embed 出現。

    最多檢查 POLL_MAX_ATTEMPTS 次、每次間隔 POLL_INTERVAL_SECONDS 秒
    （預設 5 次 * 2 秒 = 最多等 10 秒）。一看到「翻譯自」標籤出現，
    代表 Fxtwitter 真的跑完了，就提早結束等待。

    回傳 (check_text, embed_full_text)；如果整段時間都抓不到任何 embed 內容，
    兩個都會是空字串。
    """
    check_text = ""
    embed_full_text = ""

    for _ in range(POLL_MAX_ATTEMPTS):
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        try:
            updated_msg = await channel.fetch_message(message_id)
            if updated_msg.embeds:
                embed_dict = updated_msg.embeds[0].to_dict()
                check_text = updated_msg.embeds[0].description or ""
                embed_full_text = collect_embed_text(embed_dict)

                if "翻譯自" in embed_full_text:
                    break
        except Exception as e:
            logger.warning(f"⚠️ 檢查卡片時出錯: {e} | 網址: {log_url}")

    return check_text, embed_full_text

def evaluate_translation(check_text, embed_full_text):
    """
    判斷這次抓到的卡片內容算不算「翻譯成功」。

    回傳 (status, reason)：
      - "ok"     翻譯正常，不用理它
      - "skip"   本來就不需要翻譯（純符號/純中文/純圖片…）
      - "retry"  翻譯結果有問題，需要送出重整網址
      - "no_card" 完全抓不到卡片內容（可能還在跑、也可能真的失敗）
    """
    if not (check_text or embed_full_text):
        return "no_card", "等待超時，抓不到卡片內容"

    text_for_check = check_text or embed_full_text
    if not check_needs_translation(text_for_check):
        return "skip", "內容為空或無意義符號"

    # 優先級 1：卡片裡有「翻譯自」(代表 Fxtwitter 有嘗試翻譯)
    if "翻譯自" in embed_full_text:
        translated_part, original_part = extract_translation_parts(check_text, embed_full_text)

        if original_part is None:
            # 有翻譯標籤卻拆不到原文時，不能直接當作成功，避免空白翻譯被誤判。
            return "retry", "有「翻譯自」但無法拆出原文對照，疑似翻譯卡片格式異常"

        if not translated_part:
            return "retry", "翻譯結果為空白"
        if not check_needs_translation(translated_part):
            return "skip", "翻譯結果為純符號/Emoji，無需處理"
        if translated_part == original_part:
            return "retry", "翻譯結果與原文相同 (無效翻譯)"
        if is_japanese(translated_part):
            return "retry", "翻譯結果仍包含日文假名"
        if not has_chinese(translated_part):
            return "retry", "翻譯結果完全不含中文 (翻譯失敗)"
        return "ok", "偵測到有效翻譯"

    # 優先級 2：沒有「翻譯自」標記(代表 Fxtwitter 全無反應)
    if not has_chinese(text_for_check) or is_japanese(text_for_check):
        return "retry", "發現未翻譯的外文推文 (無中文或含日文)"

    return "skip", "推文為純中文，不需翻譯"

async def delete_message_quietly(channel, message_id, log_url):
    """
    刪除吹雪自己之前發的一則重整卡片，刪不掉（已經被刪過等）就算了，不影響後續流程。

    只會用來刪吹雪自己發的訊息，不會去刪染岡的原始貼文，所以不需要「管理訊息」這種
    額外權限——bot 本來就可以刪自己發過的訊息。
    """
    try:
        old_msg = await channel.fetch_message(message_id)
        await old_msg.delete()
    except discord.NotFound:
        pass
    except Exception as e:
        logger.warning(f"⚠️ 刪除舊卡片時出錯: {e} | 網址: {log_url}")

async def send_final_failure_alert(original_url, reason):
    """重試次數用完了還是失敗，發一則警告到警報頻道，讓你知道這則需要自己看一下。"""
    channel = await get_alert_channel()
    if not channel:
        return
    try:
        await channel.send(
            strip_discord_mentions(
                f"⚠️ **翻譯重試失敗** 這則推文已重試 {MAX_RETRIES} 次仍無法正常翻譯"
                f"（最後一次原因：{reason}），麻煩自己看一下：\n{original_url}"
            ),
            allowed_mentions=ALLOWED_MENTIONS_NONE,
        )
    except Exception as e:
        logger.error(f"發送最終失敗警告時出錯: {e}")

async def process_message(message):
    # 1. 只處理染岡發出的訊息
    if message.author.id != TARGET_BOT_ID:
        return

    # 2. 判斷是否為 fxtwitter 連結且需要翻譯
    if not ("fxtwitter.com" in message.content and "/zh-TW" in message.content and "?" not in message.content):
        return

    original_url = message.content
    channel = message.channel
    current_message_id = message.id
    current_url = original_url

    # 3. 最多嘗試 MAX_RETRIES + 1 次（第一次是原本的訊息，之後才算重試）
    for attempt in range(MAX_RETRIES + 1):
        log_url = current_url
        logger.info(f"\n🔍 [開始檢查] (第 {attempt + 1} 次) 網址: {log_url}")

        check_text, embed_full_text = await wait_for_embed(channel, current_message_id, log_url)
        status, reason = evaluate_translation(check_text, embed_full_text)

        if status == "ok":
            logger.info(f"✅ [通過] {reason} | 網址: {log_url}")
            return
        if status == "skip":
            logger.info(f"⏭️ [省略] {reason} | 網址: {log_url}")
            return

        # status 是 "retry" 或 "no_card"，都需要送出重整網址再試一次
        logger.info(f"🔄 [需要重整] {reason} | 網址: {log_url}")

        if attempt >= MAX_RETRIES:
            logger.warning(f"🛑 [放棄] 已重試 {MAX_RETRIES} 次仍失敗（{reason}）| 原始網址: {original_url}")
            await send_final_failure_alert(original_url, reason)
            return

        # 越到後面等越久，避免馬上重試又剛好撞到同一個暫時性問題（例如 Fxtwitter 忙線中）
        backoff = RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)]
        logger.info(f"⏳ 等待 {backoff} 秒後重試...")
        await asyncio.sleep(backoff)

        # 送出新卡片前，先刪掉「吹雪自己上一次發的」重整卡片，讓同一篇推文的重試
        # 訊息互相取代、不會越疊越多。注意：染岡原本發的那則（第一次，attempt == 0）
        # 絕對不會被刪，只有吹雪自己發的重整訊息之間才會互相取代。
        if attempt > 0:
            await delete_message_quietly(channel, current_message_id, log_url)

        random_num = random.randint(100, 9999)
        refreshed_url = original_url.replace("/zh-TW", f"/zh-TW?{random_num}")

        logger.info(f"📤 [發送] 已送出重整網址: {refreshed_url}")
        sent_msg = await channel.send(
            strip_discord_mentions(
                f"**真是的～染岡同學想說的是這個吧** ❄️\n{refreshed_url}"
            ),
            allowed_mentions=ALLOWED_MENTIONS_NONE,
        )
        current_message_id = sent_msg.id
        current_url = refreshed_url

# 啟動機器人
client.run(BOT_TOKEN)
