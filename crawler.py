import math
import re
import urllib.parse
import requests
import time
import json
from bs4 import BeautifulSoup
from collections import deque, defaultdict
from datetime import datetime, timedelta

BASE_URL = "https://en.wikipedia.org"  # swap to http://localhost:8080 for Kiwix
API_URL = f"{BASE_URL}/w/api.php"
HEADERS = {"User-Agent": "wikipedia-tool/1.0 (educational project)"}


EXCLUSION_PRESETS = {
    "people": {
        "label": "People & Biographies",
        "patterns": [
            r"\d{4} births", r"\d{4} deaths", r"^living people$",
            r"^people from ", r"^people by ", r"alumni of ", r"graduates of ",
        ],
    },
    "history": {
        "label": "History",
        "patterns": [
            r"^history of ", r"historical ", r"^ancient ",
            r"^medieval ", r"\d+th.century", r"^wars ", r" wars$",
            r"^battles ", r"military history",
        ],
    },
    "geography": {
        "label": "Geography & Places",
        "patterns": [
            r"^cities in ", r"^towns in ", r"^villages in ",
            r"populated places", r"^geography of ",
            r"^rivers of ", r"^mountains of ", r"^islands of ",
        ],
    },
    "media": {
        "label": "Films, Books & Media",
        "patterns": [
            r"\bfilms\b", r"\balbums\b", r"\bnovels\b",
            r"\bsongs\b", r"^television ", r"video games",
        ],
    },
}


def normalize_topic(topic):
    """Accept a Wikipedia URL or a plain article title."""
    topic = topic.strip()
    if "wikipedia.org/wiki/" in topic:
        path = topic.split("/wiki/", 1)[1].split("#")[0]
        return urllib.parse.unquote(path).replace("_", " ")
    return topic


def _fetch_page_data(page_title, limit=10):
    """Fetch links from paragraph text and category list in one API call."""
    params = {
        "action": "parse",
        "page": page_title,
        "prop": "text|categories",
        "format": "json",
        "redirects": 1,
    }
    try:
        resp = requests.get(API_URL, params=params, headers=HEADERS, timeout=10)
        data = resp.json()
    except Exception as e:
        print(f"    Error fetching {page_title}: {e}")
        return {"links": [], "categories": []}

    if "error" in data:
        return {"links": [], "categories": []}

    parse = data["parse"]

    # Extract paragraph links
    html = parse["text"]["*"]
    soup = BeautifulSoup(html, "html.parser")
    content = soup.find("div", class_="mw-parser-output")
    links = []
    if content:
        seen = set()
        for p in content.find_all("p"):
            for a in p.find_all("a", href=True):
                href = a.get("href", "")
                if not href.startswith("/wiki/"):
                    continue
                title = href[6:].split("#")[0]
                if not title or ":" in title:
                    continue
                if "new" in a.get("class", []):
                    continue
                title = title.replace("_", " ")
                if title not in seen:
                    seen.add(title)
                    links.append(title)
                    if len(links) >= limit:
                        break
            if len(links) >= limit:
                break

    # Extract See Also links
    see_also_links = []
    if content:
        see_also_head = None
        for tag in content.find_all(["h2", "h3", "div"]):
            if tag.name in ["h2", "h3"]:
                if tag.get_text(strip=True).lower() == "see also":
                    see_also_head = tag
                    break
            elif "mw-heading" in tag.get("class", []):
                inner = tag.find(["h2", "h3"])
                if inner and inner.get_text(strip=True).lower() == "see also":
                    see_also_head = tag
                    break

        if see_also_head:
            seen_sa = set(links)
            for sibling in see_also_head.find_next_siblings():
                if sibling.name in ["h2", "h3"]:
                    break
                if "mw-heading" in sibling.get("class", []):
                    break
                for a in sibling.find_all("a", href=True):
                    href = a.get("href", "")
                    if not href.startswith("/wiki/"):
                        continue
                    title = href[6:].split("#")[0]
                    if not title or ":" in title:
                        continue
                    if "new" in a.get("class", []):
                        continue
                    title = title.replace("_", " ")
                    if title not in seen_sa:
                        seen_sa.add(title)
                        see_also_links.append(title)

    # Extract categories (skip hidden ones)
    categories = [
        cat["*"].replace("_", " ")
        for cat in parse.get("categories", [])
        if not cat.get("hidden")
    ]

    return {"links": links, "see_also": see_also_links, "categories": categories}


