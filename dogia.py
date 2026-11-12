"""Dò giá MacBook từ các web trong tab "Web dò giá" rồi ghi vào Google Sheet.

    python dogia.py            chạy thật (cần webhook_url trong config.json)
    python dogia.py --dry      dò nhưng không ghi sheet, chỉ in bảng + lưu CSV
    python dogia.py --only qmacstore.com   chỉ dò 1 web (luôn là --dry)
    python dogia.py --test     tự kiểm tra bộ đọc cấu hình
"""
import csv, html, json, logging, re, sys, time, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlparse

import lxml.html
from scrapling.fetchers import Fetcher

HERE = Path(__file__).parent
NOW = datetime.now(timezone(timedelta(hours=7))).replace(microsecond=0)
logging.getLogger("scrapling").setLevel(logging.ERROR)
MIN_PRICE = 8_000_000  # dưới mức này là tiền trả góp / tiền giảm / tin rác, không phải giá máy

# ---------------------------------------------------------------- tải trang

def fetch(url):
    err = ""
    for _ in range(2):
        try:
            r = Fetcher.get(url, impersonate="chrome", timeout=40, stealthy_headers=True)
            if r.status == 200:
                time.sleep(0.5)  # đừng dội shop
                return r.body.decode("utf-8", "replace") if isinstance(r.body, bytes) else str(r.body)
            err = f"HTTP {r.status}"
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
        time.sleep(3)
    raise RuntimeError(err[:200])

# ---------------------------------------------------------------- đọc cấu hình từ tiêu đề

RAMS = {8, 16, 18, 24, 32, 36, 48, 64, 96, 128}
SSDS = {256, 512, 1024, 2048, 4096, 8192}
CHIP = re.compile(r"\bm([1-6])(?:\s*-?\s*(pro|max|ultra))?\b")
USED = re.compile(r"\bcũ\b|\bcu\b|used|like\s*-?new|likenew|\b9\d\s*%|lướt|\bopen\b|no\s*-?box|refurbished|đã kích hoạt|da kich hoat|trầy|second")
NEW = re.compile(r"new\s*-?seal|nguyên seal|chính hãng|mới 100|\bnew\b|\bmới\b")


def parse_spec(text):
    """'MacBook Pro 14 M1 Pro 32GB/1TB' -> chip 'M1 Pro', ram 32, ssd 1024, screen 14. Khớp đầu tiên thắng."""
    t = text.lower().replace("\xa0", " ")
    s = {"chip": None, "air": bool(re.search(r"\bair\b", t)), "ram": None, "ssd": None, "screen": None, "cpugpu": ""}
    if m := CHIP.search(t):
        s["chip"] = f"M{m[1]}" + (f" {m[2].capitalize()}" if m[2] else "")
    for n, u in re.findall(r"(\d{1,4})\s*(gb|tb|g|t)\b", t):
        v = int(n) * (1024 if u[0] == "t" else 1)
        if v in RAMS and u[0] == "g" and s["ram"] is None:
            s["ram"] = v
        elif v in SSDS and s["ssd"] is None:
            s["ssd"] = v
    if s["ram"] is None or s["ssd"] is None:  # kiểu "32/512" không ghi đơn vị
        if m := re.search(r"\b(8|16|18|24|32|36|48|64|96|128)\s*(?:gb|g)?\s*[/|-]\s*(256|512|1|2|4)\s*(?:gb|tb|g|t)?\b", t):
            s["ram"] = s["ram"] or int(m[1])
            s["ssd"] = s["ssd"] or (int(m[2]) * 1024 if int(m[2]) < 8 else int(m[2]))
    m = (re.search(r"\b(13|14|15|16)(?:[.,']\d)?\s*-?\s*(?:inch|in\b|\"|”|''|′)", t)
         or re.search(r"\b(?:air|pro|neo)\s+(13|14|15|16)\b", t))
    if m:
        s["screen"] = int(m[1])
    cpu = re.search(r"(\d{1,2})\s*(?:c|-?core)?\s*cpu", t)
    gpu = re.search(r"(\d{1,2})\s*(?:c|-?core)?\s*gpu", t)
    s["cpugpu"] = "/".join(x for x in (cpu and f"{cpu[1]}CPU", gpu and f"{gpu[1]}GPU") if x)
    return s


