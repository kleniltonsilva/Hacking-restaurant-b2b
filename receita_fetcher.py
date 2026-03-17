"""
receita_fetcher.py - Funcoes de banco para CNPJs da Receita Federal
v4.0 - Apenas funcoes DB (cnpj.biz removido — RF fornece todos os dados)
"""
import json
import sqlite3
from datetime import datetime

from config import DB_PATH, normalizar_cidade
from logger import log


# ============================================================
# BANCO DE DADOS: TABELA DE CNPJs DA RECEITA
# ============================================================

def _get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_tabela_receita():
    """Cria a tabela cnpjs_receita se nao existir + migra colunas novas."""
    conn = _get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS cnpjs_receita (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cnpj TEXT UNIQUE NOT NULL,
                razao_social TEXT,
                nome_fantasia TEXT,
                situacao_cadastral TEXT,
                cnae_principal TEXT,

                -- Endereco
                logradouro TEXT,
                numero TEXT,
                complemento TEXT,
                bairro TEXT,
                cep TEXT,
                cidade TEXT,
                uf TEXT,
                endereco_completo TEXT,

                -- Contato
                email TEXT,
                telefone1 TEXT,
                telefone2 TEXT,
                telefone_proprietario TEXT,

                -- Dados societarios
                capital_social REAL,
                porte TEXT,
                natureza_juridica TEXT,
                data_abertura TEXT,
                simples INTEGER,
                mei INTEGER,
                tipo_empresa TEXT,

                -- Socios (JSON com lista completa)
                socios_json TEXT,

                -- Controle incremental
                fonte TEXT DEFAULT 'casadosdados',
                fonte_detalhamento TEXT,
                detalhado INTEGER DEFAULT 0,
                data_coleta TEXT,
                data_detalhamento TEXT,

                -- Delivery (iFood + Rappi + 99Food)
                tem_ifood INTEGER DEFAULT 0,
                ifood_nome TEXT,
                ifood_url TEXT,
                tem_rappi INTEGER DEFAULT 0,
                rappi_nome TEXT,
                rappi_url TEXT,
                tem_99food INTEGER DEFAULT 0,
                food99_nome TEXT,
                food99_url TEXT,

                -- Match com Google Maps
                restaurante_id INTEGER,
                score_match REAL,
                matched INTEGER DEFAULT 0,

                -- Multi-restaurante (socio com 2+ CNPJs)
                multi_restaurante INTEGER DEFAULT 0,

                -- RF Expandido (v4.0)
                enriquecido_rf INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_receita_cnpj ON cnpjs_receita(cnpj);
            CREATE INDEX IF NOT EXISTS idx_receita_cidade ON cnpjs_receita(cidade, uf);
            CREATE INDEX IF NOT EXISTS idx_receita_endereco ON cnpjs_receita(logradouro, numero, cidade);
            CREATE INDEX IF NOT EXISTS idx_receita_matched ON cnpjs_receita(matched);
            CREATE INDEX IF NOT EXISTS idx_receita_detalhado ON cnpjs_receita(detalhado);

            -- Tabela de controle de varreduras da Receita
            CREATE TABLE IF NOT EXISTS varreduras_receita (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cidade TEXT NOT NULL,
                uf TEXT NOT NULL,
                cnae TEXT NOT NULL,
                total_encontrados INTEGER DEFAULT 0,
                novos_inseridos INTEGER DEFAULT 0,
                data_varredura TEXT,
                UNIQUE(cidade, uf, cnae)
            );
        """)

        # Migrar colunas novas em bancos existentes
        migracoes = [
            ("telefone_proprietario", "TEXT"),
            ("tem_ifood", "INTEGER DEFAULT 0"),
            ("ifood_nome", "TEXT"),
            ("ifood_url", "TEXT"),
            ("tipo_empresa", "TEXT"),
            ("fonte_detalhamento", "TEXT"),
            ("multi_restaurante", "INTEGER DEFAULT 0"),
            ("email_proprietario", "TEXT"),
            ("tipo_negocio", "TEXT"),
            ("data_opcao_simples", "TEXT"),
            ("data_situacao_cadastral", "TEXT"),
            ("tentativas_falha", "INTEGER DEFAULT 0"),
            ("ultima_falha", "TEXT"),
            ("cnpjbiz_inexistente", "INTEGER DEFAULT 0"),
            ("enriquecido_rf", "INTEGER DEFAULT 0"),
            ("tem_rappi", "INTEGER DEFAULT 0"),
            ("rappi_nome", "TEXT"),
            ("rappi_url", "TEXT"),
            ("tem_99food", "INTEGER DEFAULT 0"),
            ("food99_nome", "TEXT"),
            ("food99_url", "TEXT"),
        ]
        cursor = conn.cursor()
        for coluna, tipo in migracoes:
            try:
                cursor.execute(f"ALTER TABLE cnpjs_receita ADD COLUMN {coluna} {tipo}")
            except sqlite3.OperationalError:
                pass

        conn.commit()
    finally:
        conn.close()


# ============================================================
# CRUD CNPJs
# ============================================================

def cnpj_ja_existe(cnpj: str) -> bool:
    """Verifica se um CNPJ ja esta no banco."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM cnpjs_receita WHERE cnpj = ?", (cnpj,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def cnpjs_existentes_cidade(cidade: str, uf: str) -> set:
    """Retorna set de CNPJs ja cadastrados para uma cidade."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT cnpj FROM cnpjs_receita WHERE cidade = ? AND uf = ?",
            (normalizar_cidade(cidade), uf.upper())
        ).fetchall()
        return {row["cnpj"] for row in rows}
    finally:
        conn.close()


def inserir_cnpj_receita(dados: dict) -> bool:
    """Insere um CNPJ da Receita no banco. Retorna True se inseriu (novo)."""
    conn = _get_connection()
    try:
        conn.execute("""
            INSERT OR IGNORE INTO cnpjs_receita (
                cnpj, razao_social, nome_fantasia, situacao_cadastral,
                cnae_principal, logradouro, numero, complemento, bairro,
                cep, cidade, uf, endereco_completo, email, telefone1,
                telefone2, capital_social, porte, natureza_juridica,
                data_abertura, fonte, data_coleta
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            dados.get("cnpj", ""),
            dados.get("razao_social", ""),
            dados.get("nome_fantasia", ""),
            dados.get("situacao_cadastral", "ATIVA"),
            dados.get("cnae_principal", ""),
            dados.get("logradouro", ""),
            dados.get("numero", ""),
            dados.get("complemento", ""),
            dados.get("bairro", ""),
            dados.get("cep", ""),
            dados.get("cidade", "").upper(),
            dados.get("uf", "").upper(),
            dados.get("endereco_completo", ""),
            dados.get("email", ""),
            dados.get("telefone1", ""),
            dados.get("telefone2", ""),
            dados.get("capital_social", 0),
            dados.get("porte", ""),
            dados.get("natureza_juridica", ""),
            dados.get("data_abertura", ""),
            dados.get("fonte", "casadosdados"),
            datetime.now().isoformat(),
        ))
        conn.commit()
        return conn.total_changes > 0
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def marcar_match(cnpj: str, restaurante_id: int, score: float):
    """Marca um CNPJ como 'matched' com um restaurante do Google Maps."""
    conn = _get_connection()
    try:
        conn.execute("""
            UPDATE cnpjs_receita
            SET restaurante_id = ?, score_match = ?, matched = 1
            WHERE cnpj = ?
        """, (restaurante_id, score, cnpj))
        conn.commit()
    finally:
        conn.close()


def obter_cnpjs_cidade(cidade: str, uf: str) -> list:
    """Retorna todos os CNPJs de restaurantes de uma cidade (para cruzamento)."""
    conn = _get_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM cnpjs_receita
            WHERE cidade = ? AND uf = ? AND situacao_cadastral = 'ATIVA'
            ORDER BY logradouro, numero
        """, (normalizar_cidade(cidade), uf.upper())).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def estatisticas_receita(cidade: str = None, uf: str = None) -> dict:
    """Estatisticas da base de CNPJs da Receita."""
    conn = _get_connection()
    try:
        where = ""
        params = []
        if cidade and uf:
            where = "WHERE cidade = ? AND uf = ?"
            params = [normalizar_cidade(cidade), uf.upper()]

        total = conn.execute(
            f"SELECT COUNT(*) FROM cnpjs_receita {where}", params
        ).fetchone()[0]

        detalhados = conn.execute(
            f"SELECT COUNT(*) FROM cnpjs_receita {where} {'AND' if where else 'WHERE'} detalhado = 1",
            params
        ).fetchone()[0]

        matched = conn.execute(
            f"SELECT COUNT(*) FROM cnpjs_receita {where} {'AND' if where else 'WHERE'} matched = 1",
            params
        ).fetchone()[0]

        com_email = conn.execute(
            f"SELECT COUNT(*) FROM cnpjs_receita {where} {'AND' if where else 'WHERE'} email != '' AND email IS NOT NULL",
            params
        ).fetchone()[0]

        com_tel = conn.execute(
            f"SELECT COUNT(*) FROM cnpjs_receita {where} {'AND' if where else 'WHERE'} telefone1 != '' AND telefone1 IS NOT NULL",
            params
        ).fetchone()[0]

        com_ifood = conn.execute(
            f"SELECT COUNT(*) FROM cnpjs_receita {where} {'AND' if where else 'WHERE'} tem_ifood = 1",
            params
        ).fetchone()[0]

        com_socios = conn.execute(
            f"SELECT COUNT(*) FROM cnpjs_receita {where} {'AND' if where else 'WHERE'} socios_json IS NOT NULL AND socios_json != '[]'",
            params
        ).fetchone()[0]

        return {
            "total": total,
            "detalhados": detalhados,
            "matched": matched,
            "com_email": com_email,
            "com_telefone": com_tel,
            "com_ifood": com_ifood,
            "com_socios": com_socios,
            "sem_detalhar": total - detalhados,
            "sem_match": total - matched,
        }
    finally:
        conn.close()


# ============================================================
# FUNCOES DELIVERY (iFood + Rappi + 99Food)
# ============================================================

# Mapeamento plataforma -> colunas no banco
_DELIVERY_COLUNAS = {
    "ifood": ("tem_ifood", "ifood_nome", "ifood_url"),
    "rappi": ("tem_rappi", "rappi_nome", "rappi_url"),
    "99food": ("tem_99food", "food99_nome", "food99_url"),
}


def obter_cnpjs_sem_ifood(cidade: str, uf: str, limite: int = 0) -> list:
    """Retorna CNPJs detalhados que ainda nao tiveram iFood verificado.
    Prioriza CNPJs com match no Maps (nome confirmado)."""
    conn = _get_connection()
    try:
        query = """
            SELECT cr.cnpj, cr.razao_social, cr.nome_fantasia, cr.cidade, cr.uf,
                   cr.matched, cr.restaurante_id,
                   r.nome as nome_maps
            FROM cnpjs_receita cr
            LEFT JOIN restaurantes r ON cr.restaurante_id = r.id
            WHERE cr.cidade = ? AND cr.uf = ? AND cr.detalhado = 1 AND cr.tem_ifood = 0
            AND cr.ifood_nome IS NULL
            ORDER BY cr.matched DESC
        """
        params = [normalizar_cidade(cidade), uf.upper()]
        if limite > 0:
            query += " LIMIT ?"
            params.append(limite)
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def atualizar_ifood_receita(cnpj: str, tem_ifood: bool, ifood_nome: str = "", ifood_url: str = ""):
    """Salva resultado da verificacao iFood na tabela cnpjs_receita."""
    conn = _get_connection()
    try:
        conn.execute("""
            UPDATE cnpjs_receita
            SET tem_ifood = ?, ifood_nome = ?, ifood_url = ?
            WHERE cnpj = ?
        """, (1 if tem_ifood else 0, ifood_nome, ifood_url, cnpj))
        conn.commit()
    finally:
        conn.close()


def atualizar_delivery_receita(cnpj: str, plataforma: str,
                                tem: bool, nome: str = "", url: str = ""):
    """Salva resultado de verificacao de delivery generico na cnpjs_receita.
    Funciona para qualquer plataforma: ifood, rappi, 99food."""
    colunas = _DELIVERY_COLUNAS.get(plataforma)
    if not colunas:
        log.warning(f"[DELIVERY] Plataforma desconhecida: {plataforma}")
        return
    col_tem, col_nome, col_url = colunas
    conn = _get_connection()
    try:
        conn.execute(f"""
            UPDATE cnpjs_receita
            SET {col_tem} = ?, {col_nome} = ?, {col_url} = ?
            WHERE cnpj = ?
        """, (1 if tem else 0, nome, url, cnpj))
        conn.commit()
    finally:
        conn.close()


def obter_cnpjs_sem_delivery(cidade: str, uf: str, plataforma: str,
                              limite: int = 0) -> list:
    """Retorna CNPJs que ainda nao tiveram verificacao de delivery para uma plataforma."""
    colunas = _DELIVERY_COLUNAS.get(plataforma)
    if not colunas:
        log.warning(f"[DELIVERY] Plataforma desconhecida: {plataforma}")
        return []
    col_tem, col_nome, _ = colunas
    conn = _get_connection()
    try:
        query = f"""
            SELECT cr.cnpj, cr.razao_social, cr.nome_fantasia, cr.cidade, cr.uf,
                   cr.matched, cr.restaurante_id,
                   r.nome as nome_maps
            FROM cnpjs_receita cr
            LEFT JOIN restaurantes r ON cr.restaurante_id = r.id
            WHERE cr.cidade = ? AND cr.uf = ? AND cr.detalhado = 1
            AND cr.{col_tem} = 0 AND cr.{col_nome} IS NULL
            ORDER BY cr.matched DESC
        """
        params = [normalizar_cidade(cidade), uf.upper()]
        if limite > 0:
            query += " LIMIT ?"
            params.append(limite)
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
