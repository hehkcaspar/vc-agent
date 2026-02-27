from pathlib import Path
from pydantic_settings import BaseSettings


# Get project root directory (parent of backend)
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()


class Settings(BaseSettings):
    DATA_ROOT: Path = PROJECT_ROOT / "data" / "entities"
    DATABASE_URL: str = f"sqlite+aiosqlite:///{PROJECT_ROOT / 'data' / 'vc_portfolio.db'}"
    
    class Config:
        env_file = ".env"


settings = Settings()
