"""Coletor de produtos afiliáveis.

Hotmart (sem login): a vitrine pública traz no HTML (`__NEXT_DATA__`) os 24 primeiros resultados de cada busca. Buscamos por
várias palavras do público "renda extra" e guardamos avaliações e alunos por dia: a temperatura é o crescimento recente.
Cada produto exibido é conferido (página abre, vendas ligadas, ativo na plataforma) no máximo a cada 6 h; o que falha
sai da vitrine na hora. Nada aparece com mais de 24 h sem conferência.
"""
from __future__ import annotations

import json
import logging
import math
import random
import re
import time

import httpx

from app.db import Banco, agora, hoje

log = logging.getLogger("rendai.coletor")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
BUSCAS = [
    "", "renda extra", "ganhar dinheiro", "marketing digital", "afiliado", "vendas", "instagram", "tiktok", "trafego pago",
    "emagrecer", "receitas", "confeitaria", "doces", "bolo", "salgados", "maquiagem", "unhas", "sobrancelha", "cabelo",
    "ingles", "concurso", "enem", "excel", "programacao", "inteligencia artificial", "chatgpt", "edicao de video", "design",
    "fotografia", "artesanato", "croche", "costura", "violao", "pet", "adestramento", "maternidade", "relacionamento",
    "investimentos", "financas pessoais", "renda com celular", "canva", "copywriting", "dropshipping", "yoga", "treino em casa",
]
# Fora da vitrine: aposta, pirâmide, adulto, promessa de cura/milagre.
BLOQUEIO = re.compile(r"\b(apostas?|bets?|cassinos?|casino|tigrinho|roleta|blaze|slots?|trader esportivo|aposta esportiva|apostas esportivas|"
                      r"pir[aâ]mide financeira|marketing multin[ií]vel|conte[uú]do adulto|sexo|er[oó]tic[oa]s?|onlyfans|"
                      r"cura (da|do|de|para|definitiva)|milagros[oa]|emagre[cç]a \d+ ?kg|perca \d+ ?kg)\b", re.I)


def _next_data(html: str) -> dict:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise ValueError("sem __NEXT_DATA__")
    return json.loads(m.group(1))


def url_produto(slug: str, codigo: str) -> str:
    return f"https://hotmart.com/pt-br/marketplace/produtos/{slug}/{codigo}"


def buscar_hotmart(cliente: httpx.Client, termo: str) -> list[dict]:
    r = cliente.get("https://hotmart.com/pt-br/marketplace/produtos", params={"q": termo, "sort": "most_rated"} if termo else {"sort": "most_rated"})
    r.raise_for_status()
    res = _next_data(r.text)["props"]["pageProps"]["resultsData"]["requestData"]["results"]
    out = []
    for x in res:
        if str(x.get("locale") or x.get("language") or "").upper() not in ("PT_BR", ""):
            continue
        out.append({
            "id": f"hotmart:{x['productId']}", "plataforma": "hotmart", "titulo": x.get("title") or "", "slug": x.get("slug"),
            "codigo": x.get("producerReferenceCode"), "imagem": x.get("finalAvatar") or x.get("avatar"), "categoria": x.get("category"),
            "topico": x.get("topic"), "autor": x.get("authorName") or x.get("ownerName"), "descricao": (x.get("description") or "")[:1200],
            "nota": float(x.get("rating") or 0), "avaliacoes": int(x.get("totalReviews") or 0),
            "vendas": str(x.get("salesEnabled")).lower() == "true", "oferta": x.get("offer"),
        })
    return out


def bloqueado(p: dict) -> bool:
    return bool(BLOQUEIO.search(f"{p.get('titulo', '')} {p.get('descricao', '')[:400]}"))


def gravar(db: Banco, p: dict) -> None:
    url = url_produto(p["slug"], p["codigo"])
    ativo = 1 if p["vendas"] and not bloqueado(p) else 0
    motivo = None if ativo else ("filtro" if bloqueado(p) else "vendas desligadas")
    with db.tx() as c:
        c.execute("""INSERT INTO produtos(id,plataforma,titulo,slug,codigo,imagem,categoria,topico,autor,descricao,nota,avaliacoes,url,ativo,motivo_inativo,visto,conferido,oferta)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(id) DO UPDATE SET titulo=excluded.titulo, imagem=excluded.imagem, nota=excluded.nota, avaliacoes=excluded.avaliacoes,
                       descricao=excluded.descricao, url=excluded.url, ativo=excluded.ativo, motivo_inativo=excluded.motivo_inativo, conferido=excluded.conferido,
                       oferta=COALESCE(excluded.oferta, oferta)""",
                  (p["id"], p["plataforma"], p["titulo"], p["slug"], p["codigo"], p["imagem"], p["categoria"], p["topico"], p["autor"], p["descricao"],
                   p["nota"], p["avaliacoes"], url, ativo, motivo, agora(), agora(), p.get("oferta")))
        c.execute("INSERT INTO historico(produto,dia,avaliacoes) VALUES(?,?,?) ON CONFLICT(produto,dia) DO UPDATE SET avaliacoes=excluded.avaliacoes",
                  (p["id"], hoje(), p["avaliacoes"]))


