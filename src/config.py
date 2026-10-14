"""
Config and environment management for EVREN CLI.
Loads settings from .env and manages project-level configuration directories.
"""

import os
import platform
from pathlib import Path
try:
    from dotenv import load_dotenv
    HAVE_DOTENV = True
except ImportError:
    HAVE_DOTENV = False


def _manual_load_env(filepath: Path):
    """Fallback parser for .env files using only standard library."""
    if not filepath.exists():
        return
    try:
        for line in filepath.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = val
    except Exception:
        pass


# Was an API key already present in the real process environment before any
# file was read? Captured first because .env and config.json both write into
# os.environ, which would otherwise hide the true origin of the key.
_PREEXISTING_KEY_ENV = bool(os.getenv("EVREN_API_KEY") or os.getenv("EVREN_API_KEYS"))

# Search for .env in current working directory, then script root, then user home
ENV_FILE_CANDIDATES = (
    Path.cwd() / ".env",
    Path(__file__).resolve().parents[1] / ".env",
    Path.home() / ".evren" / ".env",
)

# The .env file that was actually read (None when none of the candidates exist).
ENV_FILE_PATH: Path | None = None


def _load_environment():
    global ENV_FILE_PATH
    for p in ENV_FILE_CANDIDATES:
        if p.exists():
            if HAVE_DOTENV:
                load_dotenv(p, override=False)
            else:
                _manual_load_env(p)
            ENV_FILE_PATH = p
            break

_load_environment()

# Shared path for the C#-compatible user config (API keys, BaseUrl, …).
LEGACY_CONFIG_PATH = Path.home() / ".evren-cli" / "config.json"