def condition(title, url, seed=""):
    """Shop máy cũ luôn ghi rõ used/like new/9x%/open box; không ghi gì thì coi là máy mới."""
    t = (title + " " + re.sub(r"[-_/]", " ", urlparse(url).path)).lower()
    if USED.search(t):
        return "Cũ"
    if not NEW.search(title.lower()) and USED.search(re.sub(r"[-_/]", " ", urlparse(seed).path).lower()):
        return "Cũ"  # vd bộ sưu tập ".../macbook-pro-m1-cu" của QMac
    return "Mới"


def match(spec, cond, targets):
    for t in targets:
        if (spec["chip"] == t["chip"] and (not t["air"] or spec["air"]) and spec["ram"] == t["ram"]
                and spec["ssd"] == t["ssd"] and t["cond"] in ("Cả hai", cond)
                and (spec["screen"] is None or not t["screens"] or spec["screen"] in t["screens"])):
            return t


NEG = re.compile(r"\b(không|khong|ko|k|chưa|chua|sạch|no|hết|bao|clean)\b")


def keyword_hits(text, keywords):
    """-> (bỏ_qua, [cờ]). ponytail: phủ định chỉ xét vài chữ sát bên ("không dính icloud", "icloud sạch"); cần NLP thì mới nâng cấp."""
    low, skip, flags = text.lower(), False, []
    for kw, action in keywords:
        for m in re.finditer(re.escape(kw.lower()), low):
            if NEG.search(low[max(0, m.start() - 20):m.start()]) or NEG.search(low[m.end():m.end() + 12]):
                continue
            if action == "Bỏ qua":
                skip = True
            elif kw not in flags:
                flags.append(kw)
            break
    return skip, flags

# ---------------------------------------------------------------- các bộ đọc (adapter)

PRICE = re.compile(r"(?<![\d.,])(\d{1,3}(?:[.,]\d{3}){2})(?![\d.,]*\d)")
norm = lambda s: " ".join((s or "").split())
GENS = set()  # đời chip đang theo dõi, vd {"1","2","3","4"}; chỉ mở trang chi tiết cho các đời này


def gen_ok(title):
    m = CHIP.search(title.lower())
    return m is None or m[1] in GENS


def cards(seeds):
    """Bộ đọc chung, không dùng selector: link nào nhắc 'macbook' thì leo lên thẻ cha gần nhất có giá.
    ponytail: thẻ gộp nhiều biến thể (Topzone) chỉ ra biến thể mặc định; cần đủ thì mở từng trang sản phẩm."""
    seen = set()
    for seed in seeds:
        url, page = seed, 1
        while url and page <= 5:
            doc = lxml.html.fromstring(fetch(url))
            for bad in doc.xpath("//script|//style|//noscript"):
                bad.getparent().remove(bad)
            for a in doc.xpath("//a[@href]"):
                if "macbook" not in (norm(a.text_content()) + " " + (a.get("title") or "")).lower():
                    continue
                node, prices = a, []
                for _ in range(5):
                    prices = [p for p in (int(re.sub(r"\D", "", x)) for x in PRICE.findall(node.text_content())) if p >= MIN_PRICE]
                    if prices or node.getparent() is None:
                        break
                    node = node.getparent()
                if not prices or len(node.xpath(".//a[@href]")) > 6:  # >6 link: đã leo quá một thẻ
                    continue
                prices = [p for p in prices if p >= max(prices) / 2]  # bỏ tiền thu cũ / trả trước lẫn trong thẻ
                ok = lambda c: "macbook" in c.lower() and 15 <= len(c) <= 160 and not PRICE.search(c)
                cands = [c for c in (norm(e.text_content()) for e in node.iter("a", "h1", "h2", "h3", "h4", "h5", "p", "span", "div", "strong")) if ok(c)]
                own = norm(a.text_content())  # chữ của chính link là tiêu đề đầy đủ nhất, trừ khi nó ôm cả giá
                title = own if ok(own) else min(cands, key=len) if cands else norm(PRICE.sub(" ", own))[:160]
                href = urljoin(url, a.get("href"))
                if (title, min(prices)) in seen:
                    continue
                seen.add((title, min(prices)))
                yield {"title": title, "spec_text": title + " " + urlparse(href).path.replace("-", " "),
                       "price": min(prices), "list": max(prices), "url": href, "seed": seed}
            page += 1
            nxt = [h for h in doc.xpath("//a/@href") if re.search(rf"(/page/|[?&](page|p|pagenumber)=){page}(\D|$)", h)]
            url = urljoin(url, nxt[0]) if nxt else None


