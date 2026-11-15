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
    app = criar_app(Config(dados=str(tmp_path), dev=True, segredo_sessao="s", cakto_segredo="ck", coletar=False, admin_key="adm",
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


def test_meus_produtos(cli):
    c, _ = cli
    assinar(c)
    entrar(c)
    c.post("/api/acao", json={"tipo": "afiliar", "produto": "hotmart:2"})
    c.post("/api/acao", json={"tipo": "afiliar", "produto": "hotmart:5"})
    ids = [p["id"] for p in c.get("/api/meus").json()["produtos"]]
    assert set(ids) == {"hotmart:2", "hotmart:5"}


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


def test_link_de_afiliado_valida_e_entra_no_kit(cli, monkeypatch):
    c, db = cli
    assinar(c); entrar(c)
    assert c.post("/api/acao", json={"tipo": "afiliar", "produto": "hotmart:1"}).status_code == 200
    assert c.post("/api/produtos/hotmart:1/link", json={"link": "https://golpe.com/x"}).status_code == 422
    assert c.post("/api/produtos/hotmart:1/link", json={"link": "https://go.hotmart.com/A1B2C3?dp=1"}).status_code == 200
    assert c.get("/api/produtos/hotmart:1").json()["meu_link"] == "https://go.hotmart.com/A1B2C3?dp=1"
    from app import kit
    monkeypatch.setattr(kit, "gerar", lambda db, cfg, p: {"posts": ["Post A"], "whatsapp": "Oi", "hashtags": []})
    k = c.post("/api/produtos/hotmart:1/kit").json()
    assert k["posts"][0].endswith("https://go.hotmart.com/A1B2C3?dp=1") and k["whatsapp"].endswith("A1B2C3?dp=1")


def test_leitor_de_comissao_da_pagina_logada():
    from app import afiliado
    assert afiliado.ler_comissao('{"commissionPercentage": 60, "x": 1}') == 0.6
    assert afiliado.ler_comissao('<p>Comissão de até 45,5% por venda</p>') == 0.455
    assert afiliado.ler_comissao('{"price": 197}') is None
    assert afiliado.sessoes(Config()) == {}


def test_link_rendai_venda_garantia_e_saque(cli):
    """Casa afiliada (spec 44 parte 2): termos antes do link, link /r/ com clique, prazo + 2 dias úteis, reembolso, recebedor, saque manual."""
    from app import carteira, repasse
    c, db = cli
    db.exec("UPDATE produtos SET link_casa='https://go.hotmart.com/CASA1' WHERE id='hotmart:1'")
    assinar(c); entrar(c)
    c.post("/api/acao", json={"tipo": "afiliar", "produto": "hotmart:1"})
    p = c.get("/api/produtos/hotmart:1").json()
    assert p["link_rendai"] == "" and p["precisa_termos"] is True and p["cai_em"]["pix"] >= 2
    assert c.post("/api/termos/aceitar", json={"versao": "velha"}).status_code == 409
    assert c.post("/api/termos/aceitar", json={"versao": repasse.TERMOS_VERSAO}).status_code == 200
    p = c.get("/api/produtos/hotmart:1").json()
    assert "/r/" in p["link_rendai"] and p["precisa_termos"] is False
    caminho = p["link_rendai"].split(".net", 1)[-1] if ".net" in p["link_rendai"] else p["link_rendai"].split("://", 1)[1].split("/", 1)[1]
    caminho = "/" + caminho.lstrip("/")
    r = c.get(caminho, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("https://go.hotmart.com/CASA1?sck=rd")
    assert db.um("SELECT COUNT(*) n FROM cliques")["n"] == 1
    sck = r.headers["location"].split("sck=")[1]
    tx = 0.50
    assert carteira.registrar_venda(db, tx, {"transacao": "T1", "sck": sck, "comissao": 300, "status": "APPROVED", "garantia_ate": agora() + 86400, "metodo": "PIX"}) == "nova"
    assert carteira.registrar_venda(db, tx, {"transacao": "T2", "sck": sck, "comissao": 100, "status": "APPROVED", "criado": agora() - 40 * 86400, "metodo": "CREDIT_CARD"}) == "nova"
    assert carteira.registrar_venda(db, tx, {"transacao": "T3", "sck": "outra", "comissao": 50}) == "sem dono"
    assert carteira.registrar_venda(db, tx, {"transacao": "T1", "sck": sck, "comissao": 30, "status": "APPROVED"}) == "ignorada"
    t1 = db.um("SELECT libera_em FROM vendas_casa WHERE id='T1'")["libera_em"]
    assert t1 > agora() + 86400  # garantia + 2 dias úteis
    w = c.get("/api/carteira").json()
    assert w["a_liberar"] == 150.0 and w["disponivel"] == 50.0
    assert c.post("/api/carteira/saque").status_code == 422          # sem recebedor / abaixo do mínimo
    assert c.post("/api/carteira/recebedor", json={"nome": "Ze da Silva", "doc": "111.111.111-11", "pix": "ze@exemplo.com"}).status_code == 422
    assert c.post("/api/carteira/recebedor", json={"nome": "Ze da Silva", "doc": "529.982.247-25", "pix": "390.533.447-05"}).status_code == 422  # chave de outro CPF
    assert c.post("/api/carteira/recebedor", json={"nome": "Ze da Silva", "doc": "529.982.247-25", "pix": "ze@exemplo.com"}).status_code == 200
    assert carteira.registrar_venda(db, tx, {"transacao": "T2", "sck": sck, "status": "REFUNDED"}) == "reembolsada"
    assert c.get("/api/carteira").json()["disponivel"] == 0.0
    db.exec("UPDATE vendas_casa SET libera_em=? WHERE id='T1'", (agora() - 1,))
    assert carteira.liberar_vencidas(db) == 1
    r = c.post("/api/carteira/saque")
    assert r.status_code == 200 and r.json()["saque_aberto"]["valor"] == 150.0
    assert c.post("/api/carteira/saque").status_code == 422  # um aberto por vez
    s = db.um("SELECT * FROM saques ORDER BY id DESC LIMIT 1")
    assert s["nome"] == "Ze da Silva" and s["doc"] == "52998224725" and abs(float(s["prazo"]) - float(s["criado"]) - 48 * 3600) < 1
    k = c.post("/api/produtos/hotmart:1/kit")
    assert k.status_code in (200, 503)


def test_admin_pagamentos_pago_recusado_csv(cli):
    from app import carteira, repasse
    c, db = cli
    adm = {"x-admin-key": "adm"}
    db.exec("UPDATE produtos SET link_casa='https://go.hotmart.com/CASA1' WHERE id='hotmart:1'")
    assinar(c); entrar(c)
    u = db.um("SELECT * FROM usuarios WHERE email='ze@exemplo.com'")
    sck = "rd" + carteira.codigo_ref(db, u)
    carteira.registrar_venda(db, 0.5, {"transacao": "P1", "sck": sck, "comissao": 400, "status": "COMPLETE"})
    c.post("/api/carteira/recebedor", json={"nome": "Ze da Silva", "doc": "52998224725", "pix": "ze@exemplo.com"})
    assert c.post("/api/carteira/saque").status_code == 200
    j = c.get("/api/admin/saques", headers=adm).json()
    assert j["a_pagar_n"] == 1 and j["a_pagar_total"] == 200.0 and j["recebido_das_plataformas"] == 400.0 and j["alerta"] is False
    sid = j["saques"][0]["id"]
    assert c.post(f"/api/admin/saques/{sid}/recusar", json={"motivo": "x"}, headers=adm).status_code == 422
    assert c.post(f"/api/admin/saques/{sid}/recusar", json={"motivo": "Chave Pix não confere"}, headers=adm).status_code == 200
    assert c.get("/api/carteira").json()["disponivel"] == 200.0  # recusado devolve o saldo
    assert c.post("/api/carteira/saque").status_code == 200
    sid2 = c.get("/api/admin/saques", headers=adm).json()["saques"][0]["id"]
    r = c.post(f"/api/admin/saques/{sid2}/pago", content=b"\x89PNG fake", headers={**adm, "x-arquivo": "comp.png", "content-type": "image/png"})
    assert r.status_code == 200
    assert c.get(f"/api/admin/saques/{sid2}/comprovante", headers=adm).status_code == 200
    assert c.post(f"/api/admin/saques/{sid2}/pago", headers=adm).status_code == 404  # já resolvido
    csv = c.get("/api/admin/saques.csv", headers=adm).text
    assert "Ze da Silva" in csv and "recusado" in csv and "pago" in csv
    assert c.get("/api/admin/saques").status_code in (401, 403)


def test_dias_uteis_e_documentos():
    from datetime import datetime
    from app import repasse
    sexta = datetime(2026, 11, 13, 10, 0, tzinfo=repasse.FUSO).timestamp()
    terca = datetime.fromtimestamp(repasse.mais_dias_uteis(sexta, 2), repasse.FUSO)
    assert terca.strftime("%Y-%m-%d") == "2026-11-17"  # sexta + 2 úteis (fim de semana; 15/11 é domingo)
    vespera = datetime(2026, 11, 19, 10, 0, tzinfo=repasse.FUSO).timestamp()  # 20/11 é feriado
    assert datetime.fromtimestamp(repasse.mais_dias_uteis(vespera, 2), repasse.FUSO).strftime("%Y-%m-%d") == "2026-11-24"
    assert repasse.cpf_ok("529.982.247-25") and not repasse.cpf_ok("111.111.111-11")
    assert repasse.cnpj_ok("65.505.647/0001-10") and not repasse.cnpj_ok("65.505.647/0001-11")
    assert repasse.prazo_dias("kiwify", "CREDIT_CARD") == 15 and repasse.prazo_dias("hotmart", "PIX") == 2

def test_admin_importa_vendas(cli):
    c, db = cli
    c2 = c
    r = c2.post("/api/admin/vendas", json=[{"transacao": "X", "sck": "rd00000000", "comissao": 10}], headers={"x-admin-key": "errada"})
    assert r.status_code == 401


# ---------------------------------------------------------------- senha (padrão), código (opção), esqueci a senha
def test_senha_criar_entrar_trocar_e_esqueci(cli):
    from fastapi.testclient import TestClient
    c, _ = cli
    app = c.app
    em = "senha@exemplo.com"
    entrar(c, em)
    assert c.get("/api/senha").json()["tem_senha"] is False
    assert c.post("/api/senha", json={"senha": "curta"}).status_code == 422
    assert c.post("/api/senha", json={"senha": "boa-senha-1"}).status_code == 200
    assert c.get("/api/senha").json()["tem_senha"] is True
    # trocar exige a atual
    assert c.post("/api/senha", json={"senha": "outra-senha", "atual": "errada"}).status_code == 400
    assert c.post("/api/senha", json={"senha": "outra-senha", "atual": "boa-senha-1"}).status_code == 200
    # entrar com senha num cliente novo (sem cookie)
    n = TestClient(app)
    assert n.post("/api/entrar/senha", json={"email": em, "senha": "boa-senha-1"}).status_code == 400
    r = n.post("/api/entrar/senha", json={"email": em, "senha": "outra-senha"})
    assert r.status_code == 200 and n.get("/api/senha").status_code == 200
    # conta sem senha recebe dica de usar o código
    entrar(TestClient(app), "semsenha@exemplo.com")
    r = TestClient(app).post("/api/entrar/senha", json={"email": "semsenha@exemplo.com", "senha": "qualquer1"})
    assert r.status_code == 400 and "código" in r.json()["detail"]
    # esqueci a senha: código separado do login, troca a senha e já entra
    e = TestClient(app)
    j = e.post("/api/senha/esqueci", json={"email": em}).json()
    assert e.post("/api/senha/redefinir", json={"email": em, "codigo": "000000" if j["codigo_dev"] != "000000" else "111111", "senha": "nova-senha-9"}).status_code == 400
    j = None
    import app.auth as A
    A.REENVIO_MIN_S = 0
    j = e.post("/api/senha/esqueci", json={"email": em}).json()
    assert e.post("/api/senha/redefinir", json={"email": em, "codigo": j["codigo_dev"], "senha": "nova-senha-9"}).status_code == 200
    assert e.get("/api/senha").json()["tem_senha"] is True
    assert TestClient(app).post("/api/entrar/senha", json={"email": em, "senha": "nova-senha-9"}).status_code == 200
    # e-mail sem conta: mesma resposta, sem código
    assert "codigo_dev" not in e.post("/api/senha/esqueci", json={"email": "ninguem@exemplo.com"}).json()


def test_senha_bloqueia_depois_de_muitos_erros(cli):
    from app import senha_rotas
    c, _ = cli
    senha_rotas._erros.clear()
    for _ in range(senha_rotas.MAX_ERROS):
        c.post("/api/entrar/senha", json={"email": "forca@exemplo.com", "senha": "x"})
    assert c.post("/api/entrar/senha", json={"email": "forca@exemplo.com", "senha": "x"}).status_code == 429
    senha_rotas._erros.clear()
