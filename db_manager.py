"""
db_manager.py - Gerenciador do banco de dados SQLite
Responsável por todas as operações CRUD e consultas.
"""
import sqlite3
from datetime import datetime
from typing import Optional
from config import DB_PATH, STATUS_PENDENTE, normalizar_cidade
from logger import log


def get_connection():
    """Retorna uma conexão com o banco configurada."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # Melhor performance para escritas
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ============================================================
# RESTAURANTES - CRUD
# ============================================================

def inserir_restaurante(dados: dict) -> Optional[int]:
    """
    Insere um restaurante no banco. Ignora duplicatas (nome+cidade+uf).
    Retorna o ID do registro ou None se já existia.
    """
    conn = get_connection()
    try:
        cursor = conn.execute("""
            INSERT OR IGNORE INTO restaurantes 
            (nome, endereco, telefone, website, cidade, uf, latitude, longitude,
             google_maps_url, rating, total_reviews, categoria, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            dados.get("nome", ""),
            dados.get("endereco", ""),
            dados.get("telefone", ""),
            dados.get("website", ""),
            dados.get("cidade", ""),
            dados.get("uf", ""),
            dados.get("latitude", ""),
            dados.get("longitude", ""),
            dados.get("google_maps_url", ""),
            dados.get("rating", ""),
            dados.get("total_reviews", ""),
            dados.get("categoria", ""),
            STATUS_PENDENTE,
        ))
        conn.commit()
        if cursor.rowcount > 0:
            return cursor.lastrowid
        return None
    except Exception as e:
        log.error(f"[DB ERRO] Inserção falhou: {e}")
        return None
    finally:
        conn.close()


