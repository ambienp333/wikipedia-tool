import math
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


def normalize_topic(topic):
    """Accept a Wikipedia URL or a plain article title."""
    topic = topic.strip()
    if "wikipedia.org/wiki/" in topic:
        path = topic.split("/wiki/", 1)[1].split("#")[0]
        return urllib.parse.unquote(path).replace("_", " ")
    return topic


def get_links(page_title, limit=10):
    params = {
        "action": "parse",
        "page": page_title,
        "prop": "text",
        "format": "json",
        "redirects": 1,
    }
    try:
        resp = requests.get(API_URL, params=params, headers=HEADERS, timeout=10)
        data = resp.json()
    except Exception as e:
        print(f"    Error fetching {page_title}: {e}")
        return []

    if "error" in data:
        return []

    html = data["parse"]["text"]["*"]
    soup = BeautifulSoup(html, "html.parser")
    content = soup.find("div", class_="mw-parser-output")
    if not content:
        return []

    links = []
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
                    return links

    return links


def _crawl_seed(seed, link_cache, depth, links_per_page):
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
            link_cache[page] = get_links(page, limit=links_per_page)
            time.sleep(0.1)

        for link in link_cache[page]:
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


def crawl(seed_topics, depth=3, links_per_page=10):
    """
    Run an independent BFS from each seed, sharing a link cache to avoid re-fetching pages.
    Returns (seed_coverage, all_edges).
    seed_coverage[node] = number of seeds whose BFS reached that node.
    """
    link_cache = {}
    seed_coverage = defaultdict(int)
    all_edges = set()

    for i, seed in enumerate(seed_topics):
        seed = normalize_topic(seed)
        print(f"\n[Seed {i + 1}/{len(seed_topics)}] {seed}")
        visited, edges = _crawl_seed(seed, link_cache, depth, links_per_page)
        all_edges |= edges
        for node in visited:
            seed_coverage[node] += 1

    return dict(seed_coverage), all_edges


def build_graph(seed_topics, seed_coverage, all_edges, top_n=120):
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

    non_seeds = sorted(
        [(n, s) for n, s in scores.items() if n not in seed_set],
        key=lambda x: x[1],
        reverse=True,
    )
    kept = seed_set | {n for n, _ in non_seeds[:top_n]}

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

    # Deduplicate: treat (A→B) and (B→A) as one edge, flagged as bidirectional
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
        })

    return {"nodes": nodes, "links": links}


if __name__ == "__main__":
    import sys

    seeds = sys.argv[1:] if len(sys.argv) > 1 else ["Genetics", "Epigenetics", "Molecular biology"]
    print(f"Seeds: {seeds}\nCrawling...\n")

    coverage, edges = crawl(seeds, depth=3, links_per_page=10)
    graph = build_graph(seeds, coverage, edges, top_n=120)

    with open("graph.json", "w") as f:
        json.dump(graph, f)

    print(f"\nDone: {len(graph['nodes'])} nodes, {len(graph['links'])} edges → graph.json")
