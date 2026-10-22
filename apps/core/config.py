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

    # 产物落盘目录（MVP 用本地盘，后续换 MinIO 只替换 storage 实现）
    output_dir: Path = Path("var/outputs")
    # 对外访问前缀，用于拼接返回给前端的 URL
    public_base_url: str = "http://127.0.0.1:8000"

    # 单次生成的整体超时（秒）
    request_timeout_s: float = 180.0

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
