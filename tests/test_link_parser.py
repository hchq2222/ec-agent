"""商品链接解析的离线单测（不访问网络）。"""

from __future__ import annotations

from apps.api.link_parser import _extract_product


def test_jsonld_product():
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"Product","name":"测试洁面乳",
     "description":"温和氨基酸","image":"https://cdn.example.com/a.png",
     "brand":{"name":"森序"},
     "offers":{"@type":"Offer","price":"129.00","priceCurrency":"CNY"},
     "additionalProperty":[{"@type":"PropertyValue","name":"容量","value":"150ml"}]}
    </script>
    </head><body></body></html>
    """
    data = _extract_product(html)
    p = data["product"]
    assert data["source"] == "json-ld"
    assert p["name"] == "测试洁面乳"
    assert p["price"] == 129.0
    assert p["currency"] == "CNY"
    assert p["attributes"]["容量"] == "150ml"
    assert p["attributes"]["品牌"] == "森序"


def test_opengraph_fallback():
    html = """
    <html><head>
      <meta property="og:title" content="OG 商品">
      <meta property="og:description" content="描述">
      <meta property="product:price:amount" content="59.9">
      <meta property="product:price:currency" content="CNY">
    </head><body></body></html>
    """
    data = _extract_product(html)
    assert data["product"]["name"] == "OG 商品"
    assert data["product"]["price"] == 59.9
    assert data["source"] == "opengraph"


def test_title_and_price_fallback():
    html = "<html><head><title>普通商品</title></head><body>价格 ¥88.50 元</body></html>"
    data = _extract_product(html)
    assert data["product"]["name"] == "普通商品"
    assert data["product"]["price"] == 88.5
    assert any("文本匹配" in w for w in data["warnings"])
