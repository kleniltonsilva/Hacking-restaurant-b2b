"""
db_pg.py - PostgreSQL adapter para pipeline de scraping
Banco unico: derekh_crm (PostgreSQL)
Substitui funcoes SQLite de receita_fetcher.py e db_manager.py
"""
import os
import json
from datetime import datetime
from contextlib import contextmanager
from typing import Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import pool as pg_pool

from logger import log

DATABASE_URL = os.environ.get("DATABASE_URL", "")

_pool = None


def init_pg():
    """Inicializa pool de conexoes PostgreSQL."""
    global _pool, DATABASE_URL
    if _pool is not None:
        return
    DATABASE_URL = os.environ.get("DATABASE_URL", DATABASE_URL)
    if not DATABASE_URL:
        DATABASE_URL = "host=/var/run/postgresql dbname=derekh_crm"
    _pool = pg_pool.SimpleConnectionPool(
        2, 10, DATABASE_URL, cursor_factory=RealDictCursor
    )


def close_pg():
    """Fecha pool de conexoes."""
    global _pool
    if _pool:
        _pool.closeall()
        _pool = None


@contextmanager
def get_conn():
    """Context manager para conexao PG do pool."""
    if _pool is None:
        init_pg()
    conn = _pool.getconn()
    try:
        yield conn
    finally:
        _pool.putconn(conn)


# ============================================================
# DELIVERY FUNCTIONS (substitui receita_fetcher.py)
# ============================================================

_DELIVERY_COLUNAS_PG = {
    "ifood": ("tem_ifood", "nome_ifood", "url_ifood", "ifood_checked"),
    "rappi": ("tem_rappi", "nome_rappi", "url_rappi", "rappi_checked"),
    "99food": ("tem_99food", "nome_99food", "url_99food", "food99_checked"),
}


def obter_cnpjs_sem_ifood(cidade: str, uf: str, limite: int = 0) -> list:
    """Leads que ainda nao tiveram iFood verificado."""
    with get_conn() as conn:
        cur = conn.cursor()
        query = """
            SELECT cnpj, razao_social, nome_fantasia, cidade, uf,
                   COALESCE(matched, 0) as matched, nome_maps
            FROM leads
            WHERE cidade = %s AND uf = %s AND COALESCE(ifood_checked, 0) = 0
            ORDER BY COALESCE(matched, 0) DESC
        """
        params = [cidade.upper(), uf.upper()]
        if limite > 0:
            query += " LIMIT %s"
            params.append(limite)
        cur.execute(query, params)
        return [dict(r) for r in cur.fetchall()]


def obter_cnpjs_sem_delivery(cidade: str, uf: str, plataforma: str,
                              limite: int = 0) -> list:
    """Leads que ainda nao tiveram delivery verificado para uma plataforma."""
    colunas = _DELIVERY_COLUNAS_PG.get(plataforma)
    if not colunas:
        log.warning(f"[DELIVERY] Plataforma desconhecida: {plataforma}")
        return []
    _, _, _, col_checked = colunas
    with get_conn() as conn:
        cur = conn.cursor()
        query = f"""
            SELECT cnpj, razao_social, nome_fantasia, cidade, uf,
                   COALESCE(matched, 0) as matched, nome_maps
            FROM leads
            WHERE cidade = %s AND uf = %s AND COALESCE({col_checked}, 0) = 0
            ORDER BY COALESCE(matched, 0) DESC
        """
        params = [cidade.upper(), uf.upper()]
        if limite > 0:
            query += " LIMIT %s"
            params.append(limite)
        cur.execute(query, params)
        return [dict(r) for r in cur.fetchall()]


def atualizar_ifood_receita(cnpj: str, tem_ifood: bool,
                             ifood_nome: str = "", ifood_url: str = ""):
    """Salva resultado iFood no lead. Marca ifood_checked=1."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE leads
            SET tem_ifood = %s, nome_ifood = %s, url_ifood = %s, ifood_checked = 1
            WHERE cnpj = %s
        """, (1 if tem_ifood else 0, ifood_nome, ifood_url, cnpj))
        conn.commit()