def preco_hotmart(cliente: httpx.Client, codigo: str, oferta: str) -> float | None:
    """Preço à vista da oferta, lido do checkout público (Nuxt: __NUXT_DATA__ é um vetor e os campos apontam para posições)."""
    r = cliente.get(f"https://pay.hotmart.com/{codigo}", params={"off": oferta})
    if r.status_code != 200:
        return None
    m = re.search(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
    if not m:
        return None
    d = json.loads(m.group(1))
    for v in d:
        if isinstance(v, dict) and {"amount", "offer"} <= set(v):  # "installments" nem sempre vem (variante da página)
            alvo = d[v["amount"]] if isinstance(v["amount"], int) else v["amount"]
            if isinstance(alvo, dict) and "value" in alvo:
                val = d[alvo["value"]] if isinstance(alvo["value"], int) and alvo["value"] < len(d) else alvo["value"]
                try:
                    return float(val)
                except (TypeError, ValueError):
                    return None
    return None


def conferir(db: Banco, cliente: httpx.Client, pid: str) -> bool:
    """Abre a página do produto: some da vitrine se não abrir, se as vendas estiverem desligadas ou se não estiver ativo."""
    p = db.um("SELECT * FROM produtos WHERE id=?", (pid,))
    if not p:
        return False
    try:
        r = cliente.get(p["url"])
        if r.status_code == 404:
            raise LookupError("página não existe mais")
        r.raise_for_status()
        perfil = _next_data(r.text)["props"]["pageProps"].get("profile") or {}
        ok = bool(perfil.get("salesEnabled")) and perfil.get("isActiveOnPlatform") is not False
        alunos = perfil.get("totalUsers")
        if ok and p.get("codigo") and p.get("oferta"):
            try:
                preco = preco_hotmart(cliente, p["codigo"], p["oferta"])
                if preco and preco > 0:
                    db.exec("UPDATE produtos SET preco=? WHERE id=?", (preco, pid))
            except Exception as e:  # noqa: BLE001
                log.info("preço de %s indisponível: %s", pid, e)
        db.exec("UPDATE produtos SET ativo=?, motivo_inativo=?, conferido=?, alunos=COALESCE(?,alunos) WHERE id=?",
                (1 if ok and not bloqueado(p) else 0, None if ok else "vendas desligadas", agora(), alunos, pid))
        if alunos is not None:
            db.exec("INSERT INTO historico(produto,dia,alunos) VALUES(?,?,?) ON CONFLICT(produto,dia) DO UPDATE SET alunos=excluded.alunos", (pid, hoje(), alunos))
        return ok
    except LookupError as e:
        db.exec("UPDATE produtos SET ativo=0, motivo_inativo=?, conferido=? WHERE id=?", (str(e), agora(), pid))
        return False
    except Exception as e:  # noqa: BLE001  falha de rede: não derruba o produto, mas ele envelhece e sai se passar de 24 h
        log.warning("conferir %s: %s", pid, e)
        return False


def temperaturas(db: Banco) -> None:
    """0 a 100. Crescimento de avaliações (7 dias) pesa mais; avaliações totais e nota dão a base."""
    prods = db.todos("SELECT id, avaliacoes, nota FROM produtos WHERE ativo=1")
    if not prods:
        return
    cresc = {}
    for p in prods:
        h = db.todos("SELECT dia, avaliacoes FROM historico WHERE produto=? AND avaliacoes IS NOT NULL ORDER BY dia DESC LIMIT 8", (p["id"],))
        cresc[p["id"]] = max(0, (h[0]["avaliacoes"] - h[-1]["avaliacoes"])) if len(h) >= 2 else 0
    mx_c = max(cresc.values()) or 1
    mx_a = max(math.log1p(p["avaliacoes"] or 0) for p in prods) or 1
    for p in prods:
        t = 55 * (cresc[p["id"]] / mx_c) + 35 * (math.log1p(p["avaliacoes"] or 0) / mx_a) + 10 * max(0, ((p["nota"] or 0) - 3.5) / 1.5)
        db.exec("UPDATE produtos SET temperatura=? WHERE id=?", (round(min(100, t), 1), p["id"]))


def varrer(db: Banco, buscas: list[str] | None = None, pausa=(1.5, 4.0)) -> int:
    n = 0
    with httpx.Client(headers={"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9"}, timeout=30, follow_redirects=True) as c:
        for termo in buscas if buscas is not None else BUSCAS:
            try:
                for p in buscar_hotmart(c, termo):
                    gravar(db, p)
                    n += 1
            except Exception as e:  # noqa: BLE001
                log.warning("busca %r falhou: %s", termo, e)
            time.sleep(random.uniform(*pausa))  # ritmo humano
    temperaturas(db)
    return n


def conferir_vitrine(db: Banco, limite: int = 120, idade_s: float = 6 * 3600, pausa=(1.0, 2.5)) -> int:
    """Confere os produtos que aparecem na vitrine (os mais quentes) e tira da vitrine quem passou de 24 h sem conferência."""
    db.exec("UPDATE produtos SET ativo=0, motivo_inativo='sem conferência há mais de 24 h' WHERE ativo=1 AND conferido<?", (agora() - 24 * 3600,))
    # sem preço ainda (acabou de entrar pela varredura) também entra: o preço é a base do "ganhe até R$ X"
    alvos = db.todos("SELECT id FROM produtos WHERE ativo=1 AND (conferido<? OR (preco IS NULL AND oferta IS NOT NULL)) ORDER BY temperatura DESC LIMIT ?",
                     (agora() - idade_s, limite))
    with httpx.Client(headers={"User-Agent": UA, "Accept-Language": "pt-BR"}, timeout=30, follow_redirects=True) as c:
        for a in alvos:
            conferir(db, c, a["id"])
            time.sleep(random.uniform(*pausa))
    return len(alvos)
