"""运行配置：全部来自环境变量 / .env，密钥不落代码。"""

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# 让 .env 里的密钥同时进入 os.environ（provider 适配器按环境变量名读取）
load_dotenv(override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "dianshang-imagegen"

    # 渠道注册表
    providers_config: Path = Path("config/providers.yaml")

    # PostgreSQL（为空则不落库，保持纯内存/磁盘模式）
    # 例：postgresql+asyncpg://app_user:***@203.0.113.10:5432/ec_agent_pro
    database_url: str = ""

    # 渠道 API Key 的加密密钥（Fernet 派生），不配置则渠道管理不可用
    channel_secret_key: str = ""

    # 产物存储：local（本地盘）| minio（S3 协议，TD-001 的二进制真源）
    storage_backend: str = "local"
    minio_endpoint: str = ""
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket_assets: str = "assets"
    minio_region: str = "us-east-1"
    minio_ca_bundle: str = ""
    minio_presign_ttl: int = 900
    # 图片给前端的 URL 形式：presign（预签名，推荐生产）| proxy（走 API 中转，开发用）
    storage_url_mode: str = "presign"

    # 产物落盘目录（local 后端用；后续换 MinIO 只替换 storage 实现）
    output_dir: Path = Path("var/outputs")
    # 对外访问前缀，用于拼接返回给前端的 URL
    public_base_url: str = "http://127.0.0.1:8000"

    # 单次生成的整体超时（秒）
    request_timeout_s: float = 180.0

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