def atualizar_delivery_receita(cnpj: str, plataforma: str,
                                tem: bool, nome: str = "", url: str = ""):
    """Salva resultado delivery generico no lead. Marca *_checked=1."""
    colunas = _DELIVERY_COLUNAS_PG.get(plataforma)
    if not colunas:
        log.warning(f"[DELIVERY] Plataforma desconhecida: {plataforma}")
        return
    col_tem, col_nome, col_url, col_checked = colunas
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            UPDATE leads
            SET {col_tem} = %s, {col_nome} = %s, {col_url} = %s, {col_checked} = 1
            WHERE cnpj = %s
        """, (1 if tem else 0, nome, url, cnpj))
        conn.commit()


# ============================================================
# MAPS FUNCTIONS (substitui db_manager.py)
# ============================================================

def buscar_cnpjs_para_maps_direcionado(cidade: str, uf: str) -> list:
    """Leads sem match Maps, com endereco disponivel."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT cnpj, razao_social, nome_fantasia, logradouro, numero,
                   complemento, bairro, cidade, uf, cep,
                   telefone1, telefone2, email,
                   capital_social, porte, natureza_juridica,
                   simples, mei, socios_json,
                   COALESCE(detalhado, 1) as detalhado,
                   COALESCE(matched, 0) as matched
            FROM leads
            WHERE cidade = %s AND uf = %s
            AND COALESCE(maps_checked, 0) = 0
            AND logradouro IS NOT NULL AND logradouro != ''
            ORDER BY lead_score DESC
        """, (cidade.upper(), uf.upper()))
        return [dict(r) for r in cur.fetchall()]


def atualizar_lead_maps(cnpj: str, dados_maps: dict, score: float = 0):
    """Atualiza lead com dados do Google Maps."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE leads SET
                nome_maps = %s, endereco_maps = %s, telefone_maps = %s,
                website = COALESCE(NULLIF(%s, ''), website),
                rating = %s, total_reviews = %s, google_maps_url = %s,
                categoria = %s, score_match = %s,
                matched = 1, maps_checked = 1
            WHERE cnpj = %s
        """, (
            dados_maps.get("nome", ""),
            dados_maps.get("endereco", ""),
            dados_maps.get("telefone", ""),
            dados_maps.get("website", ""),
            dados_maps.get("rating"),
            dados_maps.get("total_reviews"),
            dados_maps.get("google_maps_url", ""),
            dados_maps.get("categoria", ""),
            score,
            cnpj,
        ))
        conn.commit()


def marcar_maps_checked(cnpj: str):
    """Marca lead como verificado no Maps (mesmo sem resultado)."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE leads SET maps_checked = 1 WHERE cnpj = %s", (cnpj,)
        )
        conn.commit()


# ============================================================
# CONSULTAS GERAIS
# ============================================================

def estatisticas_receita(cidade: str = None, uf: str = None) -> dict:
    """Estatisticas da base de leads (compativel com main.py)."""
    with get_conn() as conn:
        cur = conn.cursor()
        where_parts = []
        params = []
        if cidade:
            where_parts.append("cidade = %s")
            params.append(cidade.upper())
        if uf:
            where_parts.append("uf = %s")
            params.append(uf.upper())
        where = "WHERE " + " AND ".join(where_parts) if where_parts else ""

        cur.execute(f"SELECT COUNT(*) as c FROM leads {where}", params)
        total = cur.fetchone()["c"]

        def count_extra(extra_cond):
            conn_word = " AND " if where else " WHERE "
            cur.execute(
                f"SELECT COUNT(*) as c FROM leads {where}{conn_word}{extra_cond}",
                params
            )
            return cur.fetchone()["c"]

        detalhados = count_extra("COALESCE(detalhado, 0) = 1")
        matched = count_extra("COALESCE(matched, 0) = 1")

        return {
            "total": total,
            "detalhados": detalhados,
            "matched": matched,
            "com_email": count_extra("email IS NOT NULL AND email != ''"),
            "com_telefone": count_extra("telefone1 IS NOT NULL AND telefone1 != ''"),
            "com_ifood": count_extra("tem_ifood = 1"),
            "com_socios": count_extra("socios_json IS NOT NULL AND socios_json::text != '[]'"),
            "sem_detalhar": total - detalhados,
            "sem_match": total - matched,
        }


def obter_cnpjs_cidade(cidade: str, uf: str) -> list:
    """Retorna todos os leads de uma cidade."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM leads
            WHERE cidade = %s AND uf = %s AND situacao_cadastral = 'ATIVA'
            ORDER BY logradouro, numero
        """, (cidade.upper(), uf.upper()))
        return [dict(r) for r in cur.fetchall()]


