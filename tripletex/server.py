"""
NM i AI 2026 — Competition Dashboard
Run: python3 server.py
Opens at: http://localhost:9999
Auto-refreshes every 10 seconds.
Update data.json to reflect latest scores and backlog.
"""
import json
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

DATA_FILE = Path(__file__).parent / "data.json"
PORT = 9999

HTML = r"""<!DOCTYPE html>
<html lang="no">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NM i AI 2026</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:20px 24px}

/* —— Light theme —— */
body[data-theme="light"]{background:#f4f4f2;color:#1a1a1a}
body[data-theme="light"] .meta{font-size:11px;color:#aaa;margin-bottom:18px}
body[data-theme="light"] .grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:20px}
body[data-theme="light"] .metric{background:#fff;border-radius:10px;padding:13px 15px;border:1px solid #e6e6e4}
body[data-theme="light"] .mlabel{font-size:11px;color:#999;text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px}
body[data-theme="light"] .mval{font-size:24px;font-weight:600;color:#111}
body[data-theme="light"] .msub{font-size:11px;color:#bbb;margin-top:2px}
body[data-theme="light"] .scores{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:20px}
body[data-theme="light"] .score{background:#fff;border-radius:8px;padding:10px 13px;border:1px solid #e6e6e4}
body[data-theme="light"] .sname{font-size:11px;color:#999;margin-bottom:3px}
body[data-theme="light"] .sval{font-size:15px;font-weight:600;margin-bottom:5px;color:#111}
body[data-theme="light"] .bar{height:6px;border-radius:3px;background:#eee;overflow:hidden}
body[data-theme="light"] .bar-inner{height:100%;border-radius:3px;transition:width .5s}
body[data-theme="light"] .bar-tx .bar-inner{background:#534ab7}
body[data-theme="light"] .bar-ng .bar-inner{background:#1d9e75}
body[data-theme="light"] .bar-as .bar-inner{background:#ef9f27}
body[data-theme="light"] .section{font-size:11px;font-weight:600;color:#777;text-transform:uppercase;letter-spacing:.06em;margin:20px 0 9px}
body[data-theme="light"] .task-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(112px,1fr));gap:7px;margin-bottom:4px}
body[data-theme="light"] .task{background:#fff;border-radius:7px;padding:9px 11px;border:1px solid #e6e6e4;border-left-width:3px}
body[data-theme="light"] .task.zero{border-left-color:#e24b4a}
body[data-theme="light"] .task.low{border-left-color:#ef9f27}
body[data-theme="light"] .task.good{border-left-color:#1d9e75}
body[data-theme="light"] .task.great{border-left-color:#534ab7}
body[data-theme="light"] .tid{font-size:10px;color:#bbb}
body[data-theme="light"] .tscore{font-size:19px;font-weight:600;color:#111}
body[data-theme="light"] .ttries{font-size:10px;color:#ccc}
body[data-theme="light"] .ttag{display:inline-block;font-size:9px;padding:2px 6px;border-radius:3px;margin-top:4px;font-weight:500}
body[data-theme="light"] .tag-zero{background:#fdecea;color:#b71c1c}
body[data-theme="light"] .tag-low{background:#fff8e1;color:#e65100}
body[data-theme="light"] .tag-good{background:#e8f5e9;color:#2e7d32}
body[data-theme="light"] .tag-great{background:#ede7f6;color:#4527a0}
body[data-theme="light"] .backlog{display:flex;flex-direction:column;gap:7px;margin-bottom:20px}
body[data-theme="light"] .bl-item{background:#fff;border-radius:9px;border:1px solid #e6e6e4;padding:11px 14px;border-left-width:3px}
body[data-theme="light"] .bl-next{border-left-color:#534ab7;background:#faf9ff}
body[data-theme="light"] .bl-todo{border-left-color:#e6e6e4}
body[data-theme="light"] .bl-done{border-left-color:#e6e6e4;opacity:.55}
body[data-theme="light"] .bl-header{display:flex;align-items:center;gap:8px;margin-bottom:3px}
body[data-theme="light"] .bl-badge{font-size:9px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;padding:2px 7px;border-radius:3px}
body[data-theme="light"] .badge-next{background:#ede7f6;color:#4527a0}
body[data-theme="light"] .badge-todo{background:#f0f0ee;color:#888}
body[data-theme="light"] .badge-done{background:#e8f5e9;color:#2e7d32}
body[data-theme="light"] .bl-title{font-size:13px;font-weight:500;color:#111}
body[data-theme="light"] .bl-done .bl-title{text-decoration:line-through;color:#aaa}
body[data-theme="light"] .bl-detail{font-size:12px;color:#888;margin-top:2px;line-height:1.5}
body[data-theme="light"] .bl-done .bl-detail{color:#ccc}
body[data-theme="light"] .bl-affects{display:flex;gap:4px;flex-wrap:wrap;margin-top:5px}
body[data-theme="light"] .bl-chip{font-size:10px;padding:1px 6px;border-radius:3px;background:#f0effe;color:#534ab7;font-weight:500}
body[data-theme="light"] .notes{background:#fff;border-radius:9px;border:1px solid #e6e6e4;padding:12px 15px}
body[data-theme="light"] .note{font-size:12px;color:#555;padding:4px 0;border-bottom:1px solid #f4f4f2;line-height:1.5}
body[data-theme="light"] .note:last-child{border-bottom:none}
body[data-theme="light"] .note::before{content:"→ ";color:#ccc}
body[data-theme="light"] .refresh{font-size:10px;color:#ccc}
body[data-theme="light"] h1.hdr{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:4px;font-size:17px;font-weight:600}
body[data-theme="light"] .task.task-last{outline:2px solid #534ab7;outline-offset:2px}
body[data-theme="light"] .sist-badge{font-size:9px;background:#534ab7;color:#fff;border-radius:3px;padding:1px 5px;margin-left:3px;vertical-align:middle}
body[data-theme="light"] #themeToggle:hover{background:rgba(0,0,0,.06)}

/* —— Dark theme (NM i AI) —— */
body[data-theme="dark"]{background:#0d1117;color:#fff}
body[data-theme="dark"] .meta{font-size:11px;color:#8ab4c4;margin-bottom:18px}
body[data-theme="dark"] .grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:20px}
body[data-theme="dark"] .metric{background:#0f2027;border-radius:10px;padding:13px 15px;border:1px solid #1a3a4a}
body[data-theme="dark"] .mlabel{font-size:11px;color:#8ab4c4;text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px}
body[data-theme="dark"] .mval{font-size:24px;font-weight:600;color:#fff}
body[data-theme="dark"] .msub{font-size:11px;color:#8ab4c4;margin-top:2px}
body[data-theme="dark"] .scores{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:20px}
body[data-theme="dark"] .score{background:#0f2027;border-radius:8px;padding:10px 13px;border:1px solid #1a3a4a}
body[data-theme="dark"] .sname{font-size:11px;color:#8ab4c4;margin-bottom:3px}
body[data-theme="dark"] .sval{font-size:15px;font-weight:600;margin-bottom:5px;color:#fff}
body[data-theme="dark"] .bar{height:6px;border-radius:3px;background:#1a3a4a;overflow:hidden}
body[data-theme="dark"] .bar-inner{height:100%;border-radius:3px;transition:width .5s}
body[data-theme="dark"] .bar-tx .bar-inner{background:#7b6ff0}
body[data-theme="dark"] .bar-ng .bar-inner{background:#00e5a0}
body[data-theme="dark"] .bar-as .bar-inner{background:#e5a000}
body[data-theme="dark"] .section{font-size:11px;font-weight:600;color:#8ab4c4;text-transform:uppercase;letter-spacing:.06em;margin:20px 0 9px}
body[data-theme="dark"] .task-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(112px,1fr));gap:7px;margin-bottom:4px}
body[data-theme="dark"] .task{background:#0f2027;border-radius:7px;padding:9px 11px;border:1px solid #1a3a4a;border-left-width:3px}
body[data-theme="dark"] .task.zero{border-left-color:#e55050}
body[data-theme="dark"] .task.low{border-left-color:#e5a000}
body[data-theme="dark"] .task.good{border-left-color:#00e5a0}
body[data-theme="dark"] .task.great{border-left-color:#7b6ff0}
body[data-theme="dark"] .tid{font-size:10px;color:#8ab4c4}
body[data-theme="dark"] .tscore{font-size:19px;font-weight:600;color:#fff}
body[data-theme="dark"] .ttries{font-size:10px;color:#8ab4c4}
body[data-theme="dark"] .ttag{display:inline-block;font-size:9px;padding:2px 6px;border-radius:3px;margin-top:4px;font-weight:500}
body[data-theme="dark"] .tag-zero{background:rgba(229,80,80,.15);color:#e55050}
body[data-theme="dark"] .tag-low{background:rgba(229,160,0,.15);color:#e5a000}
body[data-theme="dark"] .tag-good{background:rgba(0,229,160,.12);color:#00e5a0}
body[data-theme="dark"] .tag-great{background:rgba(123,111,240,.2);color:#7b6ff0}
body[data-theme="dark"] .backlog{display:flex;flex-direction:column;gap:7px;margin-bottom:20px}
body[data-theme="dark"] .bl-item{background:#0f2027;border-radius:9px;border:1px solid #1a3a4a;padding:11px 14px;border-left-width:3px}
body[data-theme="dark"] .bl-next{border-left-color:#7b6ff0;background:#1a1f35}
body[data-theme="dark"] .bl-todo{border-left-color:#1a3a4a}
body[data-theme="dark"] .bl-done{border-left-color:#1a3a4a;opacity:.4}
body[data-theme="dark"] .bl-header{display:flex;align-items:center;gap:8px;margin-bottom:3px}
body[data-theme="dark"] .bl-badge{font-size:9px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;padding:2px 7px;border-radius:3px}
body[data-theme="dark"] .badge-next{background:rgba(123,111,240,.25);color:#c4b8ff}
body[data-theme="dark"] .badge-todo{background:#1a3a4a;color:#8ab4c4}
body[data-theme="dark"] .badge-done{background:rgba(0,229,160,.15);color:#00e5a0}
body[data-theme="dark"] .bl-title{font-size:13px;font-weight:500;color:#fff}
body[data-theme="dark"] .bl-done .bl-title{text-decoration:line-through;color:#8ab4c4}
body[data-theme="dark"] .bl-detail{font-size:12px;color:#8ab4c4;margin-top:2px;line-height:1.5}
body[data-theme="dark"] .bl-done .bl-detail{color:#5a7a8a}
body[data-theme="dark"] .bl-affects{display:flex;gap:4px;flex-wrap:wrap;margin-top:5px}
body[data-theme="dark"] .bl-chip{font-size:10px;padding:1px 6px;border-radius:3px;background:#1a1f35;color:#7b6ff0;font-weight:500}
body[data-theme="dark"] .notes{background:#0f2027;border-radius:9px;border:1px solid #1a3a4a;padding:12px 15px}
body[data-theme="dark"] .note{font-size:12px;color:#8ab4c4;padding:4px 0;border-bottom:1px solid #1a3a4a;line-height:1.5}
body[data-theme="dark"] .note:last-child{border-bottom:none}
body[data-theme="dark"] .note::before{content:"→ ";color:#5a7a8a}
body[data-theme="dark"] .refresh{font-size:10px;color:#8ab4c4}
body[data-theme="dark"] h1.hdr{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:4px;font-size:17px;font-weight:600;color:#fff}
body[data-theme="dark"] .task.task-last{outline:2px solid #7b6ff0;outline-offset:2px}
body[data-theme="dark"] .sist-badge{font-size:9px;background:#7b6ff0;color:#fff;border-radius:3px;padding:1px 5px;margin-left:3px;vertical-align:middle}
body[data-theme="dark"] #themeToggle:hover{background:rgba(123,111,240,.15)}
</style>
<meta http-equiv="refresh" content="10">
</head>
<body data-theme="dark">
<h1 class="hdr"><span>NM i AI 2026 <span class="refresh">Auto-refresh 10s</span></span><button id="themeToggle" onclick="toggleTheme()" type="button"
  style="width:32px;height:32px;border:none;background:transparent;font-size:18px;line-height:1;cursor:pointer;padding:0;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;">☀️</button></h1>
<div class="meta" id="meta"></div>

<div class="grid4" id="metrics"></div>

<div class="section">Score per komponent</div>
<div class="scores" id="scores"></div>

<div class="section">Tripletex tasks</div>
<div class="task-grid" id="tasks"></div>

<div class="section">Backlog</div>
<div class="backlog" id="backlog"></div>

<div class="section">Notater</div>
<div class="notes" id="notes"></div>

<script>
function toggleTheme() {
  const body = document.body;
  const btn = document.getElementById('themeToggle');
  if (body.dataset.theme === 'dark') {
    body.dataset.theme = 'light';
    btn.textContent = '🌙';
  } else {
    body.dataset.theme = 'dark';
    btn.textContent = '☀️';
  }
}

const data = __DATA__;

(function metaLine() {
  const parts = [];
  if (data.team_name) {
    parts.push((data.team_flag ? data.team_flag + ' ' : '') + data.team_name);
  }
  if (data.tasks_solved) parts.push(data.tasks_solved + ' Tripletex');
  const lrt = data.last_run_task != null && String(data.last_run_task).trim();
  if (lrt) parts.push('Sist kjørt: Task ' + String(data.last_run_task).trim());
  parts.push('Sist oppdatert: ' + data.updated);
  document.getElementById('meta').textContent = parts.join(' · ');
})();

const lb = data.leaderboard;
document.getElementById('metrics').innerHTML = `
  <div class="metric"><div class="mlabel">Plassering</div><div class="mval">#${lb.rank}</div><div class="msub">av ${lb.total_teams} lag</div></div>
  <div class="metric"><div class="mlabel">Totalpoeng</div><div class="mval">${lb.score.toFixed(1)}</div><div class="msub">maks ~100</div></div>
  <div class="metric"><div class="mlabel">Submissions</div><div class="mval">${data.runs_today}</div><div class="msub">totalt</div></div>
  <div class="metric"><div class="mlabel">Deadline</div><div class="mval">15:00</div><div class="msub">22. mars 2026</div></div>
`;

document.getElementById('scores').innerHTML = `
  <div class="score bar-tx"><div class="sname">Tripletex</div><div class="sval">${lb.tripletex.toFixed(1)}</div><div class="bar"><div class="bar-inner" style="width:${lb.tripletex}%"></div></div></div>
  <div class="score bar-ng"><div class="sname">NorgesGruppen</div><div class="sval">${lb.norgesgruppen.toFixed(1)}</div><div class="bar"><div class="bar-inner" style="width:${lb.norgesgruppen}%"></div></div></div>
  <div class="score bar-as"><div class="sname">Astar Island</div><div class="sval">${lb.astar.toFixed(1)}</div><div class="bar"><div class="bar-inner" style="width:${lb.astar}%"></div></div></div>
`;

const taskGrid = document.getElementById('tasks');
data.tasks.forEach(t => {
  const s = t.score;
  let cls, tagCls, tagTxt;
  if (s === null || s === 0) { cls='zero'; tagCls='tag-zero'; tagTxt='0 poeng'; }
  else if (s < 1.0) { cls='low'; tagCls='tag-low'; tagTxt='lav'; }
  else if (s >= 3.5) { cls='great'; tagCls='tag-great'; tagTxt='topp'; }
  else { cls='good'; tagCls='tag-good'; tagTxt='OK'; }
  const isLast = String(t.id) === String(data.last_run_task);
  const d = document.createElement('div');
  d.className = `task ${cls}` + (isLast ? ' task-last' : '');
  d.innerHTML = `
    <div class="tid">Task ${t.id}${isLast
      ? ' <span class="sist-badge">sist</span>'
      : ''}</div>
    <div class="tscore">${s !== null ? s.toFixed(2) : '—'}</div>
    <div class="ttries">${t.tries} forsøk</div>
    <span class="ttag ${tagCls}">${tagTxt}</span>
  `;
  taskGrid.appendChild(d);
});

const bl = document.getElementById('backlog');
data.backlog.forEach(item => {
  const d = document.createElement('div');
  d.className = `bl-item bl-${item.status}`;
  const badgeCls = item.status === 'next' ? 'badge-next' : item.status === 'done' ? 'badge-done' : 'badge-todo';
  const badgeTxt = item.status === 'next' ? 'Neste' : item.status === 'done' ? 'Ferdig' : 'Todo';
  const chips = (item.affects||[]).map(a => `<span class="bl-chip">Task ${a}</span>`).join('');
  d.innerHTML = `
    <div class="bl-header">
      <span class="bl-badge ${badgeCls}">${badgeTxt}</span>
      <span class="bl-title">${item.title}</span>
    </div>
    <div class="bl-detail">${item.detail}</div>
    ${chips ? `<div class="bl-affects">${chips}</div>` : ''}
  `;
  bl.appendChild(d);
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


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/data.json":
            try:
                raw = DATA_FILE.read_text(encoding="utf-8")
                json.loads(raw)
            except Exception as e:
                err = json.dumps({"error": str(e)})
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(err.encode("utf-8"))
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(raw.encode("utf-8"))
            return

        try:
            raw = DATA_FILE.read_text(encoding="utf-8")
            data = json.loads(raw)
        except Exception as e:
            data = {"error": str(e), "updated": "–", "leaderboard": {"rank": 0, "total_teams": 0, "score": 0, "tripletex": 0, "norgesgruppen": 0, "astar": 0}, "runs_today": 0, "last_run_task": "", "tasks": [], "backlog": [], "notes": [str(e)]}

        html = HTML.replace("__DATA__", json.dumps(data))
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, *args):
        pass

if __name__ == "__main__":
    import webbrowser
    print(f"Dashboard: http://localhost:{PORT}")
    print("Oppdater data.json for ny status. Ctrl+C for å stoppe.")
    webbrowser.open(f"http://localhost:{PORT}")
    HTTPServer(("", PORT), Handler).serve_forever()
