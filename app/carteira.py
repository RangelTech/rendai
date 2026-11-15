"""Link Rendaí (microtransação, pedido do Lucas 15/11).

A Rangel Tech é afiliada dos produtos (link da casa). Cada assinante divulga o link da casa com o código dele
(`sck=rd<código>`, parâmetro de rastreio que a Hotmart guarda em cada venda). A comissão cai na conta da casa;
o assinante fica com (1 - taxa_casa) da comissão, liberada depois da garantia, e saca por PIX.

Vendas entram por dois caminhos: a API da Hotmart (sincronizar(), quando houver credenciais no cofre) ou
importação pelo admin (lista JSON, também usada nos testes). Saque é pedido pelo assinante e marcado como pago
pelo admin depois do PIX (o PIX automático entra quando houver conta de pagamento com API).
"""
from __future__ import annotations

import base64
import logging
import re
import secrets
import time

import httpx

from app.db import Banco, agora

log = logging.getLogger("rendai.carteira")
SAQUE_MIN = 100.0
GARANTIA_PADRAO_S = 30 * 86400
ESTADOS_OK = {"APPROVED", "COMPLETE", "COMPLETED"}
ESTADOS_VOLTA = {"REFUNDED", "CHARGEBACK", "CANCELLED", "CANCELED", "PROTESTED", "PARTIALLY_REFUNDED"}


def codigo_ref(db: Banco, u: dict) -> str:
    if u.get("codigo_ref"):
        return u["codigo_ref"]
    while True:
        c = secrets.token_hex(4)
        if not db.um("SELECT 1 FROM usuarios WHERE codigo_ref=?", (c,)):
            db.exec("UPDATE usuarios SET codigo_ref=? WHERE id=?", (c, u["id"]))
            return c


def link_rendai(link_casa: str, codigo: str) -> str:
    return f"{link_casa}{'&' if '?' in link_casa else '?'}sck=rd{codigo}"


def usuario_do_sck(db: Banco, sck: str | None) -> dict | None:
    m = re.fullmatch(r"rd([0-9a-f]{8})", (sck or "").strip().lower())
    return db.um("SELECT * FROM usuarios WHERE codigo_ref=?", (m.group(1),)) if m else None


def registrar_venda(db: Banco, taxa_casa: float, v: dict) -> str:
    """v = {transacao, sck, comissao, status, garantia_ate (epoch, opcional), produto (opcional)}. Devolve o que aconteceu."""
    u = usuario_do_sck(db, v.get("sck"))
    if not u:
        return "sem dono"
    status = str(v.get("status") or "APPROVED").upper()
    atual = db.um("SELECT * FROM vendas_casa WHERE id=?", (v["transacao"],))
    if status in ESTADOS_VOLTA:
        if atual and atual["estado"] in ("pendente", "liberada"):
            db.exec("UPDATE vendas_casa SET estado='reembolsada' WHERE id=?", (v["transacao"],))
            return "reembolsada"
        return "ignorada"
    if status not in ESTADOS_OK or atual:
        if atual and status == "COMPLETE" and atual["estado"] == "pendente":
            db.exec("UPDATE vendas_casa SET estado='liberada' WHERE id=?", (v["transacao"],))
            return "liberada"
        return "ignorada"
    com = round(float(v.get("comissao") or 0), 2)
    if com <= 0:
        return "sem comissao"
    parte = round(com * (1 - taxa_casa), 2)
    libera = float(v.get("garantia_ate") or agora() + GARANTIA_PADRAO_S)
    estado = "liberada" if status in ("COMPLETE", "COMPLETED") or libera <= agora() else "pendente"
    db.exec("""INSERT INTO vendas_casa(id,usuario,produto,comissao,parte_usuario,parte_casa,estado,libera_em,criado)
               VALUES(?,?,?,?,?,?,?,?,?)""", (v["transacao"], u["id"], v.get("produto"), com, parte, round(com - parte, 2), estado, libera, agora()))
    return "nova"


def liberar_vencidas(db: Banco) -> int:
    cur = db.exec("UPDATE vendas_casa SET estado='liberada' WHERE estado='pendente' AND libera_em<=?", (agora(),))
    return cur.rowcount or 0