def inserir_restaurantes_batch(lista_dados: list) -> int:
    """Insere múltiplos restaurantes de uma vez. Retorna qtd inseridos."""
    conn = get_connection()
    inseridos = 0
    try:
        for dados in lista_dados:
            cursor = conn.execute("""
                INSERT OR IGNORE INTO restaurantes 
                (nome, endereco, telefone, website, cidade, uf, latitude, longitude,
                 google_maps_url, rating, total_reviews, categoria, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                dados.get("nome", ""),
                dados.get("endereco", ""),
                dados.get("telefone", ""),
                dados.get("website", ""),
                dados.get("cidade", ""),
                dados.get("uf", ""),
                dados.get("latitude", ""),
                dados.get("longitude", ""),
                dados.get("google_maps_url", ""),
                dados.get("rating", ""),
                dados.get("total_reviews", ""),
                dados.get("categoria", ""),
                STATUS_PENDENTE,
            ))
            if cursor.rowcount > 0:
                inseridos += 1
        conn.commit()
    except Exception as e:
        log.error(f"[DB ERRO] Batch insert falhou: {e}")
        conn.rollback()
    finally:
        conn.close()
    return inseridos


def atualizar_status(restaurante_id: int, novo_status: str):
    """Atualiza o status de um restaurante."""
    conn = get_connection()
    try:
        conn.execute("""
            UPDATE restaurantes 
            SET status = ?, data_atualizacao = ?
            WHERE id = ?
        """, (novo_status, datetime.now().isoformat(), restaurante_id))
        conn.commit()
    finally:
        conn.close()


def atualizar_ifood(restaurante_id: int, tem_ifood: bool, ifood_nome: str = "", ifood_url: str = ""):
    """Atualiza informações do iFood para um restaurante."""
    conn = get_connection()
    try:
        conn.execute("""
            UPDATE restaurantes 
            SET tem_ifood = ?, ifood_nome = ?, ifood_url = ?, 
                status = 'ifood_checked', data_atualizacao = ?
            WHERE id = ?
        """, (1 if tem_ifood else 0, ifood_nome, ifood_url, 
              datetime.now().isoformat(), restaurante_id))
        conn.commit()
    finally:
        conn.close()


def atualizar_cnpj(restaurante_id: int, dados_cnpj: dict):
    """Atualiza dados do CNPJ para um restaurante (inclui email, telefone e tel proprietário)."""
    conn = get_connection()
    try:
        # Telefones como string separada por pipe
        telefones = dados_cnpj.get("telefones_receita", [])
        telefones_str = " | ".join(telefones) if isinstance(telefones, list) else str(telefones)

        conn.execute("""
            UPDATE restaurantes
            SET cnpj = ?, razao_social = ?, nome_fantasia = ?,
                situacao_cadastral = ?, data_abertura = ?,
                natureza_juridica = ?, capital_social = ?,
                email_receita = ?, telefones_receita = ?,
                telefone_proprietario = ?,
                porte_empresa = ?, simples = ?, mei = ?,
                score_confianca = ?,
                status = 'enriquecido', data_atualizacao = ?
            WHERE id = ?
        """, (
            dados_cnpj.get("cnpj", ""),
            dados_cnpj.get("razao_social", ""),
            dados_cnpj.get("nome_fantasia", ""),
            dados_cnpj.get("situacao_cadastral", ""),
            dados_cnpj.get("data_abertura", ""),
            dados_cnpj.get("natureza_juridica", ""),
            dados_cnpj.get("capital_social", 0),
            dados_cnpj.get("email_receita", ""),
            telefones_str,
            dados_cnpj.get("telefone_proprietario", ""),
            dados_cnpj.get("porte_empresa", ""),
            1 if dados_cnpj.get("simples") else 0,
            1 if dados_cnpj.get("mei") else 0,
            dados_cnpj.get("score_confianca", 0),
            datetime.now().isoformat(),
            restaurante_id,
        ))
        conn.commit()
    finally:
        conn.close()


def inserir_socios(restaurante_id: int, socios: list):
    """Insere a lista de sócios de um restaurante."""
    conn = get_connection()
    try:
        # Limpar sócios antigos
        conn.execute("DELETE FROM socios WHERE restaurante_id = ?", (restaurante_id,))
        for socio in socios:
            conn.execute("""
                INSERT INTO socios 
                (restaurante_id, nome_socio, qualificacao, tipo, cpf_cnpj_socio,
                 data_entrada, faixa_etaria)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                restaurante_id,
                socio.get("nome", ""),
                socio.get("qualificacao", ""),
                socio.get("tipo", ""),
                socio.get("cpf_cnpj", ""),
                socio.get("data_entrada", ""),
                socio.get("faixa_etaria", ""),
            ))
        conn.commit()
    finally:
        conn.close()


# ============================================================
# CONSULTAS
# ============================================================

