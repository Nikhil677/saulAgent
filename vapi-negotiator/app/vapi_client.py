"""Asynchronous REST Client wrapper for Vapi.ai API.

Provides typed async operations for creating and updating assistants, managing phone numbers,
initiating calls, and querying call status and transcripts.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
import httpx

logger = logging.getLogger(__name__)


class VapiError(Exception):
    """Base exception for Vapi API errors."""
    def __init__(self, message: str, status_code: Optional[int] = None, payload: Optional[Any] = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class VapiAuthenticationError(VapiError):
    """Raised when Vapi returns 401 or 403."""


class VapiNotFoundError(VapiError):
    """Raised when a requested resource does not exist (404)."""


class VapiClient:
    """Async client for interacting with the Vapi.ai REST API."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.vapi.ai",
        timeout: float = 30.0,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=self.timeout,
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client session."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        method: str,
        path: str,
        json_data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Execute an HTTP request against the Vapi API with error handling."""
        client = await self._get_client()
        url = f"{self.base_url}{path}" if not path.startswith("http") else path

        try:
            response = await client.request(
                method=method,
                url=url,
                json=json_data,
                params=params,
            )

            if response.status_code in (401, 403):
                raise VapiAuthenticationError(
                    f"Authentication failed ({response.status_code}): {response.text}",
                    status_code=response.status_code,
                    payload=response.text,
                )
            if response.status_code == 404:
                raise VapiNotFoundError(
                    f"Resource not found at {path}: {response.text}",
                    status_code=404,
                    payload=response.text,
                )
            response.raise_for_status()

            if response.status_code == 204 or not response.text:
                return {}
            return response.json()

        except httpx.HTTPStatusError as e:
            logger.error("Vapi API HTTP error: %s - %s", e.response.status_code, e.response.text)
            raise VapiError(
                f"Vapi API error: {e.response.status_code} - {e.response.text}",
                status_code=e.response.status_code,
                payload=e.response.text,
            ) from e
        except httpx.RequestError as e:
            logger.error("Vapi API connection error: %s", str(e))
            raise VapiError(f"Vapi connection error: {str(e)}") from e

    # -------------------------------------------------------------------------
    # Assistants API
    # -------------------------------------------------------------------------
    async def create_assistant(self, assistant_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new assistant in Vapi."""
        logger.info("Creating assistant in Vapi: %s", assistant_payload.get("name"))
        return await self._request("POST", "/assistant", json_data=assistant_payload)

    async def get_assistant(self, assistant_id: str) -> Dict[str, Any]:
        """Retrieve assistant details by ID."""
        return await self._request("GET", f"/assistant/{assistant_id}")

    async def update_assistant(
        self,
        assistant_id: str,
        assistant_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Update an existing assistant in Vapi."""
        logger.info("Updating assistant %s", assistant_id)
        return await self._request("PATCH", f"/assistant/{assistant_id}", json_data=assistant_payload)

    async def list_assistants(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List assistants registered in the Vapi organization."""
        result = await self._request("GET", "/assistant", params={"limit": limit})
        if isinstance(result, list):
            return result
        return result.get("data", []) if isinstance(result, dict) else []

    async def delete_assistant(self, assistant_id: str) -> bool:
        """Delete an assistant by ID."""
        logger.info("Deleting assistant %s", assistant_id)
        await self._request("DELETE", f"/assistant/{assistant_id}")
        return True

    # -------------------------------------------------------------------------
    # Phone Numbers API
    # -------------------------------------------------------------------------
    async def list_phone_numbers(self) -> List[Dict[str, Any]]:
        """List phone numbers provisioned in the Vapi account."""
        result = await self._request("GET", "/phone-number")
        if isinstance(result, list):
            return result
        return result.get("data", []) if isinstance(result, dict) else []

    async def get_phone_number(self, phone_number_id: str) -> Dict[str, Any]:
        """Retrieve phone number configuration by ID."""
        return await self._request("GET", f"/phone-number/{phone_number_id}")

    async def update_phone_number(
        self,
        phone_number_id: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Update phone number properties (e.g. associate with an assistant)."""
        return await self._request("PATCH", f"/phone-number/{phone_number_id}", json_data=payload)

    # -------------------------------------------------------------------------
    # Calls API
    # -------------------------------------------------------------------------
    async def create_call(
        self,
        assistant_id: str,
        phone_number_id: Optional[str] = None,
        customer_number: Optional[str] = None,
        extra_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Place an outbound phone call via Vapi."""
        payload: Dict[str, Any] = {
            "assistantId": assistant_id,
        }
        if phone_number_id:
            payload["phoneNumberId"] = phone_number_id
        if customer_number:
            payload["customer"] = {"number": customer_number}
        if extra_payload:
            payload.update(extra_payload)

        logger.info("Initiating call via Vapi: assistant=%s, phone=%s", assistant_id, customer_number)
        return await self._request("POST", "/call", json_data=payload)

    async def get_call(self, call_id: str) -> Dict[str, Any]:
        """Retrieve call details, status, transcript, and cost."""
        return await self._request("GET", f"/call/{call_id}")

    async def list_calls(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List recent calls."""
        result = await self._request("GET", "/call", params={"limit": limit})
        if isinstance(result, list):
            return result
        return result.get("data", []) if isinstance(result, dict) else []

    async def delete_call(self, call_id: str) -> Dict[str, Any]:
        """Terminate an active call."""
        logger.info("Terminating call %s", call_id)
        return await self._request("DELETE", f"/call/{call_id}")
