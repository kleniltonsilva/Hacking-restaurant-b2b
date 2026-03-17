"""
database.py - Conexão PostgreSQL + queries do CRM Derekh
Usa pool de conexões psycopg2. DATABASE_URL via env var.
"""
import json
import os
from datetime import date, datetime
from typing import Optional
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import pool

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Pool de conexões (inicializado no startup)
_pool = None


def init_pool(min_conn: int = 2, max_conn: int = 10):
    """Inicializa pool de conexões PostgreSQL."""
    global _pool
    if _pool is None:
        _pool = pool.SimpleConnectionPool(
            min_conn, max_conn, DATABASE_URL, cursor_factory=RealDictCursor
        )


def close_pool():
    """Fecha pool de conexões."""
    global _pool
    if _pool:
        _pool.closeall()
        _pool = None


@contextmanager
def get_conn():
    """Context manager que pega conexão do pool e devolve após uso."""
    if _pool is None:
        init_pool()
    conn = _pool.getconn()
    try:
        yield conn
    finally:
        _pool.putconn(conn)


def init_schema():
    """Executa o schema.sql para criar tabelas (idempotente)."""
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path) as f:
        sql = f.read()
    with get_conn() as conn:
        conn.cursor().execute(sql)
        conn.commit()
    print("[CRM] Schema PostgreSQL aplicado.")


# ============================================================
# FUNÇÕES DASHBOARD
# ============================================================

def kpis_dashboard() -> dict:
    """KPIs do dashboard: totais de leads, quentes, contactados, pipeline, clientes, emails."""
    with get_conn() as conn:
        cur = conn.cursor()
        kpis = {}
        cur.execute("SELECT COUNT(*) as c FROM leads")
        kpis["total_leads"] = cur.fetchone()["c"]

        cur.execute("SELECT COUNT(*) as c FROM leads WHERE lead_score >= 70")
        kpis["quentes"] = cur.fetchone()["c"]

        cur.execute("SELECT COUNT(*) as c FROM leads WHERE status_pipeline NOT IN ('novo', 'perdido')")
        kpis["contactados"] = cur.fetchone()["c"]

        cur.execute("SELECT COUNT(*) as c FROM leads WHERE status_pipeline NOT IN ('novo', 'cliente', 'perdido')")
        kpis["no_pipeline"] = cur.fetchone()["c"]

        cur.execute("SELECT COUNT(*) as c FROM leads WHERE status_pipeline = 'cliente'")
        kpis["clientes"] = cur.fetchone()["c"]

        cur.execute("SELECT COUNT(*) as c FROM interacoes WHERE tipo = 'email'")
        kpis["emails_enviados"] = cur.fetchone()["c"]

        return kpis


def funil_pipeline() -> list:
    """COUNT por status_pipeline para gráfico de funil."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT status_pipeline, COUNT(*) as total
            FROM leads
            GROUP BY status_pipeline
            ORDER BY CASE status_pipeline
                WHEN 'novo' THEN 1
                WHEN 'contactado' THEN 2
                WHEN 'respondeu' THEN 3
                WHEN 'demo_agendada' THEN 4
                WHEN 'proposta_enviada' THEN 5
                WHEN 'negociando' THEN 6
                WHEN 'cliente' THEN 7
                WHEN 'perdido' THEN 8
            END
        """)
        return [dict(r) for r in cur.fetchall()]


def distribuicao_segmento() -> list:
    """COUNT por segmento para gráfico de pizza."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT segmento, COUNT(*) as total
            FROM leads
            GROUP BY segmento
            ORDER BY total DESC
        """)
        return [dict(r) for r in cur.fetchall()]


def top_cidades(limite: int = 10) -> list:
    """Top N cidades por quantidade de leads."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT cidade, uf, COUNT(*) as total
            FROM leads
            WHERE cidade IS NOT NULL AND cidade != ''
            GROUP BY cidade, uf
            ORDER BY total DESC
            LIMIT %s
        """, (limite,))
        return [dict(r) for r in cur.fetchall()]