def nomes_restaurantes_cidade(cidade: str, uf: str) -> set:
    """Retorna set de nomes (lowercase) de restaurantes já no DB para uma cidade."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT nome FROM restaurantes WHERE cidade = ? AND uf = ?",
            (cidade, uf)
        ).fetchall()
        return {row["nome"].strip().lower() for row in rows if row["nome"]}
    finally:
        conn.close()


def buscar_por_cidade(cidade: str, uf: str) -> list:
    """Retorna todos os restaurantes de uma cidade."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM restaurantes WHERE cidade = ? AND uf = ?
            ORDER BY nome
        """, (cidade, uf)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def buscar_pendentes(status: str, cidade: str = None, uf: str = None, limite: int = 100) -> list:
    """Retorna restaurantes com determinado status para processamento."""
    conn = get_connection()
    try:
        query = "SELECT * FROM restaurantes WHERE status = ?"
        params = [status]
        if cidade and uf:
            query += " AND cidade = ? AND uf = ?"
            params.extend([cidade, uf])
        query += f" LIMIT {limite}"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def buscar_socios_restaurante(restaurante_id: int) -> list:
    """Retorna os sócios de um restaurante."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM socios WHERE restaurante_id = ?
        """, (restaurante_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def estatisticas_gerais() -> dict:
    """Retorna estatísticas gerais do banco."""
    conn = get_connection()
    try:
        stats = {}
        stats["total"] = conn.execute("SELECT COUNT(*) FROM restaurantes").fetchone()[0]
        stats["pendentes"] = conn.execute(
            "SELECT COUNT(*) FROM restaurantes WHERE status = 'pendente'"
        ).fetchone()[0]
        stats["processados"] = conn.execute(
            "SELECT COUNT(*) FROM restaurantes WHERE status = 'processado'"
        ).fetchone()[0]
        stats["ifood_checked"] = conn.execute(
            "SELECT COUNT(*) FROM restaurantes WHERE status = 'ifood_checked'"
        ).fetchone()[0]
        stats["enriquecidos"] = conn.execute(
            "SELECT COUNT(*) FROM restaurantes WHERE status = 'enriquecido'"
        ).fetchone()[0]
        stats["com_ifood"] = conn.execute(
            "SELECT COUNT(*) FROM restaurantes WHERE tem_ifood = 1"
        ).fetchone()[0]
        stats["com_cnpj"] = conn.execute(
            "SELECT COUNT(*) FROM restaurantes WHERE cnpj IS NOT NULL AND cnpj != ''"
        ).fetchone()[0]
        stats["total_socios"] = conn.execute("SELECT COUNT(*) FROM socios").fetchone()[0]
        stats["com_tel_proprietario"] = conn.execute(
            "SELECT COUNT(*) FROM restaurantes WHERE telefone_proprietario IS NOT NULL AND telefone_proprietario != ''"
        ).fetchone()[0]

        # Por cidade
        rows = conn.execute("""
            SELECT cidade, uf, COUNT(*) as total,
                   SUM(CASE WHEN tem_ifood = 1 THEN 1 ELSE 0 END) as com_ifood,
                   SUM(CASE WHEN cnpj IS NOT NULL AND cnpj != '' THEN 1 ELSE 0 END) as com_cnpj,
                   SUM(CASE WHEN telefone_proprietario IS NOT NULL AND telefone_proprietario != '' THEN 1 ELSE 0 END) as com_tel_prop
            FROM restaurantes
            GROUP BY cidade, uf
            ORDER BY total DESC
        """).fetchall()
        stats["por_cidade"] = [dict(r) for r in rows]

        return stats
    finally:
        conn.close()


def buscar_todos_para_export(cidade: str = None, uf: str = None) -> list:
    """Retorna dados completos para exportação, incluindo sócios."""
    conn = get_connection()
    try:
        query = """
            SELECT r.*, 
                   GROUP_CONCAT(s.nome_socio, ' | ') as socios_nomes,
                   GROUP_CONCAT(s.qualificacao, ' | ') as socios_qualificacoes
            FROM restaurantes r
            LEFT JOIN socios s ON r.id = s.restaurante_id
        """
        params = []
        if cidade and uf:
            query += " WHERE r.cidade = ? AND r.uf = ?"
            params = [cidade, uf]
        query += " GROUP BY r.id ORDER BY r.cidade, r.nome"
        
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def buscar_leads_receita(cidade: str = None, uf: str = None) -> list:
    """Retorna TODOS os CNPJs da Receita (detalhados ou não).
    Inclui dados do Maps quando há match. Exporta a base completa."""
    conn = get_connection()
    try:
        query = """
            SELECT cr.*,
                   r.nome as nome_maps, r.endereco as endereco_maps,
                   r.telefone as telefone_maps, r.website as website_maps,
                   r.rating, r.total_reviews, r.google_maps_url,
                   r.tem_ifood as maps_tem_ifood, r.ifood_nome as maps_ifood_nome
            FROM cnpjs_receita cr
            LEFT JOIN restaurantes r ON cr.restaurante_id = r.id
            WHERE 1=1
        """
        params = []
        if cidade and uf:
            query += " AND cr.cidade = ? AND cr.uf = ?"
            params = [normalizar_cidade(cidade), uf.upper()]
        query += " ORDER BY cr.cidade, cr.razao_social"

        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def buscar_leads_detalhados(cidade: str = None, uf: str = None) -> list:
    """Retorna apenas CNPJs DETALHADOS da Receita (com dados completos).
    Usado para abas filtradas (Premium, Com Contato, Com Sócios)."""
    conn = get_connection()
    try:
        query = """
            SELECT cr.*,
                   r.nome as nome_maps, r.endereco as endereco_maps,
                   r.telefone as telefone_maps, r.website as website_maps,
                   r.rating, r.total_reviews, r.google_maps_url,
                   r.tem_ifood as maps_tem_ifood, r.ifood_nome as maps_ifood_nome
            FROM cnpjs_receita cr
            LEFT JOIN restaurantes r ON cr.restaurante_id = r.id
            WHERE cr.detalhado = 1
        """
        params = []
        if cidade and uf:
            query += " AND cr.cidade = ? AND cr.uf = ?"
            params = [normalizar_cidade(cidade), uf.upper()]
        query += " ORDER BY cr.cidade, cr.razao_social"

        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def resetar_banco():
    """Remove TODOS os dados do banco (incluindo Receita). USE COM CUIDADO."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM socios")
        conn.execute("DELETE FROM restaurantes")
        conn.execute("DELETE FROM varreduras")
        conn.execute("DELETE FROM cnpjs_receita")
        conn.execute("DELETE FROM varreduras_receita")
        conn.execute("DELETE FROM sqlite_sequence")
        conn.commit()
        log.info("[DB] 🗑️  Banco resetado com sucesso (incluindo Receita).")
    finally:
        conn.close()


