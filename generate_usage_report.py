#!/usr/bin/env python3
"""Download serverinfo JSON stats from Nextcloud WebDAV, generate an HTML usage report, and upload it back."""

import datetime
import json
import os
import re
import xml.etree.ElementTree as ET

import requests

NC_URL = os.getenv("NC_URL")
NC_USER = os.getenv("NC_ANCHOR_USER")
NC_APP_PW = os.getenv("NC_ANCHOR_APP_PW")
NC_STATS_DIR = os.getenv("NC_STATS_DIR")


def list_stats_files() -> list[str]:
    """List all stats_raw_*.json files in the WebDAV stats directory."""
    webdav_url = f"{NC_URL}/remote.php/dav/files/{NC_USER}/{NC_STATS_DIR}/"
    resp = requests.request(
        "PROPFIND", webdav_url, auth=(NC_USER, NC_APP_PW),
        headers={"Depth": "1"}
    )
    resp.raise_for_status()
    tree = ET.fromstring(resp.text)
    ns = {"d": "DAV:"}
    files = []
    for href_el in tree.findall(".//d:response/d:href", ns):
        href = href_el.text or ""
        if "stats_raw_" in href and href.endswith(".json"):
            files.append(href)
    return sorted(files)


def download_file(href: str) -> dict:
    """Download a single JSON file from WebDAV by its href path."""
    url = f"{NC_URL}{href}"
    resp = requests.get(url, auth=(NC_USER, NC_APP_PW))
    resp.raise_for_status()
    return resp.json()


def parse_timestamp(filename: str) -> str:
    """Extract a display timestamp from filename like stats_raw_2026-05-30_06-00-00.json."""
    m = re.search(r"stats_raw_(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})", filename)
    if m:
        return f"{m.group(1)} {m.group(2)}:{m.group(3)}"
    return filename


def generate_html(dates: list[str], active_24h: list[int], num_users: list[int],
                  num_files: list[int], freespace_gb: list[float], db_size_gb: list[float]) -> str:
    """Generate a self-contained HTML dashboard with Chart.js."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Nextcloud Usage Report</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
  body {{ font-family: sans-serif; margin: 2rem; background: #f5f5f5; }}
  h1 {{ color: #0082c9; }}
  .chart-container {{ background: white; border-radius: 8px; padding: 1rem; margin: 1.5rem 0; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
  canvas {{ max-height: 300px; }}
  .meta {{ color: #666; font-size: 0.9rem; }}
</style>
</head>
<body>
<h1>Nextcloud Usage Report</h1>
<p class="meta">Generated: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M")} | Data points: {len(dates)}</p>

<div class="chart-container">
  <canvas id="activeUsers"></canvas>
</div>
<div class="chart-container">
  <canvas id="storageGrowth"></canvas>
</div>
<div class="chart-container">
  <canvas id="diskSpace"></canvas>
</div>

<script>
const labels = {json.dumps(dates)};

new Chart(document.getElementById('activeUsers'), {{
  type: 'line',
  data: {{
    labels: labels,
    datasets: [{{
      label: 'Active Users (24h)',
      data: {json.dumps(active_24h)},
      borderColor: '#0082c9',
      tension: 0.3
    }}, {{
      label: 'Total Users',
      data: {json.dumps(num_users)},
      borderColor: '#e67e22',
      tension: 0.3
    }}]
  }},
  options: {{ responsive: true, plugins: {{ title: {{ display: true, text: 'Users Over Time' }} }} }}
}});

new Chart(document.getElementById('storageGrowth'), {{
  type: 'line',
  data: {{
    labels: labels,
    datasets: [{{
      label: 'Total Files',
      data: {json.dumps(num_files)},
      borderColor: '#27ae60',
      tension: 0.3
    }}, {{
      label: 'Database Size (GB)',
      data: {json.dumps(db_size_gb)},
      borderColor: '#8e44ad',
      yAxisID: 'y1',
      tension: 0.3
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ title: {{ display: true, text: 'Storage Growth' }} }},
    scales: {{
      y: {{ position: 'left', title: {{ display: true, text: 'Files' }} }},
      y1: {{ position: 'right', title: {{ display: true, text: 'GB' }}, grid: {{ drawOnChartArea: false }} }}
    }}
  }}
}});

new Chart(document.getElementById('diskSpace'), {{
  type: 'line',
  data: {{
    labels: labels,
    datasets: [{{
      label: 'Free Disk Space (GB)',
      data: {json.dumps(freespace_gb)},
      borderColor: '#e74c3c',
      tension: 0.3,
      fill: true,
      backgroundColor: 'rgba(231,76,60,0.1)'
    }}]
  }},
  options: {{ responsive: true, plugins: {{ title: {{ display: true, text: 'Free Disk Space' }} }} }}
}});
</script>
</body>
</html>"""


def main() -> None:
    print("Listing stats files...")
    hrefs = list_stats_files()
    if not hrefs:
        print("No stats files found.")
        return

    print(f"Found {len(hrefs)} files. Downloading...")
    dates, active_24h, num_users, num_files, freespace_gb, db_size_gb = [], [], [], [], [], []

    for href in hrefs:
        try:
            data = download_file(href)
            d = data["ocs"]["data"]
            dates.append(parse_timestamp(href))
            active_24h.append(int(d["activeUsers"]["last24hours"]))
            num_users.append(int(d["nextcloud"]["storage"]["num_users"]))
            num_files.append(int(d["nextcloud"]["storage"]["num_files"]))
            freespace_gb.append(round(int(d["nextcloud"]["system"]["freespace"]) / (1024**3), 2))
            db_size_gb.append(round(int(d["server"]["database"]["size"]) / (1024**3), 2))
        except (KeyError, TypeError) as e:
            print(f"  Skipping {href}: {e}")

    print(f"Parsed {len(dates)} data points. Generating report...")
    html = generate_html(dates, active_24h, num_users, num_files, freespace_gb, db_size_gb)

    filename = "usage_report.html"
    parent_dir = "/".join(NC_STATS_DIR.rstrip("/").split("/")[:-1])
    webdav_url = f"{NC_URL}/remote.php/dav/files/{NC_USER}/{parent_dir}/{filename}"
    upload_res = requests.put(webdav_url, data=html.encode("utf-8"), auth=(NC_USER, NC_APP_PW),
                              headers={"Content-Type": "text/html; charset=utf-8"})
    upload_res.raise_for_status()
    print(f"Report uploaded: {NC_STATS_DIR}/{filename}")


if __name__ == "__main__":
    main()