def followups_hoje() -> list:
    """Leads com follow-up agendado para hoje."""
    with get_conn() as conn:
        cur = conn.cursor()
        hoje = date.today()
        cur.execute("""
            SELECT id, cnpj, razao_social, nome_fantasia, cidade, uf,
                   lead_score, segmento, status_pipeline, data_proximo_contato
            FROM leads
            WHERE data_proximo_contato = %s
            ORDER BY lead_score DESC
            LIMIT 50
        """, (hoje,))
        return [dict(r) for r in cur.fetchall()]


def leads_quentes_sem_contato() -> list:
    """Leads com score >= 70 que nunca foram contactados."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, cnpj, razao_social, nome_fantasia, cidade, uf,
                   lead_score, segmento, status_pipeline,
                   telefone1, email
            FROM leads
            WHERE lead_score >= 70
            AND data_ultimo_contato IS NULL
            AND status_pipeline = 'novo'
            ORDER BY lead_score DESC
            LIMIT 50
        """)
        return [dict(r) for r in cur.fetchall()]


# ============================================================
# FUNÇÕES DELIVERY / VARREDURA
# ============================================================

def stats_delivery(cidade: str = None, uf: str = None) -> dict:
    """Stats globais ou por cidade de varredura delivery."""
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

        cur.execute(f"""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE tem_ifood = 1) as com_ifood,
                COUNT(*) FILTER (WHERE tem_rappi = 1) as com_rappi,
                COUNT(*) FILTER (WHERE tem_99food = 1) as com_99food,
                COUNT(*) FILTER (WHERE tem_ifood = 1 OR tem_rappi = 1 OR tem_99food = 1) as com_algum_delivery,
                COUNT(*) FILTER (WHERE COALESCE(tem_ifood, 0) = 0 AND COALESCE(tem_rappi, 0) = 0 AND COALESCE(tem_99food, 0) = 0) as sem_nenhum_delivery
            FROM leads
            {where}
        """, params)
        return dict(cur.fetchone())


def stats_delivery_por_cidade(limite: int = 50) -> list:
    """Stats de delivery por cidade para tabela comparativa."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                cidade, uf,
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE tem_ifood = 1) as com_ifood,
                COUNT(*) FILTER (WHERE tem_rappi = 1) as com_rappi,
                COUNT(*) FILTER (WHERE tem_99food = 1) as com_99food,
                COUNT(*) FILTER (WHERE COALESCE(tem_ifood, 0) = 0 AND COALESCE(tem_rappi, 0) = 0 AND COALESCE(tem_99food, 0) = 0) as sem_nenhum
            FROM leads
            WHERE cidade IS NOT NULL AND cidade != ''
            GROUP BY cidade, uf
            ORDER BY total DESC
            LIMIT %s
        """, (limite,))
        return [dict(r) for r in cur.fetchall()]


def cidades_escaneadas_ifood() -> list:
    """Cidades que têm pelo menos 1 lead com iFood verificado (com_ifood > 0)."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT cidade, uf, COUNT(*) as total,
                   COUNT(*) FILTER (WHERE tem_ifood = 1) as com_ifood
            FROM leads
            WHERE cidade IS NOT NULL AND cidade != ''
            GROUP BY cidade, uf
            HAVING COUNT(*) FILTER (WHERE tem_ifood = 1) > 0
            ORDER BY com_ifood DESC
        """)
        return [dict(r) for r in cur.fetchall()]


# ============================================================
# FUNÇÕES BUSCA
# ============================================================