def load_legacy_config() -> dict:
    """Loads ~/.evren-cli/config.json as a dict (empty if missing/invalid)."""
    import json

    path = LEGACY_CONFIG_PATH
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8-sig")
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_api_keys(keys: list[str]) -> Path:
    """Persists the API key list to ~/.evren-cli/config.json `ApiKeys`.

    Preserves all other fields in the file. Creates the directory/file if needed.
    Returns the path written.
    """
    import json

    path = LEGACY_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    data = load_legacy_config()
    # Deduplicate while preserving order; keep only non-empty strings.
    seen: set[str] = set()
    clean: list[str] = []
    for k in keys:
        if isinstance(k, str):
            k = k.strip()
            if k and k not in seen:
                seen.add(k)
                clean.append(k)
    data["ApiKeys"] = clean
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def prompt_for_api_key() -> str | None:
    """Interactively prompts the user for an EVREN API key and persists it.

    Used as a fallback when no key is found in the environment, .env or
    ~/.evren-cli/config.json. The entered key is saved to config.json and
    exported to the environment so the current process (and future runs) can
    use it immediately.

    Returns the key on success, or None if the user aborts / no TTY is present.
    """
    import sys

    # Only prompt when we have an interactive terminal; otherwise the caller
    # should surface a clear configuration error instead of hanging.
    if not sys.stdin.isatty():
        return None

    try:
        from src.ui import print_info, print_warning
    except Exception:  # pragma: no cover - UI is optional
        def print_info(msg):  # type: ignore
            print(f"[*] {msg}")

        def print_warning(msg):  # type: ignore
            print(f"[!] {msg}")

    print_warning("EVREN_API_KEY bulunamadı.")
    print_info(
        "EVREN LLM API anahtarınızı girin (format: evren_llm_...). "
        "Anahtar ~/.evren-cli/config.json dosyasına kaydedilecek."
    )
    try:
        key = input("EVREN API Anahtarı: ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return None

    if not key:
        return None

    # Persist for future runs and export for the current process.
    try:
        save_api_keys([key])
    except Exception as e:  # pragma: no cover - disk errors are non-fatal
        print_warning(f"Anahtar kaydedilemedi ({e}); yalnızca bu oturum için kullanılacak.")

    os.environ["EVREN_API_KEY"] = key
    os.environ["EVREN_API_KEYS"] = key
    print_info("API anahtarı ayarlandı.")
    return key


# Number of keys taken from config.json `ApiKeys` at import time (0 = none).
LEGACY_KEYS_USED = 0


def _load_legacy_evren_cli_config():
    """Surfaces ApiKeys / BaseUrl from config.json into env when unset."""
    global LEGACY_KEYS_USED
    data = load_legacy_config()
    if not data:
        return

    keys = [
        k.strip()
        for k in data.get("ApiKeys", [])
        if isinstance(k, str) and k.strip()
    ]

    # Prefer loading the full list so the pool sees every key.
    if keys and not os.getenv("EVREN_API_KEYS") and not os.getenv("EVREN_API_KEY"):
        os.environ["EVREN_API_KEYS"] = ",".join(keys)
        os.environ["EVREN_API_KEY"] = keys[0]
        LEGACY_KEYS_USED = len(keys)
    elif keys and not os.getenv("EVREN_API_KEY"):
        preferred = next((k for k in keys if k.startswith("evren_llm_")), keys[0])
        os.environ["EVREN_API_KEY"] = preferred
        LEGACY_KEYS_USED = len(keys)

    if not os.getenv("EVREN_BASE_URL") and data.get("BaseUrl"):
        os.environ["EVREN_BASE_URL"] = data["BaseUrl"]


_load_legacy_evren_cli_config()

# Core EVREN API Configuration
EVREN_API_KEY = os.getenv("EVREN_API_KEY", "")
EVREN_BASE_URL = os.getenv("EVREN_BASE_URL", "https://evren-llmapi.ssyz.org.tr/v1").rstrip("/")
EVREN_DEFAULT_MODEL = os.getenv("EVREN_DEFAULT_MODEL", "qwen3.8-flash-next")
EVREN_DEFAULT_MAX_TOKENS = int(os.getenv("EVREN_DEFAULT_MAX_TOKENS", "4096"))
EVREN_DEFAULT_TEMPERATURE = float(os.getenv("EVREN_DEFAULT_TEMPERATURE", "0.3"))
EVREN_AUTO_APPROVE = os.getenv("EVREN_AUTO_APPROVE", "true").lower() in ("true", "1", "yes")


def config_source_report(pool_size: int | None = None) -> dict:
    """Describes where the CLI read its configuration and API keys from.

    Only file paths, key counts and the key *source* are reported; key values are
    never included, not even masked, so this output is safe to print or log.
    """
    if pool_size is None:
        try:
            from src.api_key_pool import parse_api_keys

            pool_size = len(parse_api_keys())
        except Exception:
            pool_size = 0

    env_file = str(ENV_FILE_PATH) if ENV_FILE_PATH else ""
    config_exists = LEGACY_CONFIG_PATH.exists()

    if _PREEXISTING_KEY_ENV:
        key_source = "süreç ortam değişkeni (EVREN_API_KEY / EVREN_API_KEYS)"
    elif LEGACY_KEYS_USED:
        key_source = f"{LEGACY_CONFIG_PATH} (ApiKeys, {LEGACY_KEYS_USED} anahtar)"
    elif ENV_FILE_PATH:
        key_source = f"{ENV_FILE_PATH} (EVREN_API_KEY / EVREN_API_KEYS)"
    else:
        key_source = "bulunamadı"

    return {
        "env_file": env_file,
        "env_candidates": [str(p) for p in ENV_FILE_CANDIDATES],
        "config_json": str(LEGACY_CONFIG_PATH),
        "config_json_exists": config_exists,
        "config_json_keys": LEGACY_KEYS_USED,
        "key_source": key_source,
        "key_count": pool_size,
        "has_key": bool(EVREN_API_KEY) or pool_size > 0,
    }


def get_ui_theme() -> str:
    """Returns the explicitly selected terminal theme, without guessing its background."""
    theme = os.getenv("EVREN_UI_THEME", "dark").strip().lower()
    if theme not in ("light", "dark"):
        raise ValueError("EVREN_UI_THEME yalnızca light veya dark olabilir.")
    return theme


# Reasoning (akıl yürütme) yoğunluğu. Reasoning modelleri (deepseek-v4.1-flash,
# glm-5.3) görünmez bir "düşünme" akışı üretir ve bu token'lar kod çıktısıyla
# aynı max_tokens bütçesini paylaşır. Düşük değer = daha hızlı yanıt + koda daha
# çok yer. Geçerli değerler: "low" | "medium" | "high" | "none" (boş = sunucu
# varsayılanı). Örn: EVREN_REASONING_EFFORT=low
EVREN_REASONING_EFFORT = os.getenv("EVREN_REASONING_EFFORT", "low").strip().lower()
if EVREN_REASONING_EFFORT in ("", "default", "auto", "none", "off"):
    EVREN_REASONING_EFFORT = None

# SSL/TLS verification configuration.
# - EVREN_CA_BUNDLE : optional path to a custom CA bundle (PEM). Use this when the
#   server presents a self-signed / corporate certificate you want to trust explicitly.
# - EVREN_SSL_VERIFY: "true" (default) verifies certificates; set to "false" to disable
#   verification entirely (needed for self-signed certs or TLS-inspecting proxies).
EVREN_CA_BUNDLE = os.getenv("EVREN_CA_BUNDLE", "").strip()
# NOTE: verification is disabled by default so self-signed / corporate (MITM)
# certificates do not break every HTTP request. Set EVREN_SSL_VERIFY=true to
# re-enable certificate validation.
EVREN_SSL_VERIFY = os.getenv("EVREN_SSL_VERIFY", "false").lower() in ("true", "1", "yes")


def get_ssl_verify():
    """Returns the value to pass as `verify=` to httpx / the OpenAI SDK.

    Precedence:
      1. A custom CA bundle path if EVREN_CA_BUNDLE is set (explicit trust).
      2. False if EVREN_SSL_VERIFY is disabled (self-signed / MITM proxy).
      3. True otherwise (default, secure).
    """
    if EVREN_CA_BUNDLE:
        return EVREN_CA_BUNDLE
    return EVREN_SSL_VERIFY


def build_httpx_client(timeout=None):
    """Builds an httpx.Client honouring the SSL verification settings.

    Used by both direct httpx calls and the OpenAI SDK (via `http_client=`),
    so a single configuration point controls TLS behaviour everywhere.
    """
    import httpx
    return httpx.Client(verify=get_ssl_verify(), timeout=timeout)

# Known EVREN Models
RECOMMENDED_MODELS = {
    "qwen3.8-flash-next": "Varsayılan · Hızlı Yanıtlar, Vibe Coding, Yüksek Eşzamanlılık",
    "deepseek-v4.1-flash": "Kodlama ve Ajan İşleri (1M Bağlam, Hızlı)",
    "glm-5.3": "Derin Akıl Yürütme ve Çok Adımlı Karmaşık Analiz",
    "auto": "Otomatik Model Yönlendirme",
    "dots-ocr": "Belge ve Resim Metin Tanıma (OCR)",
    "deepseek-ocr-2": "Gelişmiş Belge/Görsel Analizi ve OCR",
    "qwen3-asr-1.7b": "Ses Tanıma ve Metne Dönüştürme (ASR)",
    "qwen3-embedding-8b": "Vektör Gömme ve Semantik Arama",
    "qwen3-reranker-8b": "Arama Sonuçları Yeniden Sıralama",
    "qwen3-guard-4b": "Güvenlik ve Zararlı İçerik Sınıflandırması",
}


def get_workspace_dir(custom_path: str | Path | None = None) -> Path:
    """Returns the absolute Path of the active workspace."""
    if custom_path:
        p = Path(custom_path).resolve()
        if p.exists() and p.is_dir():
            return p
    return Path.cwd().resolve()


def get_evren_dir(workspace_root: Path | None = None) -> Path:
    """Returns the path to .evren folder inside the workspace."""
    root = workspace_root or get_workspace_dir()
    evren_dir = root / ".evren"
    return evren_dir


def execution_context_block(workspace_root: Path) -> str:
    """Describes the actual local command environment, without probing hosts."""
    shell = os.environ.get("COMSPEC", "cmd.exe") if os.name == "nt" else "/bin/sh"
    return (
        "YEREL ÇALIŞTIRMA BAĞLAMI\n"
        f"İşletim sistemi: {platform.system()}\n"
        f"Çalışma alanı ve run_command çalışma dizini: {workspace_root.resolve()}\n"
        f"run_command kabuğu: {shell}\n"
        "run_command, EVREN CLI sürecinin çalıştığı makinede çalışır; SSH bağlantısı kurmaz.\n"
        "Windows'ta PowerShell sözdizimi gerekiyorsa powershell -NoProfile -Command "
        "ile açıkça çağır; run_command kabuğunu PowerShell varsayma.\n"
        "CLI bir sunucunun terminalinde çalışıyorsa o sunucu yerel makinedir."
    )


def get_backup_dir(workspace_root: Path | None = None) -> Path:
    """Returns the path to the backup folder inside .evren."""
    return get_evren_dir(workspace_root) / "backups"


def ensure_evren_dirs(workspace_root: Path | None = None):
    """Ensures that .evren and backup directories exist."""
    evren_dir = get_evren_dir(workspace_root)
    evren_dir.mkdir(parents=True, exist_ok=True)
    (evren_dir / "backups").mkdir(parents=True, exist_ok=True)
    (evren_dir / "logs").mkdir(parents=True, exist_ok=True)
    
    # Keep a gitignore in .evren up to date (additive: never drops user lines).
    gi = evren_dir / ".gitignore"
    required = (
        "backups/",
        "logs/",
        "history.json",
        "checkpoint.json",
        "decisions.json",
        "docs_state.json",
        "docs.lock",
    )
    try:
        existing = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
        have = {line.strip() for line in existing if line.strip()}
        missing = [entry for entry in required if entry not in have]
        if missing:
            lines = [line for line in existing if line.strip()]
            lines.extend(missing)
            gi.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass


def check_api_key_configured() -> bool:
    """Checks if EVREN_API_KEY is configured."""
    return bool(EVREN_API_KEY and EVREN_API_KEY.startswith("evren_llm_"))
