#!/usr/bin/env python3
"""ThingsBoard helper client with short, shared calls."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import os
import requests

TIMEOUT = int(os.getenv("TB_TIMEOUT", "15"))


class TBError(RuntimeError):
    """Raised when the remote ThingsBoard API returns an error."""


@dataclass
class TB:
    base: str
    user: str
    password: str
    timeout: int = TIMEOUT

    def __post_init__(self) -> None:
        base = self.base.rstrip("/")
        if not base or not self.user or not self.password:
            raise TBError("Se requieren TB_URL, TB_USERNAME y TB_PASSWORD")
        self.base = base
        self.session = requests.Session()

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "TB":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
        self.close()

    # API helpers ---------------------------------------------------------
    def login(self) -> str:
        resp = self.session.post(
            f"{self.base}/api/auth/login",
            json={"username": self.user, "password": self.password},
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise TBError(f"Login fallido: {resp.status_code} {resp.text}")
        token = resp.json().get("token")
        if not token:
            raise TBError("No se obtuvo token JWT")
        self.session.headers.update({"X-Authorization": f"Bearer {token}"})
        return token

    def default_profile(self) -> Optional[str]:
        for endpoint in ("deviceProfileInfos", "deviceProfiles"):
            resp = self.session.get(
                f"{self.base}/api/{endpoint}?pageSize=100&page=0",
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                for item in data:
                    if item.get("default"):
                        return item["id"]["id"]
        return None

    def device(self, name: str) -> Optional[Dict[str, Any]]:
        resp = self.session.get(
            f"{self.base}/api/tenant/devices?deviceName={name}",
            timeout=self.timeout,
        )
        if resp.status_code == 200 and resp.text and resp.text != "null":
            return resp.json()
        resp = self.session.get(
            f"{self.base}/api/tenant/devices?limit=100&page=0&textSearch={name}",
            timeout=self.timeout,
        )
        if resp.status_code == 200:
            for item in resp.json().get("data", []):
                if item.get("name") == name:
                    return item
        return None

    def save_device(
        self,
        name: str,
        *,
        label: str,
        dev_type: str,
        profile_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"name": name, "label": label, "type": dev_type}
        if profile_id:
            payload["deviceProfileId"] = {"id": profile_id, "entityType": "DEVICE_PROFILE"}
        resp = self.session.post(
            f"{self.base}/api/device",
            json=payload,
            timeout=self.timeout,
        )
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 400 and "already" in resp.text.lower():
            existing = self.device(name)
            if existing:
                return existing
        raise TBError(f"No se pudo crear o recuperar '{name}': {resp.status_code} {resp.text}")

    def token(self, device_id: str) -> str:
        resp = self.session.get(
            f"{self.base}/api/device/{device_id}/credentials",
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise TBError(f"Error credenciales: {resp.status_code} {resp.text}")
        data = resp.json()
        if data.get("credentialsType") != "ACCESS_TOKEN":
            raise TBError("Credencial no es ACCESS_TOKEN")
        token = data.get("credentialsId")
        if not token:
            raise TBError("credentialsId vacío")
        return token

    def set_attrs(self, device_id: str, attrs: Dict[str, Any]) -> bool:
        resp = self.session.post(
            f"{self.base}/api/plugins/telemetry/DEVICE/{device_id}/SERVER_SCOPE",
            json=attrs,
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            print(f"[WARN] No se guardaron attrs para {device_id}: {resp.status_code} {resp.text}")
            return False
        return True

    def delete_device(self, device_id: str) -> None:
        resp = self.session.delete(
            f"{self.base}/api/device/{device_id}",
            timeout=self.timeout,
        )
        if resp.status_code not in (200, 202, 204):
            raise TBError(f"No se pudo eliminar dispositivo {device_id}: {resp.status_code} {resp.text}")


__all__ = ["TB", "TBError"]