def buscar_leads(filtros: dict, pagina: int = 1, por_pagina: int = 50) -> tuple:
    """Busca leads com filtros e paginação server-side.
    Retorna (lista_leads, total_count)."""
    with get_conn() as conn:
        cur = conn.cursor()
        where = ["1=1"]
        params = []

        if filtros.get("uf"):
            params.append(filtros["uf"].upper())
            where.append(f"l.uf = %s")

        if filtros.get("cidade"):
            params.append(filtros["cidade"].upper())
            where.append(f"l.cidade = %s")

        if filtros.get("segmento"):
            params.append(filtros["segmento"])
            where.append(f"l.segmento = %s")

        if filtros.get("status_pipeline"):
            params.append(filtros["status_pipeline"])
            where.append(f"l.status_pipeline = %s")

        if filtros.get("score_min"):
            params.append(int(filtros["score_min"]))
            where.append(f"l.lead_score >= %s")

        if filtros.get("score_max"):
            params.append(int(filtros["score_max"]))
            where.append(f"l.lead_score <= %s")

        if filtros.get("tem_ifood") == "sim":
            where.append("l.tem_ifood = 1")
        elif filtros.get("tem_ifood") == "nao":
            where.append("(l.tem_ifood = 0 OR l.tem_ifood IS NULL)")

        if filtros.get("tem_rappi") == "sim":
            where.append("l.tem_rappi = 1")
        elif filtros.get("tem_rappi") == "nao":
            where.append("(l.tem_rappi = 0 OR l.tem_rappi IS NULL)")

        if filtros.get("tem_99food") == "sim":
            where.append("l.tem_99food = 1")
        elif filtros.get("tem_99food") == "nao":
            where.append("(l.tem_99food = 0 OR l.tem_99food IS NULL)")

        if filtros.get("eh_rede") == "sim":
            where.append("l.multi_restaurante = 1")

        if filtros.get("q"):
            termo = f"%{filtros['q']}%"
            params.extend([termo, termo, termo])
            where.append("(l.razao_social ILIKE %s OR l.nome_fantasia ILIKE %s OR l.cnpj LIKE %s)")

        where_clause = " AND ".join(where)

        # Count total
        cur.execute(f"SELECT COUNT(*) as c FROM leads l WHERE {where_clause}", params)
        total = cur.fetchone()["c"]

        # Busca com paginação
        offset = (pagina - 1) * por_pagina
        params.extend([por_pagina, offset])
        cur.execute(f"""
            SELECT l.id, l.cnpj, l.razao_social, l.nome_fantasia,
                   l.cidade, l.uf, l.lead_score, l.segmento,
                   l.status_pipeline, l.telefone1, l.email,
                   l.tem_ifood, l.tem_rappi, l.tem_99food,
                   l.capital_social, l.data_abertura,
                   l.rating, l.website, l.data_ultimo_contato
            FROM leads l
            WHERE {where_clause}
            ORDER BY l.lead_score DESC, l.razao_social ASC
            LIMIT %s OFFSET %s
        """, params)

        return [dict(r) for r in cur.fetchall()], total


def listar_ufs_disponiveis() -> list:
    """UFs distintas presentes no banco."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT uf FROM leads
            WHERE uf IS NOT NULL AND uf != ''
            ORDER BY uf
        """)
        return [r["uf"] for r in cur.fetchall()]


def listar_cidades_disponiveis(uf: str = None) -> list:
    """Cidades disponíveis, opcionalmente filtradas por UF."""
    with get_conn() as conn:
        cur = conn.cursor()
        if uf:
            cur.execute("""
                SELECT cidade, uf, COUNT(*) as total
                FROM leads
                WHERE uf = %s AND cidade IS NOT NULL AND cidade != ''
                GROUP BY cidade, uf
                ORDER BY total DESC
            """, (uf.upper(),))
        else:
            cur.execute("""
                SELECT cidade, uf, COUNT(*) as total
                FROM leads
                WHERE cidade IS NOT NULL AND cidade != ''
                GROUP BY cidade, uf
                ORDER BY total DESC
                LIMIT 100
            """)
        return [dict(r) for r in cur.fetchall()]


# ============================================================
# FUNÇÕES FICHA
# ============================================================

def obter_lead(lead_id: int) -> Optional[dict]:
    """Retorna lead completo."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM leads WHERE id = %s", (lead_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def obter_interacoes_lead(lead_id: int) -> list:
    """Retorna interações de um lead, ordem cronológica reversa."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM interacoes
            WHERE lead_id = %s
            ORDER BY created_at DESC
        """, (lead_id,))
        return [dict(r) for r in cur.fetchall()]


def obter_socios_lead(lead_id: int) -> list:
    """Retorna sócios do lead (parse do socios_json)."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT socios_json FROM leads WHERE id = %s", (lead_id,))
        row = cur.fetchone()
        if row and row["socios_json"]:
            data = row["socios_json"]
            if isinstance(data, str):
                try:
                    return json.loads(data)
                except (json.JSONDecodeError, TypeError):
                    return []
            elif isinstance(data, list):
                return data
        return []


# ============================================================
# FUNÇÕES PIPELINE (AÇÕES)
# ============================================================

