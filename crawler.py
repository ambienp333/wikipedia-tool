import requests
import time
import json
from bs4 import BeautifulSoup
from collections import deque, defaultdict

BASE_URL = "https://en.wikipedia.org"  # swap to http://localhost:8080 for Kiwix
API_URL = f"{BASE_URL}/w/api.php"
HEADERS = {"User-Agent": "wikipedia-tool/1.0 (educational project)"}


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


def crawl(seed_topics, depth=3, links_per_page=10):
    visited = set()
    frequency = defaultdict(int)
    edges = set()

    queue = deque()
    for topic in seed_topics:
        normalized = topic.strip()
        queue.append((normalized, 0))
        visited.add(normalized)

    total = 0
    while queue:
        page, current_depth = queue.popleft()

        if current_depth >= depth:
            continue

        total += 1
        print(f"[{current_depth}/{depth}] ({total}) {page}")
        links = get_links(page, limit=links_per_page)
        time.sleep(0.1)

        for link in links:
            frequency[link] += 1
            edges.add((page, link))
            if link not in visited:
                visited.add(link)
                queue.append((link, current_depth + 1))

    return frequency, edges


def build_graph(seed_topics, frequency, edges, min_frequency=2):
    seed_set = {t.strip() for t in seed_topics}

    non_seed_freqs = sorted(
        [f for n, f in frequency.items() if n not in seed_set], reverse=True
    )
    hub_threshold = (
        non_seed_freqs[max(0, len(non_seed_freqs) // 10)] if len(non_seed_freqs) > 10 else 9999
    )

    kept = seed_set | {n for n, f in frequency.items() if f >= min_frequency}

    nodes = []
    for node in kept:
        freq = frequency.get(node, 0)
        if node in seed_set:
            node_type = "seed"
        elif freq >= hub_threshold:
            node_type = "hub"
        else:
            node_type = "node"
        nodes.append({"id": node, "type": node_type, "frequency": freq})

    links = [
        {"source": src, "target": tgt}
        for src, tgt in edges
        if src in kept and tgt in kept
    ]

    return {"nodes": nodes, "links": links}


if __name__ == "__main__":
    import sys

    seeds = sys.argv[1:] if len(sys.argv) > 1 else ["Genetics", "Epigenetics", "Molecular biology"]
    print(f"Seeds: {seeds}\nCrawling...\n")

    freq, edges = crawl(seeds, depth=3, links_per_page=10)
    graph = build_graph(seeds, freq, edges, min_frequency=2)

    with open("graph.json", "w") as f:
        json.dump(graph, f)

    print(f"\nDone: {len(graph['nodes'])} nodes, {len(graph['links'])} edges → graph.json")
