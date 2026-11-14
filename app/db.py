"""Banco do Rendaí (SQLite em /data)."""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager

ESQUEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
  id INTEGER PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  apelido TEXT,
  criado REAL NOT NULL,
  plano TEXT NOT NULL DEFAULT 'gratis',
  ativo_ate REAL NOT NULL DEFAULT 0,
  pedido TEXT,
  xp INTEGER NOT NULL DEFAULT 0,
  streak INTEGER NOT NULL DEFAULT 0,
  ultimo_dia TEXT
);
CREATE TABLE IF NOT EXISTS codigos (email TEXT PRIMARY KEY, hash TEXT NOT NULL, expira REAL NOT NULL, tentativas INTEGER NOT NULL DEFAULT 0, enviado REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessoes (hash TEXT PRIMARY KEY, usuario INTEGER NOT NULL, expira REAL NOT NULL);
CREATE TABLE IF NOT EXISTS produtos (
  id TEXT PRIMARY KEY,                 -- hotmart:<productId>
  plataforma TEXT NOT NULL,
  titulo TEXT NOT NULL,
  slug TEXT, codigo TEXT, imagem TEXT, categoria TEXT, topico TEXT, autor TEXT,
  descricao TEXT,
  nota REAL, avaliacoes INTEGER, alunos INTEGER,
  url TEXT NOT NULL,
  ativo INTEGER NOT NULL DEFAULT 1,    -- 0 = sumiu, vendas desligadas ou bloqueado pelo filtro
  motivo_inativo TEXT,
  temperatura REAL NOT NULL DEFAULT 0, -- 0 a 100
  frase TEXT,                          -- "por que está vendendo" (IA)
  visto REAL NOT NULL,                 -- 1a vez que apareceu
  conferido REAL NOT NULL,             -- última conferência de que a oferta existe
  oferta TEXT, preco REAL, comissao_pct REAL  -- preço do checkout; comissão real quando lida com conta de afiliado
);
CREATE TABLE IF NOT EXISTS historico (produto TEXT NOT NULL, dia TEXT NOT NULL, avaliacoes INTEGER, alunos INTEGER, PRIMARY KEY(produto, dia));
CREATE TABLE IF NOT EXISTS acoes (
  id INTEGER PRIMARY KEY, usuario INTEGER NOT NULL, tipo TEXT NOT NULL, produto TEXT, valor REAL, xp INTEGER NOT NULL, criado REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS acoes_usuario ON acoes(usuario, criado);
CREATE TABLE IF NOT EXISTS kits (produto TEXT PRIMARY KEY, conteudo TEXT NOT NULL, criado REAL NOT NULL);
CREATE TABLE IF NOT EXISTS conquistas (usuario INTEGER NOT NULL, codigo TEXT NOT NULL, criado REAL NOT NULL, PRIMARY KEY(usuario, codigo));
CREATE TABLE IF NOT EXISTS eventos_cakto (id TEXT PRIMARY KEY, recebido REAL NOT NULL, corpo TEXT NOT NULL);
"""


class Banco:
    def __init__(self, caminho: str):
        self._trava = threading.Lock()
        self.con = sqlite3.connect(caminho, check_same_thread=False, isolation_level=None)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.executescript(ESQUEMA)
        # migrações simples (colunas novas em banco que já existe)
        cols = {r[1] for r in self.con.execute("PRAGMA table_info(produtos)")}
        for col, tipo in (("oferta", "TEXT"), ("preco", "REAL"), ("comissao_pct", "REAL")):
            if col not in cols:
                self.con.execute(f"ALTER TABLE produtos ADD COLUMN {col} {tipo}")

    @contextmanager
    def tx(self):
        with self._trava:
            self.con.execute("BEGIN IMMEDIATE")
            try:
                yield self.con
                self.con.execute("COMMIT")
            except BaseException:
                self.con.execute("ROLLBACK")
                raise

    def um(self, sql, args=()):
        with self._trava:
            r = self.con.execute(sql, args).fetchone()
        return dict(r) if r else None

    def todos(self, sql, args=()):
        with self._trava:
            return [dict(r) for r in self.con.execute(sql, args).fetchall()]

    def exec(self, sql, args=()):
        with self._trava:
            return self.con.execute(sql, args)


def agora() -> float:
    return time.time()


def hoje() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() - 3 * 3600))  # dia de Brasília
