"""Offline transcription to avoid per-minute API bills. Persian is explicit."""
from pathlib import Path

class PersianASR:
    def __init__(self, model: str = "medium", device: str = "cpu", compute_type: str = "int8"):
        self.model = model
        self.device = device
        self.compute_type = compute_type
        self._recognizer = None

    def transcribe(self, path: Path) -> str:
        if self._recognizer is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise RuntimeError("برای پشتیبانی ویس، faster-whisper را نصب کن.") from exc
            self._recognizer = WhisperModel(self.model, device=self.device, compute_type=self.compute_type)
        segments, _ = self._recognizer.transcribe(
            str(path), language="fa", beam_size=5, vad_filter=True,
            condition_on_previous_text=False,
            initial_prompt="همدل، خیریه، درخواست کمک، صدقات، اطعام، فاطمیه، کوهدشت، لرستان، هرمزگان، مشهد، کرون سوئد، تومان، ریال، شماره سوییش، کد پرداخت."
        )
        return " ".join(seg.text.strip() for seg in segments).strip()
