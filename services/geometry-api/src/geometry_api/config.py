from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    dataset_dir: str = "data/datasets"
    cache_dir: str = "data/cache"

    model_config = SettingsConfigDict(
        env_prefix="GEOMETRY_API_", env_file=("../../.env", ".env"), env_file_encoding="utf-8"
    )


settings = Settings()