def cnpjs_existentes_cidade(cidade: str, uf: str) -> set:
    """Retorna set de CNPJs existentes para uma cidade."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT cnpj FROM leads WHERE cidade = %s AND uf = %s",
            (cidade.upper(), uf.upper())
        )
        return {r["cnpj"] for r in cur.fetchall()}


# ============================================================
# SCAN JOBS (para CRM scanner)
# ============================================================

def criar_scan_job(cidades: list, etapas: list, headless: bool = True) -> int:
    """Cria um novo job de scan. Retorna o ID."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO scan_jobs (cidades, etapas, headless, status)
            VALUES (%s, %s, %s, 'pendente')
            RETURNING id
        """, (json.dumps(cidades), json.dumps(etapas), headless))
        job_id = cur.fetchone()["id"]
        conn.commit()
        return job_id


def atualizar_scan_job(job_id: int, **kwargs):
    """Atualiza campos de um scan job."""
    valid_fields = {
        "status", "cidade_atual", "etapa_atual", "total_leads",
        "processados", "encontrados", "erros", "progresso",
        "started_at", "finished_at"
    }
    sets = []
    params = []
    for k, v in kwargs.items():
        if k not in valid_fields:
            continue
        if k == "progresso":
            v = json.dumps(v)
        sets.append(f"{k} = %s")
        params.append(v)
    if not sets:
        return
    params.append(job_id)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE scan_jobs SET {', '.join(sets)} WHERE id = %s", params
        )
        conn.commit()


def obter_scan_job(job_id: int) -> Optional[dict]:
    """Retorna um scan job."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM scan_jobs WHERE id = %s", (job_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def listar_scan_jobs(limite: int = 20) -> list:
    """Lista scan jobs recentes."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM scan_jobs
            ORDER BY created_at DESC
            LIMIT %s
        """, (limite,))
        return [dict(r) for r in cur.fetchall()]


def scan_log(job_id: int, message: str, level: str = "info"):
    """Insere um log no scan job."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO scan_logs (scan_job_id, level, message)
            VALUES (%s, %s, %s)
        """, (job_id, level, message))
        conn.commit()


def obter_scan_logs(job_id: int, offset: int = 0, limite: int = 200) -> list:
    """Retorna logs de um scan job."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, level, message,
                   TO_CHAR(created_at, 'HH24:MI:SS') as hora
            FROM scan_logs
            WHERE scan_job_id = %s AND id > %s
            ORDER BY id ASC
            LIMIT %s
        """, (job_id, offset, limite))
        return [dict(r) for r in cur.fetchall()]


def existe_scan_ativo(cidade: str = None) -> Optional[dict]:
    """Verifica se existe scan ativo (para protecao contra duplicatas)."""
    with get_conn() as conn:
        cur = conn.cursor()
        if cidade:
            cur.execute("""
                SELECT * FROM scan_jobs
                WHERE status IN ('pendente', 'executando')
                AND cidades::text LIKE %s
                ORDER BY created_at DESC LIMIT 1
            """, (f'%{cidade}%',))
        else:
            cur.execute("""
                SELECT * FROM scan_jobs
                WHERE status IN ('pendente', 'executando')
                ORDER BY created_at DESC LIMIT 1
            """)
        row = cur.fetchone()
        return dict(row) if row else None


# ============================================================
# STATS PARA SCANNER (cidades com progresso)
# ============================================================

def stats_cidades_scanner() -> list:
    """Stats por cidade para a pagina do scanner."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                cidade, uf,
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE COALESCE(ifood_checked, 0) = 1) as ifood_checked,
                COUNT(*) FILTER (WHERE tem_ifood = 1) as com_ifood,
                COUNT(*) FILTER (WHERE COALESCE(rappi_checked, 0) = 1) as rappi_checked,
                COUNT(*) FILTER (WHERE tem_rappi = 1) as com_rappi,
                COUNT(*) FILTER (WHERE COALESCE(food99_checked, 0) = 1) as food99_checked,
                COUNT(*) FILTER (WHERE tem_99food = 1) as com_99food,
                COUNT(*) FILTER (WHERE COALESCE(maps_checked, 0) = 1) as maps_checked,
                COUNT(*) FILTER (WHERE COALESCE(matched, 0) = 1) as com_maps
            FROM leads
            WHERE cidade IS NOT NULL AND cidade != ''
            GROUP BY cidade, uf
            ORDER BY COUNT(*) DESC
        """)
        return [dict(r) for r in cur.fetchall()]