def woo(base):
    """WooCommerce Store API; sản phẩm nhiều biến thể thì đọc bảng biến thể trong trang sản phẩm."""
    for page in range(1, 6):
        body = fetch(f"{base}/wp-json/wc/store/v1/products?search=macbook&per_page=100&page={page}")
        prods = json.loads(body[body.find("["):])  # 2T Mobile chèn rác HTML trước JSON
        for p in prods:
            name = html.unescape(p["name"])
            if "macbook" not in name.lower():
                continue
            if p["type"] != "variable":
                price = int(p["prices"]["price"]) // 10 ** p["prices"]["currency_minor_unit"]
                yield {"title": name, "price": price, "list": int(p["prices"]["regular_price"]) // 10 ** p["prices"]["currency_minor_unit"], "url": p["permalink"]}
                continue
            if not gen_ok(name):
                yield {"title": name, "price": 0, "url": p["permalink"]}  # chỉ để đếm
                continue
            pg = fetch(p["permalink"])
            m = re.search(r'data-product_variations="([^"]*)"', pg)
            variations = json.loads(html.unescape(m[1])) if m else None
            if not isinstance(variations, list):
                raise RuntimeError(f"Không đọc được bảng biến thể: {p['permalink']}")
            # 2T Mobile đặt tên biến thể là "Cấu hình 1..n", mô tả thật nằm ở nhãn riêng
            labels = dict(re.findall(r'data-variation-name="Cấu hình (\d+)".*?option_name">\s*([^<]+?)\s*<', pg, re.S))
            for v in variations:
                if not v.get("is_in_stock", True):
                    continue
                vals = [str(x) for x in v["attributes"].values()]
                label = " ".join(labels.get(mm[1], x) if (mm := re.fullmatch(r"cau-hinh-(\d+)", x)) else x for x in vals)
                yield {"title": f"{label} | {name}", "price": int(v["display_price"]), "list": int(v["display_regular_price"]),
                       "url": p["permalink"] + "?" + urlencode(v["attributes"])}
        if len(prods) < 100:
            break


def sapo(base):
    for page in range(1, 11):
        prods = json.loads(fetch(f"{base}/products.json?limit=250&page={page}")).get("products", [])
        for p in prods:
            if "macbook" in p["name"].lower() and p.get("available"):
                vs = [v for v in p.get("variants", []) if v.get("available")] or [p]
                yield {"title": p["name"], "price": int(min(v["price"] for v in vs)),
                       "list": int(p.get("compare_at_price_max") or 0), "url": f"{base}/{p['alias']}"}
        if len(prods) < 250:
            break


def mac24h(seeds):
    """Mac24h: mỗi sản phẩm có nhiều lựa chọn cấu hình, giá từng lựa chọn nằm trong trang sản phẩm."""
    for c in cards(seeds):
        if not gen_ok(c["title"]):
            yield {**c, "price": 0}
            continue
        pg = fetch(c["url"])
        opts = re.findall(r'data-ca-product-url="([^"]+)".*?ty-product-option-checkbox"></span>(.*?)<br\s*/?>.*?<span>([\d.,]+)</span>', pg, re.S)
        if not opts:
            yield c
        for url, label, price in opts:
            yield {"title": f"{norm(html.unescape(label))} | {c['title']}", "price": int(re.sub(r"\D", "", price)), "url": html.unescape(url), "seed": c["seed"]}


def fpt(_):
    """FPT Shop: trang danh mục chỉ hiện một phần, API danh mục trả đủ (tối đa 50 sản phẩm mỗi lần gọi)."""
    for skip in range(0, 500, 50):
        r = Fetcher.post("https://papi.fptshop.com.vn/gw/v1/public/fulltext-search-service/category", impersonate="chrome", timeout=40,
                         json={"skipCount": skip, "maxResultCount": 50, "sortMethod": "noi-bat", "slug": "may-tinh-xach-tay/apple-macbook", "categoryType": "category"},
                         headers={"Origin": "https://fptshop.com.vn", "Referer": "https://fptshop.com.vn/", "order-channel": "1"})
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status} (API danh mục FPT)")
        items = json.loads(r.body).get("items", [])
        for it in items:
            yield {"title": it["name"], "price": int(it.get("currentPrice") or 0), "list": int(it.get("originalPrice") or 0), "url": "https://fptshop.com.vn/" + it["slug"]}
        if len(items) < 50:
            break


