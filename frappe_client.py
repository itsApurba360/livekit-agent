# -*- coding: utf-8 -*-
import os
import logging
import requests
import urllib.parse
from typing import Optional, Dict, Any, List

logger = logging.getLogger("frappe-client")

class FrappeRestClient:
    """
    A standalone REST API client for interacting with any remote Frappe/ERPNext instance.
    Uses standard Token authentication (API Key and API Secret).
    """
    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None, api_secret: Optional[str] = None):
        self.base_url = (base_url or os.environ.get("FRAPPE_SITE_URL") or "http://localhost").rstrip("/")
        self.api_key = api_key or os.environ.get("FRAPPE_API_KEY")
        self.api_secret = api_secret or os.environ.get("FRAPPE_API_SECRET")
        
        self.session = requests.Session()
        if self.api_key and self.api_secret:
            self.session.headers.update({
                "Authorization": f"token {self.api_key}:{self.api_secret}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            })
            logger.info(f"Initialized FrappeRestClient for site: {self.base_url} with Token authentication.")
        else:
            logger.warning(f"Initialized FrappeRestClient for site: {self.base_url} WITHOUT credentials. Environment variables may be missing.")

    def _get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}/{endpoint}"
        try:
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            err_msg = e.response.text if e.response else str(e)
            logger.error(f"GET {url} failed: {err_msg}")
            raise Exception(f"Frappe API GET failed: {err_msg}")

    def _post(self, endpoint: str, json_data: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}/{endpoint}"
        try:
            response = self.session.post(url, json=json_data, timeout=15)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            err_msg = e.response.text if e.response else str(e)
            logger.error(f"POST {url} failed: {err_msg}")
            raise Exception(f"Frappe API POST failed: {err_msg}")

    def get_resource(self, doctype: str, docname: str) -> Dict[str, Any]:
        """Fetch a specific document by type and name."""
        endpoint = f"api/resource/{urllib.parse.quote(doctype)}/{urllib.parse.quote(docname)}"
        res = self._get(endpoint)
        return res.get("data", {})

    def get_resource_list(self, doctype: str, filters: Optional[List[List[Any]]] = None, fields: Optional[List[str]] = None, order_by: Optional[str] = None, limit: int = 20, or_filters: Optional[List[List[Any]]] = None) -> List[Dict[str, Any]]:
        """Fetch a list of documents matching filters."""
        endpoint = f"api/resource/{urllib.parse.quote(doctype)}"
        params = {
            "limit_page_length": limit
        }
        if filters:
            params["filters"] = str(filters).replace("'", '"')
        if or_filters:
            params["or_filters"] = str(or_filters).replace("'", '"')
        if fields:
            params["fields"] = str(fields).replace("'", '"')
        if order_by:
            params["order_by"] = order_by
        res = self._get(endpoint, params=params)
        return res.get("data", [])

    def lookup_caller(self, phone_number: str) -> Dict[str, Any]:
        """
        Lookup the phone number in remote Customer, Contact, and Lead tables.
        Returns:
            {
               "status": "Customer" | "Lead" | "Unknown",
               "customer_id": "...",
               "lead_id": "...",
               "name": "...",
               "company": "..."
            }
        """
        if not phone_number:
            return {"status": "Unknown", "name": "जी", "company": "हमारी कंपनी"}

        # Extract last 10 digits
        cleaned = "".join(c for c in phone_number if c.isdigit())
        last_10 = cleaned[-10:] if len(cleaned) >= 10 else cleaned

        # 1. Search Customer by mobile_no fields
        for field in ["mobile_no", "custom_primary_mobile_no", "custom_alt_mobile_no"]:
            try:
                customers = self.get_resource_list(
                    "Customer",
                    filters=[[field, "like", f"%{last_10}%"]],
                    fields=["name", "customer_name"]
                )
                if customers:
                    cust = customers[0]
                    return {
                        "status": "Customer",
                        "customer_id": cust["name"],
                        "name": cust["customer_name"],
                        "company": cust["customer_name"]
                    }
            except Exception as e:
                logger.debug(f"Skipping lookup of Customer field {field}: {e}")

        # 2. Search Contact Phone child table -> Contact -> Dynamic Link
        try:
            contact_phones = self.get_resource_list(
                "Contact Phone",
                filters=[["phone", "like", f"%{last_10}%"]],
                fields=["parent"]
            )
            if contact_phones:
                parents = [c["parent"] for c in contact_phones]
                links = self.get_resource_list(
                    "Dynamic Link",
                    filters=[["parent", "in", parents], ["link_doctype", "=", "Customer"]],
                    fields=["link_name"]
                )
                if links:
                    cust_id = links[0]["link_name"]
                    cust_doc = self.get_resource("Customer", cust_id)
                    return {
                        "status": "Customer",
                        "customer_id": cust_id,
                        "name": cust_doc.get("customer_name") or cust_id,
                        "company": cust_doc.get("customer_name") or cust_id
                    }
        except Exception as e:
            logger.debug(f"Contact Phone lookup failed: {e}")

        # 3. Search Lead by mobile_no or phone
        for field in ["mobile_no", "phone"]:
            try:
                leads = self.get_resource_list(
                    "Lead",
                    filters=[[field, "like", f"%{last_10}%"]],
                    fields=["name", "lead_name", "company_name"]
                )
                if leads:
                    lead = leads[0]
                    return {
                        "status": "Lead",
                        "lead_id": lead["name"],
                        "name": lead["lead_name"] or lead["name"],
                        "company": lead["company_name"] or "हमारी कंपनी"
                    }
            except Exception as e:
                logger.debug(f"Lead lookup failed for {field}: {e}")

        return {"status": "Unknown", "name": "जी", "company": "हमारी कंपनी"}

    def send_whatsapp_message(self, mobile_number: str, message: str) -> Dict[str, Any]:
        """Call the remote whitelisted method to send a WhatsApp message."""
        endpoint = "api/method/watoolx_whatsapp.watoolx_whatsapp.doctype.watoolx_instance.watoolx_instance.send_custom_whatsapp_message"
        payload = {
            "mobile_number": mobile_number,
            "message": message
        }
        try:
            res = self._post(endpoint, json_data=payload)
            return res.get("message", {"status": False, "msg": "No response message"})
        except Exception as e:
            logger.error(f"WhatsApp OTP send failed over REST: {e}")
            return {"status": False, "msg": str(e)}

    def send_whatsapp_message_with_file(self, mobile_number: str, message: str, file_link: str) -> Dict[str, Any]:
        """Call the remote whitelisted method to send a WhatsApp message with a PDF/file attachment."""
        endpoint = "api/method/watoolx_whatsapp.watoolx_whatsapp.doctype.watoolx_instance.watoolx_instance.send_custom_whatsapp_message_with_file"
        payload = {
            "mobile_number": mobile_number,
            "message": message,
            "file_link": file_link
        }
        try:
            res = self._post(endpoint, json_data=payload)
            return res.get("message", {"status": False, "msg": "No response message"})
        except Exception as e:
            logger.error(f"WhatsApp PDF send failed over REST: {e}")
            return {"status": False, "msg": str(e)}
