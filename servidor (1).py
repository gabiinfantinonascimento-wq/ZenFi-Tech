"""ZenFi Tech - servidor (Flask + SQLite).

Render:
  Build Command:  pip install -r requirements.txt
  Start Command:  gunicorn servidor:app
"""
import json
import os
import re
import secrets
import smtplib
import sqlite3
import time
import hashlib
from email.message import EmailMessage

from flask import Flask, g, jsonify, make_response, request, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(BASE, "public")
DB_PATH = os.environ.get("DB_PATH") or (
    "/var/data/zenfi.db" if os.path.isdir("/var/data") else os.path.join(BASE, "zenfi.db")
)
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")
LIMITE_GRATIS = int(os.environ.get("LIMITE_GRATIS", "30"))
TESTE_DIAS = int(os.environ.get("TESTE_DIAS", "14"))
PRECO = os.environ.get("PRECO", "R$ 44,90")
WHATS_NUMERO = os.environ.get("WHATSAPP_NUMERO", "")  # só dígitos, ex.: 5567999999999
COOKIE = "zf_sess"
SYNC_KEY = re.compile(r"^(contas-da-casa|organiza|zenfi):")
MES_KEY = re.compile(r"^contas-da-casa:\d{4}-\d{2}$")
EMAIL_RX = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024