def _is_excluded(categories, active_exclusions):
    """Return True if any of the page's categories match an active exclusion preset."""
    cats_lower = [c.lower() for c in categories]
    for preset_key in active_exclusions:
        preset = EXCLUSION_PRESETS.get(preset_key)
        if not preset:
            continue
        for cat in cats_lower:
            for pattern in preset["patterns"]:
                if re.search(pattern, cat):
                    return True
    return False


def _crawl_seed(seed, link_cache, depth, links_per_page, include_see_also=False):
    """BFS from a single seed. Mutates link_cache in place so results are reused across seeds."""
    visited = {seed}
    edges = set()
    queue = deque([(seed, 0)])

    while queue:
        page, d = queue.popleft()
        if d >= depth:
            continue

        if page not in link_cache:
            print(f"  [{d}] {page}")
            link_cache[page] = _fetch_page_data(page, limit=links_per_page)
            time.sleep(0.1)

        page_links = link_cache[page]["links"]
        if include_see_also:
            seen = set(page_links)
            page_links = page_links + [l for l in link_cache[page]["see_also"] if l not in seen]

        for link in page_links:
            edges.add((page, link))
            if link not in visited:
                visited.add(link)
                queue.append((link, d + 1))

    return visited, edges


def fetch_pageviews(titles):
    """Fetch total monthly pageviews over the past ~3 months for each title."""
    end_str   = datetime.now().strftime("%Y%m%d")
    start_str = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")

    pageviews = {}
    total = len(titles)
    for i, title in enumerate(titles):
        encoded = requests.utils.quote(title.replace(" ", "_"), safe="")
        url = (
            f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
            f"/en.wikipedia.org/all-access/all-agents/{encoded}/monthly/{start_str}/{end_str}"
        )
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            data = resp.json()
            total_views = sum(item["views"] for item in data.get("items", []))
            pageviews[title] = max(total_views, 100)
        except Exception:
            pageviews[title] = 50000  # neutral fallback

        if (i + 1) % 20 == 0:
            print(f"  Pageviews: {i + 1}/{total}")
        time.sleep(0.05)

    return pageviews


def crawl(seed_topics, depth=3, links_per_page=10, include_see_also=False):
    """
    Run an independent BFS from each seed, sharing a link cache to avoid re-fetching pages.
    Returns (seed_coverage, all_edges, cat_cache).
    seed_coverage[node] = number of seeds whose BFS reached that node.
    """
    link_cache = {}
    seed_coverage = defaultdict(int)
    all_edges = set()

    for i, seed in enumerate(seed_topics):
        seed = normalize_topic(seed)
        print(f"\n[Seed {i + 1}/{len(seed_topics)}] {seed}")
        visited, edges = _crawl_seed(seed, link_cache, depth, links_per_page, include_see_also)
        all_edges |= edges
        for node in visited:
            seed_coverage[node] += 1

    cat_cache = {page: data["categories"] for page, data in link_cache.items()}
    return dict(seed_coverage), all_edges, cat_cache


