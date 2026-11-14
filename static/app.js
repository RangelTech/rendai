;(() => {
  const $ = (s) => document.querySelector(s)
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])
  const num = (n) => (n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1).replace('.', ',') + ' mil' : String(n ?? 0))
  const brl = (v) => 'R$ ' + Number(v || 0).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  const brl0 = (v) => 'R$ ' + Number(v || 0).toLocaleString('pt-BR', { maximumFractionDigits: 0 })
  const IC = {
    fogo: '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2s5 4.5 5 9.5a5 5 0 0 1-10 0C7 9 9 7 9 7s.5 3 2.5 3C11.5 7 12 2 12 2Z"/></svg>',
    raio: '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M13 2 3 14h7l-1 8 10-12h-7z"/></svg>',
    check: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
    trofeu: '<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6"/><path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18"/><path d="M4 22h16"/><path d="M18 2H6v7a6 6 0 0 0 12 0V2Z"/></svg>',
    estrela: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m12 2 3.1 6.3 6.9 1-5 4.9 1.2 6.8L12 17.8 5.8 21l1.2-6.8-5-4.9 6.9-1z"/></svg>',
    seta: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M7 17 17 7M7 7h10v10"/></svg>',
  }
  let eu = null
  let aba = 'inicio'
  let filtro = { q: '', categoria: '' }

  async function api(url, opt = {}) {
    const r = await fetch(url, { ...opt, headers: { 'Content-Type': 'application/json' } })
    if (r.status === 401) { location.href = '/?entrar=1'; throw new Error('entre de novo') }
    let j = {}
    try { j = await r.json() } catch {}
    if (!r.ok) { const e = new Error(typeof j.detail === 'string' ? j.detail : 'Não deu certo. Tente de novo.'); e.status = r.status; throw e }
    return j
  }
  const acao = (tipo, produto, valor) => api('/api/acao', { method: 'POST', body: JSON.stringify({ tipo, produto, valor }) })

  function toast(t, erro) {
    const el = $('#toast'); el.textContent = t; el.className = 'toast' + (erro ? ' erro' : ''); el.hidden = false
    clearTimeout(el._t); el._t = setTimeout(() => (el.hidden = true), 2600)
  }

  // confete leve em canvas (sem biblioteca)
  function confete() {
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const c = $('#confete'), x = c.getContext('2d')
    c.width = innerWidth; c.height = innerHeight
    const cores = ['#22e58b', '#ffc93c', '#ff8a3d', '#8b7cff', '#ffffff']
    const ps = Array.from({ length: 140 }, () => ({ x: innerWidth / 2, y: innerHeight / 3, vx: (Math.random() - 0.5) * 14, vy: -Math.random() * 14 - 4, r: Math.random() * 6 + 3, c: cores[(Math.random() * cores.length) | 0], a: Math.random() * 6, va: (Math.random() - 0.5) * 0.3 }))
    let t = 0
    const passo = () => {
      x.clearRect(0, 0, c.width, c.height)
      for (const p of ps) { p.vy += 0.35; p.x += p.vx; p.y += p.vy; p.a += p.va; x.save(); x.translate(p.x, p.y); x.rotate(p.a); x.fillStyle = p.c; x.fillRect(-p.r, -p.r / 2, p.r * 2, p.r); x.restore() }
      if (++t < 140) requestAnimationFrame(passo); else x.clearRect(0, 0, c.width, c.height)
    }
    passo()
  }
  function festa(titulo, desc) {
    $('#festa-ic').innerHTML = IC.trofeu; $('#festa-t').textContent = titulo; $('#festa-d').textContent = desc
    $('#festa').hidden = false; confete()
  }
  $('#festa-ok').onclick = () => ($('#festa').hidden = true)

  // mostra o que a ação rendeu: XP, subida de nível, conquistas
  async function comemorar(r) {
    if (r.xp_ganho) toast(`+${r.xp_ganho} XP`)
    if (r.subiu) festa(`Nível ${r.nivel.numero}: ${r.nivel.nome}!`, 'Você subiu de nível. Continua assim.')
    else if (r.missao?.premiada) festa('Missão cumprida!', `+${r.missao.premio_xp} XP. Semana que vem tem outra.`)
    else if (r.conquistas?.length) festa(r.conquistas[0].nome, r.conquistas[0].descricao)
    await carregarEu()
  }

  async function carregarEu() {
    eu = await api('/api/eu')
    $('#st-fogo b').textContent = eu.streak
    $('#st-xp b').textContent = num(eu.nivel.xp)
  }

  function cartaoProduto(p, rot) {
    return `<button class="pl" data-prod="${esc(p.id)}">${p.imagem ? `<img src="${esc(p.imagem)}" alt="" loading="lazy">` : '<div class="ph"></div>'}
      <div class="pi">${rot ? `<span class="tag">${esc(rot)}</span>` : ''}<b>${esc(p.titulo)}</b>${p.ganho_venda ? `<span class="ganho">Ganhe até ${brl(p.ganho_venda)} por venda</span>` : ''}
      <div class="calor">${Math.round(p.temperatura)}°<div class="temp"><i style="width:${Math.max(6, p.temperatura)}%"></i></div></div>
      <div class="meta"><span>★ ${(p.nota || 0).toFixed(1).replace('.', ',')}</span><span>${num(p.avaliacoes)} avaliações</span>${p.alunos ? `<span>${num(p.alunos)} alunos</span>` : ''}</div></div></button>`
  }
  const tempoAte = (t) => { const s = Math.max(0, t - Date.now() / 1000); const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600); return d ? `${d}d ${h}h` : `${h}h ${Math.floor((s % 3600) / 60)}min` }

  function cartoesPlanos(planos) {
    return `<div class="planos-app">${planos.map((p, i) => `<div class="pa ${i === 1 ? 'dest' : ''}">${i === 1 ? '<span class="tag">Mais escolhido</span>' : ''}
      <b class="display">${esc(p.nome)}</b><div class="pa-v">${brl(p.preco)}<small>/mês</small></div><span class="pa-de">depois do lançamento ${brl(p.depois)}</span>
      <ul>${p.itens.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>
      ${p.checkout ? `<a class="btn ${i === 1 ? 'btn-pri' : 'btn-sec'} grande" href="${esc(p.checkout)}" target="_blank" rel="noopener">Assinar o ${esc(p.nome)}</a>` : '<span class="atualizado">Abre em instantes</span>'}</div>`).join('')}</div>
      <p class="atualizado">Pague com o mesmo e-mail desta conta: o plano libera sozinho em segundos. Garantia de 7 dias.</p>`
  }
  function abrirPlanos(motivo) {
    const f = $('#folha'), c = $('#folha-corpo')
    f.hidden = false
    c.innerHTML = `<h3 class="display" style="font-size:22px;margin-right:32px">${esc(motivo || 'Escolha seu plano')}</h3><p style="color:var(--mut)">Preço de lançamento por tempo limitado.</p>${cartoesPlanos(eu.planos)}`
  }

  async function telaInicio() {
    const t = $('#tela')
    t.innerHTML = '<div class="esq" style="height:140px;margin-top:8px"></div><div class="esq" style="margin-top:16px"></div><div class="esq" style="height:220px;margin-top:16px"></div>'
    const m = await api('/api/missao')
    const n = eu.nivel
    const potencial = m.produtos.reduce((t, p) => t + (p.ganho_venda || 0) * 2, 0)
    const dias = Array.from({ length: 7 }, (_, i) => `<i class="${i < Math.min(7, eu.streak) ? 'on' : ''}">${i < eu.streak ? IC.check : ''}</i>`).join('')
    t.innerHTML = `
      ${eu.assinante ? '' : `<div class="paywall"><b class="display">Escolha seu plano e comece a vender hoje</b><span>Os produtos abaixo pagam até ${brl(Math.max(0, ...m.produtos.map((p) => p.ganho_venda || 0)))} por venda.</span><button class="btn btn-ouro grande" data-planos>Ver planos</button></div>`}
      ${potencial ? `<div class="potencial"><span>Seu potencial nesta semana</span><b>${brl0(potencial)}</b><small>vendendo 2 de cada produto da sua missão · ${brl0(potencial * 4.3)} por mês</small></div>` : ''}
      <div class="nivel"><div class="nivel-top"><div class="nivel-n">${n.numero}</div><div><b>${esc(n.nome)}</b><span>${n.proximo ? `Faltam ${num(n.falta)} XP para ${esc(n.proximo)}` : 'Nível máximo. Lenda!'}</span></div></div>
        <div class="barra"><i id="barra-xp"></i></div></div>
      <div class="checkin"><div><b>${eu.fez_checkin_hoje ? 'Presença confirmada hoje' : 'Marque presença hoje'}</b><span>${eu.streak} ${eu.streak === 1 ? 'dia seguido' : 'dias seguidos'}</span><div class="dias">${dias}</div></div>
        <button class="btn ${eu.fez_checkin_hoje ? 'btn-sec' : 'btn-ouro'}" id="b-checkin" ${eu.fez_checkin_hoje ? 'disabled' : ''}>${eu.fez_checkin_hoje ? IC.check : IC.fogo} ${eu.fez_checkin_hoje ? 'Feito' : '+10 XP'}</button></div>
      <div class="sec-t"><h2>Missão da semana</h2></div>
      <div class="missao"><div class="m-cab"><span>Até domingo</span><span class="m-tempo">termina em ${tempoAte(m.termina)}</span></div>
        <h3>${m.completa ? 'Missão cumprida!' : 'Divulgue e faça sua venda'}</h3>
        ${m.etapas.map((e) => `<div class="etapa"><span>${esc(e.texto)}</span><small>${e.feito}/${e.meta}</small><div class="barra"><i style="width:${Math.round((e.feito / e.meta) * 100)}%"></i></div></div>`).join('')}
        <span class="m-premio ${m.completa ? 'ok' : ''}">${IC.raio} ${m.completa ? 'Prêmio recebido' : `Prêmio: +${m.premio_xp} XP`}</span></div>
      <div class="sec-t"><h2>Seus produtos da semana</h2><button data-ir="vitrine">Ver todos</button></div>
      <div class="lista-p">${m.produtos.length ? m.produtos.map((p, i) => cartaoProduto(p, i === 0 ? 'Mais quente' : '')).join('') : '<div class="vazio">A vitrine está sendo atualizada. Volte em alguns minutos.</div>'}</div>`
    requestAnimationFrame(() => ($('#barra-xp').style.width = Math.round(n.pct * 100) + '%'))
    const b = $('#b-checkin')
    if (b && !b.disabled) b.onclick = async () => { b.disabled = true; try { await comemorar(await acao('checkin')); telaInicio() } catch (e) { toast(e.message, 1); b.disabled = false } }
  }

  async function telaVitrine() {
    const t = $('#tela')
    t.innerHTML = `<div class="sec-t"><h2>Vendendo agora</h2></div>
      <form class="busca" id="f-busca"><input id="q" placeholder="Buscar: bolo, inglês, maquiagem…" value="${esc(filtro.q)}" autocomplete="off"><button class="btn btn-sec" type="submit">Buscar</button></form>
      <div class="chips" id="chips"></div><div class="lista-p" id="lp">${'<div class="esq"></div>'.repeat(5)}</div><div class="atualizado" id="atu"></div>`
    $('#f-busca').onsubmit = (e) => { e.preventDefault(); filtro.q = $('#q').value.trim(); carregar() }
    const NOMES = { business_and_careers: 'Negócios', health_and_sports: 'Saúde e esporte', gastronomy: 'Culinária', education: 'Educação', personal_development: 'Desenvolvimento', languages: 'Idiomas', beauty: 'Beleza', design: 'Design', information_technology: 'Tecnologia', finance_investment: 'Finanças', hobbies: 'Hobbies', apps_software: 'Apps', fashion: 'Moda', pets: 'Pets', relationships: 'Relacionamentos', sexuality: 'Relacionamento', crafts: 'Artesanato' }
    async function carregar() {
      const j = await api(`/api/vitrine?q=${encodeURIComponent(filtro.q)}&categoria=${encodeURIComponent(filtro.categoria)}&limite=40`)
      $('#chips').innerHTML = [`<button data-cat="" class="${filtro.categoria ? '' : 'on'}">Tudo</button>`, ...j.categorias.map((c) => `<button data-cat="${esc(c.categoria)}" class="${filtro.categoria === c.categoria ? 'on' : ''}">${esc(NOMES[c.categoria] || c.categoria || 'Outros')}</button>`)].join('')
      $('#lp').innerHTML = j.produtos.length ? j.produtos.map((p, i) => cartaoProduto(p, i < 3 && !filtro.q ? 'Top ' + (i + 1) : '')).join('') : '<div class="vazio">Nada encontrado. Tente outra palavra.</div>'
      $('#atu').textContent = j.atualizado ? `Ofertas conferidas há ${Math.max(1, Math.round((Date.now() / 1000 - j.atualizado) / 60))} min` : ''
    }
    $('#chips').onclick = (e) => { const b = e.target.closest('[data-cat]'); if (b) { filtro.categoria = b.dataset.cat; carregar() } }
    carregar()
  }

  async function telaRanking() {
    const t = $('#tela')
    t.innerHTML = '<div class="esq" style="margin-top:24px"></div>'
    const r = await api('/api/ranking')
    t.innerHTML = `<div class="sec-t"><h2>Ranking da semana</h2></div>
      <p style="color:var(--mut);margin:0 0 16px;font-size:14px">XP ganho desde segunda. Zera toda semana: qualquer um pode chegar no topo.</p>
      <div class="rk">${r.top.length ? r.top.map((l) => `<div class="rk-l ${l.eu ? 'eu' : ''}"><span class="rk-p">${l.pos}</span><b>${esc(l.apelido)}${l.eu ? ' (você)' : ''}</b><span>${num(l.xp)} XP</span></div>`).join('') : '<div class="vazio">Ninguém pontuou ainda nesta semana. Seja o primeiro!</div>'}</div>
      ${r.minha_posicao && r.minha_posicao > 10 ? `<p class="atualizado">Você está em ${r.minha_posicao}º lugar.</p>` : ''}`
  }

  async function telaPerfil() {
    await carregarEu()
    const t = $('#tela')
    const tem = new Set(eu.conquistas.map((c) => c.codigo))
    t.innerHTML = `<div class="sec-t"><h2>Seu perfil</h2></div>
      <div class="cartao">
        <div class="lin"><span>Apelido no ranking</span><b>${esc(eu.apelido)}</b></div>
        <form id="f-ap" class="busca"><input id="ap" maxlength="24" placeholder="Novo apelido"><button class="btn btn-sec" type="submit">Salvar</button></form>
        <div class="lin"><span>Comissões registradas</span><b>${brl(eu.comissoes)}</b></div>
        <div class="lin"><span>Vendas</span><b>${eu.vendas}</b></div>
        <div class="lin"><span>Nível</span><b>${eu.nivel.numero} · ${esc(eu.nivel.nome)}</b></div>
      </div>
      <div class="sec-t"><h2>Conquistas</h2><span style="color:var(--mut);font-size:14px">${tem.size}/${eu.todas_conquistas.length}</span></div>
      <div class="conqs">${eu.todas_conquistas.map((c) => `<div class="cq ${tem.has(c.codigo) ? 'on' : ''}">${IC.estrela}<b>${esc(c.nome)}</b>${esc(c.descricao)}</div>`).join('')}</div>
      <div class="sec-t"><h2>Plano</h2></div>
      ${eu.assinante ? `<div class="cartao"><div class="lin"><span>Plano ${esc((eu.planos.find((x) => x.codigo === eu.plano) || {}).nome || '')}</span><b style="color:var(--ac)">Ativo</b></div><div class="lin"><span>Produtos nesta semana</span><b>${eu.escolhidos_semana}${eu.limite_semana ? ' de ' + eu.limite_semana : ''}</b></div>${eu.plano !== 'elite' ? '<button class="btn btn-sec" data-planos>Subir de plano</button>' : ''}</div>` :
        cartoesPlanos(eu.planos)}
      <button class="btn btn-sec" style="width:100%;margin-top:24px" id="sair">Sair da conta</button>`
    $('#f-ap').onsubmit = async (e) => { e.preventDefault(); try { await api('/api/eu/apelido', { method: 'POST', body: JSON.stringify({ apelido: $('#ap').value }) }); toast('Apelido salvo'); telaPerfil() } catch (er) { toast(er.message, 1) } }
    $('#sair').onclick = async () => { await fetch('/api/sair', { method: 'POST' }); location.href = '/' }
  }

  // detalhe do produto: passo a passo de afiliação, kit da IA, divulguei, registrar venda
  async function abrirProduto(id) {
    const f = $('#folha'), c = $('#folha-corpo')
    f.hidden = false
    c.innerHTML = '<div class="esq" style="height:120px"></div><div class="esq" style="margin-top:16px"></div>'
    let p
    try { p = await api('/api/produtos/' + encodeURIComponent(id)) } catch (e) { c.innerHTML = `<div class="vazio" style="margin-top:24px">${esc(e.message)}</div>`; return }
    c.innerHTML = `
      <div class="det-top">${p.imagem ? `<img src="${esc(p.imagem)}" alt="">` : ''}<div><h3>${esc(p.titulo)}</h3><span>${esc(p.autor || '')} · Hotmart</span></div></div>
      <div class="nums"><div><b style="color:var(--fogo)">${Math.round(p.temperatura)}°</b><span>temperatura</span></div><div><b>★ ${(p.nota || 0).toFixed(1).replace('.', ',')}</b><span>${num(p.avaliacoes)} avaliações</span></div><div><b>${p.alunos ? num(p.alunos) : '—'}</b><span>alunos</span></div></div>
      ${p.ganho_venda ? `<div class="ganho-box"><div class="gb-top"><span>Você ganha até</span><b>${brl(p.ganho_venda)}</b><span>por venda</span></div>
        <label for="sim">Vendas por semana: <b id="sim-v">3</b></label><input type="range" id="sim" min="1" max="20" value="3">
        <div class="gb-res"><div><span>na semana</span><b id="sim-s"></b></div><div><span>no mês</span><b id="sim-m"></b></div></div>
        <small>${p.comissao_estimada ? 'Comissão média estimada de ' + Math.round(p.comissao_pct * 100) + '% sobre ' + brl(p.preco) + ', já descontada a taxa da plataforma.' : 'Comissão de ' + Math.round(p.comissao_pct * 100) + '% sobre ' + brl(p.preco) + '.'}</small></div>` : ''}
      ${p.frase ? `<div class="frase">${esc(p.frase)}</div>` : ''}
      <ol class="passos-af">
        <li><span>Toque em <b>Quero vender</b> para guardar este produto na sua lista (vale XP).</span></li>
        <li><span>Abra a página do produto, entre com a sua conta Hotmart (é grátis) e clique em <b>Afiliar-se</b>.</span></li>
        <li><span>Copie o seu link de afiliado e divulgue com o kit pronto. A comissão cai na <b>sua</b> conta Hotmart.</span></li>
      </ol>
      <div class="botoes">
        ${p.escolhido ? '' : eu.assinante && !p.liberado ? '<button class="btn btn-ouro grande" data-planos>Limite do plano: subir de plano</button>' : `<button class="btn btn-pri grande" id="b-quero">Quero vender (+50 XP)</button>`}
        <a class="btn btn-sec grande" href="${esc(p.url)}" target="_blank" rel="noopener">Abrir na Hotmart ${IC.seta}</a>
        ${p.escolhido ? '<button class="btn btn-ouro grande" id="b-kit">Gerar kit de divulgação</button>' : ''}
      </div>
      <div id="kit"></div>
      ${p.escolhido ? `<div class="botoes" style="margin-top:16px"><button class="btn btn-sec" id="b-div">Divulguei agora (+30 XP)</button><button class="btn btn-sec" id="b-venda">Registrar uma venda (+200 XP)</button></div>` : ''}`
    const sim = $('#sim')
    if (sim) {
      const conta = () => { const v = +sim.value; $('#sim-v').textContent = v; $('#sim-s').textContent = brl0(v * p.ganho_venda); $('#sim-m').textContent = brl0(v * p.ganho_venda * 4.3) }
      sim.oninput = conta; conta()
    }
    const bq = $('#b-quero')
    if (bq) bq.onclick = async () => {
      if (!eu.assinante) return abrirPlanos('Escolha um plano para vender este produto')
      bq.disabled = true
      try { await comemorar(await acao('afiliar', id)); abrirProduto(id) } catch (e) { if (e.status === 402) abrirPlanos(e.message); else toast(e.message, 1); bq.disabled = false }
    }
    const bk = $('#b-kit')
    if (bk) bk.onclick = async () => {
      bk.disabled = true; bk.textContent = 'Escrevendo o seu kit…'
      try {
        const k = await api(`/api/produtos/${encodeURIComponent(id)}/kit`, { method: 'POST' })
        const bloco = (t, v) => `<div class="kit-b"><strong>${t}</strong>${esc(v)}<button class="copiar" data-copiar="${esc(v)}">Copiar</button></div>`
        $('#kit').innerHTML = `<div class="kit">${k.publico ? `<div class="kit-b"><strong>Para quem indicar</strong>${esc(k.publico)}</div>` : ''}
          ${(k.posts || []).map((x, i) => bloco('Post ' + (i + 1), x + (k.hashtags?.length ? '\n\n' + k.hashtags.join(' ') : ''))).join('')}
          ${k.whatsapp ? bloco('WhatsApp', k.whatsapp) : ''}${k.roteiro_video ? bloco('Roteiro de vídeo curto', k.roteiro_video) : ''}</div>`
        bk.remove(); toast('+20 XP'); carregarEu()
      } catch (e) { toast(e.message, 1); bk.disabled = false; bk.textContent = 'Gerar kit de divulgação' }
    }
    const bd = $('#b-div')
    if (bd) bd.onclick = async () => { try { await comemorar(await acao('divulgar', id)) } catch (e) { toast(e.message, 1) } }
    const bv = $('#b-venda')
    if (bv) bv.onclick = async () => {
      const v = prompt('Quanto você recebeu de comissão nessa venda? (R$)')
      if (!v) return
      try { await comemorar(await acao('venda', id, v.replace(',', '.'))) } catch (e) { toast(e.message, 1) }
    }
  }

  document.addEventListener('click', async (e) => {
    const cp = e.target.closest('[data-copiar]')
    if (cp) { try { await navigator.clipboard.writeText(cp.dataset.copiar); cp.textContent = 'Copiado!'; setTimeout(() => (cp.textContent = 'Copiar'), 1500) } catch {} return }
    if (e.target.closest('[data-planos]')) return abrirPlanos()
    const pr = e.target.closest('[data-prod]')
    if (pr) return abrirProduto(pr.dataset.prod)
    const ir = e.target.closest('[data-ir]')
    if (ir) return trocar(ir.dataset.ir)
    if (e.target.closest('[data-fechar-folha]') || e.target === $('#folha')) { $('#folha').hidden = true; if (aba === 'inicio') telaInicio() }
  })
  addEventListener('keydown', (e) => { if (e.key === 'Escape') { $('#folha').hidden = true; $('#festa').hidden = true } })
  document.querySelector('.abas').onclick = (e) => { const b = e.target.closest('[data-aba]'); if (b) trocar(b.dataset.aba) }
  function trocar(a) {
    aba = a
    document.querySelectorAll('.abas button').forEach((b) => b.classList.toggle('on', b.dataset.aba === a))
    scrollTo({ top: 0 })
    ;({ inicio: telaInicio, vitrine: telaVitrine, ranking: telaRanking, perfil: telaPerfil })[a]().catch((er) => toast(er.message, 1))
  }
  carregarEu().then(() => trocar('inicio')).catch(() => {})
})()