def chotot(targets):
    """Chợ Tốt, chỉ TP.HCM (region 13000), mục Laptop (5030). Giá do người bán tự ghi, chỉ để tham khảo."""
    seen = set()
    for q in sorted({"macbook " + t["name"].lower() for t in targets}):
        for offset in (0, 50):
            ads = json.loads(fetch("https://gateway.chotot.com/v1/public/ad-listing?" + urlencode(
                {"cg": 5030, "region_v2": 13000, "q": q, "limit": 50, "o": offset, "st": "s,k"}))).get("ads", [])
            for a in ads:
                if a["list_id"] in seen:
                    continue
                seen.add(a["list_id"])
                yield {"title": a.get("subject", ""), "text": a.get("subject", "") + "\n" + (a.get("body") or ""),
                       "spec_text": a.get("subject", "") + " " + (a.get("body") or ""),
                       "price": int(a.get("price") or 0), "url": f"https://www.chotot.com/{a['list_id']}.htm",
                       "seller": a.get("account_name", ""), "profile": f"https://www.chotot.com/user/{a['account_oid']}" if a.get("account_oid") else ""}
            if len(ads) < 50:
                break


SITES = {  # tên miền trong tab "Web dò giá" -> (bộ đọc, tham số). Web lạ: thử bộ đọc chung trên chính URL đó.
    "fptshop.com.vn": (fpt, None),
    "thegioididong.com": (cards, ["https://www.thegioididong.com/laptop-apple-macbook", "https://www.thegioididong.com/laptop-apple-macbook-air-m4-series",
                                  "https://www.thegioididong.com/laptop-apple-macbook-pro-m4-series"]),
    "topzone.vn": (cards, ["https://www.topzone.vn/mac"]),
    "cellphones.com.vn": (cards, ["https://cellphones.com.vn/laptop/mac.html", "https://cellphones.com.vn/bo-loc/macbook-air-m4",
                                  "https://cellphones.com.vn/hang-cu/mac.html", "https://cellphones.com.vn/hang-cu/mac/macbook-air.html"]),
    "didongviet.vn": (cards, ["https://didongviet.vn/apple-macbook-imac.html", "https://didongviet.vn/macbook-cu.html"]),
    "shopdunk.com": (cards, ["https://shopdunk.com/macbook-air-m4", "https://shopdunk.com/macbook-air", "https://shopdunk.com/macbook-pro", "https://shopdunk.com/may-cu"]),
    "hoanghamobile.com": (cards, ["https://hoanghamobile.com/laptop/macbook"]),
    "phongvu.vn": (cards, ["https://phongvu.vn/c/macbook"]),
    "dienthoaivui.com.vn": (cards, ["https://dienthoaivui.com.vn/may-cu/macbook-cu"]),
    "2tmobile.com": (woo, "https://2tmobile.com"),
    "mac24h.vn": (mac24h, ["https://mac24h.vn/macbook-cu/", "https://mac24h.vn/macbook-pro-2021-m1pro-m1max/", "https://mac24h.vn/macbook-m2/",
                           "https://mac24h.vn/macbook-m3-2023/", "https://mac24h.vn/macbook-pro-m4-2024/", "https://mac24h.vn/macbook-air-m4-2025/"]),
    "minhtuanmobile.com": (cards, ["https://minhtuanmobile.com/hang-cu/macbook/"]),
    "chotot.com": (chotot, None),
    "macvn.com.vn": (woo, "https://macvn.com.vn"),
    "lamphongstore.com": (sapo, "https://lamphongstore.com"),
    "macshop24h.vn": (sapo, "https://macshop24h.vn"),
    "qmacstore.com": (cards, [f"https://qmacstore.com/collections/macbook-{x}-cu" for x in ("pro-m1", "pro-m2", "pro-m3", "pro-m4", "air-m4")]
                      + ["https://qmacstore.com/macbook-pro", "https://qmacstore.com/macbook-air"]),
    "macone.vn": (cards, [f"https://macone.vn/macbook-{x}/" for x in ("pro-m1", "pro-m2", "pro-m3", "pro-m4", "air-m4")]),
}

