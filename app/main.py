"""Rendaí: renda extra com produtos afiliados, gamificado. API + páginas estáticas."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import auth, coletor, jogo, kit, mailer
from app.db import Banco, agora, hoje

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
ESTATICO = Path(__file__).resolve().parent.parent / "static"
COOKIE = "rendai_s"
COMISSAO_ESTIMADA = 0.5   # comissão média dos produtos afiliáveis da Hotmart (até lermos a real com conta de afiliado)
TAXA_PLATAFORMA = 0.10     # parte que a plataforma fica antes de repassar a comissão (aprox.)
# Planos (preço de lançamento). produtos_semana = quantos produtos a pessoa pode pegar para vender por semana (None = sem limite).
PLANOS = {
    "start": {"nome": "Start", "preco": 19.90, "depois": 29.90, "produtos_semana": 5, "kits_semana": 10,
              "itens": ["5 produtos em alta por semana", "Kit de divulgação pronto em cada um", "Missões, níveis e ranking"]},
    "pro": {"nome": "Pro", "preco": 29.90, "depois": 39.90, "produtos_semana": 15, "kits_semana": 40,
            "itens": ["15 produtos por semana", "Kits de divulgação à vontade", "Calculadora de ganhos por produto", "Ranking com prêmios de XP em dobro"]},
    "elite": {"nome": "Elite", "preco": 59.90, "depois": 79.90, "produtos_semana": None, "kits_semana": None,
              "itens": ["Vitrine completa, sem limite", "Alerta dos produtos que começaram a bombar", "Kits ilimitados e roteiro de vídeo", "Suporte prioritário"]},
}


def ganho_por_venda(preco, pct=None) -> float | None:
    if not preco:
        return None
    return round(float(preco) * (pct or COMISSAO_ESTIMADA) * (1 - TAXA_PLATAFORMA), 2)


@dataclass
class Config:
    dados: str = "/data"
    base_url: str = "https://rendai.rangeltech.net"
    llm_base: str = "https://qwen.rangeltech.net/v1"
    llm_key: str = ""
    llm_modelo: str = "qwen-abliterated"
    segredo_sessao: str = "dev"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_senha: str = ""
    smtp_from: str = "Rendaí <contato@rangeltech.net>"
    cakto_segredo: str = ""
    checkout_mensal: str = ""
    dev: bool = False
    coletar: bool = True
    checkouts: dict = None            # {"start": url, "pro": url, "elite": url}
    planos_por_produto: dict = None   # {"<id do produto na Cakto>": "start"|"pro"|"elite"}
    lancamento_ate: str = "2026-11-30"

    def __post_init__(self):
        self.checkouts = self.checkouts or {}
        self.planos_por_produto = self.planos_por_produto or {}


def carregar() -> Config:
    e = os.environ.get
    return Config(dados=e("RENDAI_DADOS", "/data"), base_url=e("RENDAI_BASE_URL", "https://rendai.rangeltech.net"),
                  llm_base=e("RENDAI_LLM_BASE", "https://qwen.rangeltech.net/v1"), llm_key=e("RENDAI_LLM_KEY", ""),
                  llm_modelo=e("RENDAI_LLM_MODELO", "qwen-abliterated"), segredo_sessao=e("RENDAI_SEGREDO_SESSAO", "dev"),
                  smtp_host=e("SMTP_HOST", ""), smtp_port=int(e("SMTP_PORT", "587") or 587), smtp_user=e("SMTP_USER", ""),
                  smtp_senha=e("SMTP_PASSWORD", ""), smtp_from=e("RENDAI_SMTP_FROM", "Rendaí <contato@rangeltech.net>"),
                  cakto_segredo=e("RENDAI_CAKTO_SEGREDO", ""), checkout_mensal=e("RENDAI_CHECKOUT_MENSAL", ""),
                  dev=e("RENDAI_DEV", "") == "1", coletar=e("RENDAI_COLETAR", "1") == "1",
                  checkouts=json.loads(e("RENDAI_CHECKOUTS", "") or "{}"), planos_por_produto=json.loads(e("RENDAI_PLANOS_CAKTO", "") or "{}"),
                  lancamento_ate=e("RENDAI_LANCAMENTO_ATE", "2026-11-30"))


def _rotina_coleta(db: Banco, parar: threading.Event) -> None:
    """Varredura completa 1x por dia; conferência dos produtos da vitrine a cada hora (cada um no máximo a cada 6 h)."""
    log = logging.getLogger("rendai.rotina")
    ultima_varredura = 0.0
    while not parar.is_set():
        try:
            if agora() - ultima_varredura > 24 * 3600 or not db.um("SELECT 1 FROM produtos LIMIT 1"):
                log.info("varredura: %s produtos", coletor.varrer(db))
                ultima_varredura = agora()
            log.info("conferência: %s produtos", coletor.conferir_vitrine(db))
            coletor.temperaturas(db)
        except Exception:  # noqa: BLE001
            log.exception("rotina de coleta falhou")
        parar.wait(3600)


def criar_app(cfg: Config | None = None, iniciar_rotina: bool = True) -> FastAPI:
    cfg = cfg or carregar()
    Path(cfg.dados).mkdir(parents=True, exist_ok=True)
    db = Banco(os.path.join(cfg.dados, "rendai.db"))
    app = FastAPI(title="Rendaí", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.cfg, app.state.db = cfg, db
    if iniciar_rotina and cfg.coletar:
        app.state.parar = threading.Event()
        threading.Thread(target=_rotina_coleta, args=(db, app.state.parar), daemon=True, name="coleta").start()

    def usuario(request: Request, obrigatorio=True):
        u = auth.usuario_da_sessao(db, cfg.segredo_sessao, request.cookies.get(COOKIE))
        if not u and obrigatorio:
            raise HTTPException(401, "entre na sua conta")
        return u

    def plano_de(u: dict) -> dict | None:
        return PLANOS.get(u.get("plano") or "start") if auth.assinante(u) else None

    def limite_semana(u: dict):
        pl = plano_de(u)
        return 0 if not pl else pl["produtos_semana"]

    def pode_pegar(u: dict, pid: str) -> bool:
        lim = limite_semana(u)
        lib = liberados(u)
        return pid in lib or lim is None or len(lib) < lim

    def liberados(u: dict) -> set[str]:
        """Produtos que a pessoa escolheu vender nesta semana (o plano limita quantos)."""
        ini, fim, _ = jogo.semana()
        return {a["produto"] for a in db.todos("SELECT DISTINCT produto FROM acoes WHERE usuario=? AND tipo='afiliar' AND criado BETWEEN ? AND ?", (u["id"], ini, fim))}

    def perfil(u: dict) -> dict:
        assin = auth.assinante(u)
        conq = db.todos("SELECT codigo, criado FROM conquistas WHERE usuario=? ORDER BY criado", (u["id"],))
        comissoes = db.um("SELECT COALESCE(SUM(valor),0) s, COUNT(*) n FROM acoes WHERE usuario=? AND tipo='venda'", (u["id"],))
        return {"email": u["email"], "apelido": u["apelido"] or f"Vendedor {u['id']}", "assinante": assin, "plano": (u.get("plano") if assin else None), "planos": planos_publicos(),
                "nivel": jogo.nivel(u["xp"]), "streak": u["streak"], "fez_checkin_hoje": u["ultimo_dia"] == hoje(),
                "comissoes": round(comissoes["s"], 2), "vendas": comissoes["n"], "escolhidos_semana": len(liberados(u)),
                "limite_semana": limite_semana(u), "ganho_estimado_vendas": round(sum(
                    ganho_por_venda(p["preco"], p["comissao_pct"]) or 0 for p in db.todos(
                        "SELECT preco, comissao_pct FROM produtos WHERE id IN (%s)" % ",".join("?" * len(liberados(u))), tuple(liberados(u)))) if liberados(u) else 0, 2),
                "conquistas": [{"codigo": c["codigo"], "nome": jogo.CONQUISTAS.get(c["codigo"], (c["codigo"], ""))[0],
                                "descricao": jogo.CONQUISTAS.get(c["codigo"], ("", ""))[1]} for c in conq],
                "todas_conquistas": [{"codigo": k, "nome": v[0], "descricao": v[1]} for k, v in jogo.CONQUISTAS.items()]}

    def planos_publicos() -> list[dict]:
        checkouts = cfg.checkouts
        return [{"codigo": k, **v, "checkout": checkouts.get(k) or "", "lancamento_ate": cfg.lancamento_ate} for k, v in PLANOS.items()]

    def produto_publico(p: dict) -> dict:
        out = {k: p.get(k) for k in ("id", "plataforma", "titulo", "imagem", "categoria", "topico", "autor", "nota", "avaliacoes", "alunos", "url",
                                      "temperatura", "frase", "conferido", "preco")}
        out["comissao_pct"] = p.get("comissao_pct") or COMISSAO_ESTIMADA
        out["comissao_estimada"] = not p.get("comissao_pct")
        out["ganho_venda"] = ganho_por_venda(p.get("preco"), p.get("comissao_pct"))
        return out

    @app.get("/health")
    def health():
        return {"ok": True}

    # ------------------------------------------------ login (código no e-mail)
    @app.post("/api/entrar/codigo")
    async def pedir_codigo(request: Request):
        b = await request.json()
        email = auth.normalizar_email(b.get("email"))
        if not email:
            raise HTTPException(422, "e-mail inválido")
        cod, motivo = auth.novo_codigo(db, cfg.segredo_sessao, email)
        if motivo == "espere":
            raise HTTPException(429, "acabamos de mandar um código; espere alguns segundos")
        enviado = mailer.codigo(cfg, email, cod)
        return {"ok": True, "enviado": enviado, **({"codigo_dev": cod} if cfg.dev else {})}

    @app.post("/api/entrar")
    async def entrar(request: Request):
        b = await request.json()
        email = auth.normalizar_email(b.get("email"))
        if not email:
            raise HTTPException(422, "e-mail inválido")
        r = auth.conferir_codigo(db, cfg.segredo_sessao, email, str(b.get("codigo") or ""))
        if r != "ok":
            raise HTTPException(400, {"invalido": "código errado", "expirado": "código vencido; peça outro", "tentativas": "muitas tentativas; peça outro código"}[r])
        u = auth.usuario_por_email(db, email)
        token = auth.criar_sessao(db, cfg.segredo_sessao, u["id"])
        resp = JSONResponse({"ok": True})
        resp.set_cookie(COOKIE, token, max_age=auth.SESSAO_S, httponly=True, secure=not cfg.dev, samesite="lax", path="/")
        return resp

    @app.post("/api/sair")
    def sair(request: Request):
        auth.encerrar_sessao(db, cfg.segredo_sessao, request.cookies.get(COOKIE))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    # ------------------------------------------------ jogo
    @app.get("/api/planos")
    def planos():
        return {"planos": planos_publicos(), "comissao_estimada": COMISSAO_ESTIMADA}

    @app.get("/api/eu")
    def eu(request: Request):
        return perfil(usuario(request))

    @app.post("/api/eu/apelido")
    async def apelido(request: Request):
        u = usuario(request)
        a = re.sub(r"[^\w .-]", "", str((await request.json()).get("apelido") or ""), flags=re.U).strip()[:24]
        if len(a) < 2:
            raise HTTPException(422, "apelido muito curto")
        db.exec("UPDATE usuarios SET apelido=? WHERE id=?", (a, u["id"]))
        return {"ok": True}

    @app.post("/api/acao")
    async def acao(request: Request):
        u = usuario(request)
        b = await request.json()
        tipo = str(b.get("tipo") or "")
        pid = b.get("produto")
        valor = b.get("valor")
        if tipo not in jogo.XP:
            raise HTTPException(422, "ação desconhecida")
        if pid and not db.um("SELECT 1 FROM produtos WHERE id=?", (pid,)):
            raise HTTPException(404, "produto não encontrado")
        if tipo == "afiliar" and not auth.assinante(u):
            raise HTTPException(402, "escolha um plano para começar a vender")
        if tipo == "afiliar" and not pode_pegar(u, pid):
            raise HTTPException(402, f"seu plano libera {limite_semana(u)} produtos por semana; suba de plano para pegar mais")
        if tipo == "venda":
            try:
                valor = round(float(valor), 2)
            except (TypeError, ValueError):
                raise HTTPException(422, "informe a comissão recebida (R$)")
            if not (0 < valor <= 5000):
                raise HTTPException(422, "valor fora do normal")
        return jogo.registrar(db, u["id"], tipo, pid, valor)

    @app.get("/api/missao")
    def missao(request: Request):
        u = usuario(request)
        m = jogo.missao(db, u["id"])
        m["produtos"] = [produto_publico(db.um("SELECT * FROM produtos WHERE id=?", (p["id"],))) for p in m["produtos"]]
        return m

    @app.get("/api/ranking")
    def ranking(request: Request):
        return jogo.ranking(db, usuario(request)["id"])

    # ------------------------------------------------ vitrine
    @app.get("/api/vitrine")
    def vitrine(request: Request, q: str = "", categoria: str = "", limite: int = 30):
        u = usuario(request, obrigatorio=False)
        sql, args = "SELECT * FROM produtos WHERE ativo=1 AND conferido>?", [agora() - 24 * 3600]
        if q:
            sql += " AND (titulo LIKE ? OR topico LIKE ?)"
            args += [f"%{q}%", f"%{q}%"]
        if categoria:
            sql += " AND categoria=?"
            args.append(categoria)
        sql += " ORDER BY temperatura DESC LIMIT ?"
        args.append(max(1, min(60, limite)) if u else 8)  # sem login: só uma amostra
        prods = [produto_publico(p) for p in db.todos(sql, args)]
        cats = db.todos("SELECT categoria, COUNT(*) n FROM produtos WHERE ativo=1 GROUP BY categoria ORDER BY n DESC LIMIT 12")
        return {"produtos": prods, "categorias": cats, "atualizado": db.um("SELECT MAX(conferido) t FROM produtos WHERE ativo=1")["t"]}

    @app.get("/api/produtos/{pid}")
    def produto(pid: str, request: Request):
        u = usuario(request)
        p = db.um("SELECT * FROM produtos WHERE id=? AND ativo=1", (pid,))
        if not p:
            raise HTTPException(404, "esse produto saiu da vitrine (a oferta acabou ou mudou)")
        lib = auth.assinante(u) and pode_pegar(u, pid)
        hist = db.todos("SELECT dia, avaliacoes, alunos FROM historico WHERE produto=? ORDER BY dia DESC LIMIT 14", (pid,))
        return {**produto_publico(p), "descricao": p["descricao"], "liberado": lib, "escolhido": pid in liberados(u), "historico": hist[::-1]}

    @app.post("/api/produtos/{pid}/kit")
    def gerar_kit(pid: str, request: Request):
        u = usuario(request)
        p = db.um("SELECT * FROM produtos WHERE id=? AND ativo=1", (pid,))
        if not p:
            raise HTTPException(404, "produto fora da vitrine")
        if not auth.assinante(u) or pid not in liberados(u):
            raise HTTPException(402, "escolha este produto para vender (botão \"Quero vender\") para liberar o kit")
        try:
            k = kit.gerar(db, cfg, p)
        except Exception:  # noqa: BLE001
            raise HTTPException(503, "não consegui montar o kit agora; tente de novo em instantes")
        jogo.registrar(db, u["id"], "kit", pid)
        return k

    # ------------------------------------------------ Cakto
    @app.post("/api/cakto")
    async def cakto(request: Request):
        cru = await request.body()
        try:
            b = json.loads(cru)
        except ValueError:
            raise HTTPException(400, "json")
        if not cfg.cakto_segredo or not hmac.compare_digest(str(b.get("secret") or ""), cfg.cakto_segredo):
            raise HTTPException(401, "segredo")
        evento = str(b.get("event") or "")
        dados = b.get("data") or {}
        if isinstance(dados, list):
            dados = dados[0] if dados else {}
        eid = str(dados.get("id") or hashlib.sha256(cru).hexdigest()[:24]) + ":" + evento
        if db.um("SELECT 1 FROM eventos_cakto WHERE id=?", (eid,)):
            return {"ok": True, "repetido": True}
        db.exec("INSERT INTO eventos_cakto(id,recebido,corpo) VALUES(?,?,?)", (eid, agora(), cru.decode("utf-8", "replace")[:20000]))
        email = auth.normalizar_email((dados.get("customer") or {}).get("email"))
        if not email:
            return {"ok": True, "ignorado": "sem email"}
        u = auth.usuario_por_email(db, email)
        if evento in ("purchase_approved", "subscription_renewed", "subscription_created"):
            prod = str((dados.get("product") or {}).get("id") or "")
            plano = cfg.planos_por_produto.get(prod, "start")
            db.exec("UPDATE usuarios SET plano=?, ativo_ate=?, pedido=? WHERE id=?", (plano, max(agora(), u["ativo_ate"]) + 32 * 86400, str(dados.get("id") or ""), u["id"]))
            return {"ok": True, "liberado": email}
        if evento in ("refund", "chargeback", "subscription_canceled", "subscription_cancelled"):
            db.exec("UPDATE usuarios SET ativo_ate=? WHERE id=?", (agora(), u["id"]))
            return {"ok": True, "revogado": email}
        return {"ok": True, "ignorado": evento}

    # ------------------------------------------------ páginas
    @app.get("/")
    def inicio():
        return FileResponse(ESTATICO / "index.html")

    @app.get("/app")
    def painel(request: Request):
        if not usuario(request, obrigatorio=False):
            return RedirectResponse("/?entrar=1", status_code=303)
        return FileResponse(ESTATICO / "app.html")

    app.mount("/s", StaticFiles(directory=ESTATICO), name="estatico")
    return app


app = criar_app() if os.environ.get("RENDAI_SEM_APP") != "1" else None