def atualizar_status_pipeline(lead_id: int, status: str, motivo_perda: str = None):
    """Atualiza status do pipeline de um lead."""
    with get_conn() as conn:
        cur = conn.cursor()
        if status == "perdido" and motivo_perda:
            cur.execute("""
                UPDATE leads SET status_pipeline = %s, motivo_perda = %s WHERE id = %s
            """, (status, motivo_perda, lead_id))
        else:
            cur.execute("""
                UPDATE leads SET status_pipeline = %s WHERE id = %s
            """, (status, lead_id))
        conn.commit()


def agendar_followup(lead_id: int, data: str):
    """Agenda data de próximo contato."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE leads SET data_proximo_contato = %s WHERE id = %s", (data, lead_id))
        conn.commit()


def registrar_interacao(lead_id: int, tipo: str, canal: str, conteudo: str,
                        resultado: str, email_message_id: str = None):
    """Registra interação e atualiza data_ultimo_contato."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO interacoes (lead_id, tipo, canal, conteudo, resultado, email_message_id)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (lead_id, tipo, canal, conteudo, resultado, email_message_id))
        cur.execute("""
            UPDATE leads SET data_ultimo_contato = NOW() WHERE id = %s
        """, (lead_id,))
        conn.commit()


def atualizar_notas(lead_id: int, notas: str):
    """Salva notas de um lead."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE leads SET notas = %s WHERE id = %s", (notas, lead_id))
        conn.commit()


# ============================================================
# FUNÇÕES PIPELINE (CONSULTA)
# ============================================================

def leads_por_pipeline(status: str, limite: int = 20) -> list:
    """Retorna top N leads de um status do pipeline, ordenados por score."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, cnpj, razao_social, nome_fantasia, cidade, uf,
                   lead_score, segmento, data_proximo_contato
            FROM leads
            WHERE status_pipeline = %s
            ORDER BY lead_score DESC
            LIMIT %s
        """, (status, limite))
        return [dict(r) for r in cur.fetchall()]


def contagem_pipeline() -> dict:
    """Retorna contagem por status do pipeline."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT status_pipeline, COUNT(*) as total
            FROM leads
            GROUP BY status_pipeline
        """)
        return {r["status_pipeline"]: r["total"] for r in cur.fetchall()}


# ============================================================
# EXPORT
# ============================================================

def buscar_leads_para_export(filtros: dict) -> list:
    """Busca leads para exportação (sem paginação)."""
    with get_conn() as conn:
        cur = conn.cursor()
        where = ["1=1"]
        params = []

        if filtros.get("uf"):
            params.append(filtros["uf"].upper())
            where.append("uf = %s")
        if filtros.get("cidade"):
            params.append(filtros["cidade"].upper())
            where.append("cidade = %s")
        if filtros.get("segmento"):
            params.append(filtros["segmento"])
            where.append("segmento = %s")
        if filtros.get("status_pipeline"):
            params.append(filtros["status_pipeline"])
            where.append("status_pipeline = %s")

        where_clause = " AND ".join(where)
        cur.execute(f"""
            SELECT * FROM leads
            WHERE {where_clause}
            ORDER BY lead_score DESC
        """, params)
        return [dict(r) for r in cur.fetchall()]


# ============================================================
# EMAIL TEMPLATES CRUD
# ============================================================

def listar_email_templates() -> list:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM email_templates ORDER BY created_at DESC")
        return [dict(r) for r in cur.fetchall()]


def obter_email_template(template_id: int) -> Optional[dict]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM email_templates WHERE id = %s", (template_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def criar_email_template(nome: str, assunto: str, corpo_html: str, segmento_alvo: str = None) -> int:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO email_templates (nome, assunto, corpo_html, segmento_alvo)
            VALUES (%s, %s, %s, %s) RETURNING id
        """, (nome, assunto, corpo_html, segmento_alvo))
        new_id = cur.fetchone()["id"]
        conn.commit()
        return new_id