def resumo(db: Banco, uid: int) -> dict:
    soma = lambda est: round(db.um("SELECT COALESCE(SUM(parte_usuario),0) s FROM vendas_casa WHERE usuario=? AND estado=?", (uid, est))["s"], 2)
    sacado = round(db.um("SELECT COALESCE(SUM(valor),0) s FROM saques WHERE usuario=? AND estado IN ('pedido','pago')", (uid,))["s"], 2)
    liberado = soma("liberada")
    u = db.um("SELECT pix FROM usuarios WHERE id=?", (uid,))
    return {"a_liberar": soma("pendente"), "liberado_total": liberado, "disponivel": round(max(0.0, liberado - sacado), 2), "sacado": sacado,
            "pix": u["pix"] if u else None, "saque_minimo": SAQUE_MIN,
            "vendas": db.todos("SELECT v.id, v.comissao, v.parte_usuario, v.estado, v.libera_em, v.criado, p.titulo FROM vendas_casa v "
                               "LEFT JOIN produtos p ON p.id=v.produto WHERE v.usuario=? ORDER BY v.criado DESC LIMIT 50", (uid,)),
            "saques": db.todos("SELECT id, valor, estado, criado, pago FROM saques WHERE usuario=? ORDER BY criado DESC LIMIT 20", (uid,))}


def pedir_saque(db: Banco, uid: int) -> dict:
    r = resumo(db, uid)
    if not r["pix"]:
        raise ValueError("cadastre sua chave PIX antes de sacar")
    if r["disponivel"] < SAQUE_MIN:
        raise ValueError(f"o saque mínimo é R$ {SAQUE_MIN:.2f}".replace(".", ","))
    db.exec("INSERT INTO saques(usuario,valor,pix,estado,criado) VALUES(?,?,?,'pedido',?)", (uid, r["disponivel"], r["pix"], agora()))
    return resumo(db, uid)


# ----------------------------------------------------------------- Hotmart (credenciais da conta da casa)
def _token(cfg) -> str | None:
    h = cfg.hotmart or {}
    if not (h.get("client_id") and h.get("client_secret")):
        return None
    basic = h.get("basic") or base64.b64encode(f"{h['client_id']}:{h['client_secret']}".encode()).decode()
    r = httpx.post("https://api-sec-vlc.hotmart.com/security/oauth/token", params={"grant_type": "client_credentials", "client_id": h["client_id"],
                   "client_secret": h["client_secret"]}, headers={"Authorization": f"Basic {basic}"}, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def sincronizar(db: Banco, cfg, dias: int = 45) -> dict:
    """Lê as vendas recentes da conta da casa (histórico + comissões) e registra as que têm sck de assinante."""
    tok = _token(cfg)
    if not tok:
        return {"ok": False, "motivo": "sem credenciais da Hotmart (cofre rendai-afiliados.env: RENDAI_HOTMART)"}
    h = {"Authorization": f"Bearer {tok}"}
    ini = int((time.time() - dias * 86400) * 1000)
    base = "https://developers.hotmart.com/payments/api/v1/sales"
    vendas, comissoes, pagina = {}, {}, None
    with httpx.Client(timeout=40, headers=h) as c:
        while True:
            q = {"start_date": ini, "max_results": 200, "transaction_status": "APPROVED,COMPLETE,REFUNDED,CHARGEBACK,CANCELLED"}
            if pagina:
                q["page_token"] = pagina
            j = c.get(base + "/history", params=q).json()
            for it in j.get("items") or []:
                p = it.get("purchase") or {}
                vendas[p.get("transaction")] = {"transacao": p.get("transaction"), "status": p.get("status"),
                                               "sck": ((p.get("tracking") or {}).get("source_sck")),
                                               "garantia_ate": (p.get("warranty_expire_date") or 0) / 1000 or None,
                                               "produto": f"hotmart:{(it.get('product') or {}).get('id')}"}
            pagina = (j.get("page_info") or {}).get("next_page_token")
            if not pagina:
                break
        pagina = None
        while True:
            q = {"start_date": ini, "max_results": 200, "commission_as": "AFFILIATE"}
            if pagina:
                q["page_token"] = pagina
            j = c.get(base + "/commissions", params=q).json()
            for it in j.get("items") or []:
                tr = it.get("transaction")
                for cm in it.get("commissions") or []:
                    if str(cm.get("source") or "").upper() == "AFFILIATE":
                        comissoes[tr] = float((cm.get("commission") or {}).get("value") or 0)
            pagina = (j.get("page_info") or {}).get("next_page_token")
            if not pagina:
                break
    contagem: dict[str, int] = {}
    for tr, v in vendas.items():
        if tr:
            r = registrar_venda(db, cfg.taxa_casa, {**v, "comissao": comissoes.get(tr, 0)})
            contagem[r] = contagem.get(r, 0) + 1
    return {"ok": True, "lidas": len(vendas), **contagem}
