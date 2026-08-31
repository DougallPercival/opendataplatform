"""Settings, read from environment / .env — see .env.example.

Nothing cluster-specific lives here: in-cluster, DATABASE_URL comes from the
platform-postgres-catalog-credentials Secret (see bootstrap/install.sh and
../../argocd/manifests/postgres-cluster.yaml's managed.roles) the same way
every other core service reads its DB credentials, not from a file baked
into an image.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "postgresql+psycopg://catalog:catalog-dev-password@localhost:5432/catalog"
    cors_origins: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(" ") if o.strip()]


settings = Settings()
