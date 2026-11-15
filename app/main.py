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
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app import senha_rotas
from app import afiliado, auth, carteira, repasse, coletor, jogo, kit, mailer
from app.db import Banco, agora, hoje

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
ESTATICO = Path(__file__).resolve().parent.parent / "static"
log = logging.getLogger("rendai")
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
    llm_planejador: str = "rt-planejar"
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
    admin_key: str = ""
    demo_email: str = ""
    demo_senha: str = ""
    afiliados: dict = None            # {"hotmart": {"cookie": "..."}} sessão de conta de afiliado (cofre: rendai-afiliados.env)
    hotmart: dict = None              # {"client_id","client_secret","basic"} API da conta da casa (vendas do link Rendaí)
    taxa_casa: float = 0.50           # parte da comissão que fica com a casa no link Rendaí

    def __post_init__(self):
        self.checkouts = self.checkouts or {}
        self.planos_por_produto = self.planos_por_produto or {}
        self.afiliados = self.afiliados or {}
        self.hotmart = self.hotmart or {}


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
                  lancamento_ate=e("RENDAI_LANCAMENTO_ATE", "2026-11-30"), admin_key=e("RENDAI_ADMIN_KEY", ""),
                  demo_email=e("RENDAI_DEMO_EMAIL", ""), demo_senha=e("RENDAI_DEMO_SENHA", ""),
                  afiliados=json.loads(e("RENDAI_AFILIADOS", "") or "{}"), hotmart=json.loads(e("RENDAI_HOTMART", "") or "{}"),
                  taxa_casa=float(e("RENDAI_TAXA_CASA", "0.50") or 0.50))


def _rotina_coleta(db: Banco, parar: threading.Event, cfg_rotina=None) -> None:
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
            if cfg_rotina is not None and afiliado.sessoes(cfg_rotina):
                afiliado.enriquecer(db, cfg_rotina)
            carteira.liberar_vencidas(db)
            if cfg_rotina is not None and cfg_rotina.hotmart:
                log.info("link Rendaí: %s", carteira.sincronizar(db, cfg_rotina))
        except Exception:  # noqa: BLE001
            log.exception("rotina de coleta falhou")
        parar.wait(3600)


