# -*- coding: utf-8 -*-
"""Small REST client for Vobiz CDR and recording metadata APIs."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import quote

import requests


def _clean_base_url(value: str) -> str:
    return (value or "https://api.vobiz.ai/api/v1").strip().rstrip("/")


class VobizRestClient:
    """REST client for Vobiz recording lookups.

    Credentials are intentionally read from server-side environment only. Do not
    expose these values to Hermes/plugin callers.
    """

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        auth_id: Optional[str] = None,
        auth_token: Optional[str] = None,
        timeout: float = 20,
    ) -> None:
        self.base_url = _clean_base_url(base_url or os.environ.get("VOBIZ_API_BASE_URL", ""))
        self.auth_id = (auth_id or os.environ.get("VOBIZ_AUTH_ID") or "").strip()
        self.auth_token = (auth_token or os.environ.get("VOBIZ_AUTH_TOKEN") or "").strip()
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.auth_id and self.auth_token)

    def _headers(self) -> dict[str, str]:
        if not self.configured:
            raise RuntimeError("Vobiz API credentials are not configured")
        return {
            "X-Auth-ID": self.auth_id,
            "X-Auth-Token": self.auth_token,
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def list_recordings(
        self,
        *,
        call_uuid: Optional[str] = None,
        recording_type: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if call_uuid:
            params["call_uuid"] = call_uuid
        if recording_type:
            params["recording_type"] = recording_type
        response = requests.get(
            self._url(f"/Account/{quote(self.auth_id)}/Recording/"),
            headers=self._headers(),
            params=params,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def get_recording(self, recording_id: str) -> dict[str, Any]:
        response = requests.get(
            self._url(f"/Account/{quote(self.auth_id)}/Recording/{quote(recording_id)}/"),
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def search_cdrs(
        self,
        *,
        from_number: Optional[str] = None,
        to_number: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        call_direction: Optional[str] = "outbound",
        page: int = 1,
        per_page: int = 20,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page": page, "per_page": per_page}
        if from_number:
            params["from_number"] = from_number
        if to_number:
            params["to_number"] = to_number
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if call_direction:
            params["call_direction"] = call_direction
        response = requests.get(
            self._url(f"/Account/{quote(self.auth_id)}/cdr/search"),
            headers=self._headers(),
            params=params,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def start_recording(
        self,
        *,
        call_uuid: str,
        callback_url: Optional[str] = None,
        time_limit: Optional[int] = None,
        file_format: Optional[str] = None,
        record_channel_type: Optional[str] = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "file_format": file_format or os.environ.get("VOBIZ_RECORDING_FORMAT") or "mp3",
            "record_channel_type": record_channel_type
            or os.environ.get("VOBIZ_RECORDING_CHANNEL_TYPE")
            or "stereo",
        }
        if callback_url:
            payload["callback_url"] = callback_url
            payload["callback_method"] = "POST"
        if time_limit:
            payload["time_limit"] = time_limit
        response = requests.post(
            self._url(f"/Account/{quote(self.auth_id)}/Call/{quote(call_uuid)}/Record/"),
            headers=self._headers(),
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()



    def download_recording(self, recording_url: str) -> tuple[bytes, str]:
        """Download the actual media file using Vobiz auth headers.
        Returns (content_bytes, content_type)
        """
        if not self.configured:
            raise RuntimeError("Vobiz API credentials are not configured")

        headers = {
            "X-Auth-ID": self.auth_id,
            "X-Auth-Token": self.auth_token,
        }
        # Do not force Content-Type for binary download
        response = requests.get(recording_url, headers=headers, timeout=self.timeout)
        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "audio/wav")
        return response.content, content_type

    def stream_recording_media(
        self,
        recording_url: str,
        *,
        range_header: Optional[str] = None,
    ):
        """Stream recording bytes from Vobiz (supports optional HTTP Range)."""
        if not self.configured:
            raise RuntimeError("Vobiz API credentials are not configured")

        headers = {
            "X-Auth-ID": self.auth_id,
            "X-Auth-Token": self.auth_token,
        }
        if range_header:
            headers["Range"] = range_header
        response = requests.get(
            recording_url,
            headers=headers,
            stream=True,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response

def normalize_phone_for_vobiz(phone_number: Optional[str]) -> str:
    return "".join(ch for ch in str(phone_number or "") if ch.isdigit())


def date_window_for_record(record: dict[str, Any], *, days: int = 1) -> tuple[str, str]:
    raw = record.get("created_at") or record.get("updated_at")
    try:
        center = datetime.fromisoformat(str(raw).replace("Z", "+00:00")) if raw else datetime.now(timezone.utc)
    except ValueError:
        center = datetime.now(timezone.utc)
    if center.tzinfo is None:
        center = center.replace(tzinfo=timezone.utc)
    start = (center - timedelta(days=days)).date().isoformat()
    end = (center + timedelta(days=days)).date().isoformat()
    return start, end


def select_best_cdr(record: dict[str, Any], cdrs: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not cdrs:
        return None
    sip_call_id = str(record.get("sip_call_id") or "")
    if sip_call_id:
        for cdr in cdrs:
            if str(cdr.get("sip_call_id") or "") == sip_call_id:
                return cdr

    target_digits = normalize_phone_for_vobiz(record.get("phone_number"))
    if target_digits:
        for cdr in cdrs:
            cdr_number = normalize_phone_for_vobiz(
                cdr.get("destination_number") or cdr.get("to_number") or cdr.get("caller_id_number")
            )
            if cdr_number.endswith(target_digits[-10:]):
                return cdr
    return cdrs[0]


def select_best_recording(recordings: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not recordings:
        return None
    with_url = [item for item in recordings if item.get("recording_url") or item.get("record_url")]
    candidates = with_url or recordings

    def sort_key(item: dict[str, Any]) -> tuple[int, str]:
        type_score = 0 if item.get("recording_type") == "call" else 1
        return (type_score, str(item.get("add_time") or item.get("recording_end_ms") or ""))

    return sorted(candidates, key=sort_key)[-1]


def find_recording_for_call(record: dict[str, Any], client: Optional[VobizRestClient] = None) -> Optional[dict[str, Any]]:
    """Find the best Vobiz recording for a call record.

    Returns a dict containing at least `vobiz_call_uuid`, `vobiz_recording_id`,
    `recording_url`, and `recording_source` when found.
    """
    client = client or VobizRestClient()
    if not client.configured:
        return None

    call_uuid = record.get("vobiz_call_uuid") or (record.get("metadata") or {}).get("vobiz_call_uuid")
    candidates = []
    if call_uuid:
        candidates.append(str(call_uuid))

    # Also try sip_call_id — Vobiz often uses the LiveKit sip_call_id as the recording call_uuid
    sip_id = record.get("sip_call_id") or (record.get("metadata") or {}).get("sip_call_id")
    if sip_id and str(sip_id) not in candidates:
        candidates.append(str(sip_id))

    if not candidates:
        start_date, end_date = date_window_for_record(record)
        phone_digits = normalize_phone_for_vobiz(record.get("phone_number"))
        search = client.search_cdrs(
            to_number=phone_digits or None,
            start_date=start_date,
            end_date=end_date,
            call_direction="outbound",
            per_page=20,
        )
        cdr = select_best_cdr(record, search.get("data") or [])
        if cdr:
            c = cdr.get("uuid") or cdr.get("call_uuid")
            if c:
                candidates.append(str(c))

    if not candidates:
        return None

    recording = None
    chosen_call_uuid = None
    for cand in candidates:
        recordings_payload = client.list_recordings(call_uuid=str(cand), limit=20, offset=0)
        rec = select_best_recording(recordings_payload.get("objects") or [])
        if rec:
            recording = rec
            chosen_call_uuid = str(cand)
            break

    if not recording:
        return {"vobiz_call_uuid": candidates[0], "recording_source": "vobiz"}

    return {
        "vobiz_call_uuid": chosen_call_uuid or candidates[0],
        "vobiz_recording_id": recording.get("recording_id"),
        "recording_source": "vobiz",
        "recording_url": recording.get("recording_url") or recording.get("record_url"),
        "recording_duration_ms": recording.get("recording_duration_ms"),
        "recording_format": recording.get("recording_format"),
        "recording_type": recording.get("recording_type"),
        "raw_recording": recording,
    }
