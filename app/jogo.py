"""Gamificação: XP por ação (nunca por sorte ou depósito), níveis, sequência de dias, conquistas, missão semanal e ranking."""
from __future__ import annotations

import hashlib
import time

from app.db import Banco, agora, hoje

XP = {"checkin": 10, "afiliar": 50, "kit": 20, "divulgar": 30, "venda": 200}
LIMITE_DIA = {"checkin": 1, "afiliar": 5, "kit": 10, "divulgar": 6, "venda": 20}  # evita farm de XP
NIVEIS = [(0, "Iniciante"), (150, "Aprendiz"), (500, "Vendedor"), (1200, "Brabo"), (2500, "Fera"), (5000, "Lenda")]
CONQUISTAS = {
    "primeiro_passo": ("Primeiro passo", "Escolheu o primeiro produto para vender"),
    "na_area": ("Na área", "3 dias seguidos entrando"),
    "semana_cheia": ("Semana cheia", "7 dias seguidos entrando"),
    "divulgador": ("Divulgador", "Divulgou 10 vezes"),
    "primeira_venda": ("Primeira venda", "Registrou a primeira venda"),
    "cem_reais": ("Cervejinha garantida", "Somou R$ 100 em comissões"),
    "missao": ("Missão cumprida", "Completou uma missão da semana"),
}


def nivel(xp: int) -> dict:
    atual = max((n for n in NIVEIS if xp >= n[0]), key=lambda n: n[0])
    i = NIVEIS.index(atual)
    prox = NIVEIS[i + 1] if i + 1 < len(NIVEIS) else None
    pct = 1.0 if not prox else (xp - atual[0]) / (prox[0] - atual[0])
    return {"nome": atual[1], "numero": i + 1, "xp": xp, "proximo": prox[1] if prox else None, "falta": (prox[0] - xp) if prox else 0, "pct": round(pct, 3)}


def semana() -> tuple[float, float, str]:
    """Início (segunda 00h de Brasília) e fim (domingo 23h59) da semana atual, e um rótulo."""
    t = time.time() - 3 * 3600
    lt = time.gmtime(t)
    inicio = t - lt.tm_wday * 86400 - lt.tm_hour * 3600 - lt.tm_min * 60 - lt.tm_sec
    return inicio + 3 * 3600, inicio + 7 * 86400 + 3 * 3600 - 1, time.strftime("%Y-W%W", lt)


def _conquistar(db: Banco, uid: int, codigo: str) -> dict | None:
    r = db.exec("INSERT OR IGNORE INTO conquistas(usuario,codigo,criado) VALUES(?,?,?)", (uid, codigo, agora()))
    if r.rowcount:
        nome, desc = CONQUISTAS[codigo]
        return {"codigo": codigo, "nome": nome, "descricao": desc}
    return None


