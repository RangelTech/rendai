"""Repasse do Link Rendaí (spec 44, parte 2): prazos por plataforma, dias úteis, termos, recebedor, saque manual (48 h), cliques.

Regra do Lucas (15/11): data mínima para o cliente sacar = data em que a plataforma libera para a casa + 2 dias úteis.
O pagamento é MANUAL (Pix pelo app do banco); o sistema só registra a solicitação e o painel mostra quem pagar.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

FUSO = timezone(timedelta(hours=-3))
TERMOS_VERSAO = "2026-11-15"
PRAZO_PAGAMENTO_S = 48 * 3600

# dias até a plataforma liberar a comissão para a casa (lido nos painéis/centrais em 15/11; editável pelo admin)
PRAZOS = {
    "hotmart": {"pix": 2, "boleto": 2, "cartao": 30},
    "kiwify": {"pix": 2, "boleto": 2, "cartao": 15},
    "monetizze": {"pix": 2, "boleto": 2, "cartao": 30},
    "eduzz": {"pix": 1, "boleto": 3, "cartao": 30},
    "braip": {"pix": 1, "boleto": 3, "cartao": 30},
}
METODOS = {"PIX": "pix", "BILLET": "boleto", "BOLETO": "boleto", "CREDIT_CARD": "cartao", "CARTAO": "cartao", "CARD": "cartao"}

# feriados nacionais (fixos + móveis de 2026 e 2027)
FERIADOS = {f"{a}-{d}" for a in (2026, 2027) for d in ("01-01", "04-21", "05-01", "09-07", "10-12", "11-02", "11-15", "11-20", "12-25")}
FERIADOS |= {"2026-02-16", "2026-02-17", "2026-04-03", "2026-06-04", "2027-02-08", "2027-02-09", "2027-03-26", "2027-05-27"}


def dia_util(d: date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in FERIADOS


def mais_dias_uteis(ts: float, n: int = 2) -> float:
    d = datetime.fromtimestamp(ts, FUSO)
    dia = d.date()
    contados = 0
    while contados < n:
        dia += timedelta(days=1)
        if dia_util(dia):
            contados += 1
    return datetime(dia.year, dia.month, dia.day, max(d.hour, 9), d.minute, tzinfo=FUSO).timestamp()


def prazo_dias(plataforma: str | None, metodo: str | None) -> int:
    p = PRAZOS.get((plataforma or "hotmart").lower(), PRAZOS["hotmart"])
    m = METODOS.get(str(metodo or "").upper(), str(metodo or "").lower())
    return p.get(m, p["cartao"])


def libera_cliente(criado_ts: float, plataforma: str | None, metodo: str | None, garantia_ate: float | None = None, liberado_plataforma: float | None = None) -> float:
    """Quando o cliente pode sacar: liberação na plataforma (dado real ou prazo da tabela, nunca antes da garantia) + 2 dias úteis."""
    base = liberado_plataforma or (criado_ts + prazo_dias(plataforma, metodo) * 86400)
    if garantia_ate:
        base = max(base, garantia_ate)
    return mais_dias_uteis(base, 2)


def estimativa_produto(plataforma: str | None, garantia_dias: int = 7) -> dict:
    """Dias aproximados até cair para o cliente, por meio de pagamento (para a página do produto)."""
    out = {}
    for m in ("pix", "cartao"):
        base = max(prazo_dias(plataforma, m), garantia_dias)
        out[m] = base + 3  # +2 dias úteis ~ 3 corridos em média
    return out


# ----------------------------------------------------------------- documentos
def so_digitos(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def cpf_ok(c: str) -> bool:
    c = so_digitos(c)
    if len(c) != 11 or c == c[0] * 11:
        return False
    for n in (9, 10):
        soma = sum(int(c[i]) * (n + 1 - i) for i in range(n))
        if (soma * 10 % 11) % 10 != int(c[n]):
            return False
    return True


def cnpj_ok(c: str) -> bool:
    c = so_digitos(c)
    if len(c) != 14 or c == c[0] * 14:
        return False
    for n, pesos in ((12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]), (13, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])):
        soma = sum(int(c[i]) * pesos[i] for i in range(n))
        dv = 0 if soma % 11 < 2 else 11 - soma % 11
        if dv != int(c[n]):
            return False
    return True


def doc_ok(c: str) -> bool:
    return cpf_ok(c) or cnpj_ok(c)


def chave_pix_ok(chave: str, doc: str) -> str | None:
    """Valida a chave Pix e a regra de titularidade (CPF/CNPJ igual ao do recebedor). Devolve erro ou None."""
    ch = (chave or "").strip()
    if len(ch) < 5:
        return "informe a chave Pix"
    dig = so_digitos(ch)
    if (len(dig) in (11, 14)) and dig == ch.replace(".", "").replace("-", "").replace("/", "") and dig != so_digitos(doc):
        return "a chave Pix de CPF/CNPJ tem que ser do mesmo documento do recebedor"
    if "@" in ch and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", ch, re.I):
        return "chave Pix de e-mail inválida"
    return None
