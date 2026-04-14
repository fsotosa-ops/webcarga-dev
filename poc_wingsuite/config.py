from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Añadimos extra="ignore" para que Pydantic no se queje por variables ajenas a la PoC
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8", 
        extra="ignore"
    )
    
    WINGSUITE_URL: str
    WINGSUITE_USER: str
    WINGSUITE_PASS: str
    BROWSER_HEADLESS: bool = False

settings = Settings()