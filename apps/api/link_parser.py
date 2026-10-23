"""商品链接解析。

优先级：
    1. JSON-LD（schema.org/Product）—— 最结构化
    2. OpenGraph / meta（og:title、product:price:amount ...）
    3. <title> + 页面价格正则兜底

只做"信息提取"，不补全商品事实；结果必须人工确认后才入库。
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from apps.providers.base import ErrorCode, ProviderError

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
MAX_BYTES = 3_000_000
PRICE_RE = re.compile(r"(?:¥|￥|US\$|\$|€|£)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)")


def _check_url(url: str) -> None:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ProviderError(ErrorCode.INVALID_PARAM, "链接需以 http:// 或 https:// 开头")
    try:
        infos = socket.getaddrinfo(p.hostname, None)
    except socket.gaierror as exc:
        raise ProviderError(ErrorCode.INVALID_PARAM, f"域名解析失败：{p.hostname}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ProviderError(ErrorCode.INVALID_PARAM, "不允许访问内网地址")


def _walk_jsonld(node: Any):
    if isinstance(node, list):
        for item in node:
            yield from _walk_jsonld(item)
    elif isinstance(node, dict):
        t = node.get("@type")
        types = t if isinstance(t, list) else [t]
        if any(str(x).lower() == "product" for x in types):
            yield node
        for v in node.values():
            yield from _walk_jsonld(v)


def _jsonld_products(soup: BeautifulSoup):
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text() or ""
        try:
            data = json.loads(raw)
        except Exception:
            continue
        yield from _walk_jsonld(data)


def _meta(soup: BeautifulSoup, *keys: str) -> str:
    for key in keys:
        tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
        if tag and tag.get("content"):
            return str(tag["content"]).strip()
    return ""


def _first_offer(offers: Any) -> dict[str, Any]:
    if isinstance(offers, list) and offers:
        offers = offers[0]
    return offers if isinstance(offers, dict) else {}


def _price_from_text(text: str) -> float | None:
    m = PRICE_RE.search(text or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _attrs_from_jsonld(product: dict[str, Any]) -> dict[str, str]:
    attrs: dict[str, str] = {}
    props = product.get("additionalProperty")
    if isinstance(props, dict):
        props = [props]
    if isinstance(props, list):
        for p in props:
            if not isinstance(p, dict):
                continue
            name, value = p.get("name"), p.get("value")
            if name and value is not None:
                attrs[str(name).strip()] = str(value).strip()
    for key, label in (
        ("color", "颜色"),
        ("material", "材质"),
        ("size", "尺寸"),
        ("weight", "重量"),
        ("sku", "SKU"),
    ):
        if product.get(key) and label not in attrs:
            attrs[label] = str(product[key]).strip()
    brand = product.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name")
    if brand:
        attrs.setdefault("品牌", str(brand).strip())
    return attrs


def _extract_product(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html[:MAX_BYTES], "html.parser")

    name = ""
    description = ""
    image = ""
    price: float | None = None
    currency = ""
    attrs: dict[str, str] = {}
    warnings: list[str] = []
    source = ""

    for product in _jsonld_products(soup):
        name = str(product.get("name") or "").strip()
        description = str(product.get("description") or "").strip()
        img = product.get("image")
        if isinstance(img, list):
            img = img[0] if img else ""
        if isinstance(img, dict):
            img = img.get("url") or ""
        image = str(img or "").strip()
        offer = _first_offer(product.get("offers"))
        raw_price = offer.get("price") or offer.get("lowPrice")
        if raw_price is not None:
            try:
                price = float(str(raw_price).replace(",", ""))
            except ValueError:
                price = None
        currency = str(offer.get("priceCurrency") or "").strip()
        attrs = _attrs_from_jsonld(product)
        if name:
            source = "json-ld"
            break

    if not name:
        name = _meta(soup, "og:title", "twitter:title") or (soup.title.get_text(strip=True) if soup.title else "")
        description = description or _meta(soup, "og:description", "description")
        image = image or _meta(soup, "og:image", "twitter:image")
        source = "opengraph" if _meta(soup, "og:title") else "html"

    if price is None:
        raw = _meta(soup, "product:price:amount", "og:price:amount")
        if raw:
            try:
                price = float(raw.replace(",", ""))
            except ValueError:
                price = None
            currency = currency or _meta(soup, "product:price:currency", "og:price:currency")
            source = source or "opengraph"

    if price is None:
        price = _price_from_text(soup.get_text(" ", strip=True)[:20000])
        if price is not None:
            warnings.append("价格来自页面文本匹配，需人工核对")

    if not currency:
        if re.search(r"[¥￥]", html):
            currency = "CNY"
        elif "£" in html:
            currency = "GBP"
        elif "€" in html:
            currency = "EUR"
        elif "$" in html:
            currency = "USD"

    if not name:
        raise ProviderError(
            ErrorCode.PROVIDER_ERROR,
            "没能从页面提取到商品名，站点可能需要登录或由 JS 渲染",
        )

    if not attrs:
        warnings.append("未提取到结构化参数，可在录入后手动补齐")
    if price is None:
        warnings.append("未提取到价格")

    return {
        "source": source or "html",
        "product": {
            "name": name[:200],
            "price": price,
            "currency": currency,
            "description": description[:1000],
            "image": image,
            "attributes": attrs,
        },
        "warnings": warnings,
    }


async def parse_product_link(url: str) -> dict[str, Any]:
    """抓取并解析商品链接。SSRF 防护 + 超时 + 明确错误。"""
    url = (url or "").strip()
    _check_url(url)

    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": UA,
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
            )
    except httpx.TimeoutException as exc:
        raise ProviderError(ErrorCode.TIMEOUT, "抓取链接超时（20s）", retryable=True) from exc
    except httpx.HTTPError as exc:
        raise ProviderError(ErrorCode.PROVIDER_ERROR, f"抓取链接失败：{exc}", retryable=True) from exc

    if resp.status_code in (401, 403):
        raise ProviderError(
            ErrorCode.PROVIDER_ERROR,
            f"站点拒绝抓取（HTTP {resp.status_code}），请手动录入或换用可访问的链接",
            status=resp.status_code,
        )
    if resp.status_code >= 400:
        raise ProviderError(
            ErrorCode.PROVIDER_ERROR, f"抓取失败 HTTP {resp.status_code}", status=resp.status_code
        )

    result = _extract_product(resp.text)
    result["url"] = str(resp.url)
    return result