# ---------------------------------------------------------------- Google Sheet (qua Apps Script webhook)

CONFIG = json.loads((HERE / "config.json").read_text("utf-8")) if (HERE / "config.json").exists() else {}
CACHE = HERE / "sheet-cache.json"


def webhook(payload):
    req = urllib.request.Request(CONFIG["webhook_url"], json.dumps({"token": CONFIG["token"], **payload}).encode(),
                                 {"Content-Type": "application/json"})
    out = json.loads(urllib.request.urlopen(req, timeout=120).read())
    if out.get("error"):
        raise RuntimeError(f"Webhook: {out['error']}")
    return out


def read_sheet():
    if CONFIG.get("webhook_url"):
        data = webhook({"action": "read"})["tabs"]
        CACHE.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
        return data
    return json.loads(CACHE.read_text("utf-8"))


def num(v):
    return int(re.sub(r"\D", "", str(v).split(",")[0].split(".0")[0]) or 0) if not isinstance(v, (int, float)) else int(v)


def as_dt(v):
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def load_settings(data):
    targets = []
    for r in data["Cấu hình mục tiêu"][1:]:
        if len(r) >= 10 and str(r[9]).strip() == "Có":
            name = str(r[1]).strip()
            targets.append({"code": r[0], "name": name, "chip": re.sub(r"^Air\s+", "", name), "air": name.startswith("Air"), "cond": str(r[2]).strip(),
                            "ram": num(r[3]), "ssd": num(r[4]), "screens": {int(x) for x in re.findall(r"\d+", str(r[7]))}})
    keywords = [(str(r[0]).strip(), str(r[1]).strip()) for r in data["Từ khóa loại trừ"][1:] if r and r[0]]
    cfg = {str(r[0]): r[1] for r in data["Cài đặt"][1:] if len(r) > 1 and r[0]}
    pick = lambda prefix, default: next((v for k, v in cfg.items() if k.startswith(prefix)), default)
    return targets, keywords, {"max_price": num(pick("Giá tối đa", 55_000_000)), "deal": num(pick("Ngưỡng giá Deal", 32_000_000)),
                               "pct": float(pick("% thấp hơn", 0.03)), "window": num(pick("Cửa sổ thống kê", 14)), "end": as_dt(pick("Ngày kết thúc", ""))}

# ---------------------------------------------------------------- chạy