# ============================================================
# VARREDURAS - CONTROLE
# ============================================================

def registrar_varredura(cidade: str, uf: str):
    """Registra ou atualiza uma varredura para uma cidade."""
    conn = get_connection()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO varreduras (cidade, uf, status, inicio)
            VALUES (?, ?, 'em_andamento', ?)
        """, (cidade, uf, datetime.now().isoformat()))
        conn.commit()
    finally:
        conn.close()


def finalizar_varredura(cidade: str, uf: str, total: int):
    """Marca uma varredura como concluída."""
    conn = get_connection()
    try:
        conn.execute("""
            UPDATE varreduras 
            SET status = 'concluida', total_encontrados = ?, fim = ?
            WHERE cidade = ? AND uf = ?
        """, (total, datetime.now().isoformat(), cidade, uf))
        conn.commit()
    finally:
        conn.close()


def buscar_cnpjs_para_maps_direcionado(cidade: str, uf: str) -> list:
    """Retorna CNPJs detalhados sem match no Maps, com endereço disponível."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM cnpjs_receita
            WHERE cidade = ? AND uf = ?
            AND detalhado = 1 AND matched = 0
            AND logradouro IS NOT NULL AND logradouro != ''
            ORDER BY data_coleta ASC
        """, (normalizar_cidade(cidade), uf.upper())).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def inserir_restaurante_e_vincular(dados_maps: dict, dados_cnpj: dict) -> Optional[int]:
    """
    Insere restaurante do Maps e vincula ao CNPJ em uma transacao.
    - INSERT OR IGNORE na tabela restaurantes
    - UPDATE cnpjs_receita SET matched=1, restaurante_id, score_match
    - Propaga dados do CNPJ para o restaurante
    Retorna o ID do restaurante ou None.
    """
    conn = get_connection()
    try:
        # Inserir restaurante (ou pegar existente)
        cursor = conn.execute("""
            INSERT OR IGNORE INTO restaurantes
            (nome, endereco, telefone, website, cidade, uf, latitude, longitude,
             google_maps_url, rating, total_reviews, categoria, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'processado')
        """, (
            dados_maps.get("nome", ""),
            dados_maps.get("endereco", ""),
            dados_maps.get("telefone", ""),
            dados_maps.get("website", ""),
            dados_maps.get("cidade", ""),
            dados_maps.get("uf", ""),
            dados_maps.get("latitude", ""),
            dados_maps.get("longitude", ""),
            dados_maps.get("google_maps_url", ""),
            dados_maps.get("rating", ""),
            dados_maps.get("total_reviews", ""),
            dados_maps.get("categoria", ""),
        ))

        if cursor.rowcount > 0:
            rest_id = cursor.lastrowid
        else:
            # Restaurante ja existe, buscar ID
            row = conn.execute(
                "SELECT id FROM restaurantes WHERE nome = ? AND cidade = ? AND uf = ?",
                (dados_maps.get("nome", ""), dados_maps.get("cidade", ""), dados_maps.get("uf", ""))
            ).fetchone()
            rest_id = row["id"] if row else None

        if not rest_id:
            conn.rollback()
            return None

        cnpj = dados_cnpj.get("cnpj", "")
        score = dados_cnpj.get("score_match", 0)

        # Vincular CNPJ ao restaurante
        conn.execute("""
            UPDATE cnpjs_receita
            SET matched = 1, restaurante_id = ?, score_match = ?
            WHERE cnpj = ?
        """, (rest_id, score, cnpj))

        # Propagar dados do CNPJ para o restaurante
        telefones = []
        t1 = dados_cnpj.get("telefone1", "")
        t2 = dados_cnpj.get("telefone2", "")
        if t1: telefones.append(t1)
        if t2: telefones.append(t2)
        telefones_str = " | ".join(telefones)

        conn.execute("""
            UPDATE restaurantes SET
                cnpj = ?,
                razao_social = ?,
                nome_fantasia = COALESCE(NULLIF(?, ''), nome_fantasia),
                situacao_cadastral = ?,
                data_abertura = ?,
                natureza_juridica = ?,
                capital_social = ?,
                email_receita = ?,
                telefones_receita = ?,
                telefone_proprietario = ?,
                porte_empresa = ?,
                simples = ?,
                mei = ?,
                score_confianca = ?,
                status = 'enriquecido',
                data_atualizacao = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            cnpj,
            dados_cnpj.get("razao_social", ""),
            dados_cnpj.get("nome_fantasia", ""),
            dados_cnpj.get("situacao_cadastral", "ATIVA"),
            dados_cnpj.get("data_abertura", ""),
            dados_cnpj.get("natureza_juridica", ""),
            dados_cnpj.get("capital_social", 0),
            dados_cnpj.get("email", ""),
            telefones_str,
            dados_cnpj.get("telefone_proprietario", ""),
            dados_cnpj.get("porte", ""),
            1 if dados_cnpj.get("simples") else 0,
            1 if dados_cnpj.get("mei") else 0,
            score,
            rest_id,
        ))

        # Inserir socios se houver
        import json
        socios_json = dados_cnpj.get("socios_json", "[]")
        try:
            socios = json.loads(socios_json) if socios_json else []
            for socio in socios:
                conn.execute("""
                    INSERT OR IGNORE INTO socios
                    (restaurante_id, nome_socio, qualificacao, tipo, cpf_cnpj_socio, data_entrada)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    rest_id,
                    socio.get("nome", ""),
                    socio.get("qualificacao", ""),
                    socio.get("tipo", ""),
                    socio.get("cpf_cnpj", ""),
                    socio.get("data_entrada", ""),
                ))
        except (json.JSONDecodeError, TypeError):
            pass

        conn.commit()
        return rest_id

    except Exception as e:
        log.error(f"[DB ERRO] inserir_restaurante_e_vincular: {e}")
        conn.rollback()
        return None
    finally:
        conn.close()


def cidades_ja_varridas() -> list:
    """Retorna lista de cidades que já foram varridas."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT cidade, uf, total_encontrados, status 
            FROM varreduras ORDER BY cidade
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