def atualizar_email_template(template_id: int, nome: str, assunto: str,
                              corpo_html: str, segmento_alvo: str = None):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE email_templates
            SET nome = %s, assunto = %s, corpo_html = %s, segmento_alvo = %s
            WHERE id = %s
        """, (nome, assunto, corpo_html, segmento_alvo, template_id))
        conn.commit()


def deletar_email_template(template_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM email_templates WHERE id = %s", (template_id,))
        conn.commit()


# ============================================================
# CAMPANHAS CRUD
# ============================================================

def listar_campanhas() -> list:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT c.*, t.nome as template_nome
            FROM campanhas_email c
            LEFT JOIN email_templates t ON c.template_id = t.id
            ORDER BY c.created_at DESC
        """)
        return [dict(r) for r in cur.fetchall()]


def obter_campanha(campanha_id: int) -> Optional[dict]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT c.*, t.nome as template_nome, t.assunto as template_assunto
            FROM campanhas_email c
            LEFT JOIN email_templates t ON c.template_id = t.id
            WHERE c.id = %s
        """, (campanha_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def criar_campanha(nome: str, template_id: int, filtros_json: dict = None) -> int:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO campanhas_email (nome, template_id, filtros_json)
            VALUES (%s, %s, %s) RETURNING id
        """, (nome, template_id, json.dumps(filtros_json) if filtros_json else None))
        new_id = cur.fetchone()["id"]
        conn.commit()
        return new_id


def atualizar_campanha_contadores(campanha_id: int, campo: str, incremento: int = 1):
    """Incrementa um contador da campanha (total_enviados, total_abertos, etc)."""
    campos_validos = {"total_enviados", "total_abertos", "total_clicados", "total_bounced"}
    if campo not in campos_validos:
        return
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            UPDATE campanhas_email SET {campo} = {campo} + %s WHERE id = %s
        """, (incremento, campanha_id))
        conn.commit()


def atualizar_status_campanha(campanha_id: int, status: str):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE campanhas_email SET status = %s WHERE id = %s", (status, campanha_id))
        conn.commit()


# ============================================================
# SEQUÊNCIAS
# ============================================================

def listar_sequencias() -> list:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT s.*,
                   COUNT(DISTINCT se.id) as total_etapas,
                   COUNT(DISTINCT ls.id) FILTER (WHERE ls.status = 'ativo') as leads_ativos
            FROM sequencias_email s
            LEFT JOIN sequencia_etapas se ON s.id = se.sequencia_id
            LEFT JOIN lead_sequencia ls ON s.id = ls.sequencia_id
            GROUP BY s.id
            ORDER BY s.created_at DESC
        """)
        return [dict(r) for r in cur.fetchall()]


def obter_sequencia(sequencia_id: int) -> Optional[dict]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM sequencias_email WHERE id = %s", (sequencia_id,))
        row = cur.fetchone()
        if not row:
            return None
        seq = dict(row)

        cur.execute("""
            SELECT se.*, t.nome as template_nome, t.assunto as template_assunto
            FROM sequencia_etapas se
            JOIN email_templates t ON se.template_id = t.id
            WHERE se.sequencia_id = %s
            ORDER BY se.ordem
        """, (sequencia_id,))
        seq["etapas"] = [dict(r) for r in cur.fetchall()]

        return seq


