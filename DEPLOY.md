# نصب روی Oracle Cloud (رایگان) با اشتراک Claude

## ۱. ساخت سرور
1. در cloud.oracle.com حساب Free Tier بساز (کارت بانکی برای تأیید هویت لازم است). Home region را اروپا بگذار (مثلاً Stockholm یا Frankfurt)؛ بعداً قابل تغییر نیست.
2. Compute → Instances → Create instance. Image: Ubuntu 24.04. Shape: یکی از شیپ‌های «Always Free-eligible» (مثلاً VM.Standard.E2.1.Micro، یا Ampere A1 اگر ظرفیت داشت).
3. در بخش SSH keys «Generate a key pair» بزن و **private key را دانلود کن** (فقط همان لحظه ممکن است). Create.
4. بعد از ساخت، Public IP را کپی کن.
5. حساب را **Pay As You Go** نکن؛ روی Free بماند تا هزینه‌ای نیاید.

## ۲. اتصال
از موبایل: اپ Termius (iOS/Android) را نصب کن، Host=Public IP، User=ubuntu، و private key را import کن.

## ۳. نصب
```
sudo apt-get install -y unzip
# فایل zip را با Termius (SFTP) به سرور بفرست، سپس:
unzip hamdel-claude-bot.zip && cd hamdel-claude-bot
sudo bash deploy/setup.sh
```

## ۴. ورود Claude Code با اشتراک
```
sudo -iu hamdel
claude setup-token
```
توکن چاپ‌شده را کپی کن و در `/opt/hamdel/env` جلوی `CLAUDE_CODE_OAUTH_TOKEN=` بگذار. (اگر این دستور در نسخهٔ تو نبود، `claude` را اجرا کن و با `/login` وارد شو.) توکن را به کسی نده.

## ۵. تنظیمات و اجرا
```
sudo nano /opt/hamdel/env      # توکن بات بله، کلیدهای Cloudflare، OWNER_ID، GROUP_CHAT_ID
sudo systemctl enable --now hamdel
journalctl -u hamdel -f        # لاگ (محتوای پیام‌ها ثبت نمی‌شود)
curl localhost:8080/health
```
`OWNER_ID` و `GROUP_CHAT_ID` را قبل از آن با دستور `/whoami` در خصوصی و داخل گروه بگیر (بات باید یک‌بار اجرا شده باشد؛ می‌توانی env را اول خالی از این دو بگذاری).

## ۶. آزمایش
یک ویس کوتاه محاوره‌ای در گروه بفرست، ۲ دقیقه صبر کن، گزارش باید در خصوصی بیاید.
قبلش از سرور بررسی کن: `curl -sI https://tapi.bale.ai | head -1` (بله از آن منطقه باز می‌شود؟).

## ۷. سنجش کیفیت تبدیل ویس (پیشنهاد می‌شود)
۳ تا ۵ ویس واقعی را روی سرور بگذار، متن درستِ هرکدام را در فایل .txt بنویس، و اجرا کن:
```
cd /opt/hamdel/app && set -a && . /opt/hamdel/env && set +a
python3 tools/bench_asr.py v1.ogg v1.txt v2.ogg v2.txt
```
درصد خطای کلمه برای هر سرویس چاپ می‌شود؛ کمتر بهتر است.
