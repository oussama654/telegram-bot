#!/usr/bin/env python3
import os
import asyncio
import random
import re
import time
import logging
import json
from datetime import datetime, timedelta
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest
from telethon.tl.functions.account import UpdateStatusRequest
from telethon.errors import FloodWaitError, AuthKeyDuplicatedError

# ========== إعدادات التسجيل ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('single_account.log'), logging.StreamHandler()]
)
logger = logging.getLogger("SingleAccount")

# ========== قراءة متغيرات البيئة ==========
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STR = os.environ["SESSION"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
NOTIFY_USER = os.environ.get("NOTIFY_USER", "me")

# تأخير عشوائي (بالثواني) بين العمليات
DELAY_MIN = int(os.environ.get("DELAY_MIN", 45))
DELAY_MAX = int(os.environ.get("DELAY_MAX", 180))

# ========== الكلمات المفتاحية ==========
# كلمات الهمسات التي يجب تجاهلها تماماً
WHISPER_KEYWORDS = ["همسة", "الهمسات", "همسة...", "صارخني", "ililbot", "همس", "سرية", "بوت صارخني", "همسة سرية"]

# كلمات تدل على أزرار المشاركة
PARTICIPATE_BUTTONS = ["مشاركة", "انضمام", "سحب", "تدوير", "دخول", "تأكيد", "اضغط هنا", "المشاركة", "اشترك", "join", "participate", "spin"]

# مسابقات خطيرة نتجنبها
DANGER_WORDS = ["أكثر نجوم", "من يضع", "تصويت بنجوم", "اكثر شخص يحط", "يحط يربح", "مزاد نجوم"]

# أنماط حل الكابتشا
MATH_PATTERNS = [
    r'ناتج\s*:\s*(\d+)\s*\+\s*(\d+)',
    r'(\d+)\s*\+\s*(\d+)\s*\?',
    r'كم\s*ناتج\s*(\d+)\s*\+\s*(\d+)',
    r'(\d+)\s*\+\s*(\d+)',
]
EMOJI_PATTERN = r'يشبه هذا الإيموجي\s*([\U00010000-\U0010FFFF])|اضغط على الزر الذي يحتوي على\s*([\U00010000-\U0010FFFF])'

# أنماط التعليق المطلوب
COMMENT_PATTERNS = [
    r'هل يستحق\s*(.*?)\?',
    r'اكتب\s*"([^"]+)"',
    r'علق\s*بـ\s*([^\s]+)',
]

# أنماط استخراج القنوات الإجبارية
CHANNEL_PATTERNS = [
    r'(?:@|t\.me/)([a-zA-Z0-9_]{5,})',
    r'الاشتراك في\s*([@a-zA-Z0-9_]+)',
    r'قنوات التالية:\s*(?:[0-9]+\.\s*)?(@[a-zA-Z0-9_]+)',
    r'(?:انضم|اشترك)\s*إلى\s*([@a-zA-Z0-9_]+)'
]

# ========== ملف حظر القنوات ==========
BLOCKLIST_FILE = "blocked_channels.json"
def load_blocklist():
    try:
        with open(BLOCKLIST_FILE, 'r') as f:
            return set(json.load(f))
    except:
        return set()
def save_blocklist(blocked):
    with open(BLOCKLIST_FILE, 'w') as f:
        json.dump(list(blocked), f)

# ========== الكلاس الرئيسي ==========
class SingleRouletteBot:
    def __init__(self):
        self.client = None
        self.running = True
        self.cache = set()
        self.stats = {
            "wins": 0,
            "joined_channels": 0,
            "captcha_solved": 0,
            "left": 0,
            "start": time.time()
        }

    # ------------------- الاتصال -------------------
    async def connect(self):
        self.client = TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH)
        for _ in range(3):
            try:
                await self.client.connect()
                if await self.client.is_user_authorized():
                    logger.info("✅ الحساب متصل بنجاح")
                    return True
            except AuthKeyDuplicatedError:
                logger.critical("🔑 جلسة مكررة! غيّر SESSION.")
                break
            except Exception as e:
                logger.error(f"فشل الاتصال: {e}")
            await asyncio.sleep(10)
        return False

    async def keep_alive(self):
        while True:
            try:
                if not self.client.is_connected():
                    await self.client.connect()
                await self.client(UpdateStatusRequest(offline=False))
            except:
                pass
            await asyncio.sleep(120)

    # ------------------- التأخير العشوائي -------------------
    async def random_delay(self):
        delay = random.uniform(DELAY_MIN, DELAY_MAX)
        logger.info(f"⏳ انتظار {delay:.1f} ثانية (تجنب الاكتشاف)")
        await asyncio.sleep(delay)

    # ------------------- تجاهل الهمسات -------------------
    async def is_whisper(self, event):
        text = event.raw_text or ""
        for kw in WHISPER_KEYWORDS:
            if kw in text:
                return True
        # إذا كان المرسل بوتاً
        if event.sender_id:
            try:
                sender = await event.get_sender()
                if sender and sender.bot:
                    return True
            except:
                pass
        return False

    # ------------------- حل الكابتشا -------------------
    async def solve_math(self, text, buttons):
        for pat in MATH_PATTERNS:
            m = re.search(pat, text)
            if m:
                a = int(m.group(1))
                b = int(m.group(2))
                result = a + b
                logger.info(f"🧮 مسألة: {a} + {b} = {result}")
                for i, row in enumerate(buttons):
                    for j, btn in enumerate(row):
                        if btn.text.strip() == str(result):
                            return i, j
        return None

    async def solve_emoji(self, text, buttons):
        m = re.search(EMOJI_PATTERN, text)
        if m:
            em = m.group(1) or m.group(2)
            if em:
                logger.info(f"😀 إيموجي مطلوب: {em}")
                for i, row in enumerate(buttons):
                    for j, btn in enumerate(row):
                        if em in btn.text:
                            return i, j
        return None

    # ------------------- التعليق المطلوب -------------------
    async def extract_comment(self, text):
        for pat in COMMENT_PATTERNS:
            m = re.search(pat, text, re.I)
            if m:
                return m.group(1).strip('"\'')
        if re.search(r'هل يستحق', text):
            return "يستحق"
        return None

    async def reply_comment(self, event, comment):
        try:
            # إذا كانت الرسالة مُعادة توجيه (محولة) نرد في القناة الأصلية
            if event.message.fwd_from and event.message.fwd_from.from_id:
                original_chat_id = event.message.fwd_from.from_id.channel_id
                if original_chat_id:
                    original_chat = await self.client.get_entity(original_chat_id)
                    await self.client.send_message(original_chat, comment, reply_to=event.message.id)
                    logger.info(f"📝 رد على منشور محول: {comment}")
                    self.stats['captcha_solved'] += 1
                    return True
            await event.reply(comment)
            logger.info(f"📝 تعليق: {comment}")
            self.stats['captcha_solved'] += 1
            return True
        except Exception as e:
            logger.error(f"فشل التعليق: {e}")
            return False

    # ------------------- أزرار المشاركة -------------------
    async def click_participate(self, event):
        if not event.reply_markup:
            return False
        # محاولة الضغط على أول زر (غالباً هو زر المشاركة)
        try:
            first_btn = event.reply_markup.rows[0].buttons[0]
            if first_btn.text not in ["إلغاء", "Cancel", "لا", "غلق"]:
                await event.click(0, 0)
                logger.info(f"✅ ضغط على أول زر: {first_btn.text}")
                self.stats['wins'] += 1
                return True
        except:
            pass
        # البحث عن زر يحتوي على كلمات المشاركة
        for i, row in enumerate(event.reply_markup.rows):
            for j, btn in enumerate(row.buttons):
                if any(k in btn.text for k in PARTICIPATE_BUTTONS):
                    await event.click(i, j)
                    logger.info(f"✅ ضغط على زر: {btn.text}")
                    self.stats['wins'] += 1
                    return True
        return False

    # ------------------- الانضمام للقنوات الإجبارية -------------------
    async def join_required_channels(self, text):
        if not self.running:
            return
        channels = set()
        for pat in CHANNEL_PATTERNS:
            matches = re.findall(pat, text)
            for m in matches:
                username = m.strip('@')
                if len(username) > 3 and username.lower() not in ['bot', 'c', 'me', 'telegram']:
                    channels.add(username)
        # تحميل قائمة المحظورين
        blocked = load_blocklist()
        for username in channels:
            if username in blocked:
                logger.info(f"🚫 قناة {username} محظورة، تخطي")
                continue
            try:
                entity = await self.client.get_entity(username)
                await self.random_delay()
                await self.client(JoinChannelRequest(entity))
                logger.info(f"✅ انضم إلى القناة: {username}")
                self.stats['joined_channels'] += 1
            except FloodWaitError as e:
                logger.warning(f"FloodWait {e.seconds}s أثناء الانضمام")
                await asyncio.sleep(e.seconds)
            except Exception as e:
                logger.error(f"فشل الانضمام لـ {username}: {e}")

    # ------------------- معالجة الرسالة الأساسية -------------------
    async def process_message(self, event):
        if not self.running:
            return
        if event.id in self.cache:
            return

        # 1. تجاهل الهمسات فوراً
        if await self.is_whisper(event):
            logger.info(f"🙊 تجاهل همسة: {event.raw_text[:50]}")
            return

        text = event.raw_text or ""

        # تجنب المسابقات الخطيرة
        if any(word in text for word in DANGER_WORDS):
            return

        # 2. الانضمام للقنوات المطلوبة
        await self.join_required_channels(text)

        # 3. حل الكابتشا (رياضية أو إيموجي)
        if event.reply_markup:
            buttons = [[b for b in row.buttons] for row in event.reply_markup.rows]
            pos = await self.solve_math(text, buttons)
            if not pos:
                pos = await self.solve_emoji(text, buttons)
            if pos:
                try:
                    await event.click(pos[0], pos[1])
                    logger.info("🧠 تم حل الكابتشا بنجاح")
                    self.stats['captcha_solved'] += 1
                    await self.random_delay()
                except Exception as e:
                    logger.error(f"فشل الضغط على حل الكابتشا: {e}")

        # 4. التعليق المطلوب
        comment = await self.extract_comment(text)
        if comment:
            await self.reply_comment(event, comment)
            await self.random_delay()

        # 5. الضغط على زر المشاركة
        if await self.click_participate(event):
            self.cache.add(event.id)
            return

        # 6. ضغط عام على أول زر (للحالات التي لم نضغط فيها شيئاً)
        if event.reply_markup and event.id not in self.cache:
            try:
                first_btn = event.reply_markup.rows[0].buttons[0]
                if first_btn.text not in ["إلغاء", "Cancel", "لا"]:
                    await event.click(0, 0)
                    logger.info(f"⚠️ ضغط عام على أول زر: {first_btn.text}")
                    self.stats['wins'] += 1
                    self.cache.add(event.id)
            except:
                pass

    # ------------------- إحصائيات حية -------------------
    async def live_stats(self):
        msg_id = None
        while True:
            uptime = str(timedelta(seconds=int(time.time() - self.stats['start'])))
            msg = (
                f"📊 **سكريبت الروليت - حساب واحد**\n"
                f"🕒 {datetime.now():%H:%M:%S}\n"
                f"⏱️ {uptime}\n"
                f"🏆 فوز: {self.stats['wins']}\n"
                f"📢 انضم: {self.stats['joined_channels']}\n"
                f"🧩 كابتشا محلولة: {self.stats['captcha_solved']}\n"
                f"🚪 مغادرة: {self.stats['left']}\n"
                f"⏲️ تأخير: {DELAY_MIN}-{DELAY_MAX} ثانية"
            )
            try:
                if msg_id:
                    await self.client.edit_message(NOTIFY_USER, msg_id, msg)
                else:
                    sent = await self.client.send_message(NOTIFY_USER, msg)
                    msg_id = sent.id
            except:
                pass
            await asyncio.sleep(15)

    # ------------------- أوامر الأدمن -------------------
    async def handle_command(self, event, parts):
        cmd = parts[0][1:].lower()
        # إيقاف التشغيل
        if cmd == "stop":
            self.running = False
            await event.reply("🛑 تم إيقاف السكريبت (يمكنك تشغيله مجدداً بـ .start)")
        # تشغيل السكريبت
        elif cmd == "start":
            self.running = True
            await event.reply("✅ تم تشغيل السكريبت")
        # إحصائيات
        elif cmd == "stats":
            await event.reply("📊 الإحصائيات تُرسل إلى الخاص")
        # عرض الإعدادات الحالية
        elif cmd == "config":
            await event.reply(f"⚙️ الإعدادات:\nتأخير: {DELAY_MIN}-{DELAY_MAX} ثانية\nحظر القنوات: مفعّل\nمراقبة VPS: معطّل")
        # حظر قناة
        elif cmd == "block":
            if len(parts) < 2:
                await event.reply("الاستخدام: .block @username")
                return
            ch = parts[1].strip('@')
            blocked = load_blocklist()
            blocked.add(ch)
            save_blocklist(blocked)
            await event.reply(f"🚫 تم حظر القناة @{ch} (لن يتم الانضمام إليها)")
        # إلغاء حظر قناة
        elif cmd == "unblock":
            if len(parts) < 2:
                await event.reply("الاستخدام: .unblock @username")
                return
            ch = parts[1].strip('@')
            blocked = load_blocklist()
            if ch in blocked:
                blocked.remove(ch)
                save_blocklist(blocked)
                await event.reply(f"✅ تم إلغاء حظر @{ch}")
            else:
                await event.reply(f"⚠️ @{ch} غير محظورة")
        # عرض القنوات المحظورة
        elif cmd == "blocklist":
            blocked = load_blocklist()
            if blocked:
                await event.reply("🚫 القنوات المحظورة:\n" + "\n".join(f"- @{b}" for b in blocked))
            else:
                await event.reply("لا توجد قنوات محظورة")
        # انضمام يدوي لقناة
        elif cmd == "join":
            if len(parts) < 2:
                await event.reply("الاستخدام: .join @username")
                return
            ch = parts[1].strip('@')
            try:
                entity = await self.client.get_entity(ch)
                await self.client(JoinChannelRequest(entity))
                await event.reply(f"✅ انضممت إلى @{ch}")
            except Exception as e:
                await event.reply(f"❌ فشل الانضمام: {e}")
        # تنظيف القنوات القديمة (مغادرة القنوات غير النشطة)
        elif cmd == "clean":
            left = 0
            async for dialog in self.client.iter_dialogs():
                if dialog.is_channel and dialog.date and (datetime.now().astimezone() - dialog.date).days > 7:
                    try:
                        await self.client(LeaveChannelRequest(dialog.entity))
                        left += 1
                        await asyncio.sleep(1)
                    except:
                        pass
            self.stats['left'] += left
            await event.reply(f"🧹 غادرت {left} قناة قديمة")
        else:
            await event.reply("أوامر متاحة:\n.start | .stop | .stats | .config\n.block @user | .unblock @user | .blocklist\n.join @channel\n.clean")

    # ------------------- التشغيل الرئيسي -------------------
    async def main(self):
        if not await self.connect():
            logger.critical("لا يمكن الاستمرار بدون اتصال")
            return

        # أوامر الأدمن
        @self.client.on(events.NewMessage(from_users=ADMIN_ID))
        async def admin_cmd(event):
            if event.raw_text.startswith("."):
                await self.handle_command(event, event.raw_text.split())

        # معالجة جميع رسائل القنوات والمجموعات
        @self.client.on(events.NewMessage)
        async def message_handler(event):
            if event.is_channel or event.is_group:
                await self.process_message(event)

        # تشغيل المهام الخلفية
        asyncio.create_task(self.keep_alive())
        asyncio.create_task(self.live_stats())

        # تنظيف دوري كل 12 ساعة
        async def periodic_clean():
            while True:
                await asyncio.sleep(43200)  # 12 ساعة
                left = 0
                async for dialog in self.client.iter_dialogs():
                    if dialog.is_channel and dialog.date and (datetime.now().astimezone() - dialog.date).days > 7:
                        try:
                            await self.client(LeaveChannelRequest(dialog.entity))
                            left += 1
                            await asyncio.sleep(1)
                        except:
                            pass
                self.stats['left'] += left
                logger.info(f"تنظيف دوري: غادر {left} قناة")
        asyncio.create_task(periodic_clean())

        await self.client(UpdateStatusRequest(offline=False))
        logger.info("🚀 السكريبت يعمل (حساب واحد، بدون VPS، بدون همسات)")
        await self.client.run_until_disconnected()

    async def run(self):
        logger.info("بدء تشغيل سكريبت الروليت (حساب واحد)")
        await self.main()

if __name__ == "__main__":
    bot = SingleRouletteBot()
    asyncio.run(bot.run())
