"""Kit de divulgação gerado pela IA (llm-access), um por produto, guardado em cache por 7 dias."""
from __future__ import annotations

import json
import re

import httpx

from app.db import Banco, agora
from app import admin  # prompts editáveis no /admin

PROMPT = """Você escreve material de divulgação para afiliados iniciantes no Brasil. Produto: "{titulo}" (de {autor}).
Descrição: {descricao}

Escreva em português do Brasil, tom animado mas honesto (sem prometer resultado garantido, sem mentir sobre o produto).
Responda SÓ com JSON:
{{"por_que_vende": "1 frase curta dizendo por que esse produto vende bem agora",
  "publico": "para quem indicar, em 1 frase",
  "posts": ["texto de post para Instagram/Facebook (até 400 letras, com emoji e chamada)", "segundo post, outro ângulo", "terceiro post, prova social/curiosidade"],
  "whatsapp": "mensagem curta para mandar no WhatsApp para amigos/grupos (até 300 letras)",
  "roteiro_video": "roteiro de vídeo curto de 20-30 s em 4 falas: gancho, problema, solução, chamada",
  "hashtags": ["#..", "#..", "#..", "#..", "#.."]}}"""


def _json(txt: str) -> dict:
    txt = re.sub(r"<think>.*?</think>", "", txt or "", flags=re.S)
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        raise ValueError("sem JSON")
    return json.loads(m.group(0))


REVISAO = """Você revisa material de divulgação de afiliados. Produto: "{titulo}". Kit (JSON):
{kit}

Critérios: honesto (sem prometer ganho garantido nem resultado milagroso), persuasivo, português correto, adequado a WhatsApp/Instagram.
Responda SÓ com JSON: {{"nota": 0-10, "problemas": ["..."]}}"""


def revisar(cfg, produto: dict, k: dict) -> dict | None:
    try:
        r = httpx.post(cfg.llm_base + "/chat/completions", headers={"Authorization": f"Bearer {cfg.llm_key}"}, timeout=90, json={
            "model": getattr(cfg, "llm_planejador", "rt-planejar"), "temperature": 0.2, "max_tokens": 400,
            "messages": [{"role": "user", "content": admin.formatar("rendai-revisao", REVISAO, titulo=produto["titulo"], kit=json.dumps(k, ensure_ascii=False)[:5000])}]})
        r.raise_for_status()
        j = _json(r.json()["choices"][0]["message"]["content"])
        return {"nota": float(j.get("nota") or 0), "problemas": [str(p)[:200] for p in (j.get("problemas") or [])][:5]}
    except Exception:  # noqa: BLE001  revisão é melhoria: sem ela o kit segue
        return None


def gerar(db: Banco, cfg, produto: dict) -> dict:
    c = db.um("SELECT conteudo, criado FROM kits WHERE produto=?", (produto["id"],))
    if c and agora() - c["criado"] < 7 * 86400:
        return json.loads(c["conteudo"])
    corpo = {"model": cfg.llm_modelo, "temperature": 0.7, "max_tokens": 1800, "chat_template_kwargs": {"enable_thinking": False},
             "messages": [{"role": "user", "content": admin.formatar("rendai-kit", PROMPT, titulo=produto["titulo"], autor=produto.get("autor") or "um especialista",
                                                                     descricao=(produto.get("descricao") or "")[:1500])}]}
    ultimo = None
    for _ in range(2):
        try:
            r = httpx.post(cfg.llm_base + "/chat/completions", headers={"Authorization": f"Bearer {cfg.llm_key}"}, json=corpo, timeout=120)
            r.raise_for_status()
            k = _json(r.json()["choices"][0]["message"]["content"])
            rev = revisar(cfg, produto, k)
            if rev and rev["nota"] < 7 and rev["problemas"]:
                # reescreve uma vez com as críticas do revisor
                corpo2 = dict(corpo)
                corpo2["messages"] = corpo["messages"] + [{"role": "assistant", "content": json.dumps(k, ensure_ascii=False)},
                                                          {"role": "user", "content": "Reescreva o kit corrigindo: " + "; ".join(rev["problemas"]) + ". Responda só com o JSON no mesmo formato."}]
                try:
                    r2 = httpx.post(cfg.llm_base + "/chat/completions", headers={"Authorization": f"Bearer {cfg.llm_key}"}, json=corpo2, timeout=120)
                    r2.raise_for_status()
                    k = _json(r2.json()["choices"][0]["message"]["content"])
                except Exception:  # noqa: BLE001
                    pass
            k["posts"] = [str(p) for p in (k.get("posts") or [])][:3]
            k["hashtags"] = [h if str(h).startswith("#") else "#" + str(h) for h in (k.get("hashtags") or [])][:8]
            db.exec("INSERT INTO kits(produto,conteudo,criado) VALUES(?,?,?) ON CONFLICT(produto) DO UPDATE SET conteudo=excluded.conteudo, criado=excluded.criado",
                    (produto["id"], json.dumps(k, ensure_ascii=False), agora()))
            if k.get("por_que_vende"):
                db.exec("UPDATE produtos SET frase=? WHERE id=?", (str(k["por_que_vende"])[:200], produto["id"]))
            return k
        except Exception as e:  # noqa: BLE001
            ultimo = e
    raise RuntimeError(f"kit: {ultimo}")
