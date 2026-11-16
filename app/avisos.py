"""Venda pelo Link Rendaí em tempo real: webhook da Hotmart (postback 2.0) -> carteira do cliente -> aviso.

Como a venda chega até nós:
1. O link que o cliente divulga é o link de afiliado da CASA + `sck=rd<código do cliente>` (carteira.link_rendai).
2. Quem compra por ele gera uma venda na Hotmart com tracking.source_sck = rd<código>.
3. A Hotmart chama POST /api/hotmart/webhook (configurado em Ferramentas > Webhook, com o hottok da conta da casa).
4. Aqui: confere o hottok, acha o dono pelo sck, registra a comissão (carteira.registrar_venda) e avisa o cliente
   no app (tabela avisos) e por e-mail. Reembolso e chargeback tiram a venda e também avisam.
A sincronização pela API (carteira.sincronizar) continua como reforço, para vendas que o webhook perder.
"""
from __future__ import annotations

import hmac
import json
import logging

from app import carteira, mailer
from app.db import Banco, agora

log = logging.getLogger("rendai.avisos")
ESQUEMA = """
CREATE TABLE IF NOT EXISTS avisos (id INTEGER PRIMARY KEY, usuario INTEGER NOT NULL, tipo TEXT NOT NULL, titulo TEXT NOT NULL,
  texto TEXT NOT NULL, criado REAL NOT NULL, lido REAL);
CREATE INDEX IF NOT EXISTS avisos_usuario ON avisos(usuario, criado);
CREATE TABLE IF NOT EXISTS webhooks_hotmart (id TEXT PRIMARY KEY, recebido REAL NOT NULL, resultado TEXT NOT NULL, corpo TEXT NOT NULL);
"""
EVENTOS = {"PURCHASE_APPROVED": "APPROVED", "PURCHASE_COMPLETE": "COMPLETE", "PURCHASE_REFUNDED": "REFUNDED",
           "PURCHASE_CHARGEBACK": "CHARGEBACK", "PURCHASE_CANCELED": "CANCELED", "PURCHASE_PROTEST": "PROTESTED"}


def preparar(db: Banco) -> None:
    db.con.executescript(ESQUEMA)


def brl(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def avisar(db: Banco, cfg, uid: int, tipo: str, titulo: str, texto: str) -> None:
    db.exec("INSERT INTO avisos(usuario,tipo,titulo,texto,criado) VALUES(?,?,?,?,?)", (uid, tipo, titulo, texto, agora()))
    u = db.um("SELECT email FROM usuarios WHERE id=?", (uid,))
    if u:
        link = cfg.base_url + "/app"
        html = (f'<p style="font-size:20px;font-weight:800">{titulo}</p><p style="font-size:16px">{texto}</p>'
                f'<p><a href="{link}" style="display:inline-block;background:#22e58b;color:#03140a;text-decoration:none;font-weight:700;'
                f'padding:12px 20px;border-radius:10px">Abrir minha carteira</a></p>')
        mailer.enviar(cfg, u["email"], titulo, f"{titulo}\n{texto}\n{link}", html)


def comissao_afiliado(data: dict) -> float:
    """Comissão da conta da casa (afiliado) no payload 2.0 da Hotmart."""
    for c in data.get("commissions") or []:
        if str(c.get("source") or "").upper() == "AFFILIATE" and c.get("value") is not None:
            return float(c["value"])
    for a in data.get("affiliates") or []:
        if a.get("commission") is not None:
            return float((a["commission"] or {}).get("value") if isinstance(a["commission"], dict) else a["commission"])
    return 0.0


def processar(db: Banco, cfg, corpo: bytes, hottok_cab: str) -> dict:
    try:
        b = json.loads(corpo)
    except ValueError:
        return {"ok": False, "motivo": "json"}
    tok = hottok_cab or str(b.get("hottok") or "")
    if not cfg.hotmart_hottok or not hmac.compare_digest(tok, cfg.hotmart_hottok):
        return {"ok": False, "motivo": "hottok"}
    evento = str(b.get("event") or "")
    data = b.get("data") or {}
    compra = data.get("purchase") or {}
    transacao = str(compra.get("transaction") or "")
    wid = f"{transacao}:{evento}"
    if not transacao or db.um("SELECT 1 FROM webhooks_hotmart WHERE id=?", (wid,)):
        return {"ok": True, "repetido": True}
    status = EVENTOS.get(evento) or str(compra.get("status") or "").upper()
    sck = ((compra.get("tracking") or {}).get("source_sck")) or ((compra.get("origin") or {}).get("sck"))
    produto = (data.get("product") or {}).get("id")
    garantia = compra.get("warranty_expire_date")
    v = {"transacao": transacao, "sck": sck, "comissao": comissao_afiliado(data), "status": status, "plataforma": "hotmart",
         "metodo": carteira_metodo((compra.get("payment") or {}).get("type")), "produto": f"hotmart:{produto}" if produto else None,
         "garantia_ate": (garantia / 1000 if garantia and garantia > 1e11 else garantia) or None}
    r = carteira.registrar_venda(db, cfg.taxa_casa, v)
    db.exec("INSERT INTO webhooks_hotmart(id,recebido,resultado,corpo) VALUES(?,?,?,?)", (wid, agora(), r, corpo.decode("utf-8", "replace")[:20000]))
    u = carteira.usuario_do_sck(db, sck)
    if u and r in ("nova", "liberada", "reembolsada"):
        venda = db.um("SELECT v.parte_usuario, v.libera_em, p.titulo FROM vendas_casa v LEFT JOIN produtos p ON p.id=v.produto WHERE v.id=?", (transacao,)) or {}
        nome = venda.get("titulo") or "um produto"
        valor = brl(venda.get("parte_usuario") or 0)
        if r == "nova":
            import time
            quando = time.strftime("%d/%m", time.gmtime((venda.get("libera_em") or agora()) - 3 * 3600))
            avisar(db, cfg, u["id"], "venda", "Você vendeu! 🎉", f"Alguém comprou {nome} pelo seu link. {valor} entram na sua carteira e ficam livres para saque em {quando}.")
            from app import jogo
            try:
                jogo.registrar(db, u["id"], "venda", v["produto"], venda.get("parte_usuario"))
            except Exception:  # noqa: BLE001
                log.exception("XP da venda")
        elif r == "liberada":
            avisar(db, cfg, u["id"], "liberada", "Comissão liberada", f"Os {valor} da venda de {nome} já podem ser sacados.")
        else:
            avisar(db, cfg, u["id"], "reembolso", "Uma venda foi reembolsada", f"O comprador de {nome} pediu reembolso, então os {valor} saíram da sua carteira.")
    log.info("webhook hotmart %s %s -> %s", evento, transacao, r)
    return {"ok": True, "resultado": r}


def carteira_metodo(tipo: str | None) -> str | None:
    from app import repasse
    return repasse.METODOS.get(str(tipo or "").upper())