def build_graph(seed_topics, seed_coverage, all_edges, cat_cache=None, exclusions=None, top_n=120):
    seed_set = {normalize_topic(t) for t in seed_topics}
    total_seeds = len(seed_topics)

    # Bidirectional edge detection: (A→B) is bidirectional if (B→A) also exists
    bidir_edges = {(a, b) for (a, b) in all_edges if (b, a) in all_edges}

    node_degree = defaultdict(int)
    node_bidir  = defaultdict(int)
    for (a, b) in all_edges:
        node_degree[a] += 1
        node_degree[b] += 1
        if (a, b) in bidir_edges:
            node_bidir[a] += 1
            node_bidir[b] += 1

    candidates = list(seed_coverage.keys())
    print(f"\nFetching pageviews for {len(candidates)} nodes...")
    pageviews = fetch_pageviews(candidates)
    max_pv = max(pageviews.values()) if pageviews else 1

    scores = {}
    for node in candidates:
        if node in seed_set:
            scores[node] = float("inf")
            continue

        # Component 1: cross-seed coverage (0–1) — weighted 50%
        coverage_score = seed_coverage.get(node, 0) / total_seeds

        # Component 2: specificity via pageviews (0–1, higher = more specific) — weighted 30%
        pv = pageviews.get(node, 50000)
        pv_score = 1.0 - (math.log(max(pv, 1)) / math.log(max(max_pv, 2)))

        # Component 3: bidirectional edge ratio (0–1) — weighted 20%
        deg = max(node_degree.get(node, 1), 1)
        bidir_ratio = node_bidir.get(node, 0) / deg

        scores[node] = (0.5 * coverage_score) + (0.3 * pv_score) + (0.2 * bidir_ratio)

    active_exclusions = exclusions or []
    cat_cache = cat_cache or {}

    # Separate excluded nodes from nodes that simply don't score high enough
    excluded = {
        n for n in scores
        if n not in seed_set
        and active_exclusions
        and _is_excluded(cat_cache.get(n, []), active_exclusions)
    }

    non_seeds = sorted(
        [(n, s) for n, s in scores.items() if n not in seed_set and n not in excluded],
        key=lambda x: x[1],
        reverse=True,
    )
    kept = seed_set | {n for n, _ in non_seeds[:top_n]}

    # Guarantee each seed has at least its top direct neighbours visible.
    # Without this, seeds whose links all scored below top_n appear as isolated nodes.
    for seed in seed_set:
        direct = sorted(
            [tgt for (src, tgt) in all_edges if src == seed and tgt not in excluded and tgt not in seed_set],
            key=lambda n: scores.get(n, 0),
            reverse=True,
        )
        for neighbour in direct[:8]:
            kept.add(neighbour)

    # Edge inheritance: when a node is excluded, bridge its kept neighbours directly
    inherited = set()
    if excluded:
        exc_out = defaultdict(set)
        exc_in  = defaultdict(set)
        for (src, tgt) in all_edges:
            if src in excluded:
                exc_out[src].add(tgt)
            if tgt in excluded:
                exc_in[tgt].add(src)
        for exc in excluded:
            for src in exc_in[exc]:
                if src not in kept:
                    continue
                for tgt in exc_out[exc]:
                    if tgt in kept and src != tgt:
                        inherited.add((src, tgt))

    kept_scores = sorted(
        [scores[n] for n in kept if n not in seed_set], reverse=True
    )
    hub_threshold = kept_scores[max(0, len(kept_scores) // 5)] if len(kept_scores) > 5 else 1.0

    nodes = []
    for node in kept:
        score = scores[node]
        if node in seed_set:
            node_type = "seed"
        elif score >= hub_threshold:
            node_type = "hub"
        else:
            node_type = "node"
        nodes.append({
            "id": node,
            "type": node_type,
            "score": round(score, 4) if score != float("inf") else 9.0,
            "coverage": seed_coverage.get(node, 0),
            "pageviews": pageviews.get(node, 0),
        })

    # Direct edges (deduplicated, bidirectional flagged)
    seen_pairs = set()
    links = []
    for (src, tgt) in all_edges:
        if src not in kept or tgt not in kept:
            continue
        pair = frozenset([src, tgt])
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        links.append({
            "source": src,
            "target": tgt,
            "bidirectional": (src, tgt) in bidir_edges,
            "inherited": False,
        })

    # Inherited edges (from excluded node bridge-removal)
    for (src, tgt) in inherited:
        pair = frozenset([src, tgt])
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        links.append({
            "source": src,
            "target": tgt,
            "bidirectional": False,
            "inherited": True,
        })

    return {"nodes": nodes, "links": links}


if __name__ == "__main__":
    import sys

    seeds = sys.argv[1:] if len(sys.argv) > 1 else ["Genetics", "Epigenetics", "Molecular biology"]
    print(f"Seeds: {seeds}\nCrawling...\n")

    coverage, edges, cats = crawl(seeds, depth=3, links_per_page=10)
    graph = build_graph(seeds, coverage, edges, cat_cache=cats, top_n=120)

    with open("graph.json", "w") as f:
        json.dump(graph, f)

    print(f"\nDone: {len(graph['nodes'])} nodes, {len(graph['links'])} edges → graph.json")
