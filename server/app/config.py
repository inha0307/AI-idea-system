from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

SERVER_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=SERVER_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    DATABASE_URL: str
    DETECTOR: Literal["fake", "yolo"] = "fake"
    MODEL_PATH: str = "./models/best.pt"
    DEVICE: str = "cpu"
    CONF_THRESHOLD: float = 0.25
    STORAGE_DIR: str = "./storage"
    CORS_ORIGINS: str = "http://localhost:3000"
    SEVERITY_CAUTION_MM: float
    SEVERITY_DANGER_MM: float

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def storage_dir(self) -> Path:
        return _resolve(self.STORAGE_DIR)

    @property
    def model_path(self) -> Path:
        return _resolve(self.MODEL_PATH)


def _resolve(p: str) -> Path:
    """상대 경로는 server/ 기준으로 해석 (어느 폴더에서 실행해도 같게)."""
    path = Path(p)
    return path if path.is_absolute() else (SERVER_DIR / path).resolve()


settings = Settings()
