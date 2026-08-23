from __future__ import annotations

import os

from .common import *


SECRET_PREFIX = "fernet:v1:"


class SecretConfigurationError(RuntimeError):
    pass


class SecretProtector:
    def protect(self, value: str) -> str:
        raise NotImplementedError

    def unprotect(self, value: str) -> str:
        raise NotImplementedError


class DpapiSecretProtector(SecretProtector):
    def protect(self, value: str) -> str:
        return _dpapi_protect(value)

    def unprotect(self, value: str) -> str:
        return _dpapi_unprotect(value)


class FernetSecretProtector(SecretProtector):
    def __init__(self, key: str | bytes) -> None:
        try:
            from cryptography.fernet import Fernet, InvalidToken
        except ImportError as exc:
            raise SecretConfigurationError(
                "Linux 服务端需要安装 cryptography 才能保护 API Key"
            ) from exc
        clean_key = key.encode("ascii") if isinstance(key, str) else bytes(key)
        try:
            self._fernet = Fernet(clean_key)
        except (TypeError, ValueError) as exc:
            raise SecretConfigurationError(
                "API_TOOLS_MASTER_KEY 必须是有效的 Fernet 密钥"
            ) from exc
        self._invalid_token = InvalidToken

    def protect(self, value: str) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError("密钥不能为空")
        token = self._fernet.encrypt(value.encode("utf-8")).decode("ascii")
        return f"{SECRET_PREFIX}{token}"

    def unprotect(self, value: str) -> str:
        clean = str(value or "")
        if not clean.startswith(SECRET_PREFIX):
            raise SecretConfigurationError("密钥不是服务端可读取的 Fernet 格式")
        try:
            decrypted = self._fernet.decrypt(clean[len(SECRET_PREFIX):].encode("ascii"))
            return decrypted.decode("utf-8")
        except (ValueError, UnicodeError, self._invalid_token) as exc:
            raise SecretConfigurationError("服务端无法解密 API Key") from exc


def default_secret_protector() -> SecretProtector:
    if sys.platform == "win32":
        return DpapiSecretProtector()
    key = os.environ.get("API_TOOLS_MASTER_KEY", "").strip()
    if not key:
        raise SecretConfigurationError(
            "Linux 服务端必须配置 API_TOOLS_MASTER_KEY"
        )
    return FernetSecretProtector(key)


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[DATA_BLOB, Any]:
    buffer = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _dpapi_protect(value: str) -> str:
    source, source_buffer = _blob(value.encode("utf-8"))
    result = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source), APP_NAME, None, None, None, 0, ctypes.byref(result)
    ):
        raise ctypes.WinError()
    try:
        encrypted = ctypes.string_at(result.pbData, result.cbData)
        return base64.b64encode(encrypted).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)
        del source_buffer


def _dpapi_unprotect(value: str) -> str:
    encrypted = base64.b64decode(value)
    source, source_buffer = _blob(encrypted)
    result = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.pbData, result.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)
        del source_buffer


def protect_secret(value: str) -> str:
    return default_secret_protector().protect(value)


def unprotect_secret(value: str) -> str:
    return default_secret_protector().unprotect(value)
