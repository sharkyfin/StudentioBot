from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    OPENAI_API_KEY: str = ""
    ALLOWED_ORIGINS: str = "http://localhost:3000"
    DATABASE_URL: str
    OPENAI_MODEL: str = "gpt-4o-mini"
    ORCHESTRATOR_MODEL: str | None = None

    @property
    def origins(self) -> list[str]:
        raw = self.ALLOWED_ORIGINS.strip()
        return [o.strip() for o in raw.split(",") if o.strip()]

settings = Settings()
