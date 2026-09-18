"""
Web UI for the on-page keyword SEO checker.

Run:
    python app.py
Then open http://127.0.0.1:5000
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, render_template, request
from seo_checker.core import run_check

app = Flask(__name__)


@app.route("/", methods=["GET", "POST"])
def index():
    result = None
    error = None
    url = ""
    keyword = ""
    render = False
    if request.method == "POST":
        url = request.form.get("url", "").strip()
        keyword = request.form.get("keyword", "").strip()
        render = request.form.get("render") == "on"
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        try:
            result = run_check(url, keyword, render=render)
        except Exception as exc:
            error = str(exc)
    return render_template("index.html", result=result, error=error, url=url, keyword=keyword, render=render)


@app.route("/title-checker")
def title_checker():
    return render_template(
        "title_checker.html",
        prefill_url=request.args.get("url", ""),
        prefill_title=request.args.get("title", ""),
        prefill_description=request.args.get("description", ""),
        prefill_keyword=request.args.get("keyword", ""),
    )


if __name__ == "__main__":
    app.run(debug=False)