def crawl(data, targets, keywords, st, only=None):
    """-> (rows khớp mục tiêu, trạng thái từng web, lỗi)"""
    GENS.update(CHIP.search(t["chip"].lower())[1] for t in targets)
    found, status, errors = [], {}, []
    for i, site in enumerate(data["Web dò giá"][1:]):
        name, domain = str(site[0]), re.sub(r"^(https?://)?(www\.)?", "", str(site[1]).strip()).split("/")[0]
        if str(site[3]).strip() != "Có" or (only and only != domain):
            continue
        fn, arg = SITES.get(domain, (cards, [f"https://{site[1]}"]))
        n_read = n_match = 0
        err, near = None, []
        try:
            for it in fn(targets if fn is chotot else arg):
                n_read += 1
                if not MIN_PRICE <= it["price"] <= st["max_price"]:
                    continue
                spec = parse_spec(it.get("spec_text", it["title"]))
                cond = "Cũ" if fn is chotot else condition(it["title"], it["url"], it.get("seed", ""))
                t = match(spec, cond, targets)
                skip, flags = keyword_hits(it["title"], keywords)
                if it.get("text"):  # mô tả tự do (Chợ Tốt): chỉ gắn cờ chứ không tự loại, vì người bán hay viết "không icloud"
                    flags += [k for k in keyword_hits(it["text"], [(k, "Gắn cờ") for k, _ in keywords])[1] if k not in flags]
                if (skip or not t) and any(match({**spec, "screen": None}, c, targets) for c in ("Cũ", "Mới")):
                    near.append(f"     ~ gần khớp ({'từ khóa loại trừ' if t else 'lệch tình trạng/màn'}): {cond} {spec['screen']}\" {it['price']:,} | {it['title'][:80]} | {it['url'][-50:]}")
                if not t or skip:
                    continue
                n_match += 1
                lst = it.get("list") or it["price"]
                found.append({"source": name, "code": t["code"], "chip": t["name"], "cpugpu": spec["cpugpu"], "ram": spec["ram"], "ssd": spec["ssd"],
                              "screen": spec["screen"] or "", "cond": cond, "list": max(lst, it["price"]), "price": it["price"],
                              "promo": f"Giảm {lst - it['price']:,}đ".replace(",", ".") if lst > it["price"] else "",
                              "seller": it.get("seller") or name, "url": it["url"], "profile": it.get("profile", ""),
                              "flags": ", ".join(flags), "note": it["title"][:200]})
            if n_read == 0:
                err = "Không đọc được sản phẩm nào (web đổi giao diện hoặc chặn?)"
        except Exception as e:
            err = f"{type(e).__name__}: {e}"[:300]
        if err:
            errors.append([NOW.isoformat(), name, str(site[1]), "Lỗi dò", err])
        status[i] = (("Lỗi: " + err[:80]) if err and not n_match else "OK" if n_match else "Không có tin phù hợp", n_match)
        print(f"  {name:<18} đọc {n_read:>4}  khớp {n_match:>3}  {'LỖI: ' + err if err else ''}", flush=True)
        if "--near" in sys.argv:
            print("\n".join(near))
    return found, status, errors


