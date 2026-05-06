import json
import os
import threading
from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
from crawler import crawl, build_graph

app = Flask(__name__)
CORS(app)

GRAPH_FILE = "graph.json"
_crawl_lock = threading.Lock()
_crawl_running = False


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/graph")
def graph():
    if not os.path.exists(GRAPH_FILE):
        return jsonify({"error": "No graph data yet. Run a crawl first."}), 404
    with open(GRAPH_FILE) as f:
        return jsonify(json.load(f))


@app.route("/crawl", methods=["POST"])
def run_crawl():
    global _crawl_running

    if _crawl_running:
        return jsonify({"error": "A crawl is already in progress."}), 409

    data = request.json or {}
    topics        = [t.strip() for t in data.get("topics", []) if t.strip()]
    depth         = max(1, min(int(data.get("depth", 3)), 4))
    links_per_page = max(5, min(int(data.get("links_per_page", 10)), 20))
    top_n         = max(20, min(int(data.get("top_n", 120)), 300))

    if not topics:
        return jsonify({"error": "No topics provided."}), 400

    with _crawl_lock:
        _crawl_running = True

    try:
        coverage, edges = crawl(topics, depth=depth, links_per_page=links_per_page)
        graph_data = build_graph(topics, coverage, edges, top_n=top_n)
        with open(GRAPH_FILE, "w") as f:
            json.dump(graph_data, f)
        return jsonify(graph_data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        with _crawl_lock:
            _crawl_running = False


if __name__ == "__main__":
    app.run(debug=True, port=5000, threaded=True)