def criar_sequencia(nome: str, descricao: str = None) -> int:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO sequencias_email (nome, descricao) VALUES (%s, %s) RETURNING id
        """, (nome, descricao))
        new_id = cur.fetchone()["id"]
        conn.commit()
        return new_id


def adicionar_etapa_sequencia(sequencia_id: int, template_id: int,
                               dias_espera: int, condicao: str = "sempre"):
    with get_conn() as conn:
        cur = conn.cursor()
        # Determinar próxima ordem
        cur.execute("""
            SELECT COALESCE(MAX(ordem), 0) + 1 as prox
            FROM sequencia_etapas WHERE sequencia_id = %s
        """, (sequencia_id,))
        prox_ordem = cur.fetchone()["prox"]

        cur.execute("""
            INSERT INTO sequencia_etapas (sequencia_id, ordem, template_id, dias_espera, condicao)
            VALUES (%s, %s, %s, %s, %s)
        """, (sequencia_id, prox_ordem, template_id, dias_espera, condicao))
        conn.commit()


def inscrever_leads_sequencia(lead_ids: list, sequencia_id: int):
    """Inscreve múltiplos leads em uma sequência."""
    with get_conn() as conn:
        cur = conn.cursor()
        for lead_id in lead_ids:
            cur.execute("""
                INSERT INTO lead_sequencia (lead_id, sequencia_id, proximo_envio)
                VALUES (%s, %s, NOW())
                ON CONFLICT (lead_id, sequencia_id) DO NOTHING
            """, (lead_id, sequencia_id))
        conn.commit()


def leads_sequencia_pendentes() -> list:
    """Leads com envio pendente em sequências ativas."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT ls.*, l.cnpj, l.nome_fantasia, l.email,
                   s.nome as sequencia_nome,
                   se.template_id, se.condicao
            FROM lead_sequencia ls
            JOIN leads l ON ls.lead_id = l.id
            JOIN sequencias_email s ON ls.sequencia_id = s.id
            JOIN sequencia_etapas se ON se.sequencia_id = ls.sequencia_id
                AND se.ordem = ls.etapa_atual
            WHERE ls.status = 'ativo'
            AND ls.proximo_envio <= NOW()
            AND s.ativo = TRUE
            AND l.email IS NOT NULL
            AND l.email_invalido = 0
            ORDER BY ls.proximo_envio
            LIMIT 100
        """)
        return [dict(r) for r in cur.fetchall()]


def avancar_etapa_sequencia(lead_sequencia_id: int, dias_proxima: int):
    """Avança lead para próxima etapa da sequência."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE lead_sequencia
            SET etapa_atual = etapa_atual + 1,
                proximo_envio = NOW() + INTERVAL '%s days'
            WHERE id = %s
        """, (dias_proxima, lead_sequencia_id))

        # Verificar se passou da última etapa
        cur.execute("""
            SELECT ls.etapa_atual, COUNT(se.id) as total_etapas
            FROM lead_sequencia ls
            JOIN sequencia_etapas se ON se.sequencia_id = ls.sequencia_id
            WHERE ls.id = %s
            GROUP BY ls.etapa_atual
        """, (lead_sequencia_id,))
        row = cur.fetchone()
        if row and row["etapa_atual"] > row["total_etapas"]:
            cur.execute("""
                UPDATE lead_sequencia SET status = 'concluido' WHERE id = %s
            """, (lead_sequencia_id,))

        conn.commit()


# ============================================================
# WEBHOOK HELPERS
# ============================================================

def marcar_email_invalido(lead_id: int):
    """Marca email de um lead como inválido (bounced)."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE leads SET email_invalido = 1 WHERE id = %s", (lead_id,))
        conn.commit()


def buscar_interacao_por_email_id(email_message_id: str) -> Optional[dict]:
    """Busca interação pelo email_message_id do Resend."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT i.*, l.id as lead_id_fk
            FROM interacoes i
            JOIN leads l ON i.lead_id = l.id
            WHERE i.email_message_id = %s
        """, (email_message_id,))
        row = cur.fetchone()
        return dict(row) if row else None


# ============================================================
# CONFIGURAÇÕES DO SISTEMA
# ============================================================

def obter_configuracao(chave: str) -> Optional[str]:
    """Retorna valor de uma configuração ou None."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT valor FROM configuracoes WHERE chave = %s", (chave,))
        row = cur.fetchone()
        return row["valor"] if row else None


def obter_configuracoes_todas() -> dict:
    """Retorna todas as configurações como dict {chave: valor}."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT chave, valor FROM configuracoes")
        return {r["chave"]: r["valor"] for r in cur.fetchall()}


def salvar_configuracao(chave: str, valor: str):
    """UPSERT de uma configuração."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO configuracoes (chave, valor, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (chave) DO UPDATE SET valor = EXCLUDED.valor, updated_at = NOW()
        """, (chave, valor))
        conn.commit()


def cidade_tem_delivery_verificado(cidade: str, uf: str) -> bool:
    """Verifica se pelo menos 1 lead da cidade tem delivery verificado (tem_ifood=1 ou tem_rappi=1 ou tem_99food=1)."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*) as c FROM leads
            WHERE cidade = %s AND uf = %s
            AND (tem_ifood = 1 OR tem_rappi = 1 OR tem_99food = 1)
        """, (cidade.upper(), uf.upper()))
        return cur.fetchone()["c"] > 0
