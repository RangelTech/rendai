"""Coleta com a conta de afiliado (desligada até existir sessão no cofre).

Sem conta, o Rendaí só vê a vitrine pública e estima a comissão em 50%. Com a sessão de uma conta de afiliado
(cookie do navegador logado, guardado no cofre em rendai-afiliados.env e passado em RENDAI_AFILIADOS), o coletor
abre a página de cada produto como afiliado e lê a comissão real. Cada plataforma é uma entrada:

    RENDAI_AFILIADOS={"hotmart": {"cookie": "<cabeçalho Cookie inteiro do navegador logado>"}}

Nada aqui roda sem essa variável. O formato da página logada só pode ser confirmado com a conta de verdade:
o leitor procura os campos de comissão no JSON da página (__NEXT_DATA__ / __NUXT_DATA__) e no HTML,
e /api/admin/afiliados/testar mostra o que achou em poucos produtos antes de ligar para a vitrine toda.
"""
from __future__ import annotations

import json
import logging
import random
import re
import time

import httpx

from app.db import Banco, agora

log = logging.getLogger("rendai.afiliado")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
_CAMPOS = re.compile(r'"(?:commission(?:Percentage|Percent|Value|Rate)?|affiliateCommission(?:Percentage)?|comissao(?:Percentual)?)"\s*:\s*"?(\d{1,3}(?:[.,]\d+)?)', re.I)
_TEXTO = re.compile(r"comiss[aã]o[^0-9%]{0,40}?(\d{1,2}(?:[.,]\d+)?)\s*%", re.I)
HOSTS_LINK = re.compile(r"^https://([a-z0-9-]+\.)*(hotmart\.com|hotm\.art|cakto\.com\.br|kiwify\.com\.br|kiwify\.app|eduzz\.com|braip\.com|monetizze\.com\.br)(/|$)", re.I)


def sessoes(cfg) -> dict:
    return {k: v for k, v in (cfg.afiliados or {}).items() if isinstance(v, dict) and v.get("cookie")}


def ler_comissao(html: str) -> float | None:
    """Fração (0 a 1) da comissão achada na página logada; None se não achou."""
    for rx in (_CAMPOS, _TEXTO):
        for m in rx.finditer(html):
            v = float(m.group(1).replace(",", "."))
            if 1 <= v <= 95:
                return round(v / 100, 4)
            if 0 < v < 1:
                return round(v, 4)
    return None


def _cliente(cookie: str) -> httpx.Client:
    return httpx.Client(timeout=25, follow_redirects=True, headers={"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9", "Cookie": cookie})


def logado(cfg, plataforma: str = "hotmart") -> dict:
    """Confere se a sessão ainda vale: a área logada não redireciona para o login."""
    s = sessoes(cfg).get(plataforma)
    if not s:
        return {"plataforma": plataforma, "configurada": False}
    url = s.get("teste_url") or "https://app.hotmart.com/market"
    try:
        with _cliente(s["cookie"]) as c:
            r = c.get(url)
        ok = r.status_code == 200 and not re.search(r"(sso|login|signin)", str(r.url), re.I)
        return {"plataforma": plataforma, "configurada": True, "logado": ok, "status": r.status_code, "final": str(r.url)[:120]}
    except httpx.HTTPError as e:
        return {"plataforma": plataforma, "configurada": True, "logado": False, "erro": str(e)[:120]}


def enriquecer(db: Banco, cfg, limite: int = 60, pausa=(1.5, 3.5), gravar: bool = True) -> list[dict]:
    """Lê a comissão real dos produtos ativos da Hotmart mais quentes. Devolve o que achou (para o teste do admin)."""
    s = sessoes(cfg).get("hotmart")
    if not s:
        return []
    prods = db.todos("SELECT id, url, titulo FROM produtos WHERE ativo=1 AND plataforma='hotmart' ORDER BY temperatura DESC LIMIT ?", (limite,))
    achados = []
    with _cliente(s["cookie"]) as c:
        for p in prods:
            try:
                r = c.get(p["url"])
                pct = ler_comissao(r.text) if r.status_code == 200 else None
            except httpx.HTTPError:
                pct = None
            achados.append({"id": p["id"], "titulo": p["titulo"][:60], "comissao_pct": pct})
            if pct and gravar:
                db.exec("UPDATE produtos SET comissao_pct=? WHERE id=?", (pct, p["id"]))
            time.sleep(random.uniform(*pausa))
    log.info("afiliado: %s/%s com comissão real", sum(1 for a in achados if a["comissao_pct"]), len(achados))
    return achados


def link_valido(link: str) -> bool:
    return bool(HOSTS_LINK.match((link or "").strip()))
