"""
NM i AI 2026 — utvikler-dashboard (HTML fra data.json).
Brukes av FastAPI (`/dev/dashboard`) og valgfritt `python server.py` (egen port).
"""
from __future__ import annotations

import json
import os
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

DASHBOARD_DATA_FILE = Path(__file__).resolve().parent / "data.json"
DASHBOARD_PORT = int(os.environ.get("DASHBOARD_PORT", "9999"))

_HTML = """<!DOCTYPE html>
<html lang="no">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NM i AI 2026 — Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #f5f5f5; color: #1a1a1a; padding: 20px; }
  h1 { font-size: 18px; font-weight: 600; margin-bottom: 16px; color: #111; }
  .meta { font-size: 12px; color: #888; margin-bottom: 20px; }
  .metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 24px; }
  .metric { background: #fff; border-radius: 10px; padding: 14px 16px;
            border: 1px solid #e8e8e8; }
  .metric-label { font-size: 11px; color: #888; text-transform: uppercase;
                  letter-spacing: 0.05em; margin-bottom: 4px; }
  .metric-value { font-size: 26px; font-weight: 600; color: #111; }
  .metric-sub { font-size: 11px; color: #aaa; margin-top: 3px; }
  .section { font-size: 12px; font-weight: 600; color: #666; text-transform: uppercase;
             letter-spacing: 0.06em; margin: 20px 0 10px; }
  .task-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
               gap: 8px; margin-bottom: 24px; }
  .task { background: #fff; border-radius: 8px; padding: 10px 12px;
          border: 1px solid #e8e8e8; border-left-width: 3px; }
  .task.zero  { border-left-color: #e24b4a; }
  .task.low   { border-left-color: #ef9f27; }
  .task.good  { border-left-color: #1d9e75; }
  .task.great { border-left-color: #534ab7; }
  .task-id    { font-size: 11px; color: #999; }
  .task-score { font-size: 20px; font-weight: 600; margin: 2px 0; }
  .task-tries { font-size: 11px; color: #bbb; }
  .tag { display: inline-block; font-size: 10px; padding: 2px 7px;
         border-radius: 4px; margin-top: 5px; font-weight: 500; }
  .tag-zero  { background: #fdecea; color: #b71c1c; }
  .tag-low   { background: #fff8e1; color: #e65100; }
  .tag-good  { background: #e8f5e9; color: #2e7d32; }
  .tag-great { background: #ede7f6; color: #4527a0; }
  .notes { background: #fff; border-radius: 10px; border: 1px solid #e8e8e8;
           padding: 14px 16px; }
  .note { font-size: 13px; color: #444; padding: 5px 0;
          border-bottom: 1px solid #f0f0f0; }
  .note:last-child { border-bottom: none; }
  .note::before { content: "→ "; color: #aaa; }
  .bar-wrap { background: #f0f0f0; border-radius: 4px; height: 8px;
              overflow: hidden; margin-top: 6px; }
  .bar { height: 100%; border-radius: 4px; transition: width 0.5s; }
  .bar-tx { background: #534ab7; }
  .bar-ng { background: #1d9e75; }
  .bar-as { background: #ef9f27; }
  .scores-row { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px;
                margin-bottom: 20px; }
  .score-item { background: #fff; border-radius: 8px; padding: 10px 12px;
                border: 1px solid #e8e8e8; }
  .score-name { font-size: 11px; color: #888; margin-bottom: 4px; }
  .score-val  { font-size: 16px; font-weight: 600; }
  .refresh { font-size: 11px; color: #bbb; float: right; }
</style>
<meta http-equiv="refresh" content="10">
</head>
<body>
<h1>NM i AI 2026 <span class="refresh">Auto-refresh hvert 10s</span></h1>
<div class="meta" id="meta"></div>

<div class="metrics" id="metrics"></div>

<div class="section">Poengscore per komponent</div>
<div class="scores-row" id="scores"></div>

<div class="section">Tripletex tasks</div>
<div class="task-grid" id="tasks"></div>

<div class="section">Notater</div>
<div class="notes" id="notes"></div>

<script>
const data = __DATA__;

document.getElementById('meta').textContent =
  'Sist oppdatert: ' + data.updated;

const m = data.leaderboard;
const dl = data.deadline;
let dlTime = '15:00', dlDate = '22. mars 2026';
if (dl && String(dl).includes(' ')) {
  const p = String(dl).split(' ');
  dlDate = p[0] || dlDate;
  dlTime = p[1] || dlTime;
}

document.getElementById('metrics').innerHTML = `
  <div class="metric">
    <div class="metric-label">Plassering</div>
    <div class="metric-value">#${m.rank}</div>
    <div class="metric-sub">av ${m.total_teams} lag</div>
  </div>
  <div class="metric">
    <div class="metric-label">Totalpoeng</div>
    <div class="metric-value">${m.score.toFixed(1)}</div>
    <div class="metric-sub">maks ~100</div>
  </div>
  <div class="metric">
    <div class="metric-label">Runs totalt</div>
    <div class="metric-value">${data.runs_today}</div>
    <div class="metric-sub">submissions</div>
  </div>
  <div class="metric">
    <div class="metric-label">Deadline</div>
    <div class="metric-value">${dlTime}</div>
    <div class="metric-sub">${dlDate}</div>
  </div>
`;

document.getElementById('scores').innerHTML = `
  <div class="score-item">
    <div class="score-name">Tripletex</div>
    <div class="score-val">${m.tripletex.toFixed(1)}</div>
    <div class="bar-wrap"><div class="bar bar-tx" style="width:${m.tripletex}%"></div></div>
  </div>
  <div class="score-item">
    <div class="score-name">NorgesGruppen</div>
    <div class="score-val">${m.norgesgruppen.toFixed(1)}</div>
    <div class="bar-wrap"><div class="bar bar-ng" style="width:${m.norgesgruppen}%"></div></div>
  </div>
  <div class="score-item">
    <div class="score-name">Astar Island</div>
    <div class="score-val">${m.astar.toFixed(1)}</div>
    <div class="bar-wrap"><div class="bar bar-as" style="width:${m.astar}%"></div></div>
  </div>
`;

const taskGrid = document.getElementById('tasks');
data.tasks.forEach(t => {
  const s = t.score;
  let cls, tagCls, tagTxt;
  if (s === null || s === 0) {
    cls = 'zero'; tagCls = 'tag-zero'; tagTxt = '0 poeng';
  } else if (s < 1.0) {
    cls = 'low'; tagCls = 'tag-low'; tagTxt = 'lav';
  } else if (s >= 3.5) {
    cls = 'great'; tagCls = 'tag-great'; tagTxt = 'topp';
  } else {
    cls = 'good'; tagCls = 'tag-good'; tagTxt = 'OK';
  }
  const div = document.createElement('div');
  div.className = `task ${cls}`;
  div.innerHTML = `
    <div class="task-id">Task ${t.id}</div>
    <div class="task-score">${s !== null ? s.toFixed(2) : '—'}</div>
    <div class="task-tries">${t.tries} forsøk</div>
    <span class="tag ${tagCls}">${tagTxt}</span>
  `;
  taskGrid.appendChild(div);
});

const notesEl = document.getElementById('notes');
data.notes.forEach(n => {
  const d = document.createElement('div');
  d.className = 'note';
  d.textContent = n;
  notesEl.appendChild(d);
});
</script>
</body>
</html>
"""


def load_dashboard_data() -> dict:
    raw = DASHBOARD_DATA_FILE.read_text(encoding="utf-8")
    return json.loads(raw)


def render_dashboard_html(data: dict) -> str:
    return _HTML.replace("__DATA__", json.dumps(data))


def render_dashboard_page() -> str:
    try:
        data = load_dashboard_data()
    except Exception as e:
        data = {"error": str(e)}
    return render_dashboard_html(data)


class _StandaloneHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        html = render_dashboard_page().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html)

    def log_message(self, format: str, *args: object) -> None:
        pass


def serve_standalone() -> None:
    print(f"Dashboard: http://localhost:{DASHBOARD_PORT}")
    print("Oppdater data.json for å reflektere ny status.")
    print("Ctrl+C for å stoppe.")
    webbrowser.open(f"http://localhost:{DASHBOARD_PORT}")
    HTTPServer(("", DASHBOARD_PORT), _StandaloneHandler).serve_forever()
