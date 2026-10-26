# نصب روی Google Cloud با اشتراک Claude

## ۱. ساخت بات تلگرام
در تلگرام به @BotFather پیام بده: `/newbot`، اسم و username بده، و توکن را نگه دار. اگر می‌خواهی بات داخل گروه باشد: `/setprivacy` → بات → Disable.

## ۲. ساخت سرور (در Cloud Shell گوگل، پروژه‌ای که حساب پرداخت دارد)
```
gcloud compute instances create hamdel-bot --project=<PROJECT> --zone=us-central1-a --machine-type=e2-micro --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud --boot-disk-size=30GB --boot-disk-type=pd-standard
```
e2-micro در us-central1 در سهمیهٔ رایگان گوگل است (آی‌پی خارجی حدود ۳٫۶۵ دلار در ماه هزینه دارد).

## ۳. نصب (یک دستور از Cloud Shell)
`TG_TOKEN` توکن تلگرام، `GEMINI_KEY` کلید aistudio.google.com/apikey و `GH` توکن GitHub است:
```
gcloud compute ssh hamdel-bot --zone=us-central1-a --project=<PROJECT> --command="export GH='...' TG_TOKEN='...' GEMINI_KEY='...'; curl -fsSL -H \"Authorization: Bearer \$GH\" -H 'Accept: application/vnd.github.raw' 'https://api.github.com/repos/zganjei/hamdel-bale-agent/contents/deploy/bootstrap.sh?ref=hamdel-claude-bot' | sudo -E bash"
```

## ۴. ورود Claude با اشتراک
داخل سرور (`gcloud compute ssh ...`):
```
sudo -iu hamdel claude setup-token
sudo bash /opt/hamdel/app/deploy/set-env.sh CLAUDE_CODE_OAUTH_TOKEN
```

## ۵. شناسه‌ها و اجرا
```
sudo systemctl enable --now hamdel
```
در تلگرام برای بات `/whoami` بفرست، `user_id` را بگیر و:
```
sudo bash /opt/hamdel/app/deploy/set-env.sh OWNER_ID
curl -s localhost:8080/health
```
اگر بات در گروه است: داخل گروه `/whoami` بفرست و `chat_id` را با `set-env.sh GROUP_CHAT_ID` بگذار.

## ۶. آزمایش و سنجش ویس
یک ویس محاوره‌ای برای بات بفرست و ۲ دقیقه صبر کن. برای درصد خطای کلمه: `tools/bench_asr.py` (ویس و متن درست).
در پایان توکن GitHub را حذف و توکن‌های دیده‌شده در چت را عوض کن.
