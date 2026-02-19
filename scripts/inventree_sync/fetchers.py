"""
fetchers.py – Supplier data fetchers for LCSC and Mouser.
"""

import logging
import os
import re
from typing import Optional

import requests

from .models import PartData

logger = logging.getLogger(__name__)

# iOS User-Agent – avoids bot-blocking on LCSC's CDN / wmsc API
_IOS_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.0 Mobile/15E148 Safari/604.1"
)


class LCSCFetcher:
    """Fetches part data from the LCSC wmsc API."""

    _UA = _IOS_UA

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self._UA,
            "Accept-Language": "en-US,en",
        })
        # Initialise session / set currency cookie
        try:
            self.session.get(
                "https://wmsc.lcsc.com/wmsc/home/currency?currencyCode=EUR",
                timeout=10,
            )
        except Exception as exc:
            logger.warning("LCSC currency init failed: %s", exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_by_sku(self, lcsc_sku: str) -> Optional[PartData]:
        """Fetch a single part by its LCSC product code."""
        url = f"https://wmsc.lcsc.com/ftps/wm/product/detail?productCode={lcsc_sku}"
        try:
            resp = self.session.get(url, timeout=15)
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:
            logger.error("LCSC fetch_by_sku(%s) failed: %s", lcsc_sku, exc)
            return None

        result = body.get("result")
        if not result:
            logger.warning("LCSC fetch_by_sku(%s): empty result", lcsc_sku)
            return None
        return self._parse(result, lcsc_sku=lcsc_sku)

    def fetch_by_mpn(self, mpn: str) -> Optional[PartData]:
        """Search LCSC by MPN; prefer exact match.

        The search endpoint returns minimal product data (no paramVOList), so
        after identifying the right product code we always call fetch_by_sku to
        get the full detail (parameters, images, price breaks, …).
        """
        url = "https://wmsc.lcsc.com/ftps/wm/search/v2/global"
        try:
            resp = self.session.post(url, json={"keyword": mpn}, timeout=15)
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:
            logger.error("LCSC fetch_by_mpn(%s) failed: %s", mpn, exc)
            return None

        result = body.get("result", {})
        if not result:
            return None

        # Direct match hint from the API
        tip = result.get("tipProductDetailUrlVO")
        if tip:
            code = tip.get("productCode")
            if code:
                return self.fetch_by_sku(code)

        # Walk the search result list
        product_list = (
            result.get("productSearchResultVO", {}).get("productList") or []
        )
        # Prefer exact MPN match, fall back to first result
        best_code = None
        for product in product_list:
            if product.get("productModel", "").upper() == mpn.upper():
                best_code = product.get("productCode")
                break
        if best_code is None and product_list:
            best_code = product_list[0].get("productCode")

        if best_code:
            return self.fetch_by_sku(best_code)

        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _fix_datasheet(url: str) -> str:
        """Rewrite datasheet CDN URLs to the wmsc mirror."""
        if not url:
            return url
        return url.replace(
            "//datasheet.lcsc.com/",
            "//wmsc.lcsc.com/wmsc/upload/file/pdf/v2/",
        )

    def _parse(self, product: dict, lcsc_sku: str = "") -> PartData:
        """Convert a raw LCSC product dict into a PartData."""
        # Image: prefer big image, fall back to first in list
        image_url = product.get("productImageUrlBig", "")
        if not image_url:
            images = product.get("productImages") or []
            if images:
                image_url = images[0]

        # Datasheet
        datasheet = self._fix_datasheet(product.get("pdfUrl", ""))

        # Parameters
        params = {}
        for p in product.get("paramVOList") or []:
            name = p.get("paramNameEn", "").strip()
            value = p.get("paramValueEn", "").strip()
            if name and value:
                params[name] = value

        # Price breaks  {ladder_qty: unit_price_eur}
        price_breaks = {}
        for pb in product.get("productPriceList") or []:
            try:
                qty = int(pb["ladder"])
                price = float(pb["currencyPrice"])
                price_breaks[qty] = price
            except (KeyError, ValueError, TypeError):
                pass

        sku = lcsc_sku or product.get("productCode", "")

        return PartData(
            mpn=product.get("productModel", ""),
            manufacturer=product.get("brandNameEn", ""),
            description=product.get("productDescEn", ""),
            image_url=image_url,
            datasheet_url=datasheet,
            lcsc_sku=sku,
            package=product.get("encapStandard", ""),
            parameters=params,
            price_breaks=price_breaks,
            currency="EUR",
        )


class MouserFetcher:
    """Fetches part data from the Mouser API v2.

    Requires the ``MOUSER_API_KEY`` environment variable to be set.
    """

    _URL = "https://api.mouser.com/api/v2/search/partnumber"

    def __init__(self):
        self.api_key = os.environ.get("MOUSER_API_KEY")
        if not self.api_key:
            raise EnvironmentError(
                "MOUSER_API_KEY environment variable is not set. "
                "Export it before running this script."
            )

    def fetch(self, mouser_sku: str) -> Optional[PartData]:
        """Return PartData for a Mouser SKU, or None on failure."""
        payload = {
            "SearchByPartRequest": {
                "mouserPartNumber": mouser_sku,
                "partSearchOptions": "Exact",
            }
        }
        try:
            resp = requests.post(
                self._URL,
                params={"apiKey": self.api_key},
                json=payload,
                timeout=15,
            )
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:
            logger.error("Mouser fetch(%s) failed: %s", mouser_sku, exc)
            return None

        parts = body.get("SearchResults", {}).get("Parts") or []
        if not parts:
            logger.warning("Mouser fetch(%s): no results", mouser_sku)
            return None

        p = parts[0]

        # Strip HTML tags from description
        description = re.sub(r"<[^>]+>", "", p.get("Description", ""))

        # Category
        category_path = []
        cat = p.get("Category", "").strip()
        if cat:
            category_path = [cat]

        # Price breaks
        price_breaks = {}
        currency = "EUR"
        for pb in p.get("PriceBreaks") or []:
            try:
                qty = int(pb["Quantity"])
                price = self._parse_price(pb.get("Price", "0"))
                price_breaks[qty] = price
                if pb.get("Currency"):
                    currency = pb["Currency"]
            except (KeyError, ValueError, TypeError):
                pass

        return PartData(
            mpn=p.get("ManufacturerPartNumber", ""),
            manufacturer=p.get("Manufacturer", ""),
            description=description,
            image_url=p.get("ImagePath", ""),
            datasheet_url=p.get("DataSheetUrl", ""),
            mouser_sku=mouser_sku,
            category_path=category_path,
            price_breaks=price_breaks,
            currency=currency,
        )

    @staticmethod
    def _parse_price(price_str: str) -> float:
        """
        Parse a Mouser price string into a float.
        Handles formats like "€ 7,07", "0.1234", "$ 1.23".
        """
        cleaned = re.sub(r"[^\d,.]", "", price_str.strip())
        if not cleaned:
            return 0.0
        last_comma = cleaned.rfind(",")
        last_dot = cleaned.rfind(".")
        if last_comma > last_dot:
            # European format: 7,07 or 1.234,56
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            # US format: remove commas used as thousands separator
            cleaned = cleaned.replace(",", "")
        return float(cleaned)