def build_ops(data, found, status, errors, st):
    """So với Nhật ký hiện có -> danh sách thao tác ghi. Cột: A ID, B ngày ghi, C lần thấy cuối, D mới nhất, ... R giá, U URL."""
    log = data["Nhật ký"]
    now = NOW.isoformat()
    cd = [[r[2] if len(r) > 2 else "", r[3] if len(r) > 3 else ""] for r in log[1:]]  # cột C:D, sửa tại chỗ rồi ghi lại 1 lần
    latest, lowest, max_id = {}, {}, 0
    for i, r in enumerate(log[1:]):
        r = list(r) + [""] * (25 - len(r))
        if m := re.fullmatch(r"TIN-(\d+)", str(r[0])):
            max_id = max(max_id, int(m[1]))
        if r[4] != "FB nhập tay" and num(r[17]):
            lowest[r[6]] = min(lowest.get(r[6], 10**12), num(r[17]))
        if r[4] == "Web tự dò":
            latest[(r[5], r[20], r[6])] = (i, r[0], num(r[17]))
    new_rows, deals = [], []
    for f in found:
        key = (f["source"], f["url"], f["code"])
        note = f["note"]
        if key in latest:
            i, tin, old = latest[key]
            if old == f["price"]:
                cd[i] = [now, "Có"]
                continue
            cd[i][1] = "Không"
            note = f"Giá {'giảm' if f['price'] < old else 'tăng'} từ {old:,}đ. ".replace(",", ".") + note
        else:
            max_id += 1
            tin, old = f"TIN-{max_id:03d}", None
        new_rows.append([tin, now, now, "Có", "Web tự dò", f["source"], f["code"], f["chip"], f["cpugpu"], f["ram"], f["ssd"], f["screen"], f["cond"],
                         "", "", "", f["list"], f["price"], f["promo"], f["seller"], f["url"], f["profile"], "", f["flags"], note])
        low = lowest.get(f["code"])
        reason = (f"Giá ≤ {st['deal']:,}đ".replace(",", ".") if f["price"] <= st["deal"] else
                  f"Thấp hơn {st['pct']:.0%} so với giá thấp nhất từng ghi ({low:,}đ)".replace(",", ".") if low and f["price"] <= low * (1 - st["pct"]) else "")
        if reason and (old is None or f["price"] < old):
            deals.append([now, f["code"], f["price"], f["cond"], f["source"], f["seller"], f["url"], reason, "Mới"])
    cutoff = NOW - timedelta(days=st["window"])
    for i, r in enumerate(log[1:]):  # tin web không còn thấy trong N ngày -> thôi tính vào thống kê
        seen = as_dt(cd[i][0])
        if len(r) > 4 and r[4] == "Web tự dò" and cd[i][1] == "Có" and seen and seen < cutoff:
            cd[i][1] = "Không"
    ops = []
    if cd:
        ops.append({"tab": "Nhật ký", "range": f"C2:D{len(cd) + 1}", "values": cd})
    if new_rows:
        ops.append({"tab": "Nhật ký", "append": True, "values": new_rows})
    if deals:
        ops.append({"tab": "Deal tốt", "append": True, "values": deals})
    if errors:
        ops.append({"tab": "Nhật ký lỗi", "append": True, "values": errors})
    sites = data["Web dò giá"][1:]
    ghi = [[now, *status[i]] if i in status else [(list(s) + [""] * 9)[6], (list(s) + [""] * 9)[7], (list(s) + [""] * 9)[8]] for i, s in enumerate(sites)]
    ops.append({"tab": "Web dò giá", "range": f"G2:I{len(sites) + 1}", "values": ghi})
    return ops, new_rows, deals


def main():
    args = sys.argv[1:]
    only = args[args.index("--only") + 1] if "--only" in args else None
    dry = "--dry" in args or bool(only) or not CONFIG.get("webhook_url")
    data = read_sheet()
    targets, keywords, st = load_settings(data)
    if st["end"] and NOW > st["end"] + timedelta(days=1):
        return print("Đã qua 'Ngày kết thúc theo dõi' trong tab Cài đặt, không dò nữa.")
    print(f"{NOW:%d/%m/%Y %H:%M}  {len(targets)} cấu hình mục tiêu{'  (chạy thử, không ghi sheet)' if dry else ''}")
    found, status, errors = crawl(data, targets, keywords, st, only)
    out = HERE / "ket-qua" / f"{NOW:%Y-%m-%d_%H%M}.csv"
    out.parent.mkdir(exist_ok=True)
    with out.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["code", "price", "list", "cond", "source", "screen", "cpugpu", "flags", "note", "url"], extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(found, key=lambda f: (f["code"], f["price"])))
    if not dry:
        data = read_sheet()  # đọc lại ngay trước khi ghi để không đè sửa đổi trong lúc đang dò
    ops, new_rows, deals = build_ops(data, found, status, errors, st)
    if not dry:
        webhook({"action": "write", "ops": ops})
    print(f"Khớp {len(found)} tin, {len(new_rows)} dòng Nhật ký mới, {len(deals)} deal, {len(errors)} lỗi. CSV: {out}")


