"""Rendaí: login, vitrine (só oferta conferida), gamificação (XP, limites, missão, conquistas), plano grátis, Cakto e coletor."""
import json
import os

os.environ["RENDAI_SEM_APP"] = "1"

import pytest
from fastapi.testclient import TestClient

from app import coletor, jogo
from app.db import agora
from app.main import Config, criar_app


def produto(db, i, temp=50.0, ativo=1, conferido=None):
    db.exec("""INSERT INTO produtos(id,plataforma,titulo,slug,codigo,url,ativo,temperatura,visto,conferido,avaliacoes,nota)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f"hotmart:{i}", "hotmart", f"Curso {i}", f"curso-{i}", f"A{i}", f"https://hotmart.com/p/{i}", ativo, temp, agora(),
             conferido if conferido is not None else agora(), 100 + i, 4.5))


@pytest.fixture()
def cli(tmp_path):
    app = criar_app(Config(dados=str(tmp_path), dev=True, segredo_sessao="s", cakto_segredo="ck", coletar=False,
                           planos_por_produto={"prod-pro": "pro", "prod-elite": "elite"}, checkouts={"start": "https://pay/x"}), iniciar_rotina=False)
    db = app.state.db
    for i in range(1, 8):
        produto(db, i, temp=10 * i)
    produto(db, 90, temp=99, ativo=0)                       # fora da vitrine
    produto(db, 91, temp=98, conferido=agora() - 30 * 3600)  # sem conferência há 30 h
    return TestClient(app), db


def assinar(c, email="ze@exemplo.com", produto="prod-start", pid="p"):
    c.post("/api/cakto", json={"secret": "ck", "event": "purchase_approved", "data": {"id": pid + email, "customer": {"email": email}, "product": {"id": produto}}})


def entrar(c, email="ze@exemplo.com"):
    cod = c.post("/api/entrar/codigo", json={"email": email}).json()["codigo_dev"]
    assert c.post("/api/entrar", json={"email": email, "codigo": cod}).status_code == 200


def test_vitrine_so_oferta_valida_e_amostra_sem_login(cli):
    c, _ = cli
    ids = [p["id"] for p in c.get("/api/vitrine").json()["produtos"]]
    assert "hotmart:90" not in ids and "hotmart:91" not in ids
    assert ids[0] == "hotmart:7" and len(ids) <= 8


def test_checkin_streak_e_xp(cli):
    c, _ = cli
    entrar(c)
    r = c.post("/api/acao", json={"tipo": "checkin"}).json()
    assert r["xp_ganho"] == jogo.XP["checkin"]
    assert c.post("/api/acao", json={"tipo": "checkin"}).json()["xp_ganho"] == 0  # 1 por dia
    eu = c.get("/api/eu").json()
    assert eu["streak"] == 1 and eu["fez_checkin_hoje"] and eu["nivel"]["nome"] == "Iniciante"


def test_sem_plano_nao_pega_produto_e_start_libera_cinco(cli):
    c, _ = cli
    entrar(c)
    assert c.post("/api/acao", json={"tipo": "afiliar", "produto": "hotmart:1"}).status_code == 402
    assinar(c)
    assert c.get("/api/eu").json()["plano"] == "start"
    for i in range(1, 6):
        assert c.post("/api/acao", json={"tipo": "afiliar", "produto": f"hotmart:{i}"}).status_code == 200
    assert c.post("/api/acao", json={"tipo": "afiliar", "produto": "hotmart:1"}).json()["xp_ganho"] == 0  # repetido não dá XP
    assert c.post("/api/acao", json={"tipo": "afiliar", "produto": "hotmart:6"}).status_code == 402
    assert c.post("/api/produtos/hotmart:7/kit").status_code == 402


def test_ganho_estimado_por_produto(cli):
    c, db = cli
    db.exec("UPDATE produtos SET preco=297 WHERE id='hotmart:7'")
    p = next(x for x in c.get("/api/vitrine").json()["produtos"] if x["id"] == "hotmart:7")
    assert p["ganho_venda"] == round(297 * 0.5 * 0.9, 2) and p["comissao_estimada"] is True
    planos = c.get("/api/planos").json()["planos"]
    assert [x["preco"] for x in planos] == [19.90, 29.90, 59.90] and planos[0]["checkout"] == "https://pay/x"


def test_primeira_conquista_e_venda(cli):
    c, _ = cli
    assinar(c)
    entrar(c)
    r = c.post("/api/acao", json={"tipo": "afiliar", "produto": "hotmart:2"}).json()
    assert any(x["codigo"] == "primeiro_passo" for x in r["conquistas"])
    assert c.post("/api/acao", json={"tipo": "venda", "produto": "hotmart:2", "valor": "abc"}).status_code == 422
    r = c.post("/api/acao", json={"tipo": "venda", "produto": "hotmart:2", "valor": 120}).json()
    codigos = {x["codigo"] for x in r["conquistas"]}
    assert {"primeira_venda", "cem_reais"} <= codigos
    assert c.get("/api/eu").json()["comissoes"] == 120


def test_missao_completa_da_premio_uma_vez(cli):
    c, db = cli
    entrar(c)
    for _ in range(5):
        c.post("/api/acao", json={"tipo": "divulgar", "produto": "hotmart:1"})
    r = c.post("/api/acao", json={"tipo": "venda", "produto": "hotmart:1", "valor": 30}).json()
    assert r["missao"]["completa"] and r["missao"].get("premiada") and r["xp_ganho"] >= 300
    r2 = c.post("/api/acao", json={"tipo": "venda", "produto": "hotmart:1", "valor": 30}).json()
    assert not r2["missao"].get("premiada")
    m = c.get("/api/missao").json()
    assert len(m["produtos"]) == 3


def test_limite_diario_de_divulgar(cli):
    c, _ = cli
    entrar(c)
    ganhos = [c.post("/api/acao", json={"tipo": "divulgar", "produto": "hotmart:1"}).json()["xp_ganho"] for _ in range(8)]
    assert ganhos[:6] == [30] * 6 and ganhos[6:] == [0, 0]


def test_ranking_semana(cli):
    c, _ = cli
    entrar(c)
    c.post("/api/acao", json={"tipo": "checkin"})
    c.post("/api/eu/apelido", json={"apelido": "Zé das Vendas"})
    r = c.get("/api/ranking").json()
    assert r["minha_posicao"] == 1 and r["top"][0]["apelido"] == "Zé das Vendas"


def test_cakto_elite_libera_tudo(cli):
    c, _ = cli
    assinar(c, produto="prod-elite")
    entrar(c)
    assert c.get("/api/eu").json()["plano"] == "elite"
    for i in range(1, 8):
        assert c.post("/api/acao", json={"tipo": "afiliar", "produto": f"hotmart:{i}"}).status_code == 200
    assert c.get("/api/eu").json()["assinante"] is True


def test_coletor_le_next_data_e_filtra(tmp_path):
    from app.db import Banco
    db = Banco(str(tmp_path / "c.db"))
    res = [{"productId": 1, "title": "Curso de Bolos", "slug": "bolos", "producerReferenceCode": "B1", "rating": "4.8", "totalReviews": "500",
            "salesEnabled": "True", "locale": "PT_BR", "description": "aprenda bolos"},
           {"productId": 2, "title": "Método das apostas esportivas", "slug": "bet", "producerReferenceCode": "B2", "rating": "4.9", "totalReviews": "900",
            "salesEnabled": "True", "locale": "PT_BR", "description": ""}]
    html = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps({"props": {"pageProps": {"resultsData": {"requestData": {"results": res}}}}}) + "</script>"

    class R:
        text = html
        def raise_for_status(self): pass

    class C:
        def get(self, *a, **k): return R()

    for p in coletor.buscar_hotmart(C(), "bolo"):
        coletor.gravar(db, p)
    coletor.temperaturas(db)
    ativos = db.todos("SELECT id, temperatura FROM produtos WHERE ativo=1")
    assert [a["id"] for a in ativos] == ["hotmart:1"] and ativos[0]["temperatura"] > 0
    assert db.um("SELECT motivo_inativo FROM produtos WHERE id='hotmart:2'")["motivo_inativo"] == "filtro"


def test_admin_exige_chave_e_resume(cli):
    c = cli[0]
    app = c.app
    app.state.cfg.admin_key = "adm"
    assert c.get("/api/admin/resumo").status_code == 401
    assert c.get("/api/admin/resumo", headers={"x-admin-key": "errada"}).status_code == 401
    r = c.get("/api/admin/resumo", headers={"x-admin-key": "adm"})
    assert r.status_code == 200 and "assinantes" in r.json() and len(r.json()["planos"]) == 3
    assert c.get("/api/admin/assinantes", headers={"x-admin-key": "adm"}).status_code == 200


def test_senha_so_para_demo(cli):
    c = cli[0]
    cfg = c.app.state.cfg
    cfg.demo_email, cfg.demo_senha = "demo@rangeltech.net", "Demo2026"
    assert c.post("/api/entrar/senha", json={"email": "demo@rangeltech.net", "senha": "errada"}).status_code == 400
    assert c.post("/api/entrar/senha", json={"email": "outro@rangeltech.net", "senha": "Demo2026"}).status_code == 400
    assert c.post("/api/entrar/senha", json={"email": "Demo@RangelTech.net", "senha": "Demo2026"}).status_code == 200
    cfg.demo_senha = ""
    assert c.post("/api/entrar/senha", json={"email": "demo@rangeltech.net", "senha": ""}).status_code == 400
