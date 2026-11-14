;(() => {
  const $ = (s) => document.querySelector(s)
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])
  const num = (n) => (n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1).replace('.', ',') + ' mil' : String(n ?? 0))
  const modal = $('#m-entrar')
  let email = ''
  const msg = (t, tipo = 'erro') => { const m = $('#m-msg'); m.textContent = t || ''; m.className = 'msg ' + tipo }
  async function abrir() {
    try { const r = await fetch('/api/eu'); if (r.ok) return (location.href = '/app') } catch {}
    modal.hidden = false; msg(''); $('#passo-email').hidden = false; $('#passo-codigo').hidden = true; setTimeout(() => $('#email').focus(), 50)
  }
  document.querySelectorAll('[data-entrar]').forEach((b) => (b.onclick = abrir))
  modal.addEventListener('click', (e) => { if (e.target === modal || e.target.closest('[data-fechar]')) modal.hidden = true })
  addEventListener('keydown', (e) => { if (e.key === 'Escape') modal.hidden = true })
  const post = async (url, corpo) => {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(corpo) })
    let j = {}
    try { j = await r.json() } catch {}
    if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : 'Não deu certo. Tente de novo.')
    return j
  }
  $('#f-email').onsubmit = async (e) => {
    e.preventDefault(); const b = e.submitter; b.disabled = true; msg('')
    try {
      email = $('#email').value.trim()
      const j = await post('/api/entrar/codigo', { email })
      $('#passo-email').hidden = true; $('#passo-codigo').hidden = false
      $('#cod-txt').textContent = `Mandamos 6 números para ${email}. Confira também o spam.`
      if (j.codigo_dev) $('#codigo').value = j.codigo_dev
      setTimeout(() => $('#codigo').focus(), 50)
    } catch (err) { msg(err.message) } finally { b.disabled = false }
  }
  $('#f-cod').onsubmit = async (e) => {
    e.preventDefault(); const b = e.submitter; b.disabled = true; msg('')
    try { await post('/api/entrar', { email, codigo: $('#codigo').value }); msg('Tudo certo! Abrindo…', 'ok'); location.href = '/app' }
    catch (err) { msg(err.message) } finally { b.disabled = false }
  }
  $('#voltar-email').onclick = () => { $('#passo-email').hidden = false; $('#passo-codigo').hidden = true; msg('') }
  if (new URLSearchParams(location.search).has('entrar')) abrir()

  const brl = (v) => 'R$ ' + Number(v || 0).toLocaleString('pt-BR', { minimumFractionDigits: 0, maximumFractionDigits: 0 })
  const brl2 = (v) => 'R$ ' + Number(v || 0).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  let ganhoMedio = 60
  function calcular() {
    const p = +$('#c-prod').value, v = +$('#c-vend').value
    $('#c-prod-v').textContent = p; $('#c-vend-v').textContent = v
    const sem = p * v * ganhoMedio
    $('#c-semana').textContent = brl(sem); $('#c-mes').textContent = brl(sem * 4.3)
    $('#c-base').textContent = `${p} produto${p > 1 ? 's' : ''} × ${v} venda${v > 1 ? 's' : ''} × ${brl2(ganhoMedio)} de comissão média por venda`
  }
  ;['#c-prod', '#c-vend'].forEach((s2) => $(s2).addEventListener('input', calcular))

  fetch('/api/planos').then((r) => r.json()).then((j) => {
    const d = j.planos[0]?.lancamento_ate
    if (d) { const [a, m, di] = d.split('-'); $('#lanc-data').textContent = `${di}/${m}` }
    $('#planos').innerHTML = j.planos.map((p, i) => `<div class="plano ${i === 1 ? 'destaque' : ''}">${i === 1 ? '<span class="selo">Mais escolhido</span>' : ''}
      <b class="pn">${esc(p.nome)}</b><div class="pv">${brl2(p.preco).replace(',00', ',00')}<small>/mês</small></div>
      <div class="pv-de">Depois do lançamento: ${brl2(p.depois)}/mês</div>
      <ul class="lista">${p.itens.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>
      ${p.checkout ? `<a class="btn ${i === 1 ? 'btn-pri' : 'btn-sec'} grande" href="${esc(p.checkout)}" target="_blank" rel="noopener">Assinar o ${esc(p.nome)}</a>` : `<button class="btn btn-sec grande" data-entrar>Quero o ${esc(p.nome)}</button>`}
      <span style="color:var(--mut);font-size:12px;text-align:center;margin-top:8px">Use o mesmo e-mail para entrar depois</span></div>`).join('')
    document.querySelectorAll('#planos [data-entrar]').forEach((b) => (b.onclick = abrir))
  }).catch(() => {})

  // amostra ao vivo da vitrine e média de comissão dos produtos em alta
  fetch('/api/vitrine').then((r) => r.json()).then((j) => {
    const el = $('#amostra')
    const ganhos = (j.produtos || []).map((p) => p.ganho_venda).filter(Boolean).sort((a, b) => a - b)
    if (ganhos.length) {
      ganhoMedio = ganhos[Math.floor(ganhos.length / 2)]
      $('#mp-ganho').textContent = brl(ganhos[ganhos.length - 1])
    }
    calcular()
    if (!j.produtos?.length) { el.innerHTML = '<div class="vazio">A vitrine está sendo atualizada. Volte em alguns minutos.</div>'; return }
    el.innerHTML = j.produtos.slice(0, 8).map((p) => `<div class="pc">${p.imagem ? `<img src="${esc(p.imagem)}" alt="" loading="lazy">` : '<img alt="">'}
      <div class="pcb"><b>${esc(p.titulo)}</b>${p.ganho_venda ? `<span class="ganho">Ganhe até ${brl2(p.ganho_venda)} por venda</span>` : ''}
      <div class="calor">${Math.round(p.temperatura)}°<div class="temp"><i style="width:${Math.max(6, p.temperatura)}%"></i></div></div>
      <div class="meta"><span>★ ${(p.nota || 0).toFixed(1).replace('.', ',')}</span><span>${num(p.avaliacoes)} avaliações</span></div></div></div>`).join('')
  }).catch(() => calcular())
})()
