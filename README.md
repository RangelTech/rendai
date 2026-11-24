<!-- readme-padrao -->
# Rendaí

> Renda extra como afiliado, em forma de jogo: vitrine com os produtos que estão vendendo agora (668 produtos da Hotmart), kit de divulgação pronto pela IA, missão da semana, XP, níveis e ranking. Com o Link Rendaí o cliente vende sem se afiliar: a venda cai na conta da casa e a parte dele vai para a carteira, com saque por Pix. Start R$ 19,90, Pro R$ 29,90, Elite R$ 59,90.

![Tela inicial do Rendaí](docs/img/landing.webp)

## O que é
Renda extra como afiliado, em forma de jogo: vitrine com os produtos que estão vendendo agora (668 produtos da Hotmart), kit de divulgação pronto pela IA, missão da semana, XP, níveis e ranking. Com o Link Rendaí o cliente vende sem se afiliar: a venda cai na conta da casa e a parte dele vai para a carteira, com saque por Pix. Start R$ 19,90, Pro R$ 29,90, Elite R$ 59,90.

Estado: **no ar**. Produto da Rangel Tech, vendido pela Cakto (conta do Lucas). Registro único dos produtos: `vps_rt_infra/registro/produtos.yml` (spec 57).

## Onde está no ar
- https://rendai.rangeltech.net

**Painel do administrador:** `https://rendai.rangeltech.net/admin` (login único dos produtos; a credencial está no Infisical, `rt-produtos/_admin`).

![Tela de entrada do /admin](docs/img/admin.webp)

O usuário de teste (QA) de cada produto é obtido no painel único (`painel.rangeltech.net`, aba do produto).

## Onde faz o deploy
Este repositório **só publica** o artefato; **quem implanta é o `vps_rt_infra`** (nenhum container é trocado à mão).

1. Publicar a release: criar a tag (`v*`); o workflow `.github/workflows/release.yml` gera a imagem e o `release-manifest.json` e publica como release.
2. Implantar (de madrugada, 00h às 06h de Brasília): `gh workflow run deploy.yml --repo RangelTech/vps_rt_infra -f service=rendai`
   - Só validar, sem implantar: acrescentar `-f dry_run=true`.
3. Voltar uma versão (rollback): `gh workflow run deploy.yml --repo RangelTech/vps_rt_infra -f service=rendai -f version=<tag-antiga>`.

O serviço no mapa de releases é `rendai` (`vps_rt_infra/releases/sources.json`).

## Como funciona por dentro
- **API e site:** FastAPI (Python) em `app/`, com a landing em `static/`. Container único (`Dockerfile`).
- **Dados:** hoje SQLite no volume do container; **está planejada a migração** para o banco `produtos` do Postgres (um schema por produto) e dos arquivos para o MinIO (spec 57, seções 8 e 8.6).
- **IA:** fala com o Qwen pelo `llm-access` (chave por produto).
- **Cobrança:** webhook da Cakto (`/api/cakto` ou rota equivalente) libera o plano; a conciliação diária compara pedidos e assinaturas.
- **Login:** hoje por código no e-mail; passa para e-mail e senha com captcha invisível e "entrar por link" (spec 57, seção 5A).

## Segredos
Só os **nomes** das variáveis estão neste repositório, em [`segredos.yml`](segredos.yml). Os **valores** ficam no Infisical (projeto `rt-produtos`, ambiente `prod`, pasta `rendai`) e chegam à VPS **gerados no momento do deploy**. Nunca coloque valor de segredo no git.

## Rodar e testar localmente
```bash
python -m venv .venv && source .venv/bin/activate   # no Windows: .venv\Scripts\activate
pip install -r requirements.txt
pytest
```
Os testes de QA em produção são por cliques reais (relatório em PDF no `new_ragente/qa/evidencias`).

## Links
- Painel único: https://painel.rangeltech.net (aba do produto: links, usuário de QA, planos)
- Spec de organização geral: `new_ragente/specs/57_ORGANIZACAO_GERAL_PRODUTOS_E_PAINEL.md`
- Monitor: Uptime Kuma, `rendai`

## Notas técnicas (README anterior)

# Rendaí

Renda extra com produtos afiliados, gamificado (spec 39 do ecossistema Rangel Tech). A vitrine mostra os produtos digitais que estão vendendo agora (Hotmart, conferidos todo dia), o usuário se afilia com a própria conta, divulga com o kit escrito pela IA e sobe de nível com missões, sequência de dias, conquistas e ranking semanal.

- `app/coletor.py`: varredura da vitrine pública da Hotmart (`__NEXT_DATA__`), conferência de validade a cada 6 h, temperatura pelo crescimento de avaliações e alunos; filtro de aposta, adulto e promessa milagrosa.
- `app/jogo.py`: XP por ação (nunca por sorte), limites diários, níveis, conquistas, missão semanal e ranking.
- `app/kit.py`: kit de divulgação pela IA (llm-access), em cache por 7 dias.
- Deploy: somente pelo `RangelTech/vps_rt_infra` (stack `rendai`). Segredos em `/opt/platform/secrets/rendai.env`.

Teste local: `RENDAI_DEV=1 RENDAI_DADOS=./dados uvicorn app.main:app`.