# ---------------------------------------------------------------- banco
def db():
    if "db" not in g:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        g.db = sqlite3.connect(DB_PATH, timeout=15)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def fecha(_):
    c = g.pop("db", None)
    if c:
        c.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            senha_hash TEXT NOT NULL,
            tipo_conta TEXT NOT NULL DEFAULT 'pf',
            documento TEXT NOT NULL DEFAULT '',
            pro_ate INTEGER NOT NULL DEFAULT 0,
            assinatura_ativa INTEGER NOT NULL DEFAULT 0,
            seq INTEGER NOT NULL DEFAULT 0,
            criado INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions(
            token_hash TEXT PRIMARY KEY,
            uid INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            criado INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS kv(
            uid INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            k TEXT NOT NULL,
            v TEXT,
            s INTEGER NOT NULL,
            PRIMARY KEY(uid, k)
        );
        CREATE INDEX IF NOT EXISTS kv_s ON kv(uid, s);
        CREATE TABLE IF NOT EXISTS resets(
            token_hash TEXT PRIMARY KEY,
            uid INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expira INTEGER NOT NULL
        );
        """
    )
    c.commit()
    c.close()


init_db()


# ---------------------------------------------------------------- utilidades
def H(t):
    return hashlib.sha256(t.encode()).hexdigest()


def agora():
    return int(time.time())


def erro(msg, code=400, **extra):
    return jsonify(dict(erro=msg, **extra)), code


def corpo():
    d = request.get_json(silent=True)
    return d if isinstance(d, dict) else {}


_tentativas = {}


def muitas_tentativas(chave, limite=8, janela=600):
    agora_ = time.time()
    lst = [t for t in _tentativas.get(chave, []) if agora_ - t < janela]
    _tentativas[chave] = lst
    return len(lst) >= limite


def registra_tentativa(chave):
    _tentativas.setdefault(chave, []).append(time.time())


def senha_ok(senha, email):
    s = senha or ""
    em = (email or "").strip().lower()
    base = em.split("@")[0]
    if len(s) < 8:
        return "A senha precisa ter pelo menos 8 caracteres."
    if not (re.search(r"[A-Za-z\u00C0-\u00FF]", s) and re.search(r"\d", s)):
        return "A senha precisa ter letras e números."
    if em and (s.lower() == em or (len(base) >= 4 and s.lower() == base)):
        return "A senha não pode ser igual ao seu e-mail."
    return None


def plano_de(u):
    pro = (u["pro_ate"] or 0) > agora()
    return {
        "plano": "pro" if pro else "gratis",
        "teste": bool(pro and not u["assinatura_ativa"]),
        "ate": u["pro_ate"] if pro else None,
        "assinatura_ativa": bool(u["assinatura_ativa"]),
        "preco": PRECO,
        "limite_lancamentos_mes": None if pro else LIMITE_GRATIS,
        "whatsapp": pro,
    }


def user_json(u):
    return {
        "id": u["id"],
        "nome": u["nome"],
        "email": u["email"],
        "tipo_conta": u["tipo_conta"],
        "documento": u["documento"],
        "plano": plano_de(u),
    }


def usuario():
    tok = request.cookies.get(COOKIE)
    if not tok:
        return None
    return db().execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id=s.uid WHERE s.token_hash=?", (H(tok),)
    ).fetchone()


def resposta_com_sessao(u, dados):
    tok = secrets.token_urlsafe(32)
    db().execute("INSERT INTO sessions(token_hash,uid,criado) VALUES(?,?,?)", (H(tok), u["id"], agora()))
    db().commit()
    r = make_response(jsonify(dados))
    r.set_cookie(
        COOKIE, tok, max_age=60 * 86400, httponly=True, samesite="Lax",
        secure=request.is_secure, path="/",
    )
    return r


def exige_login(fn):
    def w(*a, **k):
        u = usuario()
        if not u:
            return erro("Sessão expirada. Entre de novo.", 401)
        g.u = u
        return fn(*a, **k)
    w.__name__ = fn.__name__
    return w


def enviar_email(para, assunto, texto):
    host = os.environ.get("SMTP_HOST")
    if not host:
        print(f"[EMAIL não configurado] Para: {para}\n{assunto}\n{texto}", flush=True)
        return
    m = EmailMessage()
    m["From"] = os.environ.get("SMTP_FROM", os.environ.get("SMTP_USER", "nao-responda@zenfi.app"))
    m["To"] = para
    m["Subject"] = assunto
    m.set_content(texto)
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=15) as s:
        s.starttls()
        if os.environ.get("SMTP_USER"):
            s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASS", ""))
        s.send_message(m)


# ---------------------------------------------------------------- conta
@app.post("/api/cadastro")
def cadastro():
    d = corpo()
    nome = re.sub(r"\s+", " ", str(d.get("nome", "")).strip())[:60]
    email = str(d.get("email", "")).strip().lower()
    senha = str(d.get("senha", ""))
    tipo = "pj" if d.get("tipo") == "pj" else "pf"
    doc = re.sub(r"\D", "", str(d.get("documento", "")))
    if len(nome) < 2:
        return erro("Digite seu nome (pelo menos 2 letras).")
    if not EMAIL_RX.match(email) or len(email) > 120:
        return erro("Digite um e-mail válido, como voce@exemplo.com.")
    msg = senha_ok(senha, email)
    if msg:
        return erro(msg)
    if doc and len(doc) not in (11, 14):
        return erro("Digite um CPF (11 dígitos) ou CNPJ (14 dígitos).")
    if db().execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
        return erro("Já existe uma conta com esse e-mail. Toque em Já tenho conta.", 409)
    cur = db().execute(
        "INSERT INTO users(nome,email,senha_hash,tipo_conta,documento,pro_ate,criado) VALUES(?,?,?,?,?,?,?)",
        (nome, email, generate_password_hash(senha), tipo, doc, agora() + TESTE_DIAS * 86400, agora()),
    )
    db().commit()
    u = db().execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    return resposta_com_sessao(u, user_json(u))


@app.post("/api/entrar")
def entrar():
    d = corpo()
    ident = str(d.get("identificador", "")).strip().lower()
    senha = str(d.get("senha", ""))
    chave = f"{request.remote_addr}|{ident}"
    if muitas_tentativas(chave):
        return erro("Muitas tentativas. Aguarde alguns minutos e tente de novo.", 429)
    u = db().execute("SELECT * FROM users WHERE email=?", (ident,)).fetchone()
    if not u or not check_password_hash(u["senha_hash"], senha):
        registra_tentativa(chave)
        return erro("E-mail ou senha incorretos.", 401)
    _tentativas.pop(chave, None)
    return resposta_com_sessao(u, user_json(u))


@app.get("/api/eu")
def eu():
    u = usuario()
    if not u:
        return erro("Não autenticado.", 401)
    return jsonify(user_json(u))


@app.post("/api/sair")
def sair():
    tok = request.cookies.get(COOKIE)
    if tok:
        db().execute("DELETE FROM sessions WHERE token_hash=?", (H(tok),))
        db().commit()
    r = make_response(jsonify(ok=True))
    r.delete_cookie(COOKIE, path="/")
    return r


@app.post("/api/senha/esqueci")
def esqueci():
    email = str(corpo().get("email", "")).strip().lower()
    msg = "Se esse e-mail tiver conta, enviamos um link para criar uma nova senha. Confira também o spam."
    if not EMAIL_RX.match(email):
        return erro("Digite um e-mail válido.")
    if muitas_tentativas("esqueci|" + request.remote_addr, 5, 900):
        return jsonify(mensagem=msg)
    registra_tentativa("esqueci|" + request.remote_addr)
    u = db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if u:
        tok = secrets.token_urlsafe(32)
        db().execute("INSERT INTO resets VALUES(?,?,?)", (H(tok), u["id"], agora() + 3600))
        db().commit()
        base = BASE_URL or request.host_url.rstrip("/")
        try:
            enviar_email(email, "ZenFi: nova senha",
                         f"Olá, {u['nome']}!\n\nPara criar uma nova senha, abra:\n{base}/#redefinir={tok}\n\n"
                         "O link vale por 1 hora. Se não foi você, ignore este e-mail.")
        except Exception as e:  # não revela falha ao usuário
            print("Falha ao enviar e-mail:", e, flush=True)
    return jsonify(mensagem=msg)


@app.post("/api/senha/redefinir")
def redefinir():
    d = corpo()
    tok, senha = str(d.get("token", "")), str(d.get("senha", ""))
    r = db().execute("SELECT * FROM resets WHERE token_hash=? AND expira>?", (H(tok), agora())).fetchone()
    if not r:
        return erro("Link inválido ou vencido. Peça um novo.", 400)
    u = db().execute("SELECT * FROM users WHERE id=?", (r["uid"],)).fetchone()
    msg = senha_ok(senha, u["email"])
    if msg:
        return erro(msg)
    db().execute("UPDATE users SET senha_hash=? WHERE id=?", (generate_password_hash(senha), u["id"]))
    db().execute("DELETE FROM sessions WHERE uid=?", (u["id"],))
    db().execute("DELETE FROM resets WHERE uid=?", (u["id"],))
    db().commit()
    return jsonify(mensagem="Senha alterada. Entre com a nova senha.")


@app.post("/api/senha/alterar")
@exige_login
def alterar():
    d = corpo()
    u = g.u
    if not check_password_hash(u["senha_hash"], str(d.get("atual", ""))):
        return erro("A senha atual está incorreta.", 403)
    msg = senha_ok(str(d.get("nova", "")), u["email"])
    if msg:
        return erro(msg)
    atual = H(request.cookies.get(COOKIE, ""))
    db().execute("UPDATE users SET senha_hash=? WHERE id=?", (generate_password_hash(d["nova"]), u["id"]))
    db().execute("DELETE FROM sessions WHERE uid=? AND token_hash<>?", (u["id"], atual))
    db().commit()
    return jsonify(mensagem="Senha alterada. Os outros aparelhos foram desconectados.")


@app.post("/api/sessoes/encerrar-outras")
@exige_login
def encerrar_outras():
    atual = H(request.cookies.get(COOKIE, ""))
    c = db().execute("DELETE FROM sessions WHERE uid=? AND token_hash<>?", (g.u["id"], atual))
    db().commit()
    n = c.rowcount
    return jsonify(encerradas=n, mensagem=f"{n} aparelho(s) desconectado(s).")


# ---------------------------------------------------------------- sincronização
def _lista_id(a):
    return {x.get("id"): x for x in a if isinstance(x, dict) and x.get("id")}


def mescla(velho, novo):
    """União por id dos lançamentos do mês (preferindo a versão mais nova do cliente)."""
    try:
        a, b = json.loads(velho), json.loads(novo)
    except Exception:
        return novo
    if not (isinstance(a, dict) and isinstance(b, dict)):
        return novo
    out = dict(b)
    for campo in ("incomes", "bills", "planned"):
        if isinstance(a.get(campo), list) or isinstance(b.get(campo), list):
            m = _lista_id(a.get(campo) or [])
            m.update(_lista_id(b.get(campo) or []))
            out[campo] = list(m.values())
    return json.dumps(out, ensure_ascii=False, separators=(",", ":"))


def conta_lancamentos(v):
    try:
        o = json.loads(v)
        return len(o.get("incomes") or []) + len(o.get("bills") or [])
    except Exception:
        return 0


@app.get("/api/sync")
@exige_login
def sync_get():
    u = g.u
    try:
        desde = int(request.args.get("desde", "0"))
    except ValueError:
        desde = 0
    rows = db().execute(
        "SELECT k,v,s FROM kv WHERE uid=? AND s>? ORDER BY s LIMIT 201", (u["id"], desde)
    ).fetchall()
    return jsonify(
        uid=u["id"], plano=plano_de(u), mais=len(rows) > 200,
        itens=[{"k": r["k"], "v": r["v"], "s": r["s"]} for r in rows[:200]],
    )


@app.post("/api/sync")
@exige_login
def sync_post():
    u = g.u
    pl = plano_de(u)
    itens = corpo().get("itens")
    if not isinstance(itens, list):
        return erro("Pedido inválido.")
    c = db()
    seq = c.execute("SELECT seq FROM users WHERE id=?", (u["id"],)).fetchone()["seq"]
    aceitos, rejeitados, devolver = [], [], []
    for it in itens[:100]:
        if not isinstance(it, dict):
            continue
        k, v, bs = it.get("k"), it.get("v"), int(it.get("bs") or 0)
        if not isinstance(k, str) or not SYNC_KEY.match(k) or len(k) > 120:
            rejeitados.append({"k": str(k)[:120], "motivo": "chave"})
            continue
        if v is not None and (not isinstance(v, str) or len(v) > 2_000_000):
            rejeitados.append({"k": k, "motivo": "tamanho"})
            continue
        cur = c.execute("SELECT v,s FROM kv WHERE uid=? AND k=?", (u["id"], k)).fetchone()
        if v is not None and cur and cur["v"] is not None and cur["s"] > bs and cur["v"] != v:
            v = mescla(cur["v"], v)  # outro aparelho mexeu antes: junta os dois
        if (pl["plano"] == "gratis" and MES_KEY.match(k) and v is not None
                and conta_lancamentos(v) > max(LIMITE_GRATIS, conta_lancamentos(cur["v"]) if cur and cur["v"] else 0)):
            rejeitados.append({"k": k, "motivo": "limite"})
            continue
        seq += 1
        c.execute(
            "INSERT INTO kv(uid,k,v,s) VALUES(?,?,?,?) ON CONFLICT(uid,k) DO UPDATE SET v=excluded.v,s=excluded.s",
            (u["id"], k, v, seq),
        )
        aceitos.append({"k": k, "s": seq})
        if v != it.get("v"):
            devolver.append({"k": k, "v": v, "s": seq})
    c.execute("UPDATE users SET seq=? WHERE id=?", (seq, u["id"]))
    c.commit()
    return jsonify(aceitos=aceitos, rejeitados=rejeitados, devolver=devolver, plano=pl)


# ---------------------------------------------------------------- assinatura e WhatsApp (ainda não ligados)
@app.post("/api/assinatura/checkout")
@exige_login
def checkout():
    return erro("Pagamento ainda não configurado neste servidor.", 501)


@app.post("/api/assinatura/portal")
@exige_login
def portal():
    return erro("Pagamento ainda não configurado neste servidor.", 501)


@app.get("/api/whatsapp/vinculo")
@exige_login
def wa_vinculo():
    return jsonify(vinculado=False, telefone="", numero_oficial=WHATS_NUMERO, configurado=False, plano=plano_de(g.u))


@app.post("/api/whatsapp/vinculo/codigo")
@exige_login
def wa_codigo():
    return erro("WhatsApp ainda não configurado neste servidor.", 501)


@app.post("/api/whatsapp/desvincular")
@exige_login
def wa_off():
    return jsonify(ok=True)


# ---------------------------------------------------------------- arquivos do app
@app.get("/healthz")
def healthz():
    return "ok"


@app.get("/")
def index():
    r = send_from_directory(PUBLIC, "index.html")
    r.headers["Cache-Control"] = "no-cache"
    return r


@app.get("/<path:nome>")
def estatico(nome):
    if nome.startswith("api/") or not re.search(r"\.(webmanifest|js|png|svg|ico|json|woff2?)$", nome):
        return erro("Não encontrado.", 404)
    return send_from_directory(PUBLIC, nome)


@app.after_request
def seguranca(r):
    r.headers.setdefault("X-Content-Type-Options", "nosniff")
    r.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.path.startswith("/api/"):
        r.headers["Cache-Control"] = "no-store"
    return r


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), debug=False)
