# نصب روی Oracle Cloud (رایگان) با اشتراک Claude

## ۱. ساخت سرور
1. در cloud.oracle.com حساب Free Tier بساز (کارت بانکی برای تأیید هویت لازم است). Home region را اروپا بگذار (مثلاً Stockholm یا Frankfurt)؛ بعداً قابل تغییر نیست.
2. Compute → Instances → Create instance. Image: Ubuntu 24.04. Shape: یکی از شیپ‌های «Always Free-eligible» (مثلاً VM.Standard.E2.1.Micro، یا Ampere A1 اگر ظرفیت داشت).
3. در بخش SSH keys «Generate a key pair» بزن و **private key را دانلود کن** (فقط همان لحظه ممکن است). Create.
4. بعد از ساخت، Public IP را کپی کن.
5. حساب را **Pay As You Go** نکن؛ روی Free بماند تا هزینه‌ای نیاید.

## ۲. اتصال
از موبایل: اپ Termius را نصب کن، Host=Public IP، User=ubuntu، و private key را import کن.

## ۳. نصب (یک paste)
توکن GitHub را به‌جای `...` بگذار و این دو خط را paste کن (ابتدا `export`، بعد `curl`). اسکریپت توکن بات بله را می‌پرسد و اول از سرور `getMe` را تست می‌کند؛ اگر بله جواب نداد، متوقف می‌شود.
```
export GH='...'
curl -fsSL -H "Authorization: Bearer $GH" -H "Accept: application/vnd.github.raw" "https://api.github.com/repos/zganjei/hamdel-bale-agent/contents/deploy/bootstrap.sh?ref=hamdel-claude-bot" | sudo -E bash
```

## ۴. ورود Claude Code با اشتراک
```
sudo -iu hamdel claude setup-token
sudo bash /opt/hamdel/app/deploy/set-env.sh CLAUDE_CODE_OAUTH_TOKEN
```
(اگر `setup-token` نبود: `sudo -iu hamdel claude` و `/login`.)

## ۵. شناسه‌ها و اجرا
در خصوصی بات و در گروه `/whoami` بفرست (بات باید اجرا باشد: `sudo systemctl enable --now hamdel`). سپس:
```
sudo bash /opt/hamdel/app/deploy/set-env.sh OWNER_ID
sudo bash /opt/hamdel/app/deploy/set-env.sh GROUP_CHAT_ID
curl -s localhost:8080/health
```
در پایان توکن GitHub را در تنظیمات حساب Delete کن.

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
