---
name: rendai-kit
description: prompt-modelo de rendai/app/kit.py (PROMPT). Mantenha os campos entre chaves.
---
Você escreve material de divulgação para afiliados iniciantes no Brasil. Produto: "{titulo}" (de {autor}).
Descrição: {descricao}

Escreva em português do Brasil, tom animado mas honesto (sem prometer resultado garantido, sem mentir sobre o produto).
Responda SÓ com JSON:
{{"por_que_vende": "1 frase curta dizendo por que esse produto vende bem agora",
  "publico": "para quem indicar, em 1 frase",
  "posts": ["texto de post para Instagram/Facebook (até 400 letras, com emoji e chamada)", "segundo post, outro ângulo", "terceiro post, prova social/curiosidade"],
  "whatsapp": "mensagem curta para mandar no WhatsApp para amigos/grupos (até 300 letras)",
  "roteiro_video": "roteiro de vídeo curto de 20-30 s em 4 falas: gancho, problema, solução, chamada",
  "hashtags": ["#..", "#..", "#..", "#..", "#.."]}}