def registrar(db: Banco, uid: int, tipo: str, produto: str | None = None, valor: float | None = None) -> dict:
    """Registra a ação, dá o XP e devolve o que mudou (para a tela comemorar)."""
    if tipo not in XP:
        raise ValueError("ação desconhecida")
    inicio_dia = agora() - ((agora() - 3 * 3600) % 86400)
    feitas = db.um("SELECT COUNT(*) n FROM acoes WHERE usuario=? AND tipo=? AND criado>=?", (uid, tipo, inicio_dia))["n"]
    ganho = XP[tipo] if feitas < LIMITE_DIA[tipo] else 0
    if tipo == "afiliar" and produto and db.um("SELECT 1 FROM acoes WHERE usuario=? AND tipo='afiliar' AND produto=?", (uid, produto)):
        ganho = 0  # o mesmo produto não dá XP duas vezes
    u = db.um("SELECT * FROM usuarios WHERE id=?", (uid,))
    antes = nivel(u["xp"])
    db.exec("INSERT INTO acoes(usuario,tipo,produto,valor,xp,criado) VALUES(?,?,?,?,?,?)", (uid, tipo, produto, valor, ganho, agora()))
    novos = []
    if tipo == "checkin":
        d = hoje()
        if u["ultimo_dia"] != d:
            ontem = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 3 * 3600 - 86400))
            streak = u["streak"] + 1 if u["ultimo_dia"] == ontem else 1
            db.exec("UPDATE usuarios SET streak=?, ultimo_dia=? WHERE id=?", (streak, d, uid))
            if streak >= 3:
                novos.append(_conquistar(db, uid, "na_area"))
            if streak >= 7:
                novos.append(_conquistar(db, uid, "semana_cheia"))
        else:
            ganho = 0
    db.exec("UPDATE usuarios SET xp=xp+? WHERE id=?", (ganho, uid))
    if tipo == "afiliar":
        novos.append(_conquistar(db, uid, "primeiro_passo"))
    if tipo == "divulgar" and db.um("SELECT COUNT(*) n FROM acoes WHERE usuario=? AND tipo='divulgar'", (uid,))["n"] >= 10:
        novos.append(_conquistar(db, uid, "divulgador"))
    if tipo == "venda":
        novos.append(_conquistar(db, uid, "primeira_venda"))
        if (db.um("SELECT COALESCE(SUM(valor),0) s FROM acoes WHERE usuario=? AND tipo='venda'", (uid,))["s"] or 0) >= 100:
            novos.append(_conquistar(db, uid, "cem_reais"))
    m = missao(db, uid)
    ini, fim, _ = semana()
    if m["completa"] and not db.um("SELECT 1 FROM acoes WHERE usuario=? AND tipo='missao' AND criado BETWEEN ? AND ?", (uid, ini, fim)):
        # prêmio da missão: uma vez por semana
        db.exec("INSERT INTO acoes(usuario,tipo,xp,criado) VALUES(?,?,?,?)", (uid, "missao", m["premio_xp"], agora()))
        db.exec("UPDATE usuarios SET xp=xp+? WHERE id=?", (m["premio_xp"], uid))
        ganho += m["premio_xp"]
        novos.append(_conquistar(db, uid, "missao"))
        m["premiada"] = True
    depois = nivel(db.um("SELECT xp FROM usuarios WHERE id=?", (uid,))["xp"])
    return {"xp_ganho": ganho, "nivel": depois, "subiu": depois["numero"] > antes["numero"], "conquistas": [c for c in novos if c], "missao": m}


def produtos_da_missao(db: Banco, uid: int) -> list[dict]:
    """3 produtos quentes, sorteados de forma fixa por pessoa e semana (todo mundo não recebe os mesmos)."""
    _, _, rotulo = semana()
    quentes = db.todos("SELECT id, titulo, imagem, temperatura, url FROM produtos WHERE ativo=1 ORDER BY temperatura DESC LIMIT 30")
    if not quentes:
        return []
    semente = int(hashlib.sha256(f"{uid}:{rotulo}".encode()).hexdigest(), 16)
    escolhidos, i = [], 0
    while len(escolhidos) < min(3, len(quentes)):
        p = quentes[(semente >> (i * 5)) % len(quentes)]
        if p not in escolhidos:
            escolhidos.append(p)
        i += 1
        if i > 60:
            break
    return escolhidos


def missao(db: Banco, uid: int) -> dict:
    """Missão da semana: divulgar 3 produtos (5 vezes no total) e registrar 1 venda até domingo."""
    ini, fim, _ = semana()
    divulg = db.um("SELECT COUNT(*) n FROM acoes WHERE usuario=? AND tipo='divulgar' AND criado BETWEEN ? AND ?", (uid, ini, fim))["n"]
    vendas = db.um("SELECT COUNT(*) n FROM acoes WHERE usuario=? AND tipo='venda' AND criado BETWEEN ? AND ?", (uid, ini, fim))["n"]
    etapas = [
        {"texto": "Divulgue 5 vezes", "feito": min(divulg, 5), "meta": 5},
        {"texto": "Registre 1 venda", "feito": min(vendas, 1), "meta": 1},
    ]
    total = sum(e["feito"] for e in etapas) / sum(e["meta"] for e in etapas)
    return {"titulo": "Missão da semana", "premio_xp": 300, "termina": fim, "etapas": etapas, "pct": round(total, 3), "completa": total >= 1,
            "produtos": produtos_da_missao(db, uid)}


def ranking(db: Banco, uid: int, n: int = 10) -> dict:
    ini, fim, _ = semana()
    linhas = db.todos("""SELECT u.id, COALESCE(u.apelido, 'Vendedor ' || u.id) apelido, SUM(a.xp) xp FROM acoes a JOIN usuarios u ON u.id=a.usuario
                         WHERE a.criado BETWEEN ? AND ? GROUP BY u.id ORDER BY xp DESC LIMIT 50""", (ini, fim))
    eu = next((i + 1 for i, l in enumerate(linhas) if l["id"] == uid), None)
    return {"top": [{"pos": i + 1, "apelido": l["apelido"], "xp": l["xp"], "eu": l["id"] == uid} for i, l in enumerate(linhas[:n])], "minha_posicao": eu}
