from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
    
    WINGSUITE_URL: str
    WINGSUITE_USER: str
    WINGSUITE_PASS: str
    BROWSER_HEADLESS: bool = False

settings = Settings()