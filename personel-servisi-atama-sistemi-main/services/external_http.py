"""Harici JSON servisleri için ortak, timeout kontrollü HTTP istemcisi."""

from __future__ import annotations

import json
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class ExternalServiceError(RuntimeError):
    """Kullanıcıya güvenle gösterilebilecek harici servis hatası."""


class NetworkUnavailableError(ExternalServiceError):
    """İnternet veya DNS bağlantısı kurulamadığında üretilir."""


class ExternalServiceTimeoutError(ExternalServiceError):
    """Harici servis zaman aşımına uğradığında üretilir."""


class InvalidExternalResponseError(ExternalServiceError):
    """Harici servis geçersiz cevap döndürdüğünde üretilir."""


class JsonHttpClient:
    def get_json(
        self,
        url: str,
        *,
        params: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> Any:
        query = urlencode(params)
        request = Request(f"{url}?{query}", headers=headers, method="GET")
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = response.read()
        except HTTPError as error:
            raise ExternalServiceError(
                f"Harici servis HTTP {error.code} hatası döndürdü."
            ) from error
        except (TimeoutError, socket.timeout) as error:
            raise ExternalServiceTimeoutError(
                "Harici servis zaman aşımına uğradı. Lütfen daha sonra tekrar deneyin."
            ) from error
        except URLError as error:
            raise NetworkUnavailableError(
                "İnternet bağlantısı kurulamadı. Bağlantınızı kontrol edip tekrar deneyin."
            ) from error
        except OSError as error:
            raise NetworkUnavailableError(
                "Harici servise bağlanılamadı. Lütfen daha sonra tekrar deneyin."
            ) from error
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise InvalidExternalResponseError(
                "Harici servis geçersiz bir cevap döndürdü."
            ) from error
