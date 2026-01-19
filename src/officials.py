import csv
import io
import os
import re
from urllib.parse import unquote, urlparse, urljoin

import geopandas as gpd
import pandas as pd
import requests


def _pick_value(data, keys):
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def _normalize_text(value, default=""):
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _normalize_ward_key(value):
    text = _normalize_text(value)
    if not text:
        return ""
    text = re.sub(r"\(.*?\)", "", text)
    text = text.replace("&", "AND")
    text = re.sub(r"[^A-Z0-9]+", "", text.upper())
    for suffix in ("DISTRICTELECTORALAREA", "DEA"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    if text.endswith("WARD"):
        text = text[:-4]
    return text


def _normalize_person_key(value):
    text = _normalize_text(value)
    if not text:
        return ""
    text = re.sub(r"\(.*?\)", "", text)
    text = re.sub(
        r"\b(cllr|councillor|councilor|alderman|dr|mr|mrs|ms|sir|lady)\b",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"[^A-Z0-9]+", "", text.upper())
    return text


def _clean_email(value):
    text = _normalize_text(value)
    if not text:
        return ""
    if text.lower().startswith("mailto:"):
        text = text[7:]
    text = text.split("?")[0].strip()
    return unquote(text)


def _clean_phone(value):
    text = _normalize_text(value)
    if not text:
        return ""
    lowered = text.lower()
    if lowered.startswith("tel:"):
        text = text[4:]
    text = unquote(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_email_from_text(value):
    text = _normalize_text(value)
    if not text:
        return ""
    match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    return match.group(0) if match else ""


def _ssl_verify_for_url(url):
    host = urlparse(url).hostname or ""
    if host.lower() == "mid-ulster.cmis-ni.org":
        return False
    return True


def _build_dea_mapping(ward_shapefile_path, dea_shapefile_path):
    if not ward_shapefile_path or not dea_shapefile_path:
        return {}
    if not os.path.exists(ward_shapefile_path) or not os.path.exists(dea_shapefile_path):
        return {}

    wards = gpd.read_file(ward_shapefile_path)
    dea = gpd.read_file(dea_shapefile_path)
    if "WardCode" not in wards.columns or "DEA" not in dea.columns:
        return {}

    wards = wards[["WardCode", "geometry"]].copy()
    wards["WardCode"] = wards["WardCode"].astype(str).str.strip()
    wards = wards[wards["WardCode"] != ""]
    wards = wards.dissolve(by="WardCode", as_index=False)

    dea = dea[["DEA", "geometry"]].copy()
    dea["DEA"] = dea["DEA"].astype(str).str.strip()
    dea = dea[dea["DEA"] != ""]

    if wards.crs and dea.crs and wards.crs != dea.crs:
        dea = dea.to_crs(wards.crs)

    ward_centroids = wards.copy()
    ward_centroids["geometry"] = ward_centroids.geometry.centroid
    joined = gpd.sjoin(ward_centroids, dea, how="left", predicate="within")

    ward_to_dea = (
        joined.dropna(subset=["DEA"])
        .set_index("WardCode")["DEA"]
        .to_dict()
    )

    mapping = {}
    for ward_code, dea_name in ward_to_dea.items():
        dea_key = _normalize_ward_key(dea_name)
        if not dea_key:
            continue
        mapping.setdefault(dea_key, []).append(ward_code)
    return mapping


def _extract_records(payload):
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("officials", "items", "data"):
            if key in payload and isinstance(payload[key], list):
                return [item for item in payload[key] if isinstance(item, dict)]
        flattened = []
        for key, value in payload.items():
            if not isinstance(value, list):
                continue
            for item in value:
                if not isinstance(item, dict):
                    continue
                clone = dict(item)
                if not _pick_value(clone, ["ward_code", "WardCode", "ward", "Ward"]):
                    clone["ward_code"] = key
                flattened.append(clone)
        return flattened
    return []


def _load_payload(api_url, api_key=None, timeout=15):
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    response = requests.get(api_url, headers=headers, timeout=timeout)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").lower()
    if "json" in content_type or response.text.lstrip().startswith(("{", "[")):
        return response.json()
    return list(csv.DictReader(io.StringIO(response.text)))


def _read_html_tables(html):
    try:
        return pd.read_html(io.StringIO(html))
    except ValueError:
        return []
    except ImportError:
        return []


def _select_councillor_table(tables):
    for table in tables:
        if not isinstance(table, pd.DataFrame) or table.empty:
            continue
        columns = [str(col).strip() for col in table.columns]
        lower = [col.lower() for col in columns]
        ward_idx = next((idx for idx, col in enumerate(lower) if "ward" in col), None)
        if ward_idx is None:
            continue
        name_idx = next(
            (
                idx
                for idx, col in enumerate(lower)
                if any(key in col for key in ("councillor", "councilor", "member", "name", "representative"))
            ),
            None,
        )
        if name_idx is None:
            continue
        name_col = columns[name_idx]
        if table[name_col].astype(str).str.contains(r"[A-Za-z]", regex=True).any():
            return table
    return None


def _extract_tables_with_mailto(html):
    try:
        from lxml import html as lxml_html
    except ImportError:
        return []

    try:
        tree = lxml_html.fromstring(html)
    except Exception:
        return []

    frames = []
    for table in tree.xpath("//table"):
        rows = []
        headers = None
        for tr in table.xpath(".//tr"):
            cells = tr.xpath("./th|./td")
            if not cells:
                continue
            values = [_normalize_text(" ".join(cell.itertext())) for cell in cells]
            mailto = ""
            tel = ""
            for link in tr.xpath(".//a[starts-with(@href, 'mailto:')]"):
                mailto = _clean_email(link.get("href"))
                if mailto:
                    break
            for link in tr.xpath(".//a[starts-with(@href, 'tel:')]"):
                tel = _clean_phone(link.get("href"))
                if tel:
                    break
            if headers is None and tr.xpath("./th"):
                headers = values
                continue
            rows.append({"values": values, "mailto": mailto, "tel": tel})
        if not rows:
            continue
        max_cols = max(len(row["values"]) for row in rows)
        if headers and len(headers) == max_cols:
            columns = headers
        else:
            columns = [f"column_{idx + 1}" for idx in range(max_cols)]
        data = [
            row["values"] + [""] * (max_cols - len(row["values"])) for row in rows
        ]
        frame = pd.DataFrame(data, columns=columns)
        frame["_mailto"] = [row["mailto"] for row in rows]
        frame["_tel"] = [row["tel"] for row in rows]
        frames.append(frame)
    return frames


def _find_column(columns, keys):
    for col in columns:
        lowered = col.lower()
        if any(key in lowered for key in keys):
            return col
    return None


def _extract_email_entries_from_table(table, source_url):
    if not isinstance(table, pd.DataFrame) or table.empty:
        return []
    columns = [str(col).strip() for col in table.columns]
    name_col = _find_column(
        columns, ["councillor", "councilor", "member", "name", "representative"]
    )
    ward_col = _find_column(
        columns, ["ward", "dea", "electoral", "area", "district"]
    )
    party_col = _find_column(columns, ["party"])
    email_col = _find_column(columns, ["email", "e-mail"])
    phone_col = _find_column(columns, ["phone", "tel", "telephone", "mobile"])

    if not name_col:
        return []

    entries = []
    for record in table.to_dict(orient="records"):
        name_cell = record.get(name_col)
        if not _normalize_text(name_cell):
            continue
        ward_name = _normalize_text(record.get(ward_col)) if ward_col else ""
        party_value = _normalize_text(record.get(party_col)) if party_col else ""
        email_value = _normalize_text(record.get(email_col)) if email_col else ""
        phone_value = _normalize_text(record.get(phone_col)) if phone_col else ""
        if not email_value:
            email_value = _normalize_text(record.get("_mailto"))
        if not phone_value:
            phone_value = _normalize_text(record.get("_tel"))
        if not email_value:
            email_value = _extract_email_from_text(" ".join(str(v) for v in record.values()))
        email_value = _clean_email(email_value)
        phone_value = _clean_phone(phone_value)
        if not email_value and not phone_value:
            continue
        for name, extracted_party in _split_official_names(name_cell):
            entries.append(
                {
                    "official_name": name,
                    "ward_name": ward_name,
                    "party": party_value or extracted_party,
                    "email": email_value,
                    "phone": phone_value,
                    "source": source_url,
                }
            )
    return entries


def _extract_text_content(html):
    try:
        from lxml import html as lxml_html
    except ImportError:
        return re.sub(r"<[^>]+>", " ", html)

    try:
        tree = lxml_html.fromstring(html)
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)
    return " ".join(tree.itertext())


def _guess_name_from_text(text):
    match = re.search(
        r"\b(?:councillor|councilor|cllr|alderman)\s+([A-Za-z\\u00C0-\\u017F' -]{3,60})",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    return _normalize_text(match.group(1))


def _guess_ward_from_text(text):
    match = re.search(
        r"\b(?:ward|dea|district electoral area)\s*:?\\s*([A-Za-z\\u00C0-\\u017F' -]{2,60})",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    return _normalize_text(match.group(1))


def _guess_party_from_text(text):
    match = re.search(
        r"\bparty\s*:?\\s*([A-Za-z\\u00C0-\\u017F' -]{2,60})",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    return _normalize_text(match.group(1))


def _strip_title_prefix(name):
    if not name:
        return ""
    return re.sub(
        r"^(councillor|councilor|cllr|alderman)\s+",
        "",
        name,
        flags=re.IGNORECASE,
    ).strip()


def _strip_party_from_name(name, party):
    if not name or not party:
        return name
    party_text = _normalize_text(party)
    if not party_text:
        return name
    pattern = re.compile(
        r"\s*-\s*%s\s*$" % re.escape(party_text), flags=re.IGNORECASE
    )
    cleaned = pattern.sub("", name).strip()
    if cleaned != name:
        return cleaned
    lower = name.lower()
    party_lower = party_text.lower()
    if lower.endswith(party_lower):
        return name[: -len(party_text)].strip()
    return name

def _extract_contacts_from_html(html, source_url):
    try:
        from lxml import html as lxml_html
    except ImportError:
        return []

    try:
        tree = lxml_html.fromstring(html)
    except Exception:
        return []

    combined = {}
    for link in tree.xpath("//a[starts-with(@href, 'mailto:') or starts-with(@href, 'tel:')]"):
        href = link.get("href", "")
        email = _clean_email(href) if href.lower().startswith("mailto:") else ""
        phone = _clean_phone(href) if href.lower().startswith("tel:") else ""
        if not email and not phone:
            continue
        container = link
        for _ in range(4):
            parent = container.getparent()
            if parent is None:
                break
            if str(parent.tag).lower() in ("div", "li", "article", "section", "tr"):
                container = parent
                break
            container = parent
        text = _normalize_text(" ".join(container.itertext()))
        name = _guess_name_from_text(text)
        party = _guess_party_from_text(text)
        ward = _guess_ward_from_text(text)

        title_nodes = container.xpath(
            ".//*[contains(translate(@class, 'TITLE', 'title'), 'title')][1] | .//h1[1] | .//h2[1] | .//h3[1] | .//h4[1]"
        )
        if title_nodes:
            title_text = _normalize_text(" ".join(title_nodes[0].itertext()))
            if title_text:
                name = title_text
            if not party:
                span_texts = [
                    _normalize_text(" ".join(node.itertext()))
                    for node in title_nodes[0].xpath(".//span")
                ]
                span_texts = [text for text in span_texts if text]
                if span_texts:
                    party = span_texts[-1]
            if name:
                span_texts = [
                    _normalize_text(" ".join(node.itertext()))
                    for node in title_nodes[0].xpath(".//span")
                ]
                for span_text in span_texts:
                    if span_text:
                        name = name.replace(span_text, "").strip()

        if not name:
            name = _guess_name_from_text(_normalize_text(" ".join(link.itertext())))

        if not name:
            continue

        clean_name = _strip_title_prefix(name)
        split = _split_official_names(clean_name)
        if split:
            clean_name, extracted_party = split[0]
            if not party:
                party = extracted_party
        if party:
            clean_name = _strip_party_from_name(clean_name, party)

        key = (
            _normalize_person_key(clean_name),
            _normalize_ward_key(ward),
            _normalize_text(party).lower(),
        )
        if key not in combined:
            combined[key] = {
                "official_name": clean_name,
                "ward_name": ward,
                "party": party,
                "email": "",
                "phone": "",
                "source": source_url,
            }
        if email and not combined[key]["email"]:
            combined[key]["email"] = email
        if phone and not combined[key]["phone"]:
            combined[key]["phone"] = phone
    return list(combined.values())


def _scrape_modern_gov_members(
    index_url, timeout=15, debug_dir=None, headers=None, verify=True
):
    response = requests.get(
        index_url, timeout=timeout, headers=headers, verify=verify
    )
    response.raise_for_status()
    html = response.text

    member_links = {}
    try:
        from lxml import html as lxml_html

        tree = lxml_html.fromstring(html)
        for link in tree.xpath("//a[@href]"):
            href = link.get("href", "")
            if "mguserinfo.aspx" not in href.lower():
                continue
            name = _normalize_text(" ".join(link.itertext()))
            member_links[urljoin(index_url, href)] = name
    except Exception:
        for href in re.findall(r'href=["\']([^"\']+mgUserInfo\\.aspx[^"\']*)["\']', html, flags=re.IGNORECASE):
            member_links[urljoin(index_url, href)] = ""

    rows = []
    for member_url, fallback_name in member_links.items():
        member_resp = requests.get(
            member_url, timeout=timeout, headers=headers, verify=verify
        )
        member_resp.raise_for_status()
        member_html = member_resp.text
        if debug_dir:
            parsed = urlparse(member_url)
            slug = re.sub(r"[^A-Za-z0-9]+", "_", parsed.path or "member")
            filename = f"council_member_{slug}.html"
            with open(
                os.path.join(debug_dir, filename),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write(member_html)

        member_rows = []
        tables = _extract_tables_with_mailto(member_html)
        if not tables:
            tables = _read_html_tables(member_html)
        for table in tables:
            member_rows.extend(_extract_email_entries_from_table(table, member_url))
        if not member_rows:
            member_rows.extend(_extract_contacts_from_html(member_html, member_url))

        if not member_rows:
            text = _extract_text_content(member_html)
            email = _extract_email_from_text(text)
            if not email:
                continue
            name = _guess_name_from_text(text) or fallback_name
            if not name:
                continue
            member_rows.append(
                {
                    "official_name": _strip_title_prefix(name),
                    "ward_name": _guess_ward_from_text(text),
                    "party": _guess_party_from_text(text),
                    "email": email,
                    "phone": _clean_phone(""),
                    "source": member_url,
                }
            )
        rows.extend(member_rows)
    return rows


def fetch_council_site_emails(sources, timeout=15, debug_dir=None):
    if not sources:
        return []

    if debug_dir:
        os.makedirs(debug_dir, exist_ok=True)

    headers = {"User-Agent": "crimemap/1.0"}
    rows = []
    stats = []
    for url in sources:
        before = len(rows)
        try:
            is_local = os.path.exists(url)
            verify = _ssl_verify_for_url(url) if not is_local else True
            if is_local:
                with open(url, "r", encoding="utf-8", errors="ignore") as handle:
                    html = handle.read()
            else:
                response = requests.get(
                    url, timeout=timeout, headers=headers, verify=verify
                )
                response.raise_for_status()
                html = response.text
            if debug_dir:
                parsed = urlparse(url)
                slug = re.sub(r"[^A-Za-z0-9]+", "_", parsed.netloc or parsed.path)
                filename = f"council_emails_{slug or 'site'}.html"
                with open(
                    os.path.join(debug_dir, filename),
                    "w",
                    encoding="utf-8",
                ) as handle:
                    handle.write(html)

            tables = _extract_tables_with_mailto(html)
            if not tables:
                tables = _read_html_tables(html)
            for table in tables:
                rows.extend(_extract_email_entries_from_table(table, url))
            rows.extend(_extract_contacts_from_html(html, url))
            if len(rows) == before and "mgmemberindex.aspx" in url.lower():
                rows.extend(
                    _scrape_modern_gov_members(
                        url,
                        timeout=timeout,
                        debug_dir=debug_dir,
                        headers=headers,
                        verify=verify,
                    )
                )

            stats.append(
                {
                    "url": url,
                    "tables": len(tables),
                    "rows": len(rows) - before,
                    "error": "",
                }
            )
        except Exception as exc:
            stats.append(
                {
                    "url": url,
                    "tables": 0,
                    "rows": 0,
                    "error": str(exc),
                }
            )
            continue

    if debug_dir:
        pd.DataFrame(stats).to_csv(
            os.path.join(debug_dir, "council_email_summary.csv"),
            index=False,
        )
    return rows


def _apply_email_overrides(rows, email_entries, dea_by_ward_code=None):
    if not rows or not email_entries:
        return 0
    email_by_key = {}
    email_by_name = {}
    phone_by_key = {}
    phone_by_name = {}
    name_counts = {}
    name_key_seen = set()
    for entry in email_entries:
        email = _clean_email(entry.get("email"))
        phone = _clean_phone(entry.get("phone"))
        if not email:
            email = ""
        name_key = _normalize_person_key(entry.get("official_name"))
        if not name_key:
            continue
        ward_key = _normalize_ward_key(entry.get("ward_name"))
        name_key_id = (name_key, ward_key)
        if name_key_id not in name_key_seen:
            name_counts[name_key] = name_counts.get(name_key, 0) + 1
            name_key_seen.add(name_key_id)
        if ward_key:
            if email:
                email_by_key[(name_key, ward_key)] = email
            if phone:
                phone_by_key[(name_key, ward_key)] = phone
        if email and name_key not in email_by_name:
            email_by_name[name_key] = email
        if phone and name_key not in phone_by_name:
            phone_by_name[name_key] = phone

    updates = 0
    for row in rows:
        name_key = _normalize_person_key(row.get("official_name"))
        if not name_key:
            continue
        has_email = bool(_normalize_text(row.get("email")))
        has_phone = bool(_normalize_text(row.get("phone")))
        ward_keys = []
        if dea_by_ward_code:
            dea_key = dea_by_ward_code.get(row.get("ward_code", ""), "")
            if dea_key:
                ward_keys.append(dea_key)
        name_key_value = _normalize_ward_key(row.get("ward_name"))
        if name_key_value and name_key_value not in ward_keys:
            ward_keys.append(name_key_value)
        email = None
        phone = None
        for ward_key in ward_keys:
            if not email:
                email = email_by_key.get((name_key, ward_key))
            if not phone:
                phone = phone_by_key.get((name_key, ward_key))
            if email and phone:
                break
        if name_counts.get(name_key, 0) == 1:
            if not email:
                email = email_by_name.get(name_key)
            if not phone:
                phone = phone_by_name.get(name_key)
        if email and not has_email:
            row["email"] = email
            updates += 1
        if phone and not has_phone:
            row["phone"] = phone
            updates += 1
    return updates


def _column_text_series(table, column):
    return table[column].astype(str).fillna("")


def _find_best_ward_column(table, ward_keys):
    best_col = None
    best_matches = 0
    row_count = len(table.index)
    if row_count == 0:
        return None, 0
    for col in table.columns:
        values = _column_text_series(table, col)
        matches = values.map(_normalize_ward_key).isin(ward_keys).sum()
        if matches > best_matches:
            best_matches = matches
            best_col = col
    threshold = max(2, min(5, max(1, row_count // 3)))
    if best_matches < threshold:
        return None, best_matches
    return best_col, best_matches


def _split_official_names(value):
    text = _normalize_text(value)
    if not text:
        return []
    if text.strip().lower() in ("vacant", "vacancy", "none"):
        return []
    parts = re.split(r"\s*(?:,|;|/|&|\n|\r| and )\s*", text, flags=re.IGNORECASE)
    results = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        party = ""
        if "(" in part and ")" in part:
            match = re.search(r"\(([^)]+)\)", part)
            if match:
                party = match.group(1).strip()
                part = part[: match.start()].strip()
        elif " - " in part:
            name_part, party_part = part.split(" - ", 1)
            part = name_part.strip()
            party = party_part.strip()
        if part:
            results.append((part, party))
    return results


def fetch_opencouncildata_officials(
    base_url,
    council_start,
    council_end,
    year,
    ward_analysis=None,
    ward_shapefile_path=None,
    dea_shapefile_path=None,
    timeout=15,
    debug_dir=None,
    council_email_sources=None,
    council_email_debug_dir=None,
):
    ward_name_map = {}
    if ward_analysis is not None:
        if "WardCode" in ward_analysis.columns and "WARDNAME" in ward_analysis.columns:
            ward_name_map = (
                ward_analysis[["WardCode", "WARDNAME"]]
                .dropna(subset=["WardCode"])
                .assign(WardCode=lambda df: df["WardCode"].astype(str).str.strip())
                .assign(WARDNAME=lambda df: df["WARDNAME"].astype(str).str.strip())
                .set_index("WardCode")["WARDNAME"]
                .to_dict()
            )

    ward_code_by_key = {
        _normalize_ward_key(name): code for code, name in ward_name_map.items() if name
    }
    ward_codes = set(ward_name_map.keys()) if ward_name_map else None
    ward_codes_by_dea = _build_dea_mapping(ward_shapefile_path, dea_shapefile_path)
    dea_by_ward_code = {}
    for dea_key, ward_list in ward_codes_by_dea.items():
        for ward_code in ward_list:
            dea_by_ward_code[ward_code] = dea_key
    ward_keys = set(ward_code_by_key.keys()) | set(ward_codes_by_dea.keys())

    if debug_dir:
        os.makedirs(debug_dir, exist_ok=True)

    council_email_rows = []
    if council_email_sources:
        try:
            council_email_rows = fetch_council_site_emails(
                council_email_sources,
                timeout=timeout,
                debug_dir=council_email_debug_dir,
            )
        except Exception:
            council_email_rows = []

    rows = []
    stats = []
    for council_id in range(council_start, council_end + 1):
        url = f"{base_url}?c={council_id}&y={year}"
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        html = response.text
        before_rows = len(rows)
        if debug_dir:
            with open(
                os.path.join(debug_dir, f"opencouncildata_{council_id}.html"),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write(html)

        tables = _read_html_tables(html)
        best_table = None
        best_meta = None
        for table in tables:
            if not isinstance(table, pd.DataFrame) or table.empty:
                continue
            ward_col, ward_matches = _find_best_ward_column(table, ward_keys)
            if not ward_col:
                continue
            if not best_meta or ward_matches > best_meta["ward_matches"]:
                best_table = table
                best_meta = {"ward_col": ward_col, "ward_matches": ward_matches}

        if best_table is None:
            stats.append(
                {
                    "council_id": council_id,
                    "url": url,
                    "tables": len(tables),
                    "ward_matches": 0,
                    "rows": 0,
                }
            )
            continue

        table = best_table
        ward_col = best_meta["ward_col"]
        columns = {str(col).strip().lower(): col for col in table.columns}
        name_candidates = [
            columns[col]
            for col in columns
            if any(
                key in col
                for key in (
                    "councillor",
                    "councilor",
                    "cllr",
                    "member",
                    "name",
                    "representative",
                )
            )
        ]
        if ward_col in name_candidates:
            name_candidates = [col for col in name_candidates if col != ward_col]
        if not name_candidates:
            alt_candidates = [
                col for col in table.columns if col != ward_col
            ]
            alt_candidates = sorted(
                alt_candidates,
                key=lambda col: _column_text_series(table, col)
                .str.contains(r"[A-Za-z]", regex=True)
                .sum(),
                reverse=True,
            )
            if alt_candidates:
                name_candidates = [alt_candidates[0]]

        party_col = next((columns[col] for col in columns if "party" in col), None)
        email_col = next((columns[col] for col in columns if "email" in col), None)
        phone_col = next(
            (columns[col] for col in columns if "phone" in col or "tel" in col),
            None,
        )
        role_col = next((columns[col] for col in columns if "role" in col or "position" in col), None)

        for record in table.to_dict(orient="records"):
            ward_name = _normalize_text(record.get(ward_col))
            if not ward_name:
                continue
            ward_key = _normalize_ward_key(ward_name)
            ward_code = ward_code_by_key.get(ward_key)
            ward_targets = [ward_code] if ward_code else ward_codes_by_dea.get(ward_key, [])
            if not ward_targets:
                continue

            party_value = _normalize_text(record.get(party_col)) if party_col else ""
            role_value = _normalize_text(record.get(role_col)) if role_col else ""
            email_value = _normalize_text(record.get(email_col)) if email_col else ""
            phone_value = _normalize_text(record.get(phone_col)) if phone_col else ""

            name_cells = [record.get(col) for col in name_candidates] if name_candidates else []
            if not name_cells:
                continue
            for cell in name_cells:
                for name, extracted_party in _split_official_names(cell):
                    final_party = party_value or extracted_party
                    for target_code in ward_targets:
                        if ward_codes and target_code not in ward_codes:
                            continue
                        rows.append(
                            {
                                "ward_code": target_code,
                                "ward_name": ward_name_map.get(target_code, ward_name),
                                "official_name": name,
                                "role": role_value,
                                "party": final_party,
                                "email": email_value,
                                "phone": phone_value,
                                "source": url,
                                "source_id": str(council_id),
                            }
                        )

        stats.append(
            {
                "council_id": council_id,
                "url": url,
                "tables": len(tables),
                "ward_matches": best_meta["ward_matches"],
                "rows": len(rows) - before_rows,
            }
        )

    if council_email_rows:
        _apply_email_overrides(rows, council_email_rows, dea_by_ward_code)

    df = pd.DataFrame(rows)
    if debug_dir:
        pd.DataFrame(stats).to_csv(
            os.path.join(debug_dir, "opencouncildata_summary.csv"),
            index=False,
        )
    if df.empty:
        return df
    return df.sort_values(["ward_code", "official_name"]).reset_index(drop=True)


def fetch_ward_officials(
    api_url,
    ward_analysis=None,
    api_key=None,
    timeout=15,
):
    if not api_url:
        return None

    ward_name_map = {}
    if ward_analysis is not None:
        if "WardCode" in ward_analysis.columns and "WARDNAME" in ward_analysis.columns:
            ward_name_map = (
                ward_analysis[["WardCode", "WARDNAME"]]
                .dropna(subset=["WardCode"])
                .assign(WardCode=lambda df: df["WardCode"].astype(str).str.strip())
                .assign(WARDNAME=lambda df: df["WARDNAME"].astype(str).str.strip())
                .set_index("WardCode")["WARDNAME"]
                .to_dict()
            )

    ward_code_by_name = {
        _normalize_ward_key(name): code for code, name in ward_name_map.items() if name
    }
    ward_codes = set(ward_name_map.keys()) if ward_name_map else None

    payload = _load_payload(api_url, api_key=api_key, timeout=timeout)
    records = _extract_records(payload)
    if not records:
        return pd.DataFrame()

    normalized = []
    for item in records:
        ward_code = _pick_value(
            item,
            [
                "ward_code",
                "WardCode",
                "ward",
                "Ward",
                "ward_code_gss",
                "gss_code",
            ],
        )
        ward_name = _pick_value(item, ["ward_name", "WardName", "ward_name_full"])
        if not ward_code and ward_name:
            ward_code = ward_code_by_name.get(_normalize_ward_key(ward_name))
        if not ward_code:
            continue
        ward_code = _normalize_text(ward_code)
        if ward_codes and ward_code not in ward_codes:
            continue

        normalized.append(
            {
                "ward_code": ward_code,
                "ward_name": ward_name_map.get(ward_code, _normalize_text(ward_name, "")),
                "official_name": _normalize_text(
                    _pick_value(item, ["name", "full_name", "official_name", "representative"])
                ),
                "role": _normalize_text(
                    _pick_value(item, ["role", "title", "position", "office"])
                ),
                "party": _normalize_text(
                    _pick_value(item, ["party", "party_name", "political_party"])
                ),
                "email": _normalize_text(
                    _pick_value(item, ["email", "email_address", "emailAddress"])
                ),
                "phone": _normalize_text(
                    _pick_value(item, ["phone", "telephone", "phone_number"])
                ),
                "source": _normalize_text(
                    _pick_value(item, ["source", "source_name"]) or api_url
                ),
                "source_id": _normalize_text(
                    _pick_value(item, ["id", "source_id", "uuid"])
                ),
            }
        )

    df = pd.DataFrame(normalized)
    if df.empty:
        return df
    return df.sort_values(["ward_code", "official_name"]).reset_index(drop=True)