def test():
    p = parse_spec
    assert (s := p("MacBook Pro 14 inch 2021 M1 Pro (8C CPU / 14C GPU) 32GB 512GB")) and (s["chip"], s["ram"], s["ssd"], s["screen"], s["cpugpu"]) == ("M1 Pro", 32, 512, 14, "8CPU/14GPU"), s
    assert (s := p("Chip M1 Max 24GPU / 32Gb / 1Tb | MacBook Pro 14 inch M1 2021 – USED")) and (s["chip"], s["ram"], s["ssd"], s["screen"]) == ("M1 Max", 32, 1024, 14), s
    assert (s := p("Macbook Pro 14 inch 2021 Gray (MKGQ3) - M1 Pro 10CPU-16GPU/ 32G/ 1T - Likenew")) and (s["chip"], s["ram"], s["ssd"]) == ("M1 Pro", 32, 1024), s
    assert (s := p("Macbook Pro M1 Max 16” 32/512gb chuẩn sạch, zin")) and (s["chip"], s["ram"], s["ssd"], s["screen"]) == ("M1 Max", 32, 512, 16), s
    assert (s := p("MacBook Air 13 M4 10CPU/10GPU/16GB/512GB")) and (s["chip"], s["air"], s["ram"], s["ssd"], s["screen"]) == ("M4", True, 16, 512, 13), s
    assert (s := p("Macbook Air - M2 / 24Gb / 512Gb - 13'6 inch 2022 - Likenew")) and (s["chip"], s["ram"], s["screen"]) == ("M2", 24, 13), s
    assert p("MacBook Pro M3 Pro 14 inch 36GB 512GB")["chip"] == "M3 Pro"
    assert condition("MacBook Pro M1 14 inch – Used", "https://x/a") == "Cũ"
    assert condition("MacBook Pro 14 M1 Pro 32GB", "https://q/x.html", "https://q/collections/macbook-pro-m1-cu") == "Cũ"
    assert condition("MacBook Air M4 Newseal", "https://x/a", "https://x/macbook-cu") == "Mới"
    assert condition("M4 16GB 512GB (Open) | MacBook Air M4 13 inch – New", "https://x/a") == "Cũ"
    assert condition("M4 / 16GB / 512GB | MACBOOK AIR 13 INCH M4 – 2025", "https://x/macbook-air-13-inch-m4-2025/") == "Mới"
    kws = [("icloud", "Bỏ qua"), ("thay pin", "Gắn cờ")]
    assert keyword_hits("Máy không dính icloud, đã thay pin", kws) == (False, ["thay pin"])
    assert keyword_hits("icloud sạch, chưa thay pin", kws) == (False, [])
    assert keyword_hits("Máy dính iCloud ẩn", kws) == (True, [])
    t = [{"code": "X", "name": "Air M4", "chip": "M4", "air": True, "cond": "Mới", "ram": 16, "ssd": 512, "screens": {13, 15}}]
    assert match(p("MacBook Air 13 M4 16GB/512GB"), "Mới", t) and not match(p("MacBook Pro 14 M4 16GB/512GB"), "Mới", t) and not match(p("MacBook Air 13 M4 16GB/512GB"), "Cũ", t)
    # ghi Nhật ký: tin cũ quá hạn -> "Không"; giá không đổi -> cập nhật lần thấy cuối; giá đổi -> dòng mới cùng ID; tin mới -> ID kế tiếp
    now, old = NOW.isoformat(), (NOW - timedelta(days=20)).isoformat()
    row = lambda tin, seen, price, url: [tin, seen, seen, "Có", "Web tự dò", "A", "X"] + [""] * 10 + [price, "", "", url] + [""] * 4
    data = {"Nhật ký": [["ID"], row("TIN-001", old, 30e6, "u1"), row("TIN-002", old, 31e6, "u2"), row("TIN-003", now, 29e6, "u3")], "Web dò giá": [["h"], ["A", "a.com", "Cũ", "Có"]]}
    f = lambda price, url: {"source": "A", "code": "X", "chip": "", "cpugpu": "", "ram": 32, "ssd": 512, "screen": 14, "cond": "Cũ", "list": price, "price": price,
                            "promo": "", "seller": "A", "url": url, "profile": "", "flags": "", "note": ""}
    ops, new_rows, deals = build_ops(data, [f(31_000_000, "u2"), f(28_000_000, "u3"), f(40_000_000, "u4")], {0: ("OK", 3)}, [], {"deal": 32_000_000, "pct": 0.03, "window": 14})
    assert ops[0]["values"] == [[old, "Không"], [now, "Có"], [now, "Không"]], ops[0]
    assert [r[0] for r in new_rows] == ["TIN-003", "TIN-004"] and len(deals) == 1 and deals[0][2] == 28_000_000, (new_rows, deals)
    print("OK")


if __name__ == "__main__":
    test() if "--test" in sys.argv else main()
