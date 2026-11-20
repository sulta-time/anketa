# -*- coding: utf-8 -*-
"""
Сервер анкетирования «ИИ в обучении».
- Ученик открывает / , отвечает, ответы уходят POST /submit.
- Каждому браузеру выдаётся свой ID (кука). Повторный ответ заменяет
  прежний ответ этого же человека, чтобы не засчитывать его дважды.
- Админ открывает /admin и вводит пароль: видит, сколько человек прошло,
  список и круговые диаграммы по каждому вопросу.

Запуск:
    python server.py            # порт 8080, пароль админа admin123
    python server.py --port 9000 --password secret

Данные сохраняются в файл responses.json рядом со скриптом.
"""
import argparse
import json
import os
import sqlite3
import sys
import threading
import uuid
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Данные можно хранить в отдельной папке (DATA_DIR). Так они переживут
# перезапуск облачного сервера, если подключить постоянный диск.
DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "responses.db")
HTML_PATH = os.path.join(BASE_DIR, "anketa.html")

COOKIE_NAME = "anketa_uid"

# Вопросы/варианты должны совпадать с anketa.html
QUESTIONS = [
    ("Пользуюсь ли ты ИИ в учебе?", ["Да", "Нет"]),
    ("Какие AI-сервисы используешь чаще всего?",
     ["Чат-боты (ChatGPT и аналоги)", "Генерация картинок", "Переводчики", "Не использую"]),
    ("Как часто ты используешь ИИ?",
     ["Каждый день", "Несколько раз в неделю", "Редко", "Не использую"]),
    ("Помогает ли ИИ понимать материал?",
     ["Да, сильно помогает", "Немного помогает", "Не помогает"]),
    ("Используешь ли ИИ для повторения предметов?", ["Да", "Нет"]),
    ("Для каких предметов используешь ИИ?",
     ["Математика и физика", "Языки (русский/иностранный)", "История и обществознание", "Другое"]),
    ("Готов ли ты применять ИИ при подготовке?",
     ["Готов, активно использую", "Готов, но редко", "Не готов"]),
    ("Насколько удобны ИИ-сервисы?",
     ["Очень удобно", "Достаточно удобно", "Не очень удобно", "Совсем неудобно"]),
]


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS responses ("
        "uid TEXT PRIMARY KEY, answers TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()


def read_html():
    with open(HTML_PATH, "r", encoding="utf-8") as f:
        return f.read()


class SurveyServer(BaseHTTPRequestHandler):
    server_version = "SurveyServer/1.0"

    # ---------- служебное ----------
    def log_message(self, fmt, *args):
        sys.stderr.write("LOG %s - %s\n" % (self.address_string(), fmt % args))

    def _cookie_uid(self):
        cookie = SimpleCookie()
        uid = (str(uuid.uuid4())).replace("-", "")
        raw = self.headers.get("Cookie")
        if raw:
            cookie.load(raw)
            if COOKIE_NAME in cookie:
                return cookie[COOKIE_NAME].value, False
        return uid, True

    def _send_json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, code, text, content_type="text/html; charset=utf-8"):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _set_uid_cookie(self, uid, new=True):
        self.send_header(
            "Set-Cookie",
            "%s=%s; Path=/; Max-Age=%d; HttpOnly" % (COOKIE_NAME, uid, 60 * 60 * 24 * 365),
        )

    # ---------- маршруты ----------
    def do_GET(self):
        path = self.path.split("?")[0]

        if path in ("/", "/index.html", "/anketa.html"):
            uid, is_new = self._cookie_uid()
            html = read_html()
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            if is_new:
                self._set_uid_cookie(uid)
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/admin":
            self._serve_admin_page()
            return

        if path == "/admin/api/data":
            self._serve_admin_data()
            return

        if path == "/admin/api/login":
            self._check_login()
            return

        self._send_text(404, "Страница не найдена")

    def _serve_admin_page(self):
        page = ADMIN_HTML
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_admin_data(self):
        pwd = self.path.split("?")
        provided = ""
        if len(pwd) > 1:
            qs = pwd[1]
            for part in qs.split("&"):
                if part.startswith("password="):
                    provided = part.split("=", 1)[1]
        import urllib.parse
        provided = urllib.parse.unquote(provided)

        if provided != SERVER_PASSWORD:
            self._send_json(403, {"error": "Неверный пароль"})
            return

        conn = get_db()
        rows = conn.execute("SELECT uid, answers FROM responses").fetchall()
        conn.close()

        total = len(rows)
        counts = [[0] * len(q[1]) for q in QUESTIONS]
        for r in rows:
            try:
                arr = json.loads(r["answers"])
            except Exception:
                continue
            if not isinstance(arr, list):
                continue
            for qi, q in enumerate(QUESTIONS):
                if qi < len(arr) and isinstance(arr[qi], int):
                    oi = arr[qi]
                    if 0 <= oi < len(q[1]):
                        counts[qi][oi] += 1

        data = {
            "total": total,
            "questions": [
                {"text": q[0], "options": q[1], "counts": counts[i]}
                for i, q in enumerate(QUESTIONS)
            ],
        }
        self._send_json(200, data)

    def _check_login(self):
        # простой GET с ?password=... идёт через /admin/api/login
        self._serve_admin_data()

    def do_POST(self):
        path = self.path.split("?")[0]
        if path != "/submit":
            self._send_json(404, {"error": "not found"})
            return

        uid, is_new = self._cookie_uid()
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send_json(400, {"error": "Неверный формат данных"})
            return

        answers = payload.get("answers")
        if not isinstance(answers, list) or len(answers) != len(QUESTIONS):
            self._send_json(400, {"error": "Нужно ответить на все вопросы"})
            return

        for v in answers:
            if not isinstance(v, int):
                self._send_json(400, {"error": "Некорректный ответ"})
                return

        # валидация диапазонов
        for qi, v in enumerate(answers):
            if not (0 <= v < len(QUESTIONS[qi][1])):
                self._send_json(400, {"error": "Некорректный вариант ответа"})
                return

        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        conn = get_db()
        # UPSERT: повторный ответ этого же человека заменяет старый
        conn.execute(
            "INSERT INTO responses (uid, answers, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(uid) DO UPDATE SET answers=excluded.answers, "
            "created_at=excluded.created_at",
            (uid, json.dumps(answers, ensure_ascii=False), now),
        )
        conn.commit()
        conn.close()

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", "2")
        if is_new:
            self._set_uid_cookie(uid)
        self.end_headers()
        self.wfile.write(b"{}")


ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Админ — Результаты анкеты</title>
<style>
  body { font-family: "Segoe UI", Arial, sans-serif; margin: 0; background: #f4f6f8; color: #222; }
  header { background: #1f3a5f; color: #fff; padding: 22px 20px; text-align: center; }
  header h1 { margin: 0 0 6px; font-size: 24px; }
  header p { margin: 0; opacity: .9; }
  .container { max-width: 780px; margin: 0 auto; padding: 24px 16px 60px; }
  .login { max-width: 360px; margin: 60px auto; background: #fff; border-radius: 12px; padding: 24px; box-shadow: 0 2px 10px rgba(0,0,0,.08); }
  .login h2 { margin-top: 0; color: #1f3a5f; }
  input[type=password] { width: 100%; padding: 12px; border: 2px solid #d5dbe2; border-radius: 8px; font-size: 15px; margin-bottom: 14px; }
  button { background: #1f3a5f; color: #fff; border: none; border-radius: 8px; padding: 12px 20px; font-size: 15px; cursor: pointer; width: 100%; }
  button:hover { background: #17304e; }
  .err { color: #c0392b; margin-top: 10px; text-align: center; }
  .stat { background: #fff; border-radius: 12px; padding: 20px; margin-bottom: 18px; box-shadow: 0 2px 8px rgba(0,0,0,.06); display: flex; gap: 24px; flex-wrap: wrap; align-items: center; }
  .big { font-size: 40px; font-weight: 700; color: #1f3a5f; }
  .card { background: #fff; border-radius: 12px; padding: 20px; margin-bottom: 18px; box-shadow: 0 2px 8px rgba(0,0,0,.06); }
  .card h2 { font-size: 17px; color: #1f3a5f; margin: 0 0 14px; }
  .row { display: flex; align-items: center; gap: 20px; flex-wrap: wrap; }
  .legend { flex: 1; min-width: 180px; font-size: 14px; }
  .legend .item { display: flex; align-items: center; margin-bottom: 6px; }
  .swatch { width: 14px; height: 14px; border-radius: 3px; margin-right: 8px; display: inline-block; }
  canvas { width: 150px; height: 150px; }
  #wrong { display: none; }
</style>
</head>
<body>
<header><h1>Админ-панель анкетирования</h1><p>Результаты опроса по использованию ИИ в обучении</p></header>
<div class="container">
  <div id="login" class="login">
    <h2>Вход для администратора</h2>
    <input type="password" id="pwd" placeholder="Пароль">
    <button id="go">Войти</button>
    <div id="err" class="err"></div>
  </div>
  <div id="panel" style="display:none">
    <div class="stat">
      <div><div class="big" id="total">0</div><div style="color:#666">человек прошли анкету</div></div>
      <button id="refresh" style="width:auto; margin-left:auto">Обновить</button>
    </div>
    <div id="charts"></div>
  </div>
</div>
<script>
var PWD = "";
const PALETTE = ["#3b82c4","#f28c28","#5cb85c","#f2c744"];
document.getElementById("go").addEventListener("click", load);
document.getElementById("pwd").addEventListener("keydown", function(e){ if(e.key==="Enter") load(); });
document.getElementById("refresh").addEventListener("click", load);

function load(){
  var pwd = document.getElementById("pwd").value;
  if(!pwd && !PWD){ showErr("Введите пароль"); return; }
  if(pwd) PWD = pwd;
  fetch("/admin/api/data?password="+encodeURIComponent(PWD))
    .then(function(r){ return r.json().then(function(j){ return {ok:r.ok, j:j}; }); })
    .then(function(res){
      if(!res.ok){
        showErr(res.j.error || "Неверный пароль");
        return;
      }
      showErr("");
      render(res.j);
    })
    .catch(function(){ showErr("Ошибка соединения с сервером"); });
}
function showErr(m){
  var e=document.getElementById("err");
  e.textContent=m;
}
function render(d){
  document.getElementById("login").style.display="none";
  document.getElementById("panel").style.display="block";
  document.getElementById("total").textContent = d.total;
  var wrap = document.getElementById("charts");
  wrap.innerHTML = "";
  d.questions.forEach(function(q,qi){
    var card=document.createElement("div"); card.className="card";
    var h=document.createElement("h2"); h.textContent=(qi+1)+". "+q.text; card.appendChild(h);
    var row=document.createElement("div"); row.className="row";
    var cv=document.createElement("canvas"); cv.width=300; cv.height=300;
    var legend=document.createElement("div"); legend.className="legend";
    var total=0; q.counts.forEach(function(c){total+=c;});
    q.options.forEach(function(opt,oi){
      var item=document.createElement("div"); item.className="item";
      var sw=document.createElement("span"); sw.className="swatch"; sw.style.background=PALETTE[oi%PALETTE.length];
      var pct= total? Math.round(q.counts[oi]/total*100):0;
      var span=document.createElement("span");
      span.textContent=opt+" — "+q.counts[oi]+" чел. ("+pct+"%)";
      item.appendChild(sw); item.appendChild(span); legend.appendChild(item);
    });
    row.appendChild(cv); row.appendChild(legend);
    card.appendChild(row); wrap.appendChild(card);
    drawPie(cv, q.counts, total);
  });
}
function drawPie(cv, counts, total){
  var ctx=cv.getContext("2d"), cx=cv.width/2, cy=cv.height/2, r=cv.width/2-8;
  ctx.clearRect(0,0,cv.width,cv.height);
  if(!total){
    ctx.beginPath(); ctx.arc(cx,cy,r*0.6,0,Math.PI*2);
    ctx.lineWidth=3; ctx.strokeStyle="#ccc"; ctx.stroke();
    ctx.fillStyle="#666"; ctx.font="16px Arial"; ctx.textAlign="center";
    ctx.fillText("Нет данных",cx,cy); return;
  }
  var start=-Math.PI/2;
  counts.forEach(function(c,i){
    if(!c) return;
    var a=c/total*Math.PI*2;
    ctx.beginPath(); ctx.moveTo(cx,cy); ctx.arc(cx,cy,r,start,start+a); ctx.closePath();
    ctx.fillStyle=PALETTE[i%PALETTE.length]; ctx.fill(); start+=a;
  });
  ctx.beginPath(); ctx.arc(cx,cy,r*0.55,0,Math.PI*2); ctx.fillStyle="#fff"; ctx.fill();
  ctx.fillStyle="#333"; ctx.font="bold 18px Arial"; ctx.textAlign="center"; ctx.textBaseline="middle";
  ctx.fillText(total+" чел.",cx,cy);
}
</script>
</body>
</html>"""


def main():
    global SERVER_PASSWORD
    import os as _os
    parser = argparse.ArgumentParser(description="Сервер анкетирования")
    parser.add_argument("--port", type=int, default=int(_os.environ.get("PORT", "8080")),
                        help="Порт (по умолчанию берётся из PORT или 8080)")
    parser.add_argument("--password", default=_os.environ.get("ADMIN_PASSWORD", "admin123"),
                        help="Пароль админа (по умолчанию из ADMIN_PASSWORD или admin123)")
    parser.add_argument("--host", default="0.0.0.0", help="Адрес (по умолчанию все интерфейсы)")
    args = parser.parse_args()
    SERVER_PASSWORD = args.password

    init_db()
    httpd = ThreadingHTTPServer((args.host, args.port), SurveyServer)
    print("=" * 60)
    print("Сервер анкетирования запущен.")
    print("  Анкета для учеников:  http://localhost:%d/" % args.port)
    local_ip = None
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass
    if local_ip:
        print("  Адрес в локальной сети:  http://%s:%d/" % (local_ip, args.port))
    print("  Админ-панель:         http://localhost:%d/admin" % args.port)
    print("  Пароль админа:         %s" % args.password)
    print("  Результаты хранятся в: %s" % DB_PATH)
    print("  Перевод в Excel: запусти python export.py")
    print("=" * 60)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nСервер остановлен.")
        sys.exit(0)


if __name__ == "__main__":
    main()