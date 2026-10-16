"""Future publisher boundary: no Telegram token, no Telegram client, no auto-send.

When Telegram is added, implement an explicit transport adapter that reads *approved*
content from the trusted Store, only after user intent selects a destination.
Never rely on Bale file IDs: download media and upload to Telegram separately.
"""

class TelegramPublisher:
    def publish(self, approved_draft, destination):
        raise NotImplementedError("ارسال به تلگرام در این نسخه عمداً غیرفعال است.")
