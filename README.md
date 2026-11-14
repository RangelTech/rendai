# Rendaí

Renda extra com produtos afiliados, gamificado (spec 39 do ecossistema Rangel Tech). A vitrine mostra os produtos digitais que estão vendendo agora (Hotmart, conferidos todo dia), o usuário se afilia com a própria conta, divulga com o kit escrito pela IA e sobe de nível com missões, sequência de dias, conquistas e ranking semanal.

- `app/coletor.py`: varredura da vitrine pública da Hotmart (`__NEXT_DATA__`), conferência de validade a cada 6 h, temperatura pelo crescimento de avaliações e alunos; filtro de aposta, adulto e promessa milagrosa.
- `app/jogo.py`: XP por ação (nunca por sorte), limites diários, níveis, conquistas, missão semanal e ranking.
- `app/kit.py`: kit de divulgação pela IA (llm-access), em cache por 7 dias.
- Deploy: somente pelo `RangelTech/vps_rt_infra` (stack `rendai`). Segredos em `/opt/platform/secrets/rendai.env`.

Teste local: `RENDAI_DEV=1 RENDAI_DADOS=./dados uvicorn app.main:app`.