def criar_app(cfg: Config | None = None, iniciar_rotina: bool = True) -> FastAPI:
    cfg = cfg or carregar()
    Path(cfg.dados).mkdir(parents=True, exist_ok=True)
    db = Banco(os.path.join(cfg.dados, "rendai.db"))
    app = FastAPI(title="Rendaí", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.cfg, app.state.db = cfg, db
    if cfg.demo_email:  # conta de demonstração: sempre com o plano Elite ativo
        u = auth.usuario_por_email(db, cfg.demo_email.lower())
        db.exec("UPDATE usuarios SET plano='elite', ativo_ate=?, apelido=COALESCE(apelido,'Demo') WHERE id=?", (agora() + 3650 * 86400, u["id"]))
    if iniciar_rotina and cfg.coletar:
        app.state.parar = threading.Event()
        threading.Thread(target=_rotina_coleta, args=(db, app.state.parar, cfg), daemon=True, name="coleta").start()

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
        out["link_rendai_disp"] = bool(p.get("link_casa"))
        out.pop("link_casa", None)
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


    def _login(u: dict) -> JSONResponse:
        token = auth.criar_sessao(db, cfg.segredo_sessao, u["id"])
        resp = JSONResponse({"ok": True})
        resp.set_cookie(COOKIE, token, max_age=auth.SESSAO_S, httponly=True, secure=not cfg.dev, samesite="lax", path="/")
        return resp

    senha_rotas.registrar(app, db, cfg, mailer, _login, usuario)  # senha (padrão), código (opção), esqueci a senha

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

    @app.get("/api/meus")
    def meus(request: Request):
        """Produtos que a pessoa escolheu vender (mais recentes primeiro), só os que continuam na vitrine."""
        u = usuario(request)
        ids = [r["produto"] for r in db.todos("SELECT produto, MAX(criado) c FROM acoes WHERE usuario=? AND tipo='afiliar' AND produto IS NOT NULL GROUP BY produto ORDER BY c DESC LIMIT 20", (u["id"],))]
        out = []
        for pid in ids:
            p = db.um("SELECT * FROM produtos WHERE id=? AND ativo=1", (pid,))
            if p:
                out.append(produto_publico(p))
        return {"produtos": out}

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
        sql += " ORDER BY (link_casa IS NOT NULL AND link_casa != '') DESC, temperatura DESC LIMIT ?"  # Link Rendaí primeiro (vende sem se afiliar)
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
        ml = db.um("SELECT link FROM links WHERE usuario=? AND produto=?", (u["id"], pid))
        termos_ok = u.get("termos_versao") == repasse.TERMOS_VERSAO
        lr = f"{cfg.base_url}/r/{carteira.codigo_ref(db, u)}/{pid}" if p.get("link_casa") and lib and termos_ok else ""
        return {**produto_publico(p), "link_rendai": lr, "taxa_casa": cfg.taxa_casa, "descricao": p["descricao"], "liberado": lib, "escolhido": pid in liberados(u), "historico": hist[::-1], "meu_link": ml["link"] if ml else "",
                "precisa_termos": bool(p.get("link_casa")) and lib and not termos_ok, "cai_em": repasse.estimativa_produto(p.get("plataforma"))}

    @app.post("/api/produtos/{pid}/link")
    async def salvar_link(pid: str, request: Request):
        """O link de afiliado da pessoa para este produto: entra no kit pronto para copiar."""
        u = usuario(request)
        b = await request.json()
        link = str(b.get("link") or "").strip()
        if link and not afiliado.link_valido(link):
            raise HTTPException(422, "esse não parece um link de afiliado (Hotmart, Cakto, Kiwify, Eduzz, Braip ou Monetizze)")
        if link:
            db.exec("INSERT INTO links(usuario,produto,link,criado) VALUES(?,?,?,?) ON CONFLICT(usuario,produto) DO UPDATE SET link=excluded.link", (u["id"], pid, link, agora()))
        else:
            db.exec("DELETE FROM links WHERE usuario=? AND produto=?", (u["id"], pid))
        return {"ok": True, "link": link}

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
        ml = db.um("SELECT link FROM links WHERE usuario=? AND produto=?", (u["id"], pid))
        if not ml and p.get("link_casa"):
            ml = {"link": carteira.link_rendai(p["link_casa"], carteira.codigo_ref(db, u))}
        if ml:  # o kit é o mesmo para todos; o link de cada um entra na hora de entregar
            k = {**k, "link": ml["link"], "posts": [f"{x}\n\n{ml['link']}" for x in k.get("posts") or []],
                 "whatsapp": (k.get("whatsapp") or "") + (f"\n\n{ml['link']}" if k.get("whatsapp") else "")}
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


    # ------------------------------------------------ admin (painel do ecossistema no llm-access)
    def exigir_admin(request: Request):
        chave = request.headers.get("x-admin-key") or ""
        if not cfg.admin_key or not hmac.compare_digest(chave, cfg.admin_key):
            raise HTTPException(401, "chave de admin")

    @app.get("/api/admin/resumo")
    def admin_resumo(request: Request):
        exigir_admin(request)
        ativos = db.todos("SELECT plano, COUNT(*) n FROM usuarios WHERE ativo_ate>? GROUP BY plano", (agora(),))
        receita = sum(r["n"] * (PLANOS.get(r["plano"]) or PLANOS["start"])["preco"] for r in ativos)
        sem = agora() - 7 * 86400
        return {"produto": "rendai", "nome": "Rendaí", "url": cfg.base_url,
                "assinantes": sum(r["n"] for r in ativos), "por_plano": {r["plano"]: r["n"] for r in ativos},
                "receita_mensal": round(receita, 2), "cadastros": db.um("SELECT COUNT(*) n FROM usuarios")["n"],
                "produtos_vitrine": db.um("SELECT COUNT(*) n FROM produtos WHERE ativo=1")["n"],
                "acoes_7d": db.um("SELECT COUNT(*) n FROM acoes WHERE criado>?", (sem,))["n"],
                "vendas_registradas_7d": db.um("SELECT COUNT(*) n FROM acoes WHERE tipo='venda' AND criado>?", (sem,))["n"],
                "planos": planos_publicos()}

    @app.get("/api/carteira")
    def ver_carteira(request: Request):
        u = usuario(request)
        return {**carteira.resumo(db, u["id"]), "taxa_casa": cfg.taxa_casa}

    @app.post("/api/carteira/pix")
    async def salvar_pix(request: Request):
        u = usuario(request)
        pix = str((await request.json()).get("pix") or "").strip()[:120]
        if len(pix) < 5:
            raise HTTPException(422, "informe a chave PIX (CPF, e-mail, telefone ou chave aleatória)")
        db.exec("UPDATE usuarios SET pix=? WHERE id=?", (pix, u["id"]))
        return carteira.resumo(db, u["id"])

    @app.post("/api/termos/aceitar")
    async def aceitar_termos(request: Request):
        u = usuario(request)
        b = await request.json()
        if b.get("versao") != repasse.TERMOS_VERSAO:
            raise HTTPException(409, "os termos mudaram; recarregue a página")
        db.exec("UPDATE usuarios SET termos_versao=?, termos_aceite=? WHERE id=?", (repasse.TERMOS_VERSAO, str(agora()), u["id"]))
        return {"ok": True, "versao": repasse.TERMOS_VERSAO}

    @app.post("/api/carteira/recebedor")
    async def salvar_recebedor(request: Request):
        u = usuario(request)
        b = await request.json()
        nome = str(b.get("nome") or "").strip()[:120]
        doc = repasse.so_digitos(str(b.get("doc") or ""))
        pix = str(b.get("pix") or "").strip()[:120]
        if len(nome) < 5:
            raise HTTPException(422, "informe o nome completo (ou a razão social)")
        if not repasse.doc_ok(doc):
            raise HTTPException(422, "CPF ou CNPJ inválido")
        erro = repasse.chave_pix_ok(pix, doc)
        if erro:
            raise HTTPException(422, erro)
        db.exec("UPDATE usuarios SET rec_nome=?, rec_doc=?, pix=? WHERE id=?", (nome, doc, pix, u["id"]))
        return carteira.resumo(db, u["id"])

    @app.get("/r/{codigo}/{pid}")
    def redirecionar(codigo: str, pid: str, request: Request):
        """Link divulgado pelo cliente: registra o clique e manda para o link da casa com o código dele."""
        if not re.fullmatch(r"[0-9a-f]{8}", codigo):
            raise HTTPException(404, "link inválido")
        p = db.um("SELECT link_casa FROM produtos WHERE id=?", (pid,))
        if not p or not p["link_casa"]:
            return RedirectResponse(cfg.base_url, status_code=302)
        db.exec("INSERT INTO cliques(codigo,produto,criado,origem) VALUES(?,?,?,?)", (codigo, pid, agora(), (request.headers.get("referer") or "")[:200]))
        return RedirectResponse(carteira.link_rendai(p["link_casa"], codigo), status_code=302)

    @app.get("/termos")
    def termos():
        return FileResponse(ESTATICO / "termos.html")

    @app.get("/admin/pagamentos")
    def admin_pagamentos_pagina():
        return FileResponse(ESTATICO / "admin-pagamentos.html")

    @app.post("/api/carteira/saque")
    def sacar(request: Request):
        u = usuario(request)
        try:
            return carteira.pedir_saque(db, u["id"])
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.post("/api/admin/produtos/{pid}/link-casa")
    async def admin_link_casa(pid: str, request: Request):
        exigir_admin(request)
        link = str((await request.json()).get("link") or "").strip()
        if link and not afiliado.link_valido(link):
            raise HTTPException(422, "link inválido")
        db.exec("UPDATE produtos SET link_casa=? WHERE id=?", (link or None, pid))
        return {"ok": True, "produto": pid, "link_casa": link}

    @app.post("/api/admin/vendas")
    async def admin_vendas(request: Request):
        """Importa vendas do link Rendaí (lista JSON: transacao, sck, comissao, status, garantia_ate, produto)."""
        exigir_admin(request)
        itens = await request.json()
        out: dict[str, int] = {}
        for v in itens if isinstance(itens, list) else []:
            if v.get("transacao"):
                r = carteira.registrar_venda(db, cfg.taxa_casa, v)
                out[r] = out.get(r, 0) + 1
        return out

    @app.post("/api/admin/hotmart/sincronizar")
    def admin_sync(request: Request, dias: int = 45):
        exigir_admin(request)
        return carteira.sincronizar(db, cfg, dias)

    @app.get("/api/admin/saques")
    def admin_saques(request: Request):
        exigir_admin(request)
        lst = db.todos("SELECT s.*, u.email FROM saques s JOIN usuarios u ON u.id=s.usuario ORDER BY CASE s.estado WHEN 'pedido' THEN 0 ELSE 1 END, COALESCE(s.prazo, s.criado) LIMIT 300")
        a_pagar = [x for x in lst if x["estado"] == "pedido"]
        recebido = db.um("SELECT COALESCE(SUM(comissao),0) s FROM vendas_casa WHERE estado='liberada'")["s"]
        pago = db.um("SELECT COALESCE(SUM(valor),0) s FROM saques WHERE estado='pago'")["s"]
        return {"saques": lst, "a_pagar_total": round(sum(x["valor"] for x in a_pagar), 2), "a_pagar_n": len(a_pagar),
                "recebido_das_plataformas": round(recebido, 2), "ja_pago": round(pago, 2),
                "alerta": round(sum(x["valor"] for x in a_pagar) + pago, 2) > round(recebido, 2),
                "casa": db.um("SELECT COALESCE(SUM(parte_casa),0) casa, COALESCE(SUM(comissao),0) bruto, COUNT(*) n FROM vendas_casa WHERE estado!='reembolsada'")}

    @app.post("/api/admin/saques/{sid}/pago")
    async def admin_saque_pago(sid: int, request: Request):
        """Marca como pago. Corpo opcional: JSON {transacao_pix} ou o comprovante cru (imagem/PDF) com x-arquivo."""
        exigir_admin(request)
        s = db.um("SELECT s.*, u.email FROM saques s JOIN usuarios u ON u.id=s.usuario WHERE s.id=? AND s.estado='pedido'", (sid,))
        if not s:
            raise HTTPException(404, "saque não encontrado ou já resolvido")
        comp, tx = None, None
        tipo = request.headers.get("content-type", "")
        bruto = await request.body()
        if bruto and "json" in tipo:
            tx = str((json.loads(bruto) or {}).get("transacao_pix") or "")[:80] or None
        elif bruto:
            nome = (request.headers.get("x-arquivo") or "comprovante.png").lower()
            ext = nome.rsplit(".", 1)[-1] if "." in nome else "png"
            if ext not in ("png", "jpg", "jpeg", "pdf"):
                raise HTTPException(422, "comprovante: PNG, JPG ou PDF")
            pasta = Path(cfg.dados) / "comprovantes"
            pasta.mkdir(parents=True, exist_ok=True)
            comp = f"saque{sid}.{ext}"
            (pasta / comp).write_bytes(bruto[:10 * 1024 * 1024])
        db.exec("UPDATE saques SET estado='pago', pago=?, comprovante=COALESCE(?,comprovante), transacao_pix=COALESCE(?,transacao_pix) WHERE id=?", (agora(), comp, tx, sid))
        try:
            v = f"R$ {s['valor']:.2f}".replace(".", ",")
            mailer.enviar(cfg, s["email"], f"Seu saque de {v} foi pago", f"Pagamos {v} na sua chave Pix {s['pix']}. Confira no seu banco.",
                          f'<p style="font-size:16px">Pagamos <b>{v}</b> na sua chave Pix <b>{s["pix"]}</b>.</p><p>Confira no seu banco e na aba Carteira do Rendaí.</p>')
        except Exception as e:  # noqa: BLE001
            log.warning("aviso de saque pago falhou: %s", e)
        return {"ok": True}

    @app.post("/api/admin/saques/{sid}/recusar")
    async def admin_saque_recusar(sid: int, request: Request):
        exigir_admin(request)
        motivo = str((await request.json()).get("motivo") or "").strip()[:300]
        if len(motivo) < 5:
            raise HTTPException(422, "diga o motivo (o cliente vê)")
        cur = db.exec("UPDATE saques SET estado='recusado', motivo=?, pago=? WHERE id=? AND estado='pedido'", (motivo, agora(), sid))
        if not cur.rowcount:
            raise HTTPException(404, "saque não encontrado ou já resolvido")
        return {"ok": True}

    @app.get("/api/admin/saques/{sid}/comprovante")
    def admin_comprovante(sid: int, request: Request):
        exigir_admin(request)
        s = db.um("SELECT comprovante FROM saques WHERE id=?", (sid,))
        if not s or not s["comprovante"]:
            raise HTTPException(404, "sem comprovante")
        return FileResponse(Path(cfg.dados) / "comprovantes" / s["comprovante"])

    @app.get("/api/admin/saques.csv")
    def admin_saques_csv(request: Request):
        exigir_admin(request)
        import csv
        import io as _io
        out = _io.StringIO()
        w = csv.writer(out, delimiter=";")
        w.writerow(["id", "email", "nome", "documento", "chave_pix", "valor", "estado", "pedido_em", "pago_em", "motivo"])
        from datetime import datetime as _dt
        f = lambda t: _dt.fromtimestamp(float(t)).strftime("%d/%m/%Y %H:%M") if t else ""  # noqa: E731
        for x in db.todos("SELECT s.*, u.email FROM saques s JOIN usuarios u ON u.id=s.usuario ORDER BY s.criado"):
            w.writerow([x["id"], x["email"], x["nome"] or "", x["doc"] or "", x["pix"], f"{x['valor']:.2f}".replace(".", ","), x["estado"], f(x["criado"]), f(x["pago"]), x["motivo"] or ""])
        return Response(out.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=saques-rendai.csv"})

    @app.get("/api/admin/afiliados")
    def admin_afiliados(request: Request):
        exigir_admin(request)
        return {"sessoes": [afiliado.logado(cfg, p) for p in (afiliado.sessoes(cfg) or {"hotmart": 0})],
                "com_comissao_real": db.um("SELECT COUNT(*) n FROM produtos WHERE ativo=1 AND comissao_pct IS NOT NULL")["n"]}

    @app.post("/api/admin/afiliados/testar")
    def admin_afiliados_testar(request: Request, n: int = 5, gravar: int = 0):
        exigir_admin(request)
        if not afiliado.sessoes(cfg):
            raise HTTPException(409, "sem sessão de afiliado: preencha RENDAI_AFILIADOS (cofre rendai-afiliados.env) e reimplante")
        return {"achados": afiliado.enriquecer(db, cfg, limite=max(1, min(n, 30)), pausa=(0.5, 1.2), gravar=bool(gravar))}

    @app.get("/api/admin/assinantes")
    def admin_assinantes(request: Request):
        exigir_admin(request)
        linhas = db.todos("""SELECT u.id, u.email, u.criado, u.plano, u.ativo_ate, u.pedido, u.xp,
                                    (SELECT MAX(criado) FROM acoes a WHERE a.usuario=u.id) ultimo
                             FROM usuarios u ORDER BY u.ativo_ate DESC, u.criado DESC LIMIT 1000""")
        for l in linhas:
            l["ativo"] = l["ativo_ate"] > agora()
        return {"assinantes": linhas}

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
