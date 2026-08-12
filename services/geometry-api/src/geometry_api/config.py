from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    dataset_dir: str = "data/datasets"
    cache_dir: str = "data/cache"

    model_config = {"env_prefix": "GEOMETRY_API_"}


settings = Settings()
