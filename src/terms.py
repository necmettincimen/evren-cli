"""
Terms of Service verification and acceptance for EVREN LLM API.
EVREN API requires accepting terms before making model completion calls.
"""

from __future__ import annotations

import httpx
from src.config import EVREN_API_KEY, EVREN_BASE_URL, get_ssl_verify
from src.ui import print_info, print_success, print_warning, print_error, prompt_confirm


def _stop_active_spinner() -> None:
    """Stops a live Rich spinner so subsequent prints/prompts are visible."""
    try:
        from src.ui import _ACTIVE_STATUS

        if _ACTIVE_STATUS is not None:
            _ACTIVE_STATUS.stop()
    except Exception:
        pass


def _resolve_key(api_key: str | None = None) -> str:
    key = (api_key or EVREN_API_KEY or "").strip()
    if not key:
        raise ValueError("EVREN_API_KEY tanımlanmamış. Lütfen .env veya /keys add ile anahtar ekleyin.")
    return key


def _get_headers(api_key: str | None = None) -> dict[str, str]:
    key = _resolve_key(api_key)
    return {
        "X-API-Key": key,
        "Authorization": f"Bearer {key}",
    }


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "evet")
    return False


def get_terms_status(api_key: str | None = None) -> dict:
    """Checks whether the user has accepted the latest terms."""
    url = f"{EVREN_BASE_URL}/terms/status"
    try:
        response = httpx.get(
            url, headers=_get_headers(api_key), timeout=20.0, verify=get_ssl_verify()
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {"raw": data}
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            raise RuntimeError("EVREN API anahtarı geçersiz (401 Unauthorized).")
        raise RuntimeError(f"Şartlar durumu alınamadı ({e.response.status_code}): {e.response.text}")
    except httpx.RequestError as e:
        raise RuntimeError(f"EVREN API sunucusuna bağlanılamadı: {e}")


def get_terms_text(api_key: str | None = None) -> dict:
    """Fetches the full text and version of the terms of service."""
    url = f"{EVREN_BASE_URL}/terms/text"
    try:
        response = httpx.get(
            url, headers=_get_headers(api_key), timeout=20.0, verify=get_ssl_verify()
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {"raw": data}
    except Exception as e:
        raise RuntimeError(f"Kullanım şartları metni alınamadı: {e}")


def accept_terms(version: int, api_key: str | None = None) -> dict:
    """Accepts the specified terms of service version."""
    url = f"{EVREN_BASE_URL}/terms/accept"
    headers = _get_headers(api_key)
    headers["Content-Type"] = "application/json"
    try:
        response = httpx.post(
            url,
            headers=headers,
            json={"version": int(version)},
            timeout=20.0,
            verify=get_ssl_verify(),
        )
        response.raise_for_status()
        data = response.json() if response.content else {}
        return data if isinstance(data, dict) else {"raw": data}
    except Exception as e:
        raise RuntimeError(f"Kullanım şartları onaylanamadı: {e}")


def ensure_terms_accepted(
    interactive: bool = True,
    api_key: str | None = None,
    *,
    force: bool = False,
    silent_ok: bool = False,
) -> bool:
    """
    Verifies that terms are accepted. If not, prompts the user to accept.

    Returns True if accepted, False otherwise.
    When already accepted, prints a confirmation unless ``silent_ok`` is True.
    """
    _stop_active_spinner()

    try:
        status = get_terms_status(api_key)
    except Exception as e:
        print_warning(f"Kullanım şartları kontrolü yapılamadı: {e}")
        return False

    accepted = _as_bool(status.get("accepted", False))
    current_version = status.get("current_version", 1)
    try:
        current_version = int(current_version)
    except (TypeError, ValueError):
        current_version = 1

    if accepted and not force:
        if not silent_ok:
            print_success(f"Kullanım şartları zaten kabul edilmiş (v{current_version}).")
        return True

    print_warning(f"EVREN LLM API kullanım şartları (v{current_version}) henüz kabul edilmemiş.")
    if not interactive:
        print_error(
            "Etkileşimsiz modda kullanım şartları kabul edilemez. "
            "Lütfen 'evren terms' veya REPL içinde /terms çalıştırın."
        )
        return False

    # Fetch and show terms
    try:
        terms_data = get_terms_text(api_key)
        content = terms_data.get("content", "") or terms_data.get("text", "") or ""
        print("\n" + "=" * 60)
        print(f"EVREN LLM API Kullanım Şartları (Sürüm {current_version})")
        print("=" * 60)
        if content:
            print(content[:1500] + ("\n... [Devamı var]" if len(content) > 1500 else ""))
        else:
            print("(Şart metni boş döndü; yine de kabul edebilirsiniz.)")
        print("=" * 60 + "\n")
    except Exception as e:
        print_warning(f"Kullanım şartları metni okunamadı: {e}")

    confirmed = prompt_confirm(
        f"EVREN LLM Kullanım Şartları v{current_version}'i kabul ediyor musunuz?",
        default=False,
    )
    if not confirmed:
        print_error("Kullanım şartları kabul edilmediği sürece model çağrıları 403 hatası alacaktır.")
        return False

    try:
        accept_terms(current_version, api_key=api_key)
        print_success(f"Kullanım şartları başarıyla kabul edildi (v{current_version})!")
        return True
    except Exception as e:
        print_error(f"Kabul işlemi başarısız: {e}")
        return False


def ensure_terms_for_keys(
    keys: list[str],
    *,
    interactive: bool = True,
    force: bool = False,
) -> bool:
    """Checks/accepts terms for each API key. Returns True if all are accepted."""
    from src.api_key_pool import mask_key

    if not keys:
        print_warning("Kontrol edilecek API anahtarı yok.")
        return False

    all_ok = True
    for key in keys:
        masked = mask_key(key)
        print_info(f"Şartlar kontrolü: {masked}")
        ok = ensure_terms_accepted(
            interactive=interactive,
            api_key=key,
            force=force,
            silent_ok=False,
        )
        if not ok:
            all_ok = False
    return all_ok